"""
Human-readable terminal formatter for DeepLook research output.

Usage:
    python -m deeplook.formatter output/Aave_2026-03-12.json
    # or call format_report(result_dict) from code
"""

import json
import re
import sys
import textwrap
from datetime import datetime

WIDTH = 120
DIVIDER = "═" * WIDTH
THIN = "─" * WIDTH


def _b(text: str, mode: str = "ansi") -> str:
    """Bold text: ANSI escape for terminal, Markdown bold for MCP/web."""
    if mode == "ansi":
        return f"\033[1m{text}\033[0m"
    if mode == "markdown":
        return f"**{text}**"
    return str(text)


def _wrap(text: str, indent: int = 3, subsequent_indent: int = None) -> str:
    prefix = " " * indent
    sub_prefix = " " * subsequent_indent if subsequent_indent is not None else prefix
    return textwrap.fill(str(text), width=WIDTH, initial_indent=prefix, subsequent_indent=sub_prefix)


def _is_empty(val) -> bool:
    """Return True if val is effectively empty/missing/insufficient."""
    if val is None:
        return True
    s = str(val).strip().lower()
    return s in (
        "", "insufficient data", "not available from sources", "[]", "{}",
        "n/a", "not available", "not avail", "tbd", "none", "null",
        "not available.", "not avail.",
    )


def _sentiment_icon(sentiment: str) -> str:
    s = (sentiment or "").lower()
    if s == "positive":
        return "🟢"
    if s == "negative":
        return "🔴"
    return "🟡"


def _section_header(title: str, bold_mode: str = "ansi") -> str:
    return f"\n {_b(title, bold_mode)}"


def format_lookup_markdown(data: dict) -> str:
    """Compact markdown snapshot for deeplook_lookup — works with v2 and v1 pipeline output."""
    is_v2 = data.get("_version") == "2.0"

    if is_v2:
        sd = data.get("structured_data") or {}
        cm = data.get("compressed") or {}
        vd = cm.get("verdict") or {}
        price_info = sd.get("price") or {}
        company_meta = sd.get("company_meta") or {}

        company = data.get("company", "Unknown")
        ticker = data.get("ticker") or ""
        ticker_str = f" ({ticker})" if ticker else ""
        price = price_info.get("current")
        mcap = price_info.get("market_cap") or ""
        sector = company_meta.get("sector") or ""
        industry = company_meta.get("industry") or sector

        one_line = vd.get("one_line") or ""
        bull = vd.get("bull_case") or ""
        bear = vd.get("bear_case") or ""
        wait_for = vd.get("wait_for") or ""
        confidence = vd.get("confidence") or ""
    else:
        # v1 fallback
        j = data.get("judgment", data)
        ai = j.get("ai_judgment", {})
        vd = ai.get("verdict", {})
        market = j.get("market_data", {})
        overview = j.get("overview", {})

        company = j.get("company_name") or data.get("company", "Unknown")
        yf_d = ((data.get("fetcher_results") or {}).get("yfinance") or {}).get("data") or {}
        ticker = yf_d.get("symbol") or overview.get("stage", "")
        ticker_str = f" ({ticker})" if ticker and ticker.upper() != company.upper() else ""
        price = market.get("price")
        mcap = market.get("market_cap") or ""
        sector = overview.get("sector") or ""
        industry = sector

        one_line = vd.get("one_line") or ""
        bull = vd.get("bull_case") or ""
        bear = vd.get("bear_case") or ""
        wait_for = vd.get("wait_for") or ""
        confidence = vd.get("confidence") or ""

    price_str = f"${price}" if price is not None else "—"
    mcap_str = f"MCap {mcap}" if mcap else ""
    header_right = " | ".join(p for p in [price_str, mcap_str] if p)
    line1 = f"**{company}{ticker_str}** — {header_right}" if header_right else f"**{company}{ticker_str}**"

    sources = len(data.get("sources_succeeded") or [])
    elapsed = data.get("elapsed_seconds", "?")
    footer = " | ".join(p for p in [
        f"Confidence: {confidence}" if confidence else "",
        f"{sources} sources" if sources else "",
        f"{elapsed}s" if elapsed != "?" else "",
    ] if p)

    lines = [line1]
    if industry:
        lines.append(industry)
    if not _is_empty(one_line):
        lines.append(f"Verdict: {one_line}")
    if not _is_empty(bull):
        lines.append(f"🟢 Bull: {bull}")
    if not _is_empty(bear):
        lines.append(f"🔴 Bear: {bear}")
    if not _is_empty(wait_for):
        lines.append(f"⏳ Wait: {wait_for}")
    if footer:
        lines.append(footer)

    return "\n".join(lines)


# ── Structured JSON + Dual Output ─────────────────────────────────────────

def _safe_pct(val):
    if val is None: return None
    try:
        f = float(val)
        return round(f * 100, 1) if abs(f) < 5 else round(f, 1)
    except (TypeError, ValueError): return None

_BASE_SCHEMA_KEYS = frozenset({
    "company", "entity_type", "sector", "one_liner", "phase", "momentum",
    "price", "market_cap", "metrics", "valuation", "peers", "signals",
    "verdict", "catalysts", "guidance", "segments", "ceo",
    "sources_count", "generation_time_sec",
})

def _clean_dict(d):
    if not isinstance(d, dict): return d
    return {k: _clean_dict(v) for k, v in d.items()
            if k in _BASE_SCHEMA_KEYS or (v is not None and v != [] and v != {})}

_JUNK_STRINGS = frozenset((
    "not available", "n/a", "unknown", "insufficient data",
    "none", "null", "tbd", "-", "—", "na", "n/a.",
))

def _clean_junk(v):
    """Return None if v is a junk string, otherwise return v unchanged.
    For numeric strings with trailing context like '300 (as of Dec 2025)',
    extract and return the leading number as float.
    """
    if not isinstance(v, str):
        return v
    stripped = v.strip()
    if stripped.lower() in _JUNK_STRINGS:
        return None
    # Extract leading number from strings like "300 (as of December 2025)"
    import re as _re
    m = _re.match(r'^([+-]?[\d,]+\.?\d*)\s*\(', stripped)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except (ValueError, TypeError):
            pass
    return v

def build_structured_json(data):
    def _safe(fn, default=None):
        try:
            return fn()
        except Exception:
            return default

    try:
        j = data.get("judgment", data)
        ov = j.get("overview") or {}
        mk = j.get("market_data") or {}
        fu = j.get("funding") or {}
        sig = j.get("recent_signals") or []
        ai = j.get("ai_judgment") or {}
        val = j.get("valuation") or {}
        vd = ai.get("verdict") or {}
        et = data.get("entity_type","") or j.get("entity_type","")
        yfd = ((data.get("fetcher_results") or {}).get("yfinance") or {}).get("data") or {}
        tech = data.get("technical_snapshot") or {}
        pr = data.get("peer_comparison") or []
    except Exception:
        j, ov, mk, fu, sig, ai, val, vd, et, yfd, tech, pr = {}, {}, {}, {}, [], {}, {}, {}, "", {}, {}, []

    def _val_clean(v):
        if v is None or _is_empty(str(v)): return None
        try: return float(str(v).replace(",","").strip())
        except (TypeError, ValueError): return v

    # metrics — always present as dict (base schema requirement)
    metrics = {}
    if et == "public_equity" and yfd:
        metrics = {k: _clean_junk(v) for k, v in {
            "revenue_ttm": _safe(lambda: yfd.get("total_revenue")),
            "revenue_yoy_pct": _safe(lambda: _safe_pct(yfd.get("revenue_growth"))),
            "gross_margin_pct": _safe(lambda: _safe_pct(yfd.get("grossMargins"))),
            "operating_margin_pct": _safe(lambda: _safe_pct(yfd.get("operating_margins"))),
            "fcf_ttm": _safe(lambda: yfd.get("free_cashflow")),
            "pe_ratio": _safe(lambda: yfd.get("trailingPE")),
            "peg_ratio": _safe(lambda: yfd.get("peg_ratio")),
            "ps_ratio": _safe(lambda: yfd.get("priceToSalesTrailing12Months")),
            "earnings_growth_pct": _safe(lambda: _safe_pct(yfd.get("earnings_growth"))),
        }.items()}

    # valuation — always present as dict (base schema requirement)
    ra = _safe(lambda: val.get("REGIME_A_public_equity_only") or {}, {})
    rb = _safe(lambda: val.get("REGIME_B_crypto_only") or val.get("REGIME_B_crypto") or {}, {})
    if ra:
        valuation_obj = {k: _clean_junk(v) for k, v in {
            "pe_ratio": _safe(lambda: _val_clean(ra.get("pe_ratio"))),
            "price_to_sales": _safe(lambda: _val_clean(ra.get("price_to_sales"))),
            "analyst_target": _safe(lambda: _val_clean(ra.get("analyst_target_price"))),
        }.items()}
    elif rb:
        valuation_obj = {k: _clean_junk(v) for k, v in {
            "fdv": _safe(lambda: rb.get("fully_diluted_valuation")),
            "mcap_to_tvl": _safe(lambda: _val_clean(rb.get("market_cap_to_tvl"))),
            "protocol_revenue_annual": _safe(lambda: rb.get("protocol_revenue_annual")),
            "upcoming_unlocks": _safe(lambda: rb.get("upcoming_unlocks")),
        }.items()}
    else:
        valuation_obj = {}

    # peers — always present as list (base schema requirement)
    peers_list = []
    for p in pr[:3]:
        try:
            peers_list.append({
                "ticker": p.get("ticker"),
                "name": p.get("name"),
                "price": p.get("price"),
                "market_cap": p.get("market_cap"),
                "pe": p.get("trailingPE"),
                "ps": p.get("priceToSalesTrailing12Months"),
                "rev_growth_pct": _safe_pct(p.get("revenueGrowth")),
                "gross_margin_pct": _safe_pct(p.get("grossMargins")),
            })
        except Exception:
            pass

    # signals — always present as list (base schema requirement)
    signals_list = []
    try:
        ss = sorted(sig, key=lambda s: s.get("date","") or "", reverse=True)[:8]
        signals_list = [{"date": s.get("date"), "sentiment": s.get("sentiment"), "alert_type": s.get("alert_type", "normal"), "summary": s.get("summary")} for s in ss]
    except Exception:
        pass

    # catalysts — always present as list (base schema requirement)
    catalysts_list = []
    try:
        catalysts_list = [{"date": c.get("date"), "event": c.get("event")} for c in (j.get("upcoming_catalysts") or [])[:3]]
    except Exception:
        pass

    # verdict — always present with base 4 keys; stance/triggers added by validate_verdict (optional)
    verdict_obj = {
        "one_line": _safe(lambda: vd.get("one_line")) or None,
        "bull_case": _safe(lambda: vd.get("bull_case")) or None,
        "bear_case": _safe(lambda: vd.get("bear_case")) or None,
        "wait_for": _safe(lambda: vd.get("wait_for")) or None,
        "stance": _safe(lambda: vd.get("stance")) or None,
        "entry_trigger": _safe(lambda: vd.get("entry_trigger")) or None,
        "risk_trigger": _safe(lambda: vd.get("risk_trigger")) or None,
        "peer_context": _safe(lambda: vd.get("peer_context")) or None,
    }

    # CEO from yfinance
    _ceo = None
    try:
        _cn = _clean_junk(yfd.get("ceo_name"))
        _ct = _clean_junk(yfd.get("ceo_title"))
        if _cn:
            _ceo = {"name": _cn, "title": _ct}
    except Exception:
        pass

    # guidance + segments from ai_judgment (populated by ACT step)
    _guidance = _safe(lambda: ai.get("guidance"))
    _segments = _safe(lambda: ai.get("segments") or [], [])

    # Base schema — all keys always present, null if no data
    o = {
        "company": _safe(lambda: j.get("company_name") or data.get("company",""), ""),
        "entity_type": et,
        "sector": _safe(lambda: ov.get("sector")) or None,
        "one_liner": _safe(lambda: ov.get("one_liner")) or None,
        "phase": _safe(lambda: ai.get("company_phase")) or None,
        "momentum": _safe(lambda: ai.get("momentum")) or None,
        "price": _safe(lambda: mk.get("price")),
        "market_cap": _safe(lambda: mk.get("market_cap")) or None,
        "metrics": metrics,
        "valuation": valuation_obj,
        "peers": peers_list,
        "signals": signals_list,
        "verdict": verdict_obj,
        "catalysts": catalysts_list,
        "guidance": _guidance,
        "segments": _segments,
        "ceo": _ceo,
        "sources_count": _safe(lambda: len(data.get("sources_succeeded",[])), 0),
        "generation_time_sec": _safe(lambda: data.get("elapsed_seconds")),
    }

    # Optional price trend
    try:
        _trend_raw = mk.get("30d_trend")
        if _trend_raw:
            try:
                o["price_trend_30d"] = float(str(_trend_raw).replace("%","").replace("+","").strip())
            except (TypeError, ValueError):
                o["price_trend_30d"] = _trend_raw
    except Exception:
        pass

    # public_equity: technicals
    if et == "public_equity" and tech:
        try:
            o["technicals"] = {
                "52w_high": tech.get("52w_high"),
                "52w_low": tech.get("52w_low"),
                "vs_ath_pct": tech.get("pct_from_high"),
                "ma_50d": tech.get("50d_ma"),
                "ma_200d": tech.get("200d_ma"),
                "rsi_14": tech.get("rsi14"),
            }
        except Exception:
            pass

    # crypto: top-level protocol fields
    if et == "crypto":
        try:
            mk_metrics = mk.get("key_metrics") or mk.get("key_metric") or []
            def _km_val(kw):
                if not isinstance(mk_metrics, list): return None
                for m in mk_metrics:
                    if kw.lower() in str(m).lower() and ":" in str(m):
                        return str(m).split(":", 1)[1].strip()
                return None
            tvl = _safe(lambda: rb.get("tvl") or _km_val("tvl"))
            if tvl: o["tvl"] = tvl
            token_price = _safe(lambda: mk.get("price"))
            if token_price is not None: o["token_price"] = token_price
        except Exception:
            pass

    # non-public_equity: funding
    if et != "public_equity":
        try:
            ff = {}
            if fu.get("total_raised"): ff["total_raised"] = fu["total_raised"]
            if fu.get("last_round"): ff["last_round"] = fu["last_round"]
            if fu.get("key_investors"): ff["key_investors"] = fu["key_investors"]
            if ff: o["funding"] = ff
        except Exception:
            pass

    # Optional metadata fields
    try:
        for attr, key in [("hq_location","hq"), ("founded","founded"), ("team_size","team_size")]:
            v = ov.get(attr)
            if v: o[key] = v
    except Exception:
        pass

    # analysis_context from validate_verdict (optional — only present if validate ran)
    _analysis_ctx = _safe(lambda: ai.get("analysis_context"))
    if _analysis_ctx:
        o["analysis_context"] = _analysis_ctx

    # Safety net: base schema list keys must always be present
    for _bk in ("peers", "signals", "catalysts"):
        if _bk not in o:
            o[_bk] = []

    return o

def format_summary_markdown(data):
    j = data.get("judgment", data)
    ov = j.get("overview") or {}
    mk = j.get("market_data") or {}
    ai = j.get("ai_judgment") or {}
    sig = j.get("recent_signals") or []
    vd = ai.get("verdict") or {}
    name = j.get("company_name") or data.get("company","Unknown")
    price = mk.get("price")
    phase = ai.get("company_phase","")
    momentum = ai.get("momentum","")
    one_liner = ov.get("one_liner","")
    L = []
    h = "## " + name
    if price: h += " \u2014 $" + str(price)
    if phase:
        h += " | " + phase
        if momentum: h += " / " + momentum
    L.append(h); L.append("")
    if one_liner and str(one_liner).strip().lower() not in ("","n/a","not available"):
        L.append(str(one_liner).split(". ")[0].strip() + "."); L.append("")
    ss = sorted(sig, key=lambda s: s.get("date","") or "", reverse=True)[:3]
    if ss:
        L.append("**Key Signals:**")
        for s in ss:
            sn = (s.get("sentiment") or "").lower()
            ic = "\U0001F7E2" if sn=="positive" else ("\U0001F534" if sn=="negative" else "\U0001F7E1")
            L.append("- " + ic + " " + (s.get("date","")) + " \u2014 " + (s.get("summary","")))
        L.append("")
    ol = vd.get("one_line",""); bu = vd.get("bull_case",""); be = vd.get("bear_case",""); wa = vd.get("wait_for","")
    entry = vd.get("entry_trigger",""); risk_t = vd.get("risk_trigger",""); peer_ctx = vd.get("peer_context","")
    if ol:
        L.append("**Verdict:** " + ol)
        if bu: L.append("- \U0001F7E2 " + bu)
        if be: L.append("- \U0001F534 " + be)
        if wa: L.append("- \u23F3 " + wa)
        if entry: L.append("- \u25B6 Entry: " + entry)
        if risk_t: L.append("- \u26A0 Exit if: " + risk_t)
        if peer_ctx: L.append("- \U0001F4CA " + peer_ctx)
        L.append("")
    cats = j.get("upcoming_catalysts") or []
    if cats:
        L.append("**Upcoming:**")
        for cc in cats[:2]:
            if cc.get("event"): L.append("- " + cc.get("date","TBD") + ": " + cc["event"])
        L.append("")
    el = data.get("elapsed_seconds","?"); sc = len(data.get("sources_succeeded",[]))
    L.append("*" + str(sc) + " sources | " + str(el) + "s \u2014 Generated by [DeepLook](https://github.com/OSOJDJD/deeplook)*")
    return "\n".join(L)

def build_structured_json_v2(data: dict) -> dict:
    """Assemble clean v2 JSON schema from v2 pipeline output."""
    sd = data.get("structured_data") or {}
    cm = data.get("compressed") or {}
    et = data.get("entity_type", "") or sd.get("entity_type", "")

    peers = []
    for p in (sd.get("peers") or []):
        try:
            peers.append({
                "ticker": p.get("ticker"),
                "name": p.get("name"),
                "price": p.get("price"),
                "market_cap": p.get("market_cap"),
                "pe": p.get("pe"),
                "ps": p.get("ps"),
                "rev_growth_pct": p.get("rev_growth_pct"),
                "gross_margin_pct": p.get("gross_margin_pct"),
                "rsi_14": p.get("rsi_14"),
            })
        except Exception:
            pass

    recent_news = [
        {"date": n.get("date"), "summary": n.get("summary"), "sentiment": n.get("sentiment")}
        for n in (cm.get("recent_news") or [])
    ]

    price_info = sd.get("price") or {}
    financials = sd.get("financials") or {}
    valuation = sd.get("valuation") or {}
    technicals_raw = sd.get("technicals") or {}
    earnings = sd.get("earnings") or {}
    funding = sd.get("funding") or {}
    meta_overview = sd.get("company_meta") or {}

    out = {
        "version": "2.0",
        "company": data.get("company", ""),
        "ticker": data.get("ticker"),
        "entity_type": et,
        "research_date": sd.get("research_date", ""),
        "price": {
            "current": price_info.get("current"),
            "change_1d": technicals_raw.get("change_1d"),
            "change_30d": technicals_raw.get("change_30d"),
            "market_cap": price_info.get("market_cap"),
            "currency": price_info.get("currency", "USD"),
        },
        "financials": {
            "revenue_ttm": financials.get("revenue_ttm"),
            "revenue_growth_yoy": financials.get("revenue_growth"),
            "earnings_growth_yoy": financials.get("earnings_growth"),
            "gross_margin": financials.get("gross_margin"),
            "operating_margin": financials.get("operating_margin"),
            "net_margin": financials.get("net_margin"),
            "net_income_ttm": financials.get("net_income_ttm"),
            "fcf_ttm": financials.get("fcf"),
        },
        "valuation": valuation,
        "technicals": {
            "rsi_14": technicals_raw.get("rsi_14"),
            "ma50": technicals_raw.get("ma50"),
            "ma50_signal": technicals_raw.get("ma50_signal"),
            "ma50_distance_pct": technicals_raw.get("ma50_distance_pct"),
            "ma200": technicals_raw.get("ma200"),
            "ma200_signal": technicals_raw.get("ma200_signal"),
            "ma200_distance_pct": technicals_raw.get("ma200_distance_pct"),
            "high_52w": technicals_raw.get("high_52w"),
            "low_52w": technicals_raw.get("low_52w"),
            "position_52w_pct": technicals_raw.get("position_52w_pct"),
            "volume": technicals_raw.get("volume"),
            "avg_volume_20d": technicals_raw.get("avg_volume_20d"),
            "volume_ratio": technicals_raw.get("volume_ratio"),
            "next_earnings": str(earnings.get("next_earnings_date"))[:10] if earnings.get("next_earnings_date") else None,
        },
        "peers": peers,
        "recent_news": recent_news,
        "forward_looking": cm.get("forward_looking", []),
        "entity_context": cm.get("entity_context", []),
        "verdict": cm.get("verdict") or {},
        "funding": funding if et not in ("public_equity",) else {},
        "meta": {
            "company_meta": meta_overview,
            "sources_ok": len(data.get("sources_succeeded", [])),
            "sources_failed": len(data.get("sources_failed", [])),
            "sources_list": data.get("sources_succeeded", []),
            "generation_time_sec": data.get("elapsed_seconds"),
            "version": "2.0",
        },
    }

    # Entity-specific supplemental fields
    if et == "crypto":
        cn = sd.get("crypto_numbers") or {}
        out["crypto"] = {
            "token_price": cn.get("token_price"),
            "price_change_24h": cn.get("price_change_24h"),
            "price_change_30d": cn.get("price_change_30d"),
            "market_cap": cn.get("market_cap"),
            "volume_24h": cn.get("volume_24h"),
            "tvl": cn.get("tvl"),
            "mcap_tvl_ratio": cn.get("mcap_tvl_ratio"),
            "category": cn.get("category"),
            "chains_count": cn.get("chains_count"),
        }
    elif et in ("venture_capital", "private_or_unlisted", "foundation"):
        vn = sd.get("vc_numbers") or {}
        out["vc"] = {
            "aum": vn.get("aum"),
            "total_investments": vn.get("total_investments"),
            "notable_portfolio": vn.get("notable_investments"),
            "last_fund": vn.get("last_fund"),
            "last_fund_size": vn.get("last_fund_size"),
            "stage_focus": vn.get("stage_focus"),
            "hq": vn.get("hq"),
        }
    elif et == "defunct":
        out["defunct"] = {
            "peak_valuation": None,
            "shutdown_date": None,
            "creditor_recovery_pct": None,
            "cause": None,
        }

    return out


def format_dual_output_v2(data: dict) -> str:
    """v2 pipeline: 5-section entity-specific output + structured JSON."""
    import json as _json

    structured = build_structured_json_v2(data)
    company = structured.get("company", "")
    ticker = structured.get("ticker") or ""
    ticker_str = f" ({ticker})" if ticker else ""
    et = structured.get("entity_type", "")
    price_info = structured.get("price") or {}
    fin = structured.get("financials") or {}
    val = structured.get("valuation") or {}
    tech = structured.get("technicals") or {}
    peers = structured.get("peers") or []
    recent_news = structured.get("recent_news") or []
    forward_looking = structured.get("forward_looking") or []
    entity_context = structured.get("entity_context") or []
    verdict_v2 = structured.get("verdict") or {}
    meta = (structured.get("meta") or {}).get("company_meta") or {}
    crypto = structured.get("crypto") or {}
    vc = structured.get("vc") or {}
    defunct_info = structured.get("defunct") or {}

    L = []

    # ── Section 1: IDENTITY ─────────────────────────────────────────────────
    L.append(f"## {company}{ticker_str}")

    if et == "public_equity":
        id_parts = []
        if meta.get("sector"):
            id_parts.append(f"Sector: {meta['sector']}")
        if meta.get("industry"):
            id_parts.append(f"Industry: {meta['industry']}")
        if meta.get("team_size"):
            ts = meta["team_size"]
            id_parts.append(f"Employees: {ts:,}" if isinstance(ts, int) else f"Employees: {ts}")
        if id_parts:
            L.append(" | ".join(id_parts))
        id2 = []
        if meta.get("ceo"):
            id2.append(f"CEO: {meta['ceo']}")
        if price_info.get("market_cap"):
            id2.append(f"Market Cap: {price_info['market_cap']}")
        if id2:
            L.append(" | ".join(id2))

    elif et == "crypto":
        id_parts = []
        if crypto.get("category"):
            id_parts.append(f"Type: {crypto['category']}")
        if crypto.get("chains_count"):
            id_parts.append(f"Chains: {crypto['chains_count']}")
        if id_parts:
            L.append(" | ".join(id_parts))
        if price_info.get("market_cap"):
            L.append(f"Market Cap: {price_info['market_cap']}")

    elif et in ("venture_capital", "private_or_unlisted", "foundation"):
        id_parts = []
        if et == "venture_capital":
            id_parts.append("Type: Venture Capital")
        if vc.get("stage_focus"):
            id_parts.append(f"Stage Focus: {vc['stage_focus']}")
        if vc.get("hq"):
            id_parts.append(f"HQ: {vc['hq']}")
        if id_parts:
            L.append(" | ".join(id_parts))
        if vc.get("aum"):
            L.append(f"AUM: {vc['aum']}")

    elif et == "defunct":
        id_parts = ["Type: Defunct"]
        if defunct_info.get("shutdown_date"):
            id_parts.append(f"Shutdown: {defunct_info['shutdown_date']}")
        if id_parts:
            L.append(" | ".join(id_parts))
        if defunct_info.get("cause"):
            L.append(f"Cause: {defunct_info['cause']}")

    L.append("")

    # ── Section 2: NUMBERS ──────────────────────────────────────────────────
    L.append("## NUMBERS")

    def _fmt_vol(v):
        if v is None:
            return None
        try:
            v = float(v)
            if v >= 1e6:
                return f"{v/1e6:.1f}M"
            elif v >= 1e3:
                return f"{v/1e3:.0f}K"
            return str(int(v))
        except Exception:
            return None

    def _fmt_money(v):
        if v is None:
            return None
        try:
            v = float(v)
            if v >= 1e12:
                return f"${v/1e12:.1f}T"
            elif v >= 1e9:
                return f"${v/1e9:.1f}B"
            elif v >= 1e6:
                return f"${v/1e6:.1f}M"
            return f"${v:,.0f}"
        except Exception:
            return None

    if et == "public_equity":
        # Line 1: Price, Change 1D, Change 30D
        p1 = []
        cur = price_info.get("current")
        if cur is not None:
            try:
                p1.append(f"Price: ${float(cur):,.2f}")
            except Exception:
                p1.append(f"Price: ${cur}")
        c1d = price_info.get("change_1d")
        if c1d is not None:
            try:
                p1.append(f"Change 1D: {'+' if float(c1d) >= 0 else ''}{float(c1d):.1f}%")
            except Exception:
                pass
        c30d = price_info.get("change_30d")
        if c30d is not None:
            try:
                p1.append(f"Change 30D: {'+' if float(c30d) >= 0 else ''}{float(c30d):.1f}%")
            except Exception:
                pass
        if p1:
            L.append(" | ".join(p1))

        # Line 2: 52W
        p2 = []
        h52 = tech.get("high_52w")
        l52 = tech.get("low_52w")
        if h52 is not None:
            p2.append(f"52W High: ${h52:,.2f}")
        if l52 is not None:
            p2.append(f"52W Low: ${l52:,.2f}")
        pos52 = tech.get("position_52w_pct")
        if pos52 is not None:
            p2.append(f"52W Position: {pos52:.1f}%")
        if p2:
            L.append(" | ".join(p2))

        # Line 3: RSI, MA
        p3 = []
        rsi = tech.get("rsi_14")
        if rsi is not None:
            p3.append(f"RSI-14: {rsi:.1f}")
        ma50 = tech.get("ma50")
        if ma50 is not None:
            sig = tech.get("ma50_signal", "")
            dist = tech.get("ma50_distance_pct")
            dist_str = f", {'+' if (dist or 0) >= 0 else ''}{dist:.1f}%" if dist is not None else ""
            p3.append(f"MA50: ${ma50:,.2f} ({sig}{dist_str})")
        ma200 = tech.get("ma200")
        if ma200 is not None:
            sig = tech.get("ma200_signal", "")
            dist = tech.get("ma200_distance_pct")
            dist_str = f", {'+' if (dist or 0) >= 0 else ''}{dist:.1f}%" if dist is not None else ""
            p3.append(f"MA200: ${ma200:,.2f} ({sig}{dist_str})")
        if p3:
            L.append(" | ".join(p3))

        # Line 4: Volume
        p4 = []
        vol = tech.get("volume")
        avg_vol = tech.get("avg_volume_20d")
        vol_ratio = tech.get("volume_ratio")
        if vol is not None:
            vs = _fmt_vol(vol)
            if vs:
                p4.append(f"Volume: {vs}")
        if avg_vol is not None:
            vs = _fmt_vol(avg_vol)
            if vs:
                p4.append(f"Avg Vol 20D: {vs}")
        if vol_ratio is not None:
            p4.append(f"Vol Ratio: {vol_ratio:.2f}x")
        if p4:
            L.append(" | ".join(p4))

        # Next Earnings
        ne = tech.get("next_earnings")
        if ne:
            L.append(f"Next Earnings: {ne}")

        # Financials
        r1 = []
        rv = _fmt_money(fin.get("revenue_ttm"))
        if rv:
            r1.append(f"Revenue TTM: {rv}")
        if fin.get("revenue_growth_yoy"):
            r1.append(f"Rev Growth YoY: {fin['revenue_growth_yoy']}")
        if r1:
            L.append(" | ".join(r1))
        r2 = []
        ni = _fmt_money(fin.get("net_income_ttm"))
        if ni:
            r2.append(f"Net Income TTM: {ni}")
        nm = fin.get("net_margin")
        if nm is not None:
            r2.append(f"Net Margin: {nm*100:.1f}%")
        if r2:
            L.append(" | ".join(r2))
        r3 = []
        if val.get("pe_ratio") is not None:
            r3.append(f"P/E: {val['pe_ratio']:.1f}")
        if val.get("fwd_pe_ratio") is not None:
            r3.append(f"Fwd P/E: {val['fwd_pe_ratio']:.1f}")
        if val.get("peg_ratio") is not None:
            r3.append(f"PEG: {val['peg_ratio']:.2f}")
        if r3:
            L.append(" | ".join(r3))

    elif et == "crypto":
        # Token price + changes
        n1 = []
        tp = crypto.get("token_price")
        if tp is not None:
            try:
                n1.append(f"Token Price: ${float(tp):,.4f}" if float(tp) < 1 else f"Token Price: ${float(tp):,.2f}")
            except Exception:
                n1.append(f"Token Price: ${tp}")
        c24h = crypto.get("price_change_24h")
        if c24h is not None:
            try:
                n1.append(f"24h Change: {'+' if float(c24h) >= 0 else ''}{float(c24h):.1f}%")
            except Exception:
                pass
        c30d = crypto.get("price_change_30d")
        if c30d is not None:
            try:
                n1.append(f"30D Change: {'+' if float(c30d) >= 0 else ''}{float(c30d):.1f}%")
            except Exception:
                pass
        if n1:
            L.append(" | ".join(n1))
        # Market cap + FDV + volume
        n2 = []
        mc = _fmt_money(crypto.get("market_cap"))
        if mc:
            n2.append(f"Market Cap: {mc}")
        vol24h = _fmt_money(crypto.get("volume_24h"))
        if vol24h:
            n2.append(f"24h Volume: {vol24h}")
        if n2:
            L.append(" | ".join(n2))
        # TVL + MCap/TVL
        n3 = []
        tvl_val = _fmt_money(crypto.get("tvl"))
        if tvl_val:
            n3.append(f"TVL: {tvl_val}")
        mcap_tvl = crypto.get("mcap_tvl_ratio")
        if mcap_tvl is not None:
            try:
                n3.append(f"MCap/TVL: {float(mcap_tvl):.3f}")
            except Exception:
                pass
        if n3:
            L.append(" | ".join(n3))

    elif et in ("venture_capital", "private_or_unlisted", "foundation"):
        n1 = []
        if vc.get("aum"):
            n1.append(f"AUM: {vc['aum']}")
        if vc.get("total_investments") is not None:
            n1.append(f"Total Investments: {vc['total_investments']}")
        if n1:
            L.append(" | ".join(n1))
        if vc.get("last_fund"):
            fund_str = vc["last_fund"]
            if vc.get("last_fund_size"):
                fund_str += f" ({vc['last_fund_size']})"
            L.append(f"Last Fund: {fund_str}")
        portfolio = vc.get("notable_portfolio") or []
        if portfolio:
            L.append(f"Notable Portfolio: {', '.join(str(p) for p in portfolio[:5])}")

    elif et == "defunct":
        if defunct_info.get("peak_valuation"):
            L.append(f"Peak Valuation: {_fmt_money(defunct_info['peak_valuation'])}")
        if defunct_info.get("creditor_recovery_pct") is not None:
            L.append(f"Creditor Recovery: ~{defunct_info['creditor_recovery_pct']}% of claims")

    L.append("")

    # ── Section 3: PEERS ────────────────────────────────────────────────────
    if peers and et == "public_equity":
        L.append("## PEERS")
        L.append("| Company | P/E | Rev Growth | Margin | RSI-14 |")
        L.append("|---------|-----|-----------|--------|--------|")
        for p in peers[:4]:
            p_name = p.get("name") or p.get("ticker") or "—"
            p_pe = f"{p['pe']:.1f}" if p.get("pe") is not None else "N/A"
            p_rg = f"{p['rev_growth_pct']*100:+.1f}%" if p.get("rev_growth_pct") is not None else "N/A"
            p_margin = f"{p['gross_margin_pct']*100:.1f}%" if p.get("gross_margin_pct") is not None else "N/A"
            p_rsi = f"{p['rsi_14']:.1f}" if p.get("rsi_14") is not None else "N/A"
            L.append(f"| {p_name} | {p_pe} | {p_rg} | {p_margin} | {p_rsi} |")
        L.append("")
    elif et != "defunct":
        L.append("## PEERS")
        # For crypto/private: no live peer data — just note comparables if we had them
        L.append("")

    # ── Section 4: RECENT ───────────────────────────────────────────────────
    L.append("## RECENT")
    if recent_news:
        for n in recent_news[:5]:
            date_str = (n.get("date") or "")[:10]
            summary = n.get("summary", "")
            L.append(f"- [{date_str}] {summary}")
    else:
        L.append("- Insufficient data")
    # Append entity_context as Context: lines
    if entity_context:
        ctx_str = " ".join(str(c) for c in entity_context[:3])
        L.append(f"Context: {ctx_str}")
    L.append("")

    # ── Section 5: FORWARD ──────────────────────────────────────────────────
    L.append("## FORWARD")
    if forward_looking:
        for item in forward_looking[:3]:
            L.append(f"- {item}")
    else:
        L.append("- Insufficient data")
    L.append("")

    # ── Section 6: VERDICT ──────────────────────────────────────────────────
    if verdict_v2:
        L.append("## Verdict")
        one_line = verdict_v2.get("one_line", "")
        stance = verdict_v2.get("stance", "")
        confidence = verdict_v2.get("confidence", "")
        bull = verdict_v2.get("bull_case", "")
        bear = verdict_v2.get("bear_case", "")
        wait_for = verdict_v2.get("wait_for", "")
        action = verdict_v2.get("action", "")
        header = f"**{one_line}**"
        if stance or confidence:
            badge_parts = []
            if stance:
                badge_parts.append(stance)
            if confidence:
                badge_parts.append(f"{confidence} confidence")
            header += f" ({' | '.join(badge_parts)})"
        L.append(header)
        if bull:
            L.append(f"🟢 {bull}")
        if bear:
            L.append(f"🔴 {bear}")
        if wait_for:
            L.append(f"⏳ {wait_for}")
        if action:
            L.append(f"▶ {action}")
        L.append("")

    # ── Footer ──────────────────────────────────────────────────────────────
    n_sources = (structured.get("meta") or {}).get("sources_ok", 0)
    elapsed = data.get("elapsed_seconds", "?")
    L.append(f"*{n_sources} sources | {elapsed}s | DeepLook v2.0*")
    L.append("")

    summary = "\n".join(L)
    json_str = _json.dumps(structured, ensure_ascii=False, indent=2, default=str)
    return summary + "\n\n<!-- STRUCTURED_DATA_START\n" + json_str + "\nSTRUCTURED_DATA_END -->\n"


def format_dual_output(data):
    import json as _json
    import os as _os
    import time as _time
    if data.get("_version") == "2.0":
        return format_dual_output_v2(data)
    _t_fmt = _time.time()
    summary = format_summary_markdown(data)
    structured = build_structured_json(data)
    try:
        _tw = (data.get("_timing") or {}).get("total_wall")
        if _tw is not None:
            structured["generation_time_sec"] = round(float(_tw), 1)
    except Exception:
        pass
    json_str = _json.dumps(structured, ensure_ascii=False, indent=2, default=str)
    _formatter_time = round(_time.time() - _t_fmt, 2)
    timing_block = ""
    if _os.environ.get("DEBUG_TIMING", "").lower() == "true":
        _tm = data.get("_timing") or {}
        # Ordered keys for the block
        _lines = []
        _lines.append(f"entity_routing: {_tm.get('entity_routing', '?')}s")
        # Per-fetcher lines — collect all fetcher_* keys in insertion order
        _fetcher_keys = [k for k in _tm if k.startswith("fetcher_")]
        for _fk in _fetcher_keys:
            _fv = _tm[_fk]
            _fname = _fk[len("fetcher_"):]
            if _fv is None:
                _lines.append(f"fetcher_{_fname}: skipped")
            else:
                _lines.append(f"fetcher_{_fname}: {_fv}s")
        _lines.append(f"llm_search_queries: {_tm.get('llm_search_queries', '?')}s")
        _lines.append(f"llm_extract: {_tm.get('llm_extract', '?')}s")
        _lines.append(f"llm_judge: {_tm.get('llm_judge', '?')}s")
        _lines.append(f"llm_act: {_tm.get('llm_act', '?')}s")
        _validate_t = ((data.get("judgment") or data).get("metadata") or {}).get("_timing_llm_validate")
        _lines.append(f"llm_validate: {_validate_t if _validate_t is not None else '?'}s")
        _lines.append(f"formatter: {_formatter_time}s")
        _lines.append(f"total_wall: {_tm.get('total_wall', '?')}s")
        timing_block = "\n\n<!-- TIMING_DEBUG\n" + "\n".join(_lines) + "\n-->"

    display_hint = """<!-- DISPLAY_HINT:
Build an HTML artifact dashboard with these sections in order:

1. HEADER: Company name, ticker, price with 30d change, phase badge (green=EXPANDING, yellow=STABLE, red=CONTRACTING), momentum badge.
   Sub-line: CEO name and title (from ceo object), HQ location, founded year, employee count.

2. CRITICAL ALERTS: If any signal has alert_type="critical", show as highlighted warning box (amber background, bold text) ABOVE everything else.

3. METRIC CARDS ROW: 4-6 cards — Market Cap, P/E ratio (show PEG if available from metrics.peg_ratio), Revenue Growth %, Operating Margin, FCF, Earnings Growth. Use the metrics and valuation objects.

4. VERDICT BOX: One-line verdict in bold. Stance badge (green=bullish, red=bearish, grey=neutral). Bull case (green left border), Bear case (red left border), Wait for (amber left border). If entry_trigger present: show as "▶ Entry:" row. If risk_trigger present: show as "⚠ Exit if:" row. If peer_context present: show as italic comparison note below verdict.

5. GUIDANCE TABLE: If guidance is non-null, show forward guidance as compact table with columns: Metric, Guidance Value, Sentiment (green=strong, amber=in-line, red=weak).

6. BUSINESS SEGMENTS: If segments array is non-empty, show as compact list or horizontal bar chart with segment name, metric, and context.

7. SIGNALS TIMELINE: 5-8 signals with colored dots (green=positive, red=negative, yellow=neutral) and dates.

8. PEER TABLE: If peers array is non-empty, show comparison table with columns: Name, Price, Market Cap, P/E, Revenue Growth, Gross Margin. Highlight the subject company row.

9. CATALYSTS: Upcoming events with dates.

10. FOOTER: Sources count, generation time, DeepLook link.

Style: Clean, professional. Light background cards with subtle borders. Monospace font for numbers. System fonts for text.
If a section's data is null/empty, skip that section entirely — do not show empty placeholders.
-->"""
    return summary + "\n\n<!-- STRUCTURED_DATA_START\n" + json_str + "\nSTRUCTURED_DATA_END -->\n\n" + display_hint + timing_block

def main():
    if len(sys.argv) < 2:
        print("Usage: python -m deeplook.formatter <output_file.json>", file=sys.stderr)
        sys.exit(1)

    path = sys.argv[1]
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    format_report(data)


def build_structured_json_v3(data: dict) -> dict:
    """Assemble clean v3 JSON schema from v3 pipeline output."""
    import json as _json
    from datetime import date as _date

    sd = data.get("structured_data") or {}
    v3 = sd.get("_v3") or {}
    cm = data.get("compressed") or {}
    et = data.get("entity_type", "") or sd.get("entity_type", "")

    result = {
        "version": "3.0",

        # 1. IDENTITY
        "identity": {
            "name": data.get("company", ""),
            "entity_type": et,
            "sector": (v3.get("identity") or {}).get("sector"),
            "description": cm.get("description"),
            "founded": (v3.get("identity") or {}).get("founded"),
            "headquarters": (v3.get("identity") or {}).get("headquarters"),
        },

        # 2. SCALE
        "scale": {
            "employees": (v3.get("scale") or {}).get("employees"),
            "revenue": (v3.get("scale") or {}).get("revenue"),
            "market_cap": (v3.get("scale") or {}).get("market_cap"),
            "valuation": None,
            "valuation_source": None,
            "valuation_date": None,
            "funding_total": (sd.get("funding") or {}).get("total_raised"),
        },

        # 3. PEOPLE
        "people": {
            "ceo": (v3.get("people") or {}).get("ceo"),
            "founders": cm.get("founders") or [],
            "key_people": [],
        },

        # 4. SIGNALS
        "signals": {
            "events": cm.get("events") or [],
            "context": cm.get("context") or [],
        },

        # 5. PEERS
        "peers": sd.get("peers") or [],

        # 6. OUTLOOK
        "outlook": {
            "catalysts": cm.get("catalysts") or [],
            "next_event": (v3.get("outlook") or {}).get("next_event"),
            "next_event_date": (v3.get("outlook") or {}).get("next_event_date"),
        },

        # 7. VERDICT
        "verdict": cm.get("verdict") or {},

        # 8. META
        "meta": {
            "sources": data.get("sources_succeeded") or [],
            "sources_ok": len(data.get("sources_succeeded") or []),
            "sources_failed": len(data.get("sources_failed") or []),
            "freshness": str(_date.today()),
            "generation_time_sec": round(data.get("elapsed_seconds") or 0, 1),
            "schema_version": "3.0",
        },

        # 9. MODULES
        "modules": {
            "financial": None,
            "crypto": None,
        },
    }

    # Fill private company valuation from LLM extraction
    val_extract = cm.get("valuation_extract")
    if val_extract and isinstance(val_extract, dict):
        result["scale"]["valuation"] = val_extract.get("value")
        result["scale"]["valuation_source"] = val_extract.get("source")
        result["scale"]["valuation_date"] = val_extract.get("date")

    # Fill financial module (public_equity only)
    if et == "public_equity":
        result["modules"]["financial"] = {
            "price": sd.get("price") or {},
            "valuation": sd.get("valuation") or {},
            "technicals": sd.get("technicals") or {},
            "margins": ((v3.get("modules") or {}).get("financial") or {}).get("margins") or {},
        }

    # Fill crypto module
    if et == "crypto":
        result["modules"]["crypto"] = (v3.get("modules") or {}).get("crypto") or {}

    return result


def format_output_v3(data: dict) -> str:
    """v3 pipeline: clean markdown output + embedded JSON."""
    import json as _json

    sj = build_structured_json_v3(data)

    identity = sj["identity"]
    scale = sj["scale"]
    people = sj["people"]
    signals = sj["signals"]
    peers = sj["peers"]
    outlook = sj["outlook"]
    verdict = sj["verdict"]
    meta = sj["meta"]
    modules = sj["modules"]

    lines = []

    # === HEADER ===
    ticker = data.get("ticker")
    if ticker:
        lines.append(f"## {identity['name']} ({ticker})")
    else:
        lines.append(f"## {identity['name']}")

    # Subheader: sector · industry · founded · employees
    sd_raw = data.get("structured_data") or {}
    industry = (sd_raw.get("company_meta") or {}).get("industry") or (sd_raw.get("_v3") or {}).get("identity", {}).get("industry")
    parts = []
    if identity.get("sector"):
        parts.append(identity["sector"])
    if industry and industry != identity.get("sector"):
        parts.append(industry)
    if identity.get("founded"):
        parts.append(f"Founded {identity['founded']}")
    emp = scale.get("employees")
    if emp:
        try:
            emp_int = int(emp)
            parts.append(f"{emp_int // 1000}K people" if emp_int >= 1000 else f"{emp_int} people")
        except (TypeError, ValueError):
            pass
    if parts:
        lines.append(" · ".join(parts))

    # Scale line: market cap / valuation + revenue + funding
    scale_parts = []
    if scale.get("market_cap"):
        scale_parts.append(f"Market cap: {scale['market_cap']}")
    elif scale.get("valuation"):
        scale_parts.append(f"Valuation: {scale['valuation']} ({scale.get('valuation_source') or 'estimated'})")
    if scale.get("revenue"):
        scale_parts.append(f"Revenue: {scale['revenue']}")
    if scale.get("funding_total"):
        scale_parts.append(f"Raised: {scale['funding_total']}")
    if scale_parts:
        lines.append(" · ".join(scale_parts))

    ceo_name = people.get("ceo") or ""
    if ceo_name:
        for prefix in ["Mr. ", "Mrs. ", "Ms. ", "Dr. "]:
            ceo_name = ceo_name.replace(prefix, "")
        ceo_name = ceo_name.strip()
        if ceo_name:
            lines.append(f"CEO: {ceo_name}")

    lines.append("")

    # === WHAT'S HAPPENING ===
    events = signals.get("events") or []
    if events:
        lines.append("## What's happening")
        for event in events[:5]:
            date_str = event.get("date", "")
            summary = event.get("summary", "")
            lines.append(f"- [{date_str}] {summary}")
        lines.append("")

    # === CONTEXT (what / so what / but what) ===
    context = signals.get("context") or []
    if len(context) >= 1:
        lines.append("## So what")
        lines.append(context[0])
        lines.append("")
    if len(context) >= 2:
        lines.append(context[1])
        lines.append("")
    if len(context) >= 3:
        lines.append("## Watch out")
        lines.append(context[2])
        lines.append("")

    # === PEERS ===
    if peers:
        lines.append("## Compared to")
        if modules.get("financial") and peers:
            lines.append("| Company | P/E | Rev Growth | Margin | RSI-14 |")
            lines.append("|---------|-----|-----------|--------|--------|")
            for p in peers[:5]:
                pe = p.get("pe", "N/A")
                if isinstance(pe, (int, float)):
                    pe = f"{pe:.1f}"
                rg = p.get("rev_growth_pct")
                rg_str = f"+{rg*100:.1f}%" if rg and rg > 0 else (f"{rg*100:.1f}%" if rg else "N/A")
                gm = p.get("gross_margin_pct")
                gm_str = f"{gm*100:.1f}%" if gm else "N/A"
                rsi = p.get("rsi_14", "N/A")
                if isinstance(rsi, (int, float)):
                    rsi = f"{rsi:.1f}"
                lines.append(f"| {p.get('name', '')} | {pe} | {rg_str} | {gm_str} | {rsi} |")
        lines.append("")

    # === COMING UP ===
    catalysts = outlook.get("catalysts") or []
    next_event = outlook.get("next_event")
    if catalysts or next_event:
        lines.append("## Coming up")
        if next_event:
            lines.append(f"→ {next_event} ({outlook.get('next_event_date', '')})")
        for cat in catalysts[:4]:
            lines.append(f"→ {cat}")
        lines.append("")

    # === VERDICT ===
    if verdict:
        lines.append("## Verdict")
        one_line = verdict.get("one_line", "")
        momentum = verdict.get("momentum", "")
        lines.append(f"**{one_line}** ({momentum})")
        tailwind = verdict.get("tailwind", "")
        headwind = verdict.get("headwind", "")
        watch_for = verdict.get("watch_for", "")
        if tailwind:
            lines.append(f"↑ {tailwind}")
        if headwind:
            lines.append(f"↓ {headwind}")
        if watch_for:
            lines.append(f"⏳ {watch_for}")
        lines.append("")

    # === FINANCIAL DETAILS ===
    fin = modules.get("financial")
    if fin:
        lines.append("## Financial details")
        price_data = fin.get("price") or {}
        val_data = fin.get("valuation") or {}
        tech_data = fin.get("technicals") or {}
        margins_data = fin.get("margins") or {}

        price_parts = []
        if price_data.get("current"):
            price_parts.append(f"Price: ${price_data['current']}")
        _change_1d = price_data.get("change_1d") if price_data.get("change_1d") is not None else tech_data.get("change_1d")
        if _change_1d is not None:
            try:
                price_parts.append(f"1D: {float(_change_1d):+.1f}%")
            except (TypeError, ValueError):
                pass
        _change_30d = price_data.get("change_30d") if price_data.get("change_30d") is not None else tech_data.get("change_30d")
        if _change_30d is not None:
            try:
                price_parts.append(f"30D: {float(_change_30d):+.1f}%")
            except (TypeError, ValueError):
                pass
        if price_parts:
            lines.append(" | ".join(price_parts))

        range_parts = []
        h52 = tech_data.get("high_52w")
        l52 = tech_data.get("low_52w")
        pos = tech_data.get("position_52w_pct")
        if h52 and l52:
            pos_str = f" ({pos:.1f}%)" if pos is not None else ""
            range_parts.append(f"52W: ${l52} - ${h52}{pos_str}")
        vol_ratio = tech_data.get("volume_ratio")
        if vol_ratio is not None:
            try:
                range_parts.append(f"Vol: {float(vol_ratio):.2f}x avg")
            except (TypeError, ValueError):
                pass
        if range_parts:
            lines.append(" | ".join(range_parts))

        val_parts = []
        if val_data.get("pe_ratio"):
            try:
                val_parts.append(f"P/E: {float(val_data['pe_ratio']):.1f}")
            except (TypeError, ValueError):
                pass
        if val_data.get("peg_ratio"):
            try:
                val_parts.append(f"PEG: {float(val_data['peg_ratio']):.2f}")
            except (TypeError, ValueError):
                pass
        if val_data.get("ps_ratio"):
            try:
                val_parts.append(f"P/S: {float(val_data['ps_ratio']):.1f}")
            except (TypeError, ValueError):
                pass
        if val_parts:
            lines.append(" | ".join(val_parts))

        tech_parts = []
        if tech_data.get("rsi_14"):
            try:
                tech_parts.append(f"RSI: {float(tech_data['rsi_14']):.1f}")
            except (TypeError, ValueError):
                pass
        if tech_data.get("ma50_signal"):
            try:
                tech_parts.append(f"MA50: {tech_data['ma50_signal']} ({float(tech_data.get('ma50_distance_pct', 0)):+.1f}%)")
            except (TypeError, ValueError):
                pass
        if tech_data.get("ma200_signal"):
            try:
                tech_parts.append(f"MA200: {tech_data['ma200_signal']} ({float(tech_data.get('ma200_distance_pct', 0)):+.1f}%)")
            except (TypeError, ValueError):
                pass
        if tech_parts:
            lines.append(" | ".join(tech_parts))

        margin_parts = []
        if margins_data.get("revenue_growth_yoy"):
            margin_parts.append(f"Rev Growth: {margins_data['revenue_growth_yoy']}")
        if margins_data.get("gross_margin"):
            try:
                margin_parts.append(f"Gross: {float(margins_data['gross_margin'])*100:.1f}%")
            except (TypeError, ValueError):
                pass
        if margins_data.get("operating_margin"):
            try:
                margin_parts.append(f"Op: {float(margins_data['operating_margin'])*100:.1f}%")
            except (TypeError, ValueError):
                pass
        if margin_parts:
            lines.append(" | ".join(margin_parts))

        lines.append("")

    # === CRYPTO DETAILS ===
    crypto = modules.get("crypto")
    if crypto:
        lines.append("## Token details")
        crypto_parts = []
        if crypto.get("token_price"):
            try:
                crypto_parts.append(f"Price: ${float(crypto['token_price']):.2f}")
            except (TypeError, ValueError):
                pass
        if crypto.get("market_cap"):
            try:
                crypto_parts.append(f"MCap: ${float(crypto['market_cap']):,.0f}")
            except (TypeError, ValueError):
                pass
        if crypto.get("tvl"):
            try:
                crypto_parts.append(f"TVL: ${float(crypto['tvl']):,.0f}")
            except (TypeError, ValueError):
                pass
        if crypto.get("mcap_tvl_ratio"):
            try:
                crypto_parts.append(f"MCap/TVL: {float(crypto['mcap_tvl_ratio']):.1f}")
            except (TypeError, ValueError):
                pass
        if crypto_parts:
            lines.append(" | ".join(crypto_parts))
        lines.append("")

    # === FOOTER ===
    lines.append(f"*{meta['sources_ok']} sources · {meta['generation_time_sec']}s · DeepLook v3.0*")

    # === EMBEDDED JSON ===
    markdown = "\n".join(lines)
    json_str = _json.dumps(sj, indent=2, ensure_ascii=False, default=str)

    return markdown + "\n\n<!-- STRUCTURED_DATA_START\n" + json_str + "\nSTRUCTURED_DATA_END -->\n"


if __name__ == "__main__":
    main()
