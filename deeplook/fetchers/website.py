import re
import asyncio
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from .utils import robust_search
from .cache import cache_key, get_cached, set_cache
from ..debug_log import log

_HEADERS = {
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": "Mozilla/5.0 (compatible; DeepLook/1.0)",
}

_BLACKLIST_DOMAINS = {
    "wikipedia.org", "linkedin.com", "crunchbase.com", "bloomberg.com",
    "reuters.com", "yahoo.com", "google.com", "facebook.com", "twitter.com",
    "youtube.com", "github.com", "reddit.com",
}


def _is_blacklisted(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower().lstrip("www.")
        return any(host == d or host.endswith("." + d) for d in _BLACKLIST_DOMAINS)
    except Exception:
        return False


async def _search_official_url(company_name: str) -> str | None:
    """Use search to find the official website URL."""
    results = await robust_search(f"{company_name} official website", "text", max_results=5, timeout=10)
    for item in results:
        url = item.get("url", "")
        if url and not _is_blacklisted(url):
            return url
    return None


def _guess_url(company_name: str) -> str:
    """Guess official website URL from company name."""
    slug = company_name.strip().lower()
    slug_nospace = re.sub(r"\s+", "", slug)
    slug_nospace = re.sub(r"[^a-z0-9]", "", slug_nospace)
    return f"https://{slug_nospace}.com"


def _guess_url_hyphenated(company_name: str) -> str:
    """Hyphenated fallback (e.g. "Morpho Labs" → "morpho-labs.com")."""
    slug = company_name.strip().lower()
    slug = re.sub(r"\s+", "-", slug)
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    slug = slug.strip("-")
    return f"https://{slug}.com"


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    return soup.get_text(separator=" ", strip=True)


def _scrape(url: str, client: httpx.Client) -> str | None:
    try:
        resp = client.get(url, follow_redirects=True)
        if resp.status_code == 200 and resp.text:
            return _html_to_text(resp.text)
    except Exception:
        pass
    return None


async def _fetch(company_name: str) -> dict:
    log('website', 'START', company_name)

    # Primary: DuckDuckGo search for official URL (async, no zombie threads)
    searched_url = await _search_official_url(company_name)
    if searched_url:
        log('website', 'SEARCH_HIT', searched_url)

    # Build candidate list: search result first, then guesses as fallback
    guessed = _guess_url(company_name)
    hyphenated = _guess_url_hyphenated(company_name)

    candidates = []
    if searched_url:
        candidates.append(searched_url)
    candidates.append(guessed)
    if hyphenated != guessed:
        candidates.append(hyphenated)

    def _do_http():
        with httpx.Client(timeout=10, headers=_HEADERS) as client:
            for url in candidates:
                text = _scrape(url, client)
                if text:
                    log('website', 'SUCCESS', f'url={url} content_length={len(text)}')
                    return {
                        "source": "website",
                        "url": url,
                        "content": text[:3000],
                        "success": True,
                    }
        return None

    result = await asyncio.to_thread(_do_http)
    if result:
        return result

    fallback_url = searched_url or guessed
    log('website', 'FAIL', 'All URL attempts failed')
    return {
        "source": "website",
        "url": fallback_url,
        "success": False,
        "error": "All URL attempts failed (search + guessed + hyphenated)",
    }


async def fetch_website(company_name: str) -> dict:
    key = cache_key("website", company_name)
    cached = get_cached(key)
    if cached is not None:
        log('website', 'CACHE_HIT', company_name)
        return cached
    try:
        result = await _fetch(company_name)
        if result.get("success"):
            set_cache(key, result)
        return result
    except Exception as e:
        return {"source": "website", "success": False, "error": str(e)}
