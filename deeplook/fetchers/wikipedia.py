import httpx
from .cache import cache_key, get_cached, set_cache


async def fetch_wikipedia(company_name: str) -> dict:
    """Fetch Wikipedia summary via REST API. Lightweight, no auth needed."""
    key = cache_key("wikipedia", company_name)
    cached = get_cached(key)
    if cached is not None:
        return cached

    slug = company_name.replace(" ", "_")
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{slug}"

    try:
        headers = {
            "User-Agent": "DeepLook/1.0 (https://github.com/deeplook; research@deeplook.dev)",
            "Accept-Language": "en-US,en;q=0.9",
        }
        async with httpx.AsyncClient(timeout=10, headers=headers) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                extract = data.get("extract", "")
                result = {
                    "source": "wikipedia",
                    "success": True,
                    "title": data.get("title", ""),
                    "extract": extract[:3000],
                    "description": data.get("description", ""),
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
