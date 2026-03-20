"""
DeFiLlama fetcher — TVL, chain breakdown, protocol revenue for crypto/DeFi companies.
No API key required.
"""

import httpx

BASE_URL = "https://api.llama.fi"

# Cache the full protocols list to avoid re-fetching 1MB+ per query
_protocols_cache: list[dict] | None = None

# For chains (not DeFi protocols), use /v2/chains endpoint instead of /tvl/{slug}
CHAIN_OVERRIDES = {
    "ton": "TON",
    "ton foundation": "TON",
    "toncoin": "TON",
    # Solana: use /v2/chains endpoint for chain-level TVL (not DeFi protocol endpoint)
    "solana": "Solana",
    "sol": "Solana",
}

SLUG_OVERRIDES = {
    "ondo finance": "ondo-finance",
    "solana foundation": "solana",
    "ethereum foundation": "ethereum",
    "arbitrum foundation": "arbitrum",
    "chainlink": "chainlink",
    "uniswap": "uniswap",
    "aave": "aave",
    "compound": "compound-finance",
    "lido": "lido",
    "makerdao": "makerdao",
    "curve": "curve-dex",
    "pendle": "pendle",
    "jupiter": "jupiter",
    "hyperliquid": "hyperliquid-dex",
}


def _fmt_usd(value: float) -> str:
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.1f}B"
    elif value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    else:
        return f"${value / 1_000:.1f}K"


async def _get_protocols(client: httpx.AsyncClient) -> list[dict]:
    global _protocols_cache
    if _protocols_cache is not None:
        return _protocols_cache
    resp = await client.get(f"{BASE_URL}/protocols", timeout=10)
    resp.raise_for_status()
    _protocols_cache = resp.json()
    return _protocols_cache


async def _resolve_defillama_slug(company_name: str, client: httpx.AsyncClient) -> str | None:
    """Resolve company_name → DeFiLlama protocol slug."""
    name_lower = company_name.lower().strip()

    # 1. Hardcoded overrides
    if name_lower in SLUG_OVERRIDES:
        return SLUG_OVERRIDES[name_lower]

    # 2. Direct lowercase attempt (e.g. "Aave" → "aave")
    slug_candidate = name_lower.replace(" ", "-")
    try:
        resp = await client.get(f"{BASE_URL}/tvl/{slug_candidate}", timeout=10)
        if resp.status_code == 200:
            return slug_candidate
    except Exception:
        pass

    # 3. Fuzzy match against full protocols list
    try:
        protocols = await _get_protocols(client)
        for p in protocols:
            p_name = p.get("name", "").lower()
            p_slug = p.get("slug", "")
            if p_name == name_lower:
                return p_slug
        # Partial match: name is a substring
        for p in protocols:
            p_name = p.get("name", "").lower()
            p_slug = p.get("slug", "")
            if name_lower in p_name or p_name in name_lower:
                return p_slug
    except Exception:
        pass

    return None


async def _fetch_chain_tvl(company_name: str, chain_name: str) -> dict:
    """Fetch TVL for a Layer-1 chain from /v2/chains (TON, Solana, etc.)."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{BASE_URL}/v2/chains")
            resp.raise_for_status()
            chains = resp.json()
            chain = next(
                (c for c in chains if c.get("name", "").lower() == chain_name.lower()),
                None,
            )
            if not chain:
                return {"source": "defillama", "success": False, "error": f"Chain '{chain_name}' not found"}
            tvl = float(chain.get("tvl") or 0)
            print(f"[defillama] {company_name} -> chain={chain_name}, tvl={_fmt_usd(tvl)}")
            return {
                "source": "defillama",
                "success": True,
                "protocol_name": chain.get("name", chain_name),
                "slug": chain_name.lower(),
                "category": "Chain",
                "tvl": tvl,
                "tvl_formatted": _fmt_usd(tvl),
                "chain_breakdown": {},
                "top_3_chains": "",
                "mcap": chain.get("mcap") or None,
                "mcap_to_tvl": None,
                "chains_count": 0,
                "raises": [],
            }
    except httpx.HTTPStatusError as e:
        return {"source": "defillama", "success": False, "error": f"HTTP {e.response.status_code}"}
    except Exception as e:
        return {"source": "defillama", "success": False, "error": str(e)}


async def fetch_defillama(company_name: str) -> dict:
    """
    Fetch TVL, chain breakdown, and protocol metadata from DeFiLlama.

    Returns:
        {
            "source": "defillama",
            "success": True/False,
            "protocol_name": str,
            "slug": str,
            "category": str,
            "tvl": float,
            "tvl_formatted": str,
            "chain_breakdown": dict,
            "top_3_chains": str,
            "mcap": float | None,
            "mcap_to_tvl": float | None,
            "chains_count": int,
            "raises": list,
        }
    """
    # Chain entities use a different endpoint
    name_lower = company_name.lower().strip()
    if name_lower in CHAIN_OVERRIDES:
        return await _fetch_chain_tvl(company_name, CHAIN_OVERRIDES[name_lower])

    try:
        async with httpx.AsyncClient() as client:
            slug = await _resolve_defillama_slug(company_name, client)
            if not slug:
                print(f"[defillama] {company_name} -> slug not found")
                return {"source": "defillama", "success": False, "error": "Protocol not found"}

            # Fetch current TVL (lightweight endpoint — always fast)
            tvl_resp = await client.get(f"{BASE_URL}/tvl/{slug}", timeout=10)
            tvl_resp.raise_for_status()
            tvl: float = float(tvl_resp.text.strip())

            # Fetch full protocol detail (larger payload — use longer timeout)
            protocol_name: str = company_name
            category: str = ""
            mcap: float | None = None
            chain_breakdown: dict = {}
            raises: list = []

            try:
                detail_resp = await client.get(f"{BASE_URL}/protocol/{slug}", timeout=20)
                detail_resp.raise_for_status()
                detail = detail_resp.json()

                protocol_name = detail.get("name", company_name)
                category = detail.get("category", "")
                mcap = detail.get("mcap") or None
                chain_breakdown = detail.get("currentChainTvls", {})
                raises = detail.get("raises", [])

            except (httpx.TimeoutException, httpx.ReadTimeout):
                print(f"[defillama] /protocol/{slug} timeout, using /tvl fallback")
                # Pull category + chains from /protocols list cache
                try:
                    protocols = await _get_protocols(client)
                    for p in protocols:
                        if p.get("slug") == slug:
                            protocol_name = p.get("name", company_name)
                            category = p.get("category", "")
                            chain_breakdown = p.get("chainTvls", {})
                            break
                except Exception:
                    pass

            chains_count: int = len(chain_breakdown)

            # Top 3 chains by TVL
            sorted_chains = sorted(chain_breakdown.items(), key=lambda x: x[1], reverse=True)
            top_3_chains = ", ".join(
                f"{chain} ({_fmt_usd(val)})"
                for chain, val in sorted_chains[:3]
            )

            mcap_to_tvl: float | None = None
            if mcap and tvl and tvl > 0:
                mcap_to_tvl = round(mcap / tvl, 3)

            print(f"[defillama] {company_name} -> slug={slug}, tvl={_fmt_usd(tvl)}")

            return {
                "source": "defillama",
                "success": True,
                "protocol_name": protocol_name,
                "slug": slug,
                "category": category,
                "tvl": tvl,
                "tvl_formatted": _fmt_usd(tvl),
                "chain_breakdown": chain_breakdown,
                "top_3_chains": top_3_chains,
                "mcap": mcap,
                "mcap_to_tvl": mcap_to_tvl,
                "chains_count": chains_count,
                "raises": raises,
            }

    except httpx.HTTPStatusError as e:
        print(f"[defillama] HTTP error for {company_name}: {e.response.status_code}")
        return {"source": "defillama", "success": False, "error": f"HTTP {e.response.status_code}"}
    except Exception as e:
        print(f"[defillama] error for {company_name}: {e}")
        return {"source": "defillama", "success": False, "error": str(e)}
