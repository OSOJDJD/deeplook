import httpx
from .cache import cache_key, get_cached, set_cache


async def fetch_wikipedia(company_name: str) -> dict:
    """Fetch Wikipedia summary via REST API. Lightweight, no auth needed."""
    key = cache_key("wikipedia", company_name)
    cached = get_cached(key)
    if cached is not None:
        return cached

    slug = company_name.replace(" ", "_")
    # Use MediaWiki action API with exintro=true to get the full intro section
    # (REST summary /page/summary/ only returns a 3-sentence snippet)
    url = (
        "https://en.wikipedia.org/w/api.php"
        f"?action=query&prop=extracts|description&exintro=true&explaintext=true"
        f"&titles={slug}&format=json&redirects=1"
    )

    try:
        headers = {
            "User-Agent": "DeepLook/1.0 (https://github.com/deeplook; research@deeplook.dev)",
            "Accept-Language": "en-US,en;q=0.9",
        }
        async with httpx.AsyncClient(timeout=10, headers=headers) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                pages = (data.get("query") or {}).get("pages") or {}
                page = next(iter(pages.values()), {})
                if page.get("missing") is not None:
                    return {"source": "wikipedia", "success": False, "error": "page not found"}
                extract = page.get("extract", "")
                description = page.get("description", "") or ""
                result = {
                    "source": "wikipedia",
                    "success": True,
                    "title": page.get("title", ""),
                    "extract": extract[:3000],
                    "description": description,
                }
                set_cache(key, result)
                return result
            return {
                "source": "wikipedia",
                "success": False,
                "error": f"HTTP {resp.status_code}",
            }
    except Exception as e:
        return {
            "source": "wikipedia",
            "success": False,
            "error": str(e),
        }
