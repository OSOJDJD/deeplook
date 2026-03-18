import os
import re
from pathlib import Path
import httpx
import asyncio
from .cache import cache_key, get_cached, set_cache


def _save_key_to_env(api_key: str) -> None:
    """Append ROOTDATA_SKILL_KEY to .env if not already present."""
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if env_path.exists():
        content = env_path.read_text()
        if re.search(r"^ROOTDATA_SKILL_KEY=", content, re.MULTILINE):
            return  # already saved
    with open(env_path, "a") as f:
        f.write(f"\nROOTDATA_SKILL_KEY={api_key}\n")
    print(f"[rootdata] saved skill key to {env_path}")


async def fetch_rootdata(company_name: str) -> dict:
    key = cache_key("rootdata", company_name)
    cached = get_cached(key)
    if cached is not None:
        return cached

    api_key = os.environ.get("ROOTDATA_SKILL_KEY")
    base = "https://api.rootdata.com/open/skill"

    if not api_key:
        # Auto-init: request a skill key from RootData
        try:
            async with httpx.AsyncClient(timeout=10) as init_client:
                init_resp = await init_client.post(
                    f"{base}/init",
                    headers={"Content-Type": "application/json"},
                )
                init_resp.raise_for_status()
                init_data = init_resp.json()
                api_key = init_data.get("data", {}).get("api_key") or init_data.get("api_key")
                if not api_key:
                    return {"source": "rootdata", "success": False, "error": f"skill/init returned no api_key: {init_data}"}
                _save_key_to_env(api_key)
                os.environ["ROOTDATA_SKILL_KEY"] = api_key
                print(f"[rootdata] auto-init skill key: {api_key}")
        except Exception as e:
            return {"source": "rootdata", "success": False, "error": f"skill/init failed: {e}"}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=10, headers=headers) as client:
            # Step 1: Search for the company
            search_resp = await client.post(
                f"{base}/ser_inv",
                json={"query": company_name, "precise_x_search": False},
            )
            search_resp.raise_for_status()
            search_data = search_resp.json()

            results = search_data.get("data") or []
            if not results:
                return {
                    "source": "rootdata",
                    "success": False,
                    "search_results": search_data,
                    "error": "No results found",
                }

            # P0-1: name validation — reject if result name has no word overlap with query
            first_result = results[0]
            result_name = (first_result.get("name") or "").lower().replace(".", " ")
            query_words = set(company_name.lower().split())
            result_words = set(result_name.split())
            if result_words and not query_words.intersection(result_words):
                print(f"[rootdata] NAME_MISMATCH: '{company_name}' -> '{first_result.get('name')}' — no overlap, rejecting")
                return {
                    "source": "rootdata",
                    "success": False,
                    "error": f"Name mismatch: '{company_name}' != '{first_result.get('name')}'",
                }

            project_id = first_result.get("id")
            if not project_id:
                return {
                    "source": "rootdata",
                    "success": False,
                    "search_results": search_data,
                    "error": "No project_id in first result",
                }

            # Step 2: Fetch item details and funding in parallel
            details_coro = client.post(
                f"{base}/get_item",
                json={"project_id": project_id, "include_investors": True},
            )
            funding_coro = client.post(
                f"{base}/get_fac",
                json={"project_id": project_id, "page": 1, "page_size": 5},
            )
            details_resp, funding_resp = await asyncio.gather(details_coro, funding_coro)

            details_resp.raise_for_status()
            funding_resp.raise_for_status()

            result = {
                "source": "rootdata",
                "success": True,
                "search_results": search_data,
                "project_details": details_resp.json(),
                "funding": funding_resp.json(),
            }
            set_cache(key, result)
            return result
    except Exception as e:
        return {"source": "rootdata", "success": False, "error": str(e)}
