import asyncio
import logging
import httpx
import yfinance as yf
from .cache import cache_key, get_cached, set_cache

logger = logging.getLogger(__name__)


_TICKER_HARDCODED: dict[str, str] = {
    "Snowflake": "SNOW",
    "Rivian": "RIVN",
    "Grab Holdings": "GRAB",
    "Reliance Industries": "RELIANCE.NS",
    "Shopify": "SHOP",
    # Disambiguation: Coherent Corp (II-VI merger 2022), not old Coherent Inc
    "Coherent Corp": "COHR",
    "Coherent": "COHR",
}


def _check_ticker_direct(symbol: str) -> bool:
    """Blocking: return True if symbol resolves to a real equity with marketCap."""
    try:
        info = yf.Ticker(symbol).info
        return bool(info.get("marketCap") and info["marketCap"] > 0)
    except Exception:
        return False


async def _resolve_ticker(company_name: str) -> str | None:
    """把公司名轉成 ticker。
    優先順序: 1) direct ticker check  2) Yahoo Finance search  3) hardcoded dict
    """
    # 1. Direct check — handles SHOP, TSM, MSFT etc. without any search
    direct_ok = await asyncio.to_thread(_check_ticker_direct, company_name)
    if direct_ok:
        print(f"[resolve] '{company_name}' -> '{company_name}' (method: direct)", flush=True)
        return company_name

    # 2. Yahoo Finance search API — prefer US market equities
    url = "https://query2.finance.yahoo.com/v1/finance/search"
    params = {"q": company_name, "quotesCount": 5, "newsCount": 0}
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, headers=headers, timeout=10.0)
            data = response.json()
            us_equities = []
            other_equities = []
            for quote in data.get("quotes", []):
                if quote.get("quoteType") == "EQUITY":
                    if quote.get("market") == "us_market":
                        us_equities.append(quote)
                    else:
                        other_equities.append(quote)
            preferred = us_equities or other_equities
            if preferred:
                resolved = preferred[0].get("symbol")
                market = preferred[0].get("market", "unknown")
                print(f"[resolve] '{company_name}' -> '{resolved}' (method: search, market={market})", flush=True)
                return resolved
    except Exception as e:
        import traceback
        logger.warning(f"Ticker search failed for '{company_name}': {e}")
        traceback.print_exc()

    # 3. Hardcoded fallback for known misses
    hardcoded = _TICKER_HARDCODED.get(company_name)
    if hardcoded:
        print(f"[resolve] '{company_name}' -> '{hardcoded}' (method: hardcoded)", flush=True)
        return hardcoded

    print(f"[resolve] '{company_name}' -> None (method: all_failed)", flush=True)
    return None


def _get_news(ticker_symbol: str, max_items: int = 10) -> list[dict]:
    """Blocking: fetch recent news from Yahoo Finance for a ticker."""
    try:
        ticker = yf.Ticker(ticker_symbol)
        raw = ticker.news or []
        articles = []
        for item in raw[:max_items]:
            # New yfinance structure: item → { "content": { title, pubDate, canonicalUrl, ... } }
            content = item.get("content") or item  # fall back to flat if old format
            title = content.get("title", "")
            date_str = content.get("pubDate", "")
            url = (
                (content.get("canonicalUrl") or {}).get("url", "")
                or content.get("previewUrl", "")
                or item.get("link", "")
            )
            snippet = content.get("summary", "") or content.get("description", "")
            articles.append({
                "title": title,
                "url": url,
                "content": snippet[:2000],
                "date": date_str,
            })
        return articles
    except Exception as e:
        logger.warning(f"yfinance news failed for '{ticker_symbol}': {e}")
        return []


async def fetch_yfinance_news(ticker_symbol: str, max_items: int = 10) -> dict:
    """Fetch recent Yahoo Finance news articles for a ticker. Free, no API key."""
    key = cache_key("yfinance_news", ticker_symbol)
    cached = get_cached(key)
    if cached is not None:
        return cached

    articles = await asyncio.to_thread(_get_news, ticker_symbol, max_items)
    print(f"[yfinance_news] {ticker_symbol}: {len(articles)} articles")
    result = {"source": "yfinance_news", "articles": articles, "success": bool(articles)}
    if result["success"]:
        set_cache(key, result)
    return result


def _calculate_rsi(series, period: int = 14) -> float | None:
    """Calculate RSI(period) from a closing price Series."""
    try:
        delta = series.diff()
        gain = delta.where(delta > 0, 0).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        val = rsi.iloc[-1]
        import math
        if val is None or math.isnan(float(val)):
            return None
        return round(float(val), 1)
    except Exception:
        return None


def _get_info(ticker_symbol: str) -> tuple:
    """Blocking yfinance call — must run in thread.
    Returns (info, hist_3mo, calendar_dict).
    """
    ticker = yf.Ticker(ticker_symbol)
    info = ticker.info
    if not info or info.get("regularMarketPrice") is None and info.get("currentPrice") is None:
        return None, None, {}

    hist = ticker.history(period="3mo")

    calendar = {}
    try:
        cal = ticker.calendar
        if isinstance(cal, dict):
            calendar = cal
        elif cal is not None and hasattr(cal, "to_dict"):
            calendar = cal.to_dict()
    except Exception:
        pass

    return info, hist, calendar


async def fetch_yfinance(company_name: str) -> dict:
    _key = cache_key("yfinance", company_name)
    _cached = get_cached(_key)
    if _cached is not None:
        return _cached

    result = {
        "source": "yfinance",
        "success": False,
        "symbol": None,
        "price": None,
        "market_cap": None,
        "sector": None,
        "industry": None,
        "employees": None,
        "30d_change_pct": None,
        # Growth signals
        "revenue_growth": None,
        "earnings_growth": None,
        "total_revenue": None,
        "operating_margins": None,
        "free_cashflow": None,
        "recommendation_key": None,
        "target_mean_price": None,
        "earnings_surprise_pct": None,
        # Peer comparison fields
        "trailingPE": None,
        "priceToSalesTrailing12Months": None,
        "grossMargins": None,
        # Technical snapshot fields
        "fiftyTwoWeekHigh": None,
        "fiftyTwoWeekLow": None,
        "fiftyDayAverage": None,
        "twoHundredDayAverage": None,
        "rsi14": None,
        # Upcoming catalysts
        "earnings_date": None,
        # Leadership & company profile
        "peg_ratio": None,
        "ceo_name": None,
        "ceo_title": None,
        # Identity fields (used by validate_result)
        "shortName": None,
        "longName": None,
        "error": None,
    }

    try:
        # 先嘗試直接用 company_name 當 ticker
        info, hist, calendar = await asyncio.to_thread(_get_info, company_name)

        # 查不到時，用 Yahoo Finance search 解析真實 ticker
        if info is None:
            resolved = await _resolve_ticker(company_name)
            if resolved:
                info, hist, calendar = await asyncio.to_thread(_get_info, resolved)

        if info is None:
            result["error"] = f"No data found for '{company_name}'"
            return result

        result["symbol"] = info.get("symbol", company_name.upper())
        result["shortName"] = info.get("shortName")
        result["longName"] = info.get("longName")
        result["price"] = info.get("currentPrice") or info.get("regularMarketPrice")
        result["market_cap"] = info.get("marketCap")
        result["sector"] = info.get("sector")
        result["industry"] = info.get("industry")
        result["employees"] = info.get("fullTimeEmployees")

        # Growth & earnings signals
        result["revenue_growth"] = info.get("revenueGrowth")
        result["earnings_growth"] = info.get("earningsGrowth")
        result["total_revenue"] = info.get("totalRevenue")
        result["operating_margins"] = info.get("operatingMargins")
        result["free_cashflow"] = info.get("freeCashflow")
        result["recommendation_key"] = info.get("recommendationKey")
        result["target_mean_price"] = info.get("targetMeanPrice")

        # Leadership & company profile
        result["peg_ratio"] = info.get("trailingPegRatio")
        try:
            officers = info.get("companyOfficers") or []
            if officers:
                ceo = officers[0]
                result["ceo_name"] = ceo.get("name")
                result["ceo_title"] = ceo.get("title")
        except Exception:
            pass

        # Peer comparison fields
        result["trailingPE"] = info.get("trailingPE")
        result["priceToSalesTrailing12Months"] = info.get("priceToSalesTrailing12Months")
        result["grossMargins"] = info.get("grossMargins")

        # Technical snapshot fields
        result["fiftyTwoWeekHigh"] = info.get("fiftyTwoWeekHigh")
        result["fiftyTwoWeekLow"] = info.get("fiftyTwoWeekLow")
        result["fiftyDayAverage"] = info.get("fiftyDayAverage")
        result["twoHundredDayAverage"] = info.get("twoHundredDayAverage")

        # Earnings surprise (most recent quarter)
        trailing_eps = info.get("trailingEps")
        forward_eps = info.get("forwardEps")
        if trailing_eps and forward_eps and forward_eps > 0:
            result["earnings_surprise_pct"] = round(
                ((trailing_eps - forward_eps) / abs(forward_eps)) * 100, 2
            )

        # 30-day price change + RSI from 3mo history
        if hist is not None and len(hist) >= 2:
            start_price = hist["Close"].iloc[0]
            end_price = hist["Close"].iloc[-1]
            if start_price and start_price > 0:
                result["30d_change_pct"] = round(
                    ((end_price - start_price) / start_price) * 100, 2
                )
            if len(hist) >= 15:
                result["rsi14"] = _calculate_rsi(hist["Close"])

        # Earnings date from calendar
        try:
            dates = calendar.get("Earnings Date") or calendar.get("earningsDate") or []
            if dates and len(dates) > 0:
                d = dates[0]
                if hasattr(d, "date"):
                    result["earnings_date"] = str(d.date())
                else:
                    result["earnings_date"] = str(d)[:10]
        except Exception:
            pass

        result["success"] = True

    except Exception as e:
        result["error"] = str(e)

    if result["success"]:
        set_cache(_key, result)
    return result


def _get_peer_info(sym: str) -> dict:
    """Blocking: fetch minimal peer comparison data for a ticker."""
    t = yf.Ticker(sym)
    info = t.info
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    mcap = info.get("marketCap")
    return {
        "ticker": sym,
        "name": info.get("shortName") or sym,
        "price": price,
        "market_cap": mcap,
        "trailingPE": info.get("trailingPE"),
        "priceToSalesTrailing12Months": info.get("priceToSalesTrailing12Months"),
        "revenueGrowth": info.get("revenueGrowth"),
        "grossMargins": info.get("grossMargins"),
    }


async def fetch_peer_data(ticker_symbols: list[str]) -> list[dict]:
    """Fetch peer comparison data for up to 3 competitor tickers — all in parallel."""
    async def _fetch_one(sym: str) -> dict | None:
        try:
            data = await asyncio.wait_for(
                asyncio.to_thread(_get_peer_info, sym),
                timeout=10.0,
            )
            print(f"[peers] {sym}: price={data.get('price')} mcap={data.get('market_cap')}")
            return data
        except Exception as e:
            logger.warning(f"fetch_peer_data failed for '{sym}': {e}")
            return None

    raw = await asyncio.gather(*[_fetch_one(s) for s in ticker_symbols[:3]])
    return [r for r in raw if r is not None and r.get("price") is not None]
