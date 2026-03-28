import logging
import os
from datetime import date, timedelta

logger = logging.getLogger(__name__)


def _get_client():
    """Return a Finnhub client, or None if API key is not set."""
    api_key = os.getenv("FINNHUB_API_KEY")
    if not api_key:
        return None
    try:
        import finnhub
        return finnhub.Client(api_key=api_key)
    except ImportError:
        return None


def fetch_finnhub(ticker: str) -> dict:
    """Fetch analyst, earnings, and news data from Finnhub for a ticker.

    Uses at most 4 API calls (free tier: 60/min).
    Returns {} if FINNHUB_API_KEY is not set.
    Each sub-call is independently protected with try/except.
    """
    client = _get_client()
    if client is None:
        return {}

    today = date.today()
    result: dict = {}

    # 1. Analyst recommendations — latest period only
    try:
        trends = client.recommendation_trends(ticker)
        if trends:
            latest = trends[0]
            result["analyst_recommendations"] = {
                "period":     latest.get("period"),
                "buy":        latest.get("buy"),
                "hold":       latest.get("hold"),
                "sell":       latest.get("sell"),
                "strongBuy":  latest.get("strongBuy"),
                "strongSell": latest.get("strongSell"),
            }
        else:
            result["analyst_recommendations"] = None
    except Exception as e:
        logger.warning(f"[finnhub] recommendation_trends failed for '{ticker}': {e}")
        result["analyst_recommendations"] = None

    # 2. Earnings surprise — last 4 quarters
    try:
        earnings = client.company_earnings(ticker, limit=4)
        if earnings:
            result["earnings_surprise"] = [
                {
                    "period":      e.get("period"),
                    "actual":      e.get("actual"),
                    "estimate":    e.get("estimate"),
                    "surprise":    e.get("surprise"),
                    "surprisePct": e.get("surprisePercent"),
                }
                for e in earnings
            ]
        else:
            result["earnings_surprise"] = []
    except Exception as e:
        logger.warning(f"[finnhub] company_earnings failed for '{ticker}': {e}")
        result["earnings_surprise"] = []

    # 3. Next earnings date — filter this ticker from the calendar
    try:
        cal = client.earnings_calendar(
            _from=today.strftime("%Y-%m-%d"),
            to=(today + timedelta(days=90)).strftime("%Y-%m-%d"),
            symbol=ticker,
        )
        earnings_list = (cal or {}).get("earningsCalendar") or []
        result["earnings_calendar"] = earnings_list[0] if earnings_list else None
    except Exception as e:
        logger.warning(f"[finnhub] earnings_calendar failed for '{ticker}': {e}")
        result["earnings_calendar"] = None

    # 4. Company news — last 7 days, max 10 articles
    try:
        news_raw = client.company_news(
            ticker,
            _from=(today - timedelta(days=7)).strftime("%Y-%m-%d"),
            to=today.strftime("%Y-%m-%d"),
        )
        result["company_news"] = [
            {
                "headline": n.get("headline"),
                "url":      n.get("url"),
                "datetime": n.get("datetime"),
            }
            for n in (news_raw or [])[:10]
        ]
    except Exception as e:
        logger.warning(f"[finnhub] company_news failed for '{ticker}': {e}")
        result["company_news"] = []

    return result
