"""
Main entry point for the DeepLook company research agent.

Usage: python deeplook/research.py "Blockstream"
"""

import asyncio
import io
import json
import os
import sys
import time
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path

import httpx
import yfinance as yf
from dotenv import load_dotenv

from deeplook.fetchers.website import fetch_website
from deeplook.fetchers.news import fetch_news
from deeplook.fetchers.rootdata import fetch_rootdata
from deeplook.fetchers.coingecko import fetch_coingecko
from deeplook.fetchers.defillama import fetch_defillama, _resolve_defillama_slug
from deeplook.fetchers.yfinance_data import fetch_yfinance, fetch_yfinance_news, _resolve_ticker, fetch_peer_data
from deeplook.fetchers.wikipedia import fetch_wikipedia
from deeplook.fetchers.youtube import fetch_youtube
from deeplook.fetchers.sec_edgar import fetch_sec_edgar
from deeplook.fetchers.finnhub_fetcher import fetch_finnhub, _get_client as _finnhub_get_client
from deeplook.fetchers.search_strategy import (
    build_search_queries,
    get_active_fetchers,
    get_time_limits,
    get_fetcher_limits,
    validate_result,
    deduplicate_news,
    rank_articles,
)
from deeplook.debug_log import log


PROJECT_ROOT = Path(__file__).parent.parent


_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def _load_disambiguation() -> dict:
    """Load disambiguation table from data/disambiguation.json."""
    path = os.path.join(_DATA_DIR, "disambiguation.json")
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"WARNING: Failed to load disambiguation.json: {e}", file=sys.stderr)
        return {}


def _load_defunct() -> set:
    """Load defunct companies list from data/defunct.json."""
    path = os.path.join(_DATA_DIR, "defunct.json")
    try:
        with open(path, "r") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"WARNING: Failed to load defunct.json: {e}", file=sys.stderr)
        return set()


DEFUNCT_COMPANIES = _load_defunct()

DISAMBIGUATION = _load_disambiguation()


def load_env():
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    # Also load from the current project directory (picks up FINNHUB_API_KEY etc.)
    load_dotenv(override=False)

    llm_keys = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY")
    if not any(os.environ.get(k) for k in llm_keys):
        print(
            "ERROR: No LLM API key found. Set at least one of: "
            + ", ".join(llm_keys),
            file=sys.stderr,
        )
        sys.exit(1)


def refine_entity_type(company_name: str, company_type: str) -> str:
    """把粗分類細化成具體 entity type"""
    name_lower = company_name.lower().strip()

    # Defunct lock — never reclassify a known dead company
    if company_type == "defunct":
        return "defunct"

    # Disambiguation lock — don't override what was explicitly resolved
    if name_lower in DISAMBIGUATION:
        return DISAMBIGUATION[name_lower]["intended_type"]

    vc_keywords = ["capital", "ventures", "partners", "fund", "labs invest",
                   "a16z", "andreessen", "dragonfly", "pantera", "paradigm",
                   "polychain", "multicoin", "hack vc", "fulgur"]
    if any(kw in name_lower for kw in vc_keywords):
        return "venture_capital"

    exchange_keywords = ["binance", "coinbase", "kraken", "okx", "bybit",
                         "bitmart", "mexc", "gate.io", "kucoin", "bitget",
                         "exchange"]
    if any(kw in name_lower for kw in exchange_keywords):
        return "exchange"

    foundation_keywords = ["foundation", "protocol foundation"]
    if any(kw in name_lower for kw in foundation_keywords):
        return "foundation"

    return company_type


async def _coingecko_search_mcap(company_name: str, client: httpx.AsyncClient) -> tuple[str | None, int]:
    """Search CoinGecko for company_name. Returns (coin_id, market_cap_usd) or (None, 0)."""
    coingecko_key = os.environ.get("COINGECKO_API_KEY", "")
    try:
        resp = await client.get(
            "https://api.coingecko.com/api/v3/search",
            params={"query": company_name},
            headers={"x-cg-demo-api-key": coingecko_key} if coingecko_key else {},
        )
        resp.raise_for_status()
        data = resp.json()
        name_lower = company_name.lower()
        for coin in data.get("coins", []):
            coin_name = coin.get("name", "").lower()
            coin_symbol = coin.get("symbol", "").lower()
            if not (coin_name == name_lower or coin_symbol == name_lower
                    or name_lower == coin_name.split()[0]):
                continue
            coin_id = coin.get("id", "")
            mcap_rank = coin.get("market_cap_rank")
            if mcap_rank is not None and mcap_rank > 0:
                # Fetch actual mcap from detail endpoint
                try:
                    detail_resp = await client.get(
                        f"https://api.coingecko.com/api/v3/coins/{coin_id}",
                        params={"localization": "false", "tickers": "false",
                                "community_data": "false", "developer_data": "false"},
                        headers={"x-cg-demo-api-key": coingecko_key} if coingecko_key else {},
                    )
                    detail_resp.raise_for_status()
                    detail = detail_resp.json()
                    mcap = (detail.get("market_data") or {}).get("market_cap", {}).get("usd", 0)
                    if mcap and mcap > 10_000_000:  # $10M threshold — ignore meme/micro-cap tokens
                        return coin_id, int(mcap)
                except Exception:
                    pass
                return coin_id, 0
    except Exception as e:
        print(f"[detect] CoinGecko search failed: {e}", file=sys.stderr)
    return None, 0


async def detect_company_type(company_name: str) -> tuple[str, str | None, str | None]:
    """Detect whether the company is crypto, public_equity, or private.

    Returns (company_type, resolved_ticker, company_full_name).
    company_full_name is the human-readable name from yfinance (e.g. "Coherent Corp.").
    """
    _route_start = time.time()
    _yf_method = None

    # 0a. Defunct check — skip all API calls for known collapsed companies
    name_lower = company_name.lower().strip()
    if name_lower in DEFUNCT_COMPANIES:
        print(f"[detect] DEFUNCT: '{company_name}' is in known defunct companies list")
        log("detect", "ENTITY_ROUTE",
            f"'{company_name}' → defunct via defunct "
            f"(ticker=None) [{time.time()-_route_start:.1f}s]")
        return "defunct", None, None

    # 0. Disambiguation table — known name conflicts, checked before any API call
    if name_lower in DISAMBIGUATION:
        d = DISAMBIGUATION[name_lower]
        print(f"[detect] DISAMBIGUATION: '{company_name}' -> {d['intended_type']} (ticker={d.get('ticker')}) ({d['note']})")
        log("detect", "ENTITY_ROUTE",
            f"'{company_name}' → {d['intended_type']} via disambiguation "
            f"(ticker={d.get('ticker')}) [{time.time()-_route_start:.1f}s]")
        return d["intended_type"], d.get("ticker"), None

    # 1. Try yfinance first — direct ticker match
    yf_mcap = 0
    yf_ticker = None
    yf_fullname = None
    try:
        def _direct_check():
            info = yf.Ticker(company_name).info
            mcap = (info.get("marketCap") or 0) if info else 0
            name = (info.get("shortName") or info.get("longName")) if info else None
            return mcap, name
        yf_mcap, yf_fullname = await asyncio.wait_for(
            asyncio.to_thread(_direct_check), timeout=10
        )
        if yf_mcap > 0:
            yf_ticker = company_name
            _yf_method = "yfinance_direct"
        else:
            yf_fullname = None
    except (asyncio.TimeoutError, Exception) as e:
        print(f"yfinance detection failed: {e}", file=sys.stderr)

    # 1b. Try resolving company name → ticker via Yahoo Finance search
    if not yf_ticker:
        try:
            resolved = await asyncio.wait_for(_resolve_ticker(company_name), timeout=10)
            if resolved:
                def _resolved_check():
                    info = yf.Ticker(resolved).info
                    mcap = (info.get("marketCap") or 0) if info else 0
                    name = (info.get("shortName") or info.get("longName")) if info else None
                    return mcap, name
                _mcap, _name = await asyncio.wait_for(
                    asyncio.to_thread(_resolved_check), timeout=10
                )
                if _mcap > 0:
                    # Layer 3 sanity check: reject if resolved company name has zero
                    # word overlap with the query (e.g. "Canva" → "Covanta Holding").
                    # Skip this check when the query is already in ticker format.
                    if not (company_name.isupper() and len(company_name) <= 5) and _name:
                        q_words = set(company_name.lower().split())
                        n_words = set(_name.lower().replace(".", " ").split())
                        if not q_words.intersection(n_words):
                            print(f"[detect] NAME_MISMATCH: '{company_name}' -> '{_name}' "
                                  f"(ticker={resolved}) — no overlap, continuing to crypto checks")
                            _mcap = 0  # reject; fall through to CoinGecko / DeFiLlama
                    if _mcap > 0:
                        yf_mcap = _mcap
                        yf_ticker = resolved
                        yf_fullname = _name
                        _yf_method = "yfinance_search"
        except (asyncio.TimeoutError, Exception) as e:
            import traceback
            print(f"yfinance name resolution failed: {e}", file=sys.stderr)
            traceback.print_exc()

    # 1d. Finnhub symbol_lookup fallback — only runs if yfinance both steps failed
    if not yf_ticker:
        try:
            fh_client = _finnhub_get_client()
            if fh_client is not None:
                def _finnhub_lookup():
                    return fh_client.symbol_lookup(company_name)
                fh_result = await asyncio.wait_for(
                    asyncio.to_thread(_finnhub_lookup), timeout=5
                )
                fh_matches = [
                    r for r in (fh_result.get("result") or [])
                    if r.get("type") in ("Common Stock", "ADR")
                ]
                # Prefer US symbols (no dot) over international
                us_matches = [r for r in fh_matches if "." not in r.get("symbol", "")]
                candidates = us_matches or fh_matches
                for r in candidates:
                    symbol = r.get("symbol", "")
                    description = r.get("description", "")
                    # Name overlap sanity check (same logic as step 1b)
                    if not (company_name.isupper() and len(company_name) <= 5) and description:
                        q_words = set(company_name.lower().split())
                        d_words = set(description.lower().replace(".", " ").split())
                        if not q_words.intersection(d_words):
                            print(f"[detect] FINNHUB_NAME_MISMATCH: '{company_name}' -> '{description}' ({symbol}) — no overlap, skipping")
                            continue
                    yf_ticker = symbol
                    yf_fullname = description
                    _yf_method = "finnhub"
                    print(f"[detect] FINNHUB_FALLBACK: '{company_name}' -> '{symbol}' ({description})")
                    break
        except (asyncio.TimeoutError, Exception) as e:
            print(f"[detect] Finnhub fallback failed: {e}", file=sys.stderr)

    # 1c. If yfinance found a match, compare with CoinGecko market cap to resolve conflicts
    if yf_ticker:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                cg_coin_id, cg_mcap = await _coingecko_search_mcap(company_name, client)
                if cg_mcap > 0 and yf_mcap > 0 and cg_mcap > yf_mcap * 10:
                    print(f"[detect] MCAP_OVERRIDE: CoinGecko ${cg_mcap:,.0f} >> yfinance ${yf_mcap:,.0f}, using crypto")
                    log("detect", "ENTITY_ROUTE",
                        f"'{company_name}' → crypto via coingecko_override "
                        f"(ticker=None) [{time.time()-_route_start:.1f}s]")
                    return "crypto", None, None
        except Exception as e:
            print(f"[detect] CoinGecko mcap comparison failed: {e}", file=sys.stderr)
        print(f"Detected type: public_equity (yfinance ticker={yf_ticker} fullname={yf_fullname} marketCap={yf_mcap})")
        log("detect", "ENTITY_ROUTE",
            f"'{company_name}' → public_equity via {_yf_method or 'yfinance'} "
            f"(ticker={yf_ticker}) [{time.time()-_route_start:.1f}s]")
        return "public_equity", yf_ticker, yf_fullname

    # 2. Try CoinGecko with strict name matching
    coingecko_key = os.environ.get("COINGECKO_API_KEY", "")
    _cg_matched_coin = None  # set when a valid crypto match is found
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.coingecko.com/api/v3/search",
                params={"query": company_name},
                headers={"x-cg-demo-api-key": coingecko_key} if coingecko_key else {},
            )
            resp.raise_for_status()
            data = resp.json()
            name_lower = company_name.lower()
            for coin in data.get("coins", []):
                coin_name = coin.get("name", "").lower()
                coin_symbol = coin.get("symbol", "").lower()
                if not (coin_name == name_lower or coin_symbol == name_lower
                        or name_lower == coin_name.split()[0]):
                    continue
                mcap_rank = coin.get("market_cap_rank")
                if mcap_rank is not None and mcap_rank > 0:
                    _cg_matched_coin = coin.get("name")
                    break
                coin_id = coin.get("id", "")
                try:
                    detail_resp = await client.get(
                        f"https://api.coingecko.com/api/v3/coins/{coin_id}",
                        params={"localization": "false", "tickers": "false",
                                "community_data": "false", "developer_data": "false"},
                        headers={"x-cg-demo-api-key": coingecko_key} if coingecko_key else {},
                    )
                    detail_resp.raise_for_status()
                    detail = detail_resp.json()
                    mcap = (detail.get("market_data") or {}).get("market_cap", {}).get("usd", 0)
                    if mcap and mcap > 10_000_000:  # $10M threshold — ignore meme/micro-cap tokens
                        _cg_matched_coin = coin.get("name")
                        break
                    elif mcap:
                        print(f"[detect] MEME_TOKEN_SKIP: '{coin.get('name')}' mcap=${mcap:,.0f} < $10M threshold — treating as not-crypto")
                except Exception:
                    pass
    except Exception as e:
        print(f"CoinGecko detection failed: {e}", file=sys.stderr)

    if _cg_matched_coin:
        # Wikipedia guard: verify this is actually a crypto project, not a real company
        # with a same-name meme token on CoinGecko.
        _wiki_confirmed = True  # default: trust CoinGecko
        try:
            wiki_result = await asyncio.wait_for(
                fetch_wikipedia(company_name), timeout=3.0
            )
            wiki_text = (wiki_result or {}).get("extract", "")
            if len(wiki_text) > 100:
                _crypto_kws = [
                    "blockchain", "cryptocurrency", "token", "defi",
                    "web3", "decentralized", "crypto", "mining pool",
                    "consensus mechanism", "smart contract", "dapp",
                    "decentralized finance", "liquidity pool",
                ]
                if not any(kw in wiki_text.lower() for kw in _crypto_kws):
                    _wiki_confirmed = False
                    log("detect", "WIKI_GUARD",
                        f"'{company_name}' Wikipedia article has no crypto keywords "
                        f"— overriding CoinGecko match '{_cg_matched_coin}'")
                else:
                    log("detect", "WIKI_GUARD",
                        f"'{company_name}' Wikipedia has crypto keywords "
                        f"— confirming CoinGecko match '{_cg_matched_coin}'")
        except (asyncio.TimeoutError, Exception) as e:
            log("detect", "WIKI_GUARD",
                f"'{company_name}' Wikipedia check failed ({e}) — keeping CoinGecko match")

        if not _wiki_confirmed:
            log("detect", "ENTITY_ROUTE",
                f"'{company_name}' → private_or_unlisted via coingecko_wiki_blocked "
                f"(ticker=None) [{time.time()-_route_start:.1f}s]")
            return "private_or_unlisted", None, None

        print(f"Detected type: crypto (CoinGecko matched '{_cg_matched_coin}')")
        log("detect", "ENTITY_ROUTE",
            f"'{company_name}' → crypto via coingecko "
            f"(coin='{_cg_matched_coin}', ticker=None) [{time.time()-_route_start:.1f}s]")
        return "crypto", None, None

    # 3. Try DeFiLlama — catches DeFi protocols not on CoinGecko
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            slug = await _resolve_defillama_slug(company_name, client)
            if slug:
                tvl_resp = await client.get(
                    f"https://api.llama.fi/tvl/{slug}", timeout=10
                )
                if tvl_resp.status_code == 200:
                    tvl = float(tvl_resp.text.strip())
                    found = tvl > 0
                    print(f"[detect] DeFiLlama check for '{company_name}': found={found} (slug={slug}, tvl={tvl:,.0f})")
                    if found:
                        print(f"Detected type: crypto (DeFiLlama slug='{slug}', tvl={tvl:,.0f})")
                        log("detect", "ENTITY_ROUTE",
                            f"'{company_name}' → crypto via defillama "
                            f"(ticker=None) [{time.time()-_route_start:.1f}s]")
                        return "crypto", None, None
                else:
                    print(f"[detect] DeFiLlama check for '{company_name}': found=False (HTTP {tvl_resp.status_code})")
            else:
                print(f"[detect] DeFiLlama check for '{company_name}': found=False (no slug resolved)")
    except Exception as e:
        print(f"DeFiLlama detection failed: {e}", file=sys.stderr)

    # 4. Default — not public, not crypto: treat as private/unlisted startup
    print("Detected type: private_or_unlisted")
    log("detect", "ENTITY_ROUTE",
        f"'{company_name}' → private_or_unlisted via default_private "
        f"(ticker=None) [{time.time()-_route_start:.1f}s]")
    return "private_or_unlisted", None, None


async def run_fetcher(name: str, coro, timeout_seconds: int = 30):
    """Run a single fetcher with a timeout and error handling."""
    _t = time.time()
    try:
        result = await asyncio.wait_for(coro, timeout=timeout_seconds)
        return name, {"status": "ok", "data": result, "_timing_s": round(time.time() - _t, 2)}
    except asyncio.TimeoutError:
        print(f"[strategy] {name} timed out after {timeout_seconds}s", file=sys.stderr)
        return name, {"status": "timeout", "data": None, "_timing_s": round(time.time() - _t, 2)}
    except Exception as e:
        print(f"Fetcher {name} failed: {e}", file=sys.stderr)
        return name, {"status": "error", "error": str(e), "data": None, "_timing_s": round(time.time() - _t, 2)}


async def _round2_search(
    company_name: str,
    company_full_name: str | None,
    fetcher_results: dict,
) -> list[dict]:
    """Layer 3: Use Haiku to find info gaps from round 1, generate follow-up queries."""
    try:
        display_name = company_full_name or company_name

        # Build a compact summary of round 1 findings
        summary_parts: list[str] = []
        news = fetcher_results.get("news", {})
        if news.get("status") == "ok":
            for a in (news.get("data") or {}).get("articles", [])[:5]:
                summary_parts.append(f"- {a.get('title', '')}: {a.get('content', '')[:200]}")
        website = fetcher_results.get("website", {})
        if website.get("status") == "ok" and website.get("data"):
            summary_parts.append(str(website["data"])[:400])
        yf = fetcher_results.get("yfinance", {})
        if yf.get("status") == "ok" and yf.get("data"):
            summary_parts.append(str(yf["data"])[:300])

        if not summary_parts:
            print("[round2] no round 1 data to analyze, skipping")
            return []

        summary_text = "\n".join(summary_parts[:10])
        prompt = (
            f"Based on the following research results about {display_name}, "
            f"what key information is still missing? "
            f"Generate 2-3 follow-up search queries to fill the gaps.\n\n"
            f"Research so far:\n{summary_text}\n\n"
            f"Respond with ONLY a JSON array of query strings. "
            f'Example: ["query 1", "query 2"]'
        )

        from deeplook.judgment.synthesize import get_llm_response
        raw_text, _, _ = get_llm_response(prompt)
        raw = raw_text.strip()
        import re as _re
        match = _re.search(r"\[.*?\]", raw, _re.DOTALL)
        if not match:
            print(f"[round2] could not parse query array from: {raw[:100]}")
            return []
        follow_up_queries: list[str] = json.loads(match.group())
        follow_up_queries = [q for q in follow_up_queries if isinstance(q, str)][:3]
        print(f"[round2] follow-up queries: {follow_up_queries}")

        round2_news = await asyncio.wait_for(
            fetch_news(company_name, queries=follow_up_queries),
            timeout=10,
        )
        articles = round2_news.get("articles", [])
        print(f"[round2] found {len(articles)} additional articles")
        return articles
    except Exception as e:
        print(f"[round2] failed: {e}")
        return []


# ── Mega-cap override: hand-curated peers for large/mega caps ─────────────
MEGA_CAP_PEERS: dict[str, list[str]] = {
    "AAPL":  ["MSFT", "GOOGL", "AMZN"],
    "MSFT":  ["AAPL", "GOOGL", "AMZN"],
    "GOOGL": ["MSFT", "META", "AMZN"],
    "GOOG":  ["MSFT", "META", "AMZN"],
    "AMZN":  ["MSFT", "GOOGL", "WMT"],
    "META":  ["GOOGL", "SNAP", "PINS"],
    "NVDA":  ["AMD", "INTC", "AVGO"],
    "TSLA":  ["RIVN", "F", "GM"],
    "AVGO":  ["NVDA", "AMD", "QCOM"],
    "JPM":   ["BAC", "WFC", "GS"],
    "V":     ["MA", "AXP", "PYPL"],
    "MA":    ["V", "AXP", "PYPL"],
    "UNH":   ["CVS", "CI", "HUM"],
    "XOM":   ["CVX", "COP", "BP"],
}

# ── Peer ticker mapping by yfinance industry ──────────────────────────────
INDUSTRY_PEER_TICKERS: dict[str, list[str]] = {
    "Semiconductors": ["AMD", "INTC", "QCOM"],
    "Semiconductor Equipment & Materials": ["KLAC", "AMAT", "LRCX"],
    "Electronic Components": ["LITE", "MACOM", "CIEN"],
    "Scientific & Technical Instruments": ["LITE", "MTSI", "CIEN"],
    "Communication Equipment": ["CSCO", "NOK", "ERIC"],
    "Software—Infrastructure": ["MSFT", "ORCL", "IBM"],
    "Software—Application": ["CRM", "SAP", "NOW"],
    "Internet Retail": ["AMZN", "BABA", "SHOP"],
    "Internet Content & Information": ["META", "GOOGL", "SNAP"],
    "Consumer Electronics": ["AAPL", "SONO", "GPRO"],
    "Drug Manufacturers—General": ["JNJ", "PFE", "ABBV"],
    "Biotechnology": ["AMGN", "GILD", "REGN"],
    "Banks—Diversified": ["JPM", "BAC", "WFC"],
    "Asset Management": ["BLK", "SCHW", "MS"],
    "Aerospace & Defense": ["LMT", "RTX", "NOC"],
    "Oil & Gas Integrated": ["XOM", "CVX", "BP"],
    "Auto Manufacturers": ["TSLA", "GM", "F"],
    "Entertainment": ["DIS", "NFLX", "WBD"],
    "Telecom Services": ["T", "VZ", "TMUS"],
}


def _get_peer_tickers(fetcher_results: dict, main_ticker: str | None) -> list[str]:
    """Return up to 3 competitor tickers. Checks MEGA_CAP_PEERS first, then
    INDUSTRY_PEER_TICKERS with market cap filter (0.1x–10x of target)."""
    try:
        yf_result = fetcher_results.get("yfinance") or {}
        if yf_result.get("status") != "ok":
            return []
        data = yf_result.get("data") or {}
        main = (main_ticker or "").upper()

        # 1. Mega-cap override — use if ticker is in the table
        if main and main in MEGA_CAP_PEERS:
            return MEGA_CAP_PEERS[main][:3]

        # Fallback: read ticker from yfinance data if caller didn't resolve it
        if not main:
            main = (data.get("symbol") or "").upper()
            if main and main in MEGA_CAP_PEERS:
                return MEGA_CAP_PEERS[main][:3]

        # 2. Industry fallback with market cap filter
        industry = data.get("industry", "")
        sector = data.get("sector", "")
        candidates = INDUSTRY_PEER_TICKERS.get(industry) or INDUSTRY_PEER_TICKERS.get(sector) or []
        candidates = [t for t in candidates if t.upper() != main]

        target_mcap = data.get("market_cap") or 0
        if target_mcap <= 0:
            return candidates[:3]  # no mcap data, return as-is

        filtered = []
        for t in candidates:
            try:
                import yfinance as _yf
                info = _yf.Ticker(t).fast_info
                peer_mcap = getattr(info, "market_cap", None) or 0
                if peer_mcap > 0 and (0.1 * target_mcap) <= peer_mcap <= (10 * target_mcap):
                    filtered.append(t)
            except Exception:
                filtered.append(t)  # include on error rather than drop
            if len(filtered) >= 3:
                break
        return filtered
    except Exception:
        return []


def _build_technical_snapshot(yf_data: dict) -> dict | None:
    """Build technical snapshot dict from yfinance data."""
    try:
        if not yf_data or not yf_data.get("success"):
            return None
        price = yf_data.get("price")
        high52 = yf_data.get("fiftyTwoWeekHigh")
        low52 = yf_data.get("fiftyTwoWeekLow")
        ma50 = yf_data.get("fiftyDayAverage")
        ma200 = yf_data.get("twoHundredDayAverage")
        rsi = yf_data.get("rsi14")
        snap = {
            "price": price,
            "52w_high": round(high52, 2) if high52 else None,
            "52w_low": round(low52, 2) if low52 else None,
            "pct_from_high": round((price - high52) / high52 * 100, 1) if price and high52 else None,
            "50d_ma": round(ma50, 2) if ma50 else None,
            "200d_ma": round(ma200, 2) if ma200 else None,
            "rsi14": rsi,
        }
        return snap if any(v is not None for k, v in snap.items() if k != "price") else None
    except Exception:
        return None


# ── v2 Code Processing Layer ───────────────────────────────────────────────

def _calculate_rsi(closes: list, period: int = 14) -> float | None:
    """Standard RSI calculation from list of closing prices. Returns None if insufficient data."""
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def _fetch_rsi_for_ticker(ticker: str) -> float | None:
    """Fetch 1mo history for a peer ticker and compute RSI-14."""
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="1mo", timeout=10)
        if hist.empty or len(hist) < 15:
            return None
        return _calculate_rsi(hist["Close"].tolist())
    except Exception:
        return None


def _fetch_history_metrics(ticker: str) -> dict:
    """Fetch 3mo history and compute change_1d, change_30d, volume metrics."""
    result = {}
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="3mo", timeout=10)
        if hist.empty or len(hist) < 2:
            return result
        closes = hist["Close"].tolist()
        volumes = hist["Volume"].tolist()
        try:
            result["change_1d"] = round((closes[-1] - closes[-2]) / closes[-2] * 100, 1)
        except Exception:
            pass
        try:
            if len(closes) >= 22:
                result["change_30d"] = round((closes[-1] - closes[-22]) / closes[-22] * 100, 1)
        except Exception:
            pass
        try:
            if len(volumes) >= 20:
                avg = sum(volumes[-20:]) / 20
                result["volume"] = volumes[-1]
                result["avg_volume_20d"] = avg
                if avg > 0:
                    result["volume_ratio"] = round(volumes[-1] / avg, 2)
        except Exception:
            pass
    except Exception as e:
        print(f"[v2] _fetch_history_metrics({ticker}) failed: {e}")
    return result


def _truncate(text: str, max_chars: int) -> str:
    if not text:
        return ""
    return str(text)[:max_chars]


def _safe_float(val) -> float | None:
    try:
        if val is None:
            return None
        return float(str(val).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _format_pct(val) -> str | None:
    try:
        if val is None:
            return None
        f = float(val)
        sign = "+" if f >= 0 else ""
        return f"{sign}{f * 100:.1f}%"
    except (TypeError, ValueError):
        return None


def _format_currency(val) -> str | None:
    try:
        if val is None:
            return None
        v = float(val)
        if v >= 1e12:
            return f"${v / 1e12:.1f}T"
        elif v >= 1e9:
            return f"${v / 1e9:.1f}B"
        elif v >= 1e6:
            return f"${v / 1e6:.1f}M"
        else:
            return f"${v:,.0f}"
    except (TypeError, ValueError):
        return None


def _extract_price(r1_data: dict, entity_type: str) -> dict:
    try:
        if entity_type == "public_equity":
            yfd = (r1_data.get("yfinance") or {}).get("data") or {}
            current = _safe_float(yfd.get("current_price") or yfd.get("regularMarketPrice") or yfd.get("price"))
            market_cap = yfd.get("market_cap") or yfd.get("marketCap")
            return {
                "current": current,
                "change_30d": _format_pct(yfd.get("52WeekChange")),
                "market_cap": _format_currency(market_cap),
                "currency": yfd.get("currency", "USD"),
            }
        elif entity_type == "crypto":
            # CoinGecko fetcher returns a FLAT dict (market_data is not nested):
            # keys: price_usd, market_cap, price_change_30d_pct, volume_24h
            cg = (r1_data.get("coingecko") or {}).get("data") or {}
            current = _safe_float(cg.get("price_usd"))
            market_cap = _safe_float(cg.get("market_cap"))
            change_30d = _safe_float(cg.get("price_change_30d_pct"))
            pct_str = f"{'+' if (change_30d or 0) >= 0 else ''}{change_30d:.1f}%" if change_30d is not None else None
            return {"current": current, "change_30d": pct_str, "market_cap": _format_currency(market_cap), "currency": "USD"}
    except Exception as e:
        print(f"[v2] _extract_price failed: {e}")
    return {"current": None, "change_30d": None, "market_cap": None, "currency": "USD"}


def _extract_financials(r1_data: dict, entity_type: str) -> dict:
    try:
        if entity_type == "public_equity":
            yfd = (r1_data.get("yfinance") or {}).get("data") or {}
            return {
                "revenue_growth": _format_pct(yfd.get("revenue_growth") or yfd.get("revenueGrowth")),
                "earnings_growth": _format_pct(yfd.get("earnings_growth") or yfd.get("earningsGrowth")),
                "gross_margin": _safe_float(yfd.get("grossMargins")),
                "operating_margin": _safe_float(yfd.get("operating_margins") or yfd.get("operatingMargins")),
                "net_margin": _safe_float(yfd.get("profitMargins")),
                "net_income_ttm": _safe_float(yfd.get("netIncomeToCommon")),
                "fcf": _format_currency(yfd.get("free_cashflow") or yfd.get("freeCashflow")),
                "revenue_ttm": _safe_float(yfd.get("total_revenue") or yfd.get("totalRevenue")),
            }
        elif entity_type == "crypto":
            dl = (r1_data.get("defillama") or {}).get("data") or {}
            return {"tvl": _format_currency(dl.get("tvl"))}
    except Exception as e:
        print(f"[v2] _extract_financials failed: {e}")
    return {}


def _extract_valuation(r1_data: dict, entity_type: str) -> dict:
    try:
        if entity_type == "public_equity":
            yfd = (r1_data.get("yfinance") or {}).get("data") or {}
            return {
                "pe_ratio": _safe_float(yfd.get("trailingPE")),
                "fwd_pe_ratio": _safe_float(yfd.get("forwardPE")),
                "peg_ratio": _safe_float(yfd.get("peg_ratio") or yfd.get("pegRatio")),
                "ps_ratio": _safe_float(yfd.get("priceToSalesTrailing12Months")),
                "ev_to_ebitda": _safe_float(yfd.get("enterprise_to_ebitda") or yfd.get("enterpriseToEbitda")),
                "analyst_target": _safe_float(yfd.get("target_mean_price") or yfd.get("targetMeanPrice")),
            }
        elif entity_type == "crypto":
            # CoinGecko flat keys: market_cap (not market_data.market_cap.usd)
            # DeFiLlama already computes mcap_to_tvl
            cg = (r1_data.get("coingecko") or {}).get("data") or {}
            dl = (r1_data.get("defillama") or {}).get("data") or {}
            mcap = _safe_float(cg.get("market_cap"))
            tvl = _safe_float(dl.get("tvl"))
            # Use DeFiLlama's pre-computed ratio if available, else compute
            mcap_to_tvl = dl.get("mcap_to_tvl") or (round(mcap / tvl, 2) if mcap and tvl else None)
            return {"mcap_to_tvl": mcap_to_tvl}
    except Exception as e:
        print(f"[v2] _extract_valuation failed: {e}")
    return {}


def _extract_crypto_numbers(r1_data: dict) -> dict:
    """Extract crypto-specific metrics from CoinGecko/DeFiLlama data.
    CoinGecko fetcher returns flat keys: price_usd, market_cap, price_change_30d_pct, volume_24h.
    DeFiLlama returns: tvl, mcap_to_tvl, category, chains_count.
    """
    try:
        cg = (r1_data.get("coingecko") or {}).get("data") or {}
        dl = (r1_data.get("defillama") or {}).get("data") or {}

        mcap = _safe_float(cg.get("market_cap"))
        tvl = _safe_float(dl.get("tvl"))
        mcap_to_tvl = dl.get("mcap_to_tvl") or (round(mcap / tvl, 3) if mcap and tvl and tvl > 0 else None)

        return {
            "token_price": _safe_float(cg.get("price_usd")),
            "price_change_24h": None,  # not in fetcher output currently
            "price_change_30d": _safe_float(cg.get("price_change_30d_pct")),
            "market_cap": mcap,
            "volume_24h": _safe_float(cg.get("volume_24h")),
            "tvl": tvl,
            "mcap_tvl_ratio": mcap_to_tvl,
            "category": dl.get("category"),
            "chains_count": dl.get("chains_count"),
            "top_3_chains": dl.get("top_3_chains"),
            "coin_id": cg.get("coin_id"),
        }
    except Exception as e:
        print(f"[v2] _extract_crypto_numbers failed: {e}")
        return {}


def _extract_vc_numbers(r1_data: dict) -> dict:
    """Extract VC/private company metrics from RootData.
    RootData returns nested: project_details (raw API JSON) + funding (raw API JSON).
    We drill into known RootData API response shapes.
    """
    try:
        rd = (r1_data.get("rootdata") or {}).get("data") or {}
        if not rd.get("success"):
            print(f"[v2] _extract_vc_numbers: rootdata not ok, keys={list(rd.keys())}")
            return {}

        # RootData project_details is typically {"code": 200, "data": {...}}
        pd_raw = rd.get("project_details") or {}
        pd = pd_raw.get("data") or pd_raw  # unwrap if nested

        # RootData funding response: {"code": 200, "data": {"items": [...]}}
        fund_raw = rd.get("funding") or {}
        fund_data = fund_raw.get("data") or fund_raw
        fund_items = fund_data.get("items") or fund_data.get("list") or []

        # Debug: print top-level keys so we can tune if schema differs
        print(f"[v2] rootdata project_details keys: {list(pd.keys())}")
        print(f"[v2] rootdata funding keys: {list(fund_data.keys())}")

        # Extract portfolio/investment list
        portfolio = pd.get("portfolios") or pd.get("investments") or pd.get("projects") or []
        notable = [p.get("name") or p.get("project_name") for p in portfolio[:5] if p.get("name") or p.get("project_name")]

        # Extract most recent fund from fund_items
        last_fund_item = fund_items[0] if fund_items else {}

        return {
            "aum": pd.get("aum") or pd.get("total_fund_size") or pd.get("fund_size"),
            "total_investments": pd.get("investment_count") or pd.get("portfolio_count") or pd.get("total_investments") or len(portfolio) or None,
            "notable_investments": notable,
            "last_fund": last_fund_item.get("fund_name") or last_fund_item.get("name"),
            "last_fund_size": last_fund_item.get("amount") or last_fund_item.get("fund_size"),
            "stage_focus": pd.get("stage") or pd.get("investment_stage") or pd.get("focus_stage"),
            "hq": pd.get("location") or pd.get("headquarters") or pd.get("country"),
            "founded": pd.get("founded") or pd.get("established"),
            "description": (pd.get("description") or "")[:300],
        }
    except Exception as e:
        print(f"[v2] _extract_vc_numbers failed: {e}")
        return {}


def _format_technical_snapshot_v2(technical: dict | None) -> dict:
    """Format enriched technical snapshot dict. Input is the technical_snapshot dict
    (from _build_technical_snapshot), optionally enriched by _fetch_history_metrics."""
    if not technical:
        return {}
    result = {}
    try:
        price = _safe_float(technical.get("price"))
        h52 = _safe_float(technical.get("52w_high"))
        l52 = _safe_float(technical.get("52w_low"))
        ma50 = _safe_float(technical.get("50d_ma"))
        ma200 = _safe_float(technical.get("200d_ma"))
        rsi = _safe_float(technical.get("rsi14"))

        result["rsi_14"] = rsi
        result["high_52w"] = h52
        result["low_52w"] = l52

        try:
            if price is not None and h52 is not None and l52 is not None and h52 != l52:
                result["position_52w_pct"] = round((price - l52) / (h52 - l52) * 100, 1)
        except Exception as e:
            print(f"[v2] 52W position failed: {e}")

        try:
            if ma50 is not None and price is not None:
                result["ma50"] = ma50
                result["ma50_signal"] = "above" if price > ma50 else "below"
                result["ma50_distance_pct"] = round((price - ma50) / ma50 * 100, 1)
        except Exception as e:
            print(f"[v2] MA50 format failed: {e}")

        try:
            if ma200 is not None and price is not None:
                result["ma200"] = ma200
                result["ma200_signal"] = "above" if price > ma200 else "below"
                result["ma200_distance_pct"] = round((price - ma200) / ma200 * 100, 1)
        except Exception as e:
            print(f"[v2] MA200 format failed: {e}")

        # History-enriched fields added by _fetch_history_metrics in run_research
        for key in ("change_1d", "change_30d", "volume", "avg_volume_20d", "volume_ratio"):
            if technical.get(key) is not None:
                result[key] = technical[key]

    except Exception as e:
        print(f"[v2] _format_technical_snapshot_v2 failed: {e}")
    return result


def _format_peer_table(peer_data: list) -> list:
    result = []
    for p in (peer_data or []):
        try:
            result.append({
                "ticker": p.get("ticker"),
                "name": p.get("name"),
                "price": _safe_float(p.get("price")),
                "market_cap": _safe_float(p.get("market_cap")),
                "pe": _safe_float(p.get("trailingPE")),
                "ps": _safe_float(p.get("priceToSalesTrailing12Months")),
                "rev_growth_pct": _safe_float(p.get("revenueGrowth")),
                "gross_margin_pct": _safe_float(p.get("grossMargins")),
                "rsi_14": p.get("rsi_14"),  # enriched async in run_research (v2 only)
            })
        except Exception as e:
            print(f"[v2] peer row failed: {e}")
    return result


def _prepare_news_v2(fetcher_results: dict) -> list:
    articles = []
    news = fetcher_results.get("news") or {}
    if news.get("status") == "ok":
        for a in ((news.get("data") or {}).get("articles") or [])[:10]:
            try:
                articles.append({
                    "title": a.get("title", ""),
                    "source": a.get("source", ""),
                    "date": a.get("published_at") or a.get("date") or "",
                    "url": a.get("url", ""),
                    "snippet": _truncate(a.get("content") or a.get("description") or "", 200),
                })
            except Exception:
                pass
    return articles


def _extract_earnings(r1_data: dict, entity_type: str) -> dict:
    if entity_type != "public_equity":
        return {}
    try:
        yfd = (r1_data.get("yfinance") or {}).get("data") or {}
        finnhub = (r1_data.get("finnhub") or {}).get("data") or {}
        return {
            "next_earnings_date": yfd.get("earnings_date"),
            "eps_estimate": finnhub.get("eps_estimate"),
            "eps_actual": finnhub.get("eps_actual"),
        }
    except Exception as e:
        print(f"[v2] _extract_earnings failed: {e}")
    return {}


def _extract_guidance(r1_data: dict, entity_type: str) -> dict:
    if entity_type != "public_equity":
        return {}
    try:
        finnhub = (r1_data.get("finnhub") or {}).get("data") or {}
        guidance_raw = finnhub.get("guidance") or []
        items = [
            {"metric": g.get("metric", ""), "guidance": g.get("value") or g.get("guidance", ""), "sentiment": g.get("sentiment", "neutral")}
            for g in (guidance_raw if isinstance(guidance_raw, list) else [])
        ]
        return {"period": finnhub.get("period", ""), "items": items}
    except Exception as e:
        print(f"[v2] _extract_guidance failed: {e}")
    return {}


def _extract_segments(r1_data: dict, entity_type: str) -> list:
    if entity_type != "public_equity":
        return []
    try:
        sec = (r1_data.get("sec_edgar") or {}).get("data") or {}
        segments_raw = sec.get("segments") or []
        return [
            {"name": s.get("name", ""), "metric": s.get("metric", ""), "context": s.get("context", "")}
            for s in (segments_raw if isinstance(segments_raw, list) else [])
        ]
    except Exception as e:
        print(f"[v2] _extract_segments failed: {e}")
    return []


def _extract_funding(r1_data: dict, entity_type: str) -> dict:
    if entity_type in ("public_equity", "crypto"):
        return {}
    try:
        rd = (r1_data.get("rootdata") or {}).get("data") or {}
        return {
            "total_raised": rd.get("total_funding") or rd.get("total_raised"),
            "last_round": rd.get("last_round") or rd.get("latest_round"),
            "key_investors": rd.get("investors") or rd.get("key_investors") or [],
        }
    except Exception as e:
        print(f"[v2] _extract_funding failed: {e}")
    return {}


def _extract_company_meta(fetcher_results: dict) -> dict:
    """Extract company metadata (sector, industry, hq, ceo, team_size) from yfinance data."""
    meta: dict = {}
    try:
        yfd = ((fetcher_results.get("yfinance") or {}).get("data") or {})
        if yfd.get("sector"):
            meta["sector"] = yfd["sector"]
        if yfd.get("industry"):
            meta["industry"] = yfd["industry"]
        # yfinance fetcher stores CEO as ceo_name (pre-extracted from companyOfficers)
        if yfd.get("ceo_name"):
            meta["ceo"] = yfd["ceo_name"]
        # employees stored as "employees" key by the yfinance fetcher
        if yfd.get("employees"):
            meta["team_size"] = yfd["employees"]
    except Exception as e:
        print(f"[v2] _extract_company_meta failed: {e}")
    return meta


def prepare_structured_data(
    company_name: str,
    entity_type: str,
    r1_data: dict,
    fetcher_results: dict,
    peer_data: list,
    technical: dict | None,
) -> dict:
    """Pure code extraction layer (v2). No LLM — all values directly from API responses."""
    base = {
        "company_name": company_name,
        "entity_type": entity_type,
        "research_date": date.today().isoformat(),
        "price": _extract_price(r1_data, entity_type),
        "financials": _extract_financials(r1_data, entity_type),
        "valuation": _extract_valuation(r1_data, entity_type),
        "technicals": _format_technical_snapshot_v2(technical),
        "peers": _format_peer_table(peer_data),
        "news_for_compression": _prepare_news_v2(fetcher_results),
        "text_for_compression": {
            "website": _truncate((r1_data.get("website") or {}).get("data") or "", 2000),
            "wikipedia": _truncate((r1_data.get("wikipedia") or {}).get("data") or "", 2000),
        },
        "earnings": _extract_earnings(r1_data, entity_type),
        "guidance": _extract_guidance(r1_data, entity_type),
        "segments": _extract_segments(r1_data, entity_type),
        "funding": _extract_funding(r1_data, entity_type),
        "company_meta": _extract_company_meta(fetcher_results),
    }
    # Entity-specific supplemental data
    if entity_type == "crypto":
        base["crypto_numbers"] = _extract_crypto_numbers(r1_data)
    elif entity_type in ("venture_capital", "private_or_unlisted", "foundation"):
        base["vc_numbers"] = _extract_vc_numbers(r1_data)

    # ── v3 groupings (added alongside existing fields; v2 is unaffected) ────
    import re as _re
    company_meta = base["company_meta"]
    price_data = base["price"]
    financials_data = base["financials"]
    earnings_data = base["earnings"]
    technicals_data = base["technicals"]

    # headquarters: from yfinance city/state/country
    yf_info = (fetcher_results.get("yfinance") or {}).get("data") or {}
    _hq_parts = [p for p in [yf_info.get("city", ""), yf_info.get("state", ""), yf_info.get("country", "")] if p]
    headquarters = ", ".join(_hq_parts) if _hq_parts else None

    # founded: extract year from Wikipedia text
    founded = None
    _wiki_raw = (fetcher_results.get("wikipedia") or {}).get("data") or {}
    # Wikipedia fetcher returns a dict with "extract" key; fall back to string repr
    wiki_text = _wiki_raw.get("extract", "") if isinstance(_wiki_raw, dict) else str(_wiki_raw)
    if wiki_text:
        _m = _re.search(r'(?:founded|established|incorporated)\s+(?:in\s+)?(\d{4})', wiki_text, _re.IGNORECASE)
        if _m:
            _year = int(_m.group(1))
            if 1800 <= _year <= 2026:
                founded = _year

    # headquarters: Wikipedia fallback (only used if yfinance has nothing)
    wiki_headquarters = None
    if wiki_text:
        _hq_m = _re.search(
            r'(?:headquartered|based|headquarters)\s+in\s+([A-Z][a-zA-Z\s,]+?)(?:\.|,\s+(?:it|the|and|which))',
            wiki_text,
        )
        if _hq_m:
            wiki_headquarters = _hq_m.group(1).strip().rstrip(",")

    # CEO: Wikipedia fallback (only used if yfinance CEO is null)
    wiki_ceo = None
    if wiki_text:
        # Pattern 1: "CEO Dario Amodei" / "led by CEO Sam Altman" / "chief executive officer Jensen Huang"
        _ceo_m = _re.search(
            r'(?:CEO|chief executive officer|led by)\s+([A-Z][a-z]+\s+[A-Z][a-z]+)',
            wiki_text,
        )
        if _ceo_m:
            wiki_ceo = _ceo_m.group(1).strip()
        else:
            # Pattern 2: "Dario Amodei, who is/are ... CEO" (Wikipedia infobox-style prose)
            _ceo_m2 = _re.search(
                r'([A-Z][a-z]+\s+[A-Z][a-z]+),\s+who\s+(?:is|are)\s+(?:\w+\s+and\s+)?CEO\b',
                wiki_text,
            )
            if _ceo_m2:
                wiki_ceo = _ceo_m2.group(1).strip()

    # revenue as formatted string
    _rev_raw = financials_data.get("revenue_ttm")
    revenue_str = _format_currency(_rev_raw) if _rev_raw else None

    # next earnings for outlook
    _next_earnings = earnings_data.get("next_earnings_date")
    _next_earnings_iso = str(_next_earnings)[:10] if _next_earnings else None

    # crypto dict for modules
    _crypto_numbers = base.get("crypto_numbers") or {}
    _crypto_module = None
    if entity_type == "crypto":
        _crypto_module = {
            "token_price": _crypto_numbers.get("token_price"),
            "price_change_24h": _crypto_numbers.get("price_change_24h"),
            "price_change_30d": _crypto_numbers.get("price_change_30d"),
            "market_cap": _crypto_numbers.get("market_cap"),
            "volume_24h": _crypto_numbers.get("volume_24h"),
            "tvl": _crypto_numbers.get("tvl"),
            "mcap_tvl_ratio": _crypto_numbers.get("mcap_tvl_ratio"),
            "category": _crypto_numbers.get("category"),
            "chains_count": _crypto_numbers.get("chains_count"),
        }

    _hq_final = headquarters or wiki_headquarters
    _ceo_final = company_meta.get("ceo") or wiki_ceo

    base["_v3"] = {
        "identity": {
            "sector": company_meta.get("sector"),
            "industry": company_meta.get("industry"),
            "headquarters": _hq_final,
            "founded": founded,
        },
        "scale": {
            "employees": company_meta.get("team_size"),
            "revenue": revenue_str,
            "market_cap": price_data.get("market_cap"),
        },
        "people": {
            "ceo": _ceo_final,
        },
        "modules": {
            "financial": {
                "price": price_data,
                "valuation": base["valuation"],
                "technicals": technicals_data,
                "margins": {
                    "gross_margin": financials_data.get("gross_margin"),
                    "operating_margin": financials_data.get("operating_margin"),
                    "revenue_growth_yoy": financials_data.get("revenue_growth"),
                    "fcf_ttm": financials_data.get("fcf"),
                },
            } if entity_type == "public_equity" else None,
            "crypto": _crypto_module,
        },
        "peers": base["peers"],
        "outlook": {
            "next_event": "Earnings" if _next_earnings_iso else None,
            "next_event_date": _next_earnings_iso,
        },
    }

    return base


async def run_research(company_name: str, include_youtube: bool = True, output_file: str | None = None, layer1: bool = False) -> dict:
    t0 = time.time()
    _timing: dict = {}

    _t = time.time()
    company_type, resolved_ticker, company_full_name = await detect_company_type(company_name)
    entity_type = refine_entity_type(company_name, company_type)
    _timing["entity_routing"] = round(time.time() - _t, 2)
    print(f"[pipeline] {company_name} -> type={company_type}, ticker={resolved_ticker}, full_name={company_full_name}")
    print(f"[pipeline] entity_type refined: {company_type} -> {entity_type}")

    # ── Search Intelligence Layer ──────────────────────────────────────────
    queries = build_search_queries(company_name, entity_type, resolved_ticker, company_full_name)
    active = get_active_fetchers(entity_type)
    limits = get_fetcher_limits()
    time_limits = get_time_limits()

    # Build fetcher tasks (only active ones, with per-fetcher timeouts + queries)
    _include_news = active.get("news", False)
    _include_youtube = active.get("youtube", False) and include_youtube

    # ── Round 1: deterministic fetchers (ticker/name → structured data) ────
    r1_tasks = []

    for fetcher_name, is_active in active.items():
        if not is_active:
            print(f"[strategy] SKIP {fetcher_name} (not relevant for {company_type})")
            continue
        if fetcher_name in ("news", "youtube"):
            continue  # handled in Round 2

        timeout = limits.get(fetcher_name, {}).get("timeout_seconds", 10)

        if fetcher_name == "website":
            r1_tasks.append(run_fetcher("website", fetch_website(company_name), timeout))

        elif fetcher_name == "wikipedia":
            r1_tasks.append(run_fetcher("wikipedia", fetch_wikipedia(company_name), timeout))

        elif fetcher_name == "yfinance":
            yf_input = resolved_ticker if resolved_ticker else company_name
            r1_tasks.append(run_fetcher("yfinance", fetch_yfinance(yf_input), timeout))
            # Bonus: Yahoo Finance news feed — free, no extra API key
            r1_tasks.append(run_fetcher("yfinance_news", fetch_yfinance_news(yf_input), timeout))

        elif fetcher_name == "coingecko":
            r1_tasks.append(run_fetcher("coingecko", fetch_coingecko(company_name), timeout))

        elif fetcher_name == "rootdata":
            r1_tasks.append(run_fetcher("rootdata", fetch_rootdata(company_name), timeout))

        elif fetcher_name == "defillama":
            r1_tasks.append(run_fetcher("defillama", fetch_defillama(company_name), timeout))

        elif fetcher_name == "sec_edgar":
            ticker_input = resolved_ticker if resolved_ticker else company_name
            r1_tasks.append(run_fetcher("sec_edgar", fetch_sec_edgar(ticker_input), timeout))

        elif fetcher_name == "finnhub":
            ticker_input = resolved_ticker if resolved_ticker else company_name
            r1_tasks.append(run_fetcher("finnhub", asyncio.to_thread(fetch_finnhub, ticker_input), timeout))

    _t = time.time()
    r1_results_list = await asyncio.gather(*r1_tasks)
    _timing["fetchers_r1_wall"] = round(time.time() - _t, 2)
    for _n, _r in r1_results_list:
        _timing[f"fetcher_{_n}"] = _r.pop("_timing_s", None)
    r1_data = {name: result for name, result in r1_results_list}

    # ── Round 1.5: Haiku generates context-aware search queries ────────────
    from deeplook.judgment.synthesize import generate_search_queries
    _t = time.time()
    search_queries_haiku = await asyncio.to_thread(
        generate_search_queries, company_name, entity_type, r1_data
    )
    _timing["llm_search_queries"] = round(time.time() - _t, 2)
    print(f"[search_queries] youtube={search_queries_haiku.get('youtube_queries')}, "
          f"news={search_queries_haiku.get('news_queries')}")

    # ── Round 2: search-based fetchers (using Haiku queries) ───────────────
    r2_tasks = []

    if _include_news:
        news_queries_r2 = search_queries_haiku.get("news_queries") or [f"{company_name} latest news"]
        max_age = time_limits.get("news")
        timeout_news = limits.get("news", {}).get("timeout_seconds", 10)
        r2_tasks.append(run_fetcher(
            "news",
            fetch_news(company_name, queries=news_queries_r2, max_age_days=max_age),
            timeout_news,
        ))

    if _include_youtube:
        yt_queries = search_queries_haiku.get("youtube_queries") or [company_name]
        yt_query = yt_queries[0] if yt_queries else company_name
        max_age_yt = time_limits.get("youtube")
        transcript_timeout = limits.get("youtube", {}).get("transcript_timeout", 10)
        max_results_yt = limits.get("youtube", {}).get("max_items", 3)
        timeout_yt = limits.get("youtube", {}).get("timeout_seconds", 10)
        r2_tasks.append(run_fetcher(
            "youtube",
            fetch_youtube(
                company_name,
                query=yt_query,
                max_results=max_results_yt,
                max_age_days=max_age_yt,
                transcript_timeout=transcript_timeout,
            ),
            timeout_yt,
        ))
    elif active.get("youtube", False):
        print(f"[strategy] SKIP youtube (--no-youtube flag)")

    _t = time.time()
    r2_results_list = await asyncio.gather(*r2_tasks)
    _timing["fetchers_r2_wall"] = round(time.time() - _t, 2)
    for _n, _r in r2_results_list:
        _timing[f"fetcher_{_n}"] = _r.pop("_timing_s", None)

    # ── Merge Round 1 + Round 2 results ────────────────────────────────────
    results_list = r1_results_list + r2_results_list

    fetcher_results = {}
    succeeded = []
    failed = []

    for name, result in results_list:
        # Layer 4: Validate result relevance
        if result["status"] == "ok":
            if not validate_result(name, company_name, result):
                result["status"] = "rejected"
                failed.append(name)
            else:
                succeeded.append(name)
        else:
            failed.append(name)
        fetcher_results[name] = result

    # ── Merge yfinance_news into news pool (public_equity only) ───────────
    yf_news = fetcher_results.pop("yfinance_news", None)
    if (yf_news and yf_news.get("status") == "ok"
            and "news" in fetcher_results
            and fetcher_results["news"]["status"] == "ok"):
        extra = yf_news["data"].get("articles", [])
        existing_urls = {a["url"] for a in fetcher_results["news"]["data"].get("articles", [])}
        merged = [a for a in extra if a["url"] not in existing_urls]
        fetcher_results["news"]["data"]["articles"].extend(merged)
        print(f"[strategy] yfinance_news merged: +{len(merged)} articles")

    # ── Post-process news: dedup + rank ────────────────────────────────────
    if "news" in fetcher_results and fetcher_results["news"]["status"] == "ok":
        articles = fetcher_results["news"]["data"].get("articles", [])
        articles = deduplicate_news(articles)  # Layer 6
        articles = rank_articles(articles, company_name)  # Layer 7
        # Priority filter: keep articles above threshold (v2 quality improvement applied to all pipelines)
        _HIGH_PRI = 0.65
        filtered = [a for a in articles if a.get("_priority_score", 0) >= _HIGH_PRI][:10]
        if len(filtered) < 3:
            filtered = articles[:5]
        fetcher_results["news"]["data"]["articles"] = filtered
        print(f"[strategy] news final: {len(filtered)} articles after dedup+rank+filter (threshold={_HIGH_PRI})")

    _pipeline_v2 = os.environ.get("DEEPLOOK_PIPELINE_V2", "true").lower() != "false"

    # ── Round 2: LLM-guided follow-up search (v1 only) ─────────────────────
    if not _pipeline_v2:
        round2_articles = await _round2_search(company_name, company_full_name, fetcher_results)
        if round2_articles and "news" in fetcher_results and fetcher_results["news"]["status"] == "ok":
            existing_urls = {a["url"] for a in fetcher_results["news"]["data"].get("articles", [])}
            new_articles = [a for a in round2_articles if a.get("url") not in existing_urls]
            fetcher_results["news"]["data"]["articles"].extend(new_articles)
            all_articles = fetcher_results["news"]["data"]["articles"]
            all_articles = deduplicate_news(all_articles)
            all_articles = rank_articles(all_articles, company_name)
            fetcher_results["news"]["data"]["articles"] = all_articles
            print(f"[round2] merged +{len(new_articles)} articles, total={len(all_articles)}")

    elapsed = time.time() - t0
    api_calls = len(r1_tasks) + len(r2_tasks) + 1  # +1 for detect_company_type

    print(f"\nFetchers succeeded: {succeeded}")
    print(f"Fetchers failed: {failed}")
    print(f"API calls: {api_calls}")
    print(f"Total time: {elapsed:.1f}s\n")

    print('RAW DATA PREVIEW:', {k: str(v)[:200] for k, v in fetcher_results.items()})

    # ── P1: Peer Comparison + Technical Snapshot (public_equity only) ────────
    peer_comparison: list[dict] = []
    technical_snapshot: dict | None = None

    if entity_type == "public_equity":
        yf_result = fetcher_results.get("yfinance") or {}
        yf_data = yf_result.get("data") or {}

        # P1-3: Technical Snapshot
        technical_snapshot = _build_technical_snapshot(yf_data)
        if technical_snapshot:
            print(f"[tech] 52w={technical_snapshot.get('52w_low')}-{technical_snapshot.get('52w_high')} "
                  f"RSI={technical_snapshot.get('rsi14')} vs_high={technical_snapshot.get('pct_from_high')}%")

        # P1-1: Peer Comparison — fetch all 3 in parallel
        peer_tickers = _get_peer_tickers(fetcher_results, resolved_ticker)
        if peer_tickers:
            print(f"[peers] fetching {peer_tickers} in parallel")
            try:
                peer_comparison = await asyncio.wait_for(
                    fetch_peer_data(peer_tickers),
                    timeout=12.0,
                )
                print(f"[peers] got {len(peer_comparison)} records")
            except Exception as e:
                print(f"[peers] fetch failed: {e}")

        # Inject into judgment context so LLM can reference them
        if peer_comparison:
            fetcher_results["peer_comparison"] = {"status": "ok", "data": peer_comparison}
        if technical_snapshot:
            fetcher_results["technical_snapshot"] = {"status": "ok", "data": technical_snapshot}
        # P1-2: Earnings date as upcoming catalyst seed
        earnings_date = yf_data.get("earnings_date")
        if earnings_date:
            fetcher_results["earnings_calendar"] = {
                "status": "ok",
                "data": {"next_earnings_date": earnings_date, "source": "yfinance_calendar"},
            }

    # Only pass non-rejected results to judgment
    judgment_results = {
        k: v for k, v in fetcher_results.items()
        if v.get("status") not in ("rejected",)
    }

    _timing["total_wall"] = round(time.time() - t0, 2)

    # ── v2: Async history enrichment + peer RSI (before code processing) ──
    # Fallback: if ticker wasn't resolved upfront (e.g. "Apple"), get it from yfinance data
    if _pipeline_v2 and entity_type == "public_equity" and resolved_ticker is None:
        _yf_sym = ((fetcher_results.get("yfinance") or {}).get("data") or {}).get("symbol")
        if _yf_sym:
            resolved_ticker = _yf_sym
            print(f"[v2] resolved ticker from yfinance data: {_yf_sym}")

    if _pipeline_v2 and entity_type == "public_equity" and resolved_ticker:
        if technical_snapshot is not None:
            try:
                _extra = await asyncio.wait_for(
                    asyncio.to_thread(_fetch_history_metrics, resolved_ticker),
                    timeout=10.0,
                )
                technical_snapshot.update(_extra)
                print(f"[v2] history enrich: change_1d={_extra.get('change_1d')} change_30d={_extra.get('change_30d')} vol_ratio={_extra.get('volume_ratio')}")
            except Exception as _e:
                print(f"[v2] history enrich failed: {_e}")

        if peer_comparison:
            async def _add_peer_rsi(p):
                _t = p.get("ticker")
                if not _t:
                    return
                try:
                    p["rsi_14"] = await asyncio.wait_for(
                        asyncio.to_thread(_fetch_rsi_for_ticker, _t), timeout=8.0
                    )
                except Exception:
                    p["rsi_14"] = None
            await asyncio.gather(*[_add_peer_rsi(p) for p in peer_comparison])
            print(f"[v2] peer RSI: {[(p.get('ticker'), p.get('rsi_14')) for p in peer_comparison]}")

    if _pipeline_v2:
        # ── v2: Code Processing Layer + Haiku Compress ────────────────────
        from deeplook.judgment.synthesize import compress_context
        _t = time.time()
        structured_data = prepare_structured_data(
            company_name, entity_type, r1_data, fetcher_results, peer_comparison, technical_snapshot
        )
        compressed = await compress_context(structured_data)
        _timing["compress_context"] = round(time.time() - _t, 2)

        _timing["total_wall"] = round(time.time() - t0, 2)
        output = {
            "_version": "2.0",
            "company": company_name,
            "company_type": company_type,
            "entity_type": entity_type,
            "ticker": resolved_ticker,
            "sources_succeeded": succeeded,
            "sources_failed": failed,
            "api_calls": api_calls,
            "elapsed_seconds": round(elapsed, 1),
            "structured_data": structured_data,
            "compressed": compressed,
            "_timing": _timing,
        }

    # Print to stdout
    output_json = json.dumps(output, indent=2, default=str)
    print(output_json)

    # Save to file
    today = date.today().isoformat()
    output_dir = os.path.join(os.path.dirname(__file__), "output")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{company_name}_{today}.json")
    with open(output_path, "w") as f:
        f.write(output_json)
    print(f"\nSaved to {output_path}", file=sys.stderr)

    # Print human-readable report — only when running interactively.
    # When eval.py runs this as a subprocess it captures stdout to parse JSON;
    # format_report output would appear after the JSON and cause "Extra data" errors.
    if output_file or sys.stdout.isatty():
        _schema_version = os.environ.get("DEEPLOOK_SCHEMA", "v3")
        if _pipeline_v2 and _schema_version == "v3":
            from deeplook.formatter import format_output_v3
            formatter = format_output_v3
        elif _pipeline_v2:
            from deeplook.formatter import format_dual_output_v2
            formatter = format_dual_output_v2
        if output_file:
            # Formatters may return a string (v2/v3) or print to stdout (v1)
            result = formatter(output)
            if isinstance(result, str):
                report_text = result
            else:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    formatter(output)
                report_text = buf.getvalue()
            sys.stdout.write(report_text)
            with open(output_file, "w") as f:
                f.write(report_text)
            print(f"Report saved to {output_file}", file=sys.stderr)
        else:
            result = formatter(output)
            if isinstance(result, str):
                sys.stdout.write(result)

    return output


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("company", help="Company name")
    parser.add_argument("--no-youtube", action="store_true", help="Skip YouTube fetcher")
    parser.add_argument("--output", metavar="FILE", help="Save report as markdown file (e.g. nvidia.md)")
    parser.add_argument("--layer1", action="store_true", help="Print compact Layer 1 summary only")
    args = parser.parse_args()

    load_env()
    asyncio.run(run_research(args.company, include_youtube=not args.no_youtube, output_file=args.output, layer1=args.layer1))


if __name__ == "__main__":
    main()
