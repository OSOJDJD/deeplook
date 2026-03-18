import os
import httpx
from .cache import cache_key, get_cached, set_cache


HARDCODED_FALLBACK = {
    "ondo finance": "ondo",
    "the open network": "the-open-network",
    "chainlink": "chainlink",
    "arbitrum": "arbitrum",
    "ton foundation": "the-open-network",
    "ton": "the-open-network",
    "toncoin": "the-open-network",
    "solana foundation": "solana",
    "solana": "solana",
    "ethereum foundation": "ethereum",
    "arbitrum foundation": "arbitrum",
}

STRIP_SUFFIXES = ["Finance", "Protocol", "Network", "Labs", "Foundation", "Token"]


def _strip_suffix(name: str) -> str | None:
    """Return name with common suffix removed, or None if nothing stripped."""
    for suffix in STRIP_SUFFIXES:
        if name.endswith(suffix):
            stripped = name[: -len(suffix)].strip()
            if stripped:
                return stripped
    return None


def _name_matches(company_name: str, coin: dict) -> bool:
    """Strict name matching — company name must match coin name or symbol."""
    name_lower = company_name.lower()
    coin_name = coin.get("name", "").lower()
    coin_symbol = coin.get("symbol", "").lower()
    return (name_lower in coin_name or coin_name in name_lower
            or name_lower == coin_symbol or coin_symbol == name_lower)


async def fetch_coingecko(company_name: str) -> dict:
    key = cache_key("coingecko", company_name)
    cached = get_cached(key)
    if cached is not None:
        return cached

    api_key = os.getenv("COINGECKO_API_KEY")
    headers = {"x-cg-demo-api-key": api_key} if api_key else {}

    async with httpx.AsyncClient(timeout=10, headers=headers) as client:

        async def _search(query: str) -> list:
            try:
                resp = await client.get(
                    "https://api.coingecko.com/api/v3/search",
                    params={"query": query},
                )
                resp.raise_for_status()
                return resp.json().get("coins", [])
            except (httpx.HTTPError, ValueError):
                return []

        # 1. Hardcoded fallback (case-insensitive)
        name_lower = company_name.lower().strip()
        if name_lower in HARDCODED_FALLBACK:
            coin_id = HARDCODED_FALLBACK[name_lower]
            matched_coin = {"id": coin_id, "name": company_name}
        else:
            # 2. Search full name
            coins = await _search(company_name)
            matching = [c for c in coins if _name_matches(company_name, c)]
            # Pick highest market cap (lowest market_cap_rank)
            matched_coin = min(
                matching, key=lambda c: c.get("market_cap_rank") or float("inf")
            ) if matching else None

            # 3. Strip suffix and retry
            if not matched_coin:
                stripped = _strip_suffix(company_name)
                if stripped:
                    coins2 = await _search(stripped)
                    matching2 = [c for c in coins2 if _name_matches(stripped, c)]
                    matched_coin = min(
                        matching2, key=lambda c: c.get("market_cap_rank") or float("inf")
                    ) if matching2 else None

            if not matched_coin:
                top = coins[0].get("name") if coins else "none"
                return {
                    "source": "coingecko",
                    "success": False,
                    "error": f"No coin closely matching '{company_name}' (top result was '{top}')",
                }

            coin_id = matched_coin["id"]

        # Fetch coin details
        try:
            detail_resp = await client.get(
                f"https://api.coingecko.com/api/v3/coins/{coin_id}",
            )
            detail_resp.raise_for_status()
            coin_detail = detail_resp.json()
        except (httpx.HTTPError, ValueError) as e:
            return {"source": "coingecko", "success": False, "error": f"Detail fetch failed: {e}"}

    market = coin_detail.get("market_data", {})
    description = (coin_detail.get("description", {}).get("en") or "")[:1000]

    result = {
        "source": "coingecko",
        "success": True,
        "coin_id": coin_id,
        "coin_name": matched_coin.get("name"),
        "description": description,
        "price_usd": market.get("current_price", {}).get("usd"),
        "market_cap": market.get("market_cap", {}).get("usd"),
        "price_change_30d_pct": market.get("price_change_percentage_30d"),
        "volume_24h": market.get("total_volume", {}).get("usd"),
    }
    set_cache(key, result)
    return result
