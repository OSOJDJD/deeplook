"""
deeplook/verdict_generator.py — Deterministic verdict from data conditions.
No LLM required. Used when use_llm=False (the default pipeline).
"""
import re as _re


def generate_verdict(structured_data: dict, entity_type: str) -> dict:
    """Generate deterministic verdict from structured_data. No LLM call."""
    try:
        if entity_type == "public_equity":
            return _equity_verdict(structured_data)
        elif entity_type == "crypto":
            return _crypto_verdict(structured_data)
        elif entity_type in ("private_or_unlisted", "venture_capital", "foundation"):
            return _private_verdict(structured_data)
        elif entity_type == "defunct":
            return _defunct_verdict(structured_data)
        else:
            return _private_verdict(structured_data)
    except Exception:
        name = structured_data.get("company_name", "Company")
        return {
            "one_line": f"{name} — data available, awaiting analysis.",
            "momentum": "uncertain",
            "tailwind": "Insufficient data for tailwind assessment.",
            "headwind": "Insufficient data for headwind assessment.",
            "watch_for": "Review structured data for latest developments.",
        }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _parse_rev_growth(val) -> float | None:
    """Parse revenue growth string like '+23%' or '+23% YoY' or 0.23 float."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        v = float(val)
        # yfinance returns decimals like 0.23 for 23%
        return v * 100 if abs(v) < 5 else v
    m = _re.search(r'([+-]?\d+\.?\d*)', str(val))
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def _median(values: list) -> float | None:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    n = len(vals)
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2


# ── Entity verdict functions ──────────────────────────────────────────────────

def _equity_verdict(sd: dict) -> dict:
    company = sd.get("company_name", "Company")
    tech = sd.get("technicals") or {}
    fin = sd.get("financials") or {}
    val = sd.get("valuation") or {}
    earnings = sd.get("earnings") or {}
    news = sd.get("news_for_compression") or []
    peers = sd.get("peers") or []
    price_info = sd.get("price") or {}

    rsi = tech.get("rsi_14")
    ma50_signal = tech.get("ma50_signal")
    ma200_signal = tech.get("ma200_signal")
    ma200_dist = tech.get("ma200_distance_pct")
    rev_growth = _parse_rev_growth(fin.get("revenue_growth"))
    gross_margin = fin.get("gross_margin")
    pe_ratio = val.get("pe_ratio")
    analyst_target = val.get("analyst_target")
    price_current = price_info.get("current")

    # Count positive / negative signals
    positive = 0
    negative = 0

    if rsi is not None:
        if rsi > 60:
            positive += 1
        elif rsi < 40:
            negative += 1

    if ma200_signal == "above":
        positive += 1
    elif ma200_signal == "below":
        negative += 1

    if rev_growth is not None:
        if rev_growth > 50:
            positive += 2
        elif rev_growth > 20:
            positive += 1
        elif rev_growth < 0:
            negative += 1

    # Momentum classification
    if positive >= 3 and negative == 0:
        momentum = "accelerating"
    elif positive > negative:
        momentum = "steady"
    elif negative >= 3:
        momentum = "decelerating"
    else:
        momentum = "uncertain"

    # Tailwind: strongest positive data point
    tailwind = None
    if rev_growth is not None and rev_growth > 0:
        tailwind = f"Revenue growing at {rev_growth:+.0f}% YoY."
    elif gross_margin is not None and gross_margin > 0.5:
        tailwind = f"Strong gross margin of {gross_margin*100:.0f}%."
    elif analyst_target is not None and price_current is not None:
        try:
            upside = (float(analyst_target) - float(price_current)) / float(price_current) * 100
            if upside > 0:
                tailwind = f"Analyst consensus target implies {upside:+.0f}% upside."
        except Exception:
            pass
    if tailwind is None:
        tailwind = "No dominant positive driver identified from available data."

    # Headwind: strongest negative data point
    headwind = None
    if pe_ratio is not None and pe_ratio > 40:
        peer_pes = [p.get("pe") for p in peers if p.get("pe") is not None]
        peer_median = _median(peer_pes)
        if peer_median and pe_ratio > peer_median * 1.2:
            headwind = f"P/E of {pe_ratio:.1f}x is premium vs peer median {peer_median:.1f}x."
        else:
            headwind = f"Elevated P/E ratio of {pe_ratio:.1f}x."
    elif ma200_signal == "below":
        dist_str = f" ({ma200_dist:+.1f}%)" if ma200_dist is not None else ""
        headwind = f"Trading below 200-day moving average{dist_str}."
    elif rsi is not None and rsi > 70:
        headwind = f"RSI at {rsi:.1f} — technically overbought."
    if headwind is None:
        headwind = "No dominant risk factor identified from available data."

    # Watch for: next catalyst
    watch_for = None
    next_earnings = earnings.get("next_earnings_date")
    if next_earnings:
        watch_for = f"Next earnings date: {str(next_earnings)[:10]}."
    elif ma200_signal == "below" and ma200_dist is not None:
        watch_for = f"Watch for recovery above MA200 (currently {ma200_dist:+.1f}% away)."
    elif news:
        watch_for = "Monitor latest news for developing catalysts."
    if watch_for is None:
        watch_for = "Track next earnings release for direction confirmation."

    tailwind_kw = tailwind.split(".")[0].lower()
    headwind_kw = headwind.split(".")[0].lower()
    one_line = f"{company} shows {momentum} momentum driven by {tailwind_kw}, but faces {headwind_kw}."
    if len(one_line) > 120:
        one_line = one_line[:119].rsplit(" ", 1)[0].rstrip(",") + "…"

    return {
        "one_line": one_line,
        "momentum": momentum,
        "tailwind": tailwind,
        "headwind": headwind,
        "watch_for": watch_for,
    }


def _crypto_verdict(sd: dict) -> dict:
    company = sd.get("company_name", "Token")
    cn = sd.get("crypto_numbers") or {}
    price_change_30d = cn.get("price_change_30d")
    mcap_tvl = cn.get("mcap_tvl_ratio")

    # Momentum from 30d price change
    momentum = "uncertain"
    if price_change_30d is not None:
        try:
            change = float(price_change_30d)
            if change > 20:
                momentum = "accelerating"
            elif change > 0:
                momentum = "steady"
            elif change < -20:
                momentum = "decelerating"
        except Exception:
            pass

    # Tailwind / headwind from MCap/TVL ratio
    tailwind = None
    headwind = None
    if mcap_tvl is not None:
        try:
            ratio = float(mcap_tvl)
            if ratio < 1.0:
                tailwind = f"Market cap below locked value (MCap/TVL: {ratio:.2f}) — potentially undervalued relative to protocol usage."
            elif ratio > 5.0:
                headwind = f"Market cap significantly exceeds TVL (MCap/TVL: {ratio:.2f}) — speculative premium may compress."
        except Exception:
            pass

    if tailwind is None and price_change_30d is not None:
        try:
            if float(price_change_30d) > 10:
                tailwind = f"Strong 30-day price momentum (+{float(price_change_30d):.1f}%)."
        except Exception:
            pass
    if tailwind is None:
        tailwind = "Protocol activity data available — review TVL trends for usage signals."

    if headwind is None and price_change_30d is not None:
        try:
            if float(price_change_30d) < -10:
                headwind = f"Significant 30-day price decline ({float(price_change_30d):.1f}%)."
        except Exception:
            pass
    if headwind is None:
        headwind = "Crypto market volatility remains primary risk factor."

    watch_for = "Track TVL changes and governance proposals for directional shifts."

    tw_kw = tailwind.split(".")[0].lower()
    hw_kw = headwind.split(".")[0].lower()
    one_line = f"{company} shows {momentum} momentum — {tw_kw}."

    return {
        "one_line": one_line,
        "momentum": momentum,
        "tailwind": tailwind,
        "headwind": headwind,
        "watch_for": watch_for,
    }


def _private_verdict(sd: dict) -> dict:
    company = sd.get("company_name", "Company")
    funding = sd.get("funding") or {}
    news = sd.get("news_for_compression") or []

    last_round = funding.get("last_round")
    total_raised = funding.get("total_raised")

    momentum = "uncertain"

    if last_round:
        tailwind = f"Funding trajectory: {last_round}."
    elif total_raised:
        tailwind = f"Total capital raised: {total_raised}."
    else:
        tailwind = "Private company — review funding rounds for trajectory signals."

    headwind = "Limited public data; valuation and performance metrics not disclosed."
    watch_for = "Monitor news for funding announcements or valuation updates." if news else "Watch for next funding round or IPO filing signals."

    one_line = f"{company} is a private company — momentum uncertain without public market data."

    return {
        "one_line": one_line,
        "momentum": momentum,
        "tailwind": tailwind,
        "headwind": headwind,
        "watch_for": watch_for,
    }


def _defunct_verdict(sd: dict) -> dict:
    company = sd.get("company_name", "Company")
    news = sd.get("news_for_compression") or []

    momentum = "decelerating"
    tailwind = "Recovery proceedings may return value to creditors."
    headwind = "Company ceased operations — ongoing legal and restructuring complexity."
    watch_for = "Track legal proceedings and creditor recovery distributions." if news else "Monitor court filings for recovery timeline updates."

    one_line = f"{company} is defunct — focus on recovery proceedings and creditor outcomes."

    return {
        "one_line": one_line,
        "momentum": momentum,
        "tailwind": tailwind,
        "headwind": headwind,
        "watch_for": watch_for,
    }
