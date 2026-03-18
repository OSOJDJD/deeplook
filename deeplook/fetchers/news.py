from datetime import datetime, timedelta

from .utils import robust_search
from .cache import cache_key, get_cached, set_cache
from ..debug_log import log


def _is_too_old(date_str: str, max_age_days: int) -> bool:
    """Return True if the article date is older than max_age_days. If date unknown, keep it."""
    if not date_str:
        return False  # 不確定的不刪
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%B %d, %Y"):
        try:
            pub_date = datetime.strptime(date_str[:len(fmt) + 4], fmt)
            cutoff = datetime.now() - timedelta(days=max_age_days)
            return pub_date < cutoff
        except ValueError:
            continue
    return False  # 無法解析就保留


async def fetch_news(company_name: str, queries: list[str] | None = None,
                     max_age_days: int | None = None) -> dict:
    # Build cache key from company_name + queries fingerprint
    query_fingerprint = "|".join(queries) if queries else "default"
    key = cache_key("news", f"{company_name}::{query_fingerprint}")
    cached = get_cached(key)
    if cached is not None:
        log('news', 'CACHE_HIT', company_name)
        return cached

    log('news', 'START', company_name)

    # Use provided queries or fall back to defaults
    if queries:
        search_queries = queries
        print(f"[strategy] news: using custom queries: {queries}")
    else:
        search_queries = [
            f"{company_name} latest news",
            f"{company_name} funding growth",
        ]

    seen_urls = set()
    articles = []

    for query in search_queries:
        results = await robust_search(query, "news", max_results=5, timeout=5)
        for item in results:
            url = item.get("url", "")
            if url in seen_urls:
                continue
            seen_urls.add(url)
            date_str = item.get("date", "")
            if max_age_days and _is_too_old(date_str, max_age_days):
                log('news', 'SKIP_OLD', f"url={url} date={date_str}")
                continue
            articles.append({
                "title": item.get("title", ""),
                "url": url,
                "content": (item.get("snippet", ""))[:2000],
                "date": date_str,
            })

    log('news', 'SUCCESS', f'articles={len(articles)}')
    result = {"source": "news", "articles": articles, "success": True}
    set_cache(key, result)
    return result
