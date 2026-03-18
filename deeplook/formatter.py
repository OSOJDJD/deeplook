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


def format_report(data: dict, bold_mode: str = "ansi") -> None:
    """Print a human-readable report to stdout from a research result dict."""

    # Support both top-level judgment (from run_research) and raw judgment dict
    j = data.get("judgment", data)

    # P0-4: handle error dict from synthesize() — never attempt to format a broken judgment
    if "error" in j:
        print(f"\n{DIVIDER}")
        print(f"  [PIPELINE ERROR] Report generation failed")
        print(f"  {j['error']}")
        meta = j.get("metadata", {})
        if meta:
            print(f"  LLM: {meta.get('llm_model_used', 'unknown')}  |  "
                  f"Time: {meta.get('total_time_seconds', '?')}s")
        print(DIVIDER)
        return

    overview = j.get("overview", {})
    market = j.get("market_data", {})
    funding = j.get("funding", {})
    signals = j.get("recent_signals", [])
    ai = j.get("ai_judgment", {})
    valuation = j.get("valuation", {})
    competitive = j.get("competitive_landscape", {})
    meta = j.get("metadata", {})

    # ── Header ──────────────────────────────────────────────────────────────
    name = j.get("company_name") or data.get("company", "Unknown")
    entity_type = data.get("entity_type", "") or j.get("entity_type", "")
    price = market.get("price")
    trend = market.get("30d_trend", "")
    mcap = market.get("market_cap", "")
    key_metrics_hdr = market.get("key_metrics") or market.get("key_metric", "")
    sector = overview.get("sector", "")
    founded = overview.get("founded", "")
    stage = overview.get("stage", "")
    phase = ai.get("company_phase", "")
    momentum = ai.get("momentum", "")

    def _trunc(s: str, n: int) -> str:
        s = str(s)
        return s if len(s) <= n else s[:n - 1] + "…"

    def _metric_val(metrics, *keywords) -> str:
        """Extract value from key_metrics array matching any keyword."""
        if not isinstance(metrics, list):
            return ""
        for m in metrics:
            m_str = str(m)
            for kw in keywords:
                if kw.lower() in m_str.lower() and ":" in m_str:
                    return m_str.split(":", 1)[1].strip()
        return ""

    # Build entity-type-aware header parts (omit field if data missing)
    hdr_parts = [name]
    if entity_type == "public_equity":
        if price:
            hdr_parts.append(f"${price}")
        if not _is_empty(trend):
            hdr_parts.append(trend)
        if not _is_empty(mcap):
            hdr_parts.append(f"MCap {mcap}")
    elif entity_type == "exchange":
        if not _is_empty(founded):
            hdr_parts.append(f"Est. {founded}")
        users = _metric_val(key_metrics_hdr, "users")
        if users:
            hdr_parts.append(users)
        lic = _metric_val(key_metrics_hdr, "licenses", "license", "jurisdiction")
        if lic:
            hdr_parts.append(lic)
    elif entity_type == "venture_capital":
        fund = _metric_val(key_metrics_hdr, "fund", "aum")
        if fund:
            hdr_parts.append(fund)
        focus = _metric_val(key_metrics_hdr, "focus")
        if focus:
            hdr_parts.append(focus)
    elif entity_type == "foundation":
        tvl = _metric_val(key_metrics_hdr, "tvl")
        if tvl:
            hdr_parts.append(f"TVL {tvl}")
        if not _is_empty(founded):
            hdr_parts.append(f"Est. {founded}")
        if not _is_empty(mcap):
            hdr_parts.append(f"MCap {mcap}")
    elif entity_type == "crypto":
        if price:
            hdr_parts.append(f"${price}")
        if not _is_empty(trend):
            hdr_parts.append(trend)
        if not _is_empty(mcap):
            hdr_parts.append(f"MCap {mcap}")
        tvl = _metric_val(key_metrics_hdr, "tvl")
        if tvl:
            hdr_parts.append(f"TVL {tvl}")
    else:  # private
        val = funding.get("total_raised", "")
        if not _is_empty(val):
            hdr_parts.append(val)
        if not _is_empty(founded):
            hdr_parts.append(f"Est. {founded}")

    hdr_parts[0] = _b(hdr_parts[0], bold_mode)
    print(DIVIDER)
    header_line = " " + "  |  ".join(hdr_parts)
    print(header_line)
    sub_parts = []
    if not _is_empty(sector):
        sub_parts.append(_trunc(sector, 50))
    if not _is_empty(founded):
        sub_parts.append(f"Est. {_trunc(founded, 10)}")
    phase_str = f"{_trunc(stage, 25)} ● {_trunc(phase, 20)} / {_trunc(momentum, 20)}"
    sub_parts.append(phase_str)
    print(" " + "  |  ".join(sub_parts))
    print(DIVIDER)

    # ── Key Signals ─────────────────────────────────────────────────────────
    if signals:
        print(_section_header("⚡ KEY SIGNALS", bold_mode))
        sorted_signals = sorted(
            signals,
            key=lambda s: s.get("date", "") or "",
            reverse=True,
        )[:3]  # max 3 signals
        for sig in sorted_signals:
            icon = _sentiment_icon(sig.get("sentiment", ""))
            date_str = sig.get("date", "")
            summary = sig.get("summary", "")
            so_what = sig.get("so_what", "")
            # Wrap long signal summaries, aligning continuation at col 10
            sig_prefix = f" {icon} {date_str} — "
            wrapped_summary = textwrap.wrap(summary, width=WIDTH - len(sig_prefix))
            if wrapped_summary:
                print(sig_prefix + _b(wrapped_summary[0], bold_mode))
                for extra in wrapped_summary[1:]:
                    print(" " * 10 + _b(extra, bold_mode))
            else:
                print(sig_prefix)
            # so_what: show Watch part, wrapped (no truncation)
            if so_what and not _is_empty(so_what):
                if "Watch:" in so_what:
                    watch_part = so_what.split("Watch:", 1)[1].strip()
                    prefix = "→ Watch: "
                elif "→" in so_what:
                    watch_part = so_what.rsplit("→", 1)[-1].strip()
                    prefix = "→ "
                else:
                    watch_part = ""
                    prefix = ""
                if watch_part:
                    print(_wrap(f"{prefix}{watch_part}", indent=10))

    # ── Competitive Position (MARKET POSITION + PEER COMPARISON) ────────────
    key_metrics_raw = market.get("key_metrics") or market.get("key_metric", "")
    one_liner = overview.get("one_liner", "")
    team_size = overview.get("team_size", "")
    hq = overview.get("hq_location", "")
    comp_note = competitive.get("comparison_note", "")
    comp_peers = competitive.get("main_competitors", [])
    peers = data.get("peer_comparison") or []

    if entity_type == "venture_capital":
        market_title = "📊 PORTFOLIO"
    elif entity_type == "foundation":
        market_title = "📊 ON-CHAIN METRICS"
    else:
        market_title = "📊 COMPETITIVE POSITION"

    has_market_content = (
        not _is_empty(one_liner)
        or (isinstance(key_metrics_raw, list) and any(not _is_empty(m) for m in key_metrics_raw))
        or (not isinstance(key_metrics_raw, list) and not _is_empty(key_metrics_raw))
        or not _is_empty(team_size)
        or not _is_empty(hq)
        or comp_peers
        or not _is_empty(comp_note)
        or (peers and entity_type == "public_equity")
    )

    if has_market_content:
        print(_section_header(market_title, bold_mode))

        if not _is_empty(one_liner):
            # First sentence only, truncated to one line
            first_sent = str(one_liner).split(". ")[0].strip()
            if first_sent and not first_sent.endswith("."):
                first_sent += "."
            max_len = WIDTH - 4  # 3-char indent + buffer
            if len(first_sent) > max_len:
                first_sent = first_sent[:max_len - 1] + "…"
            print(_wrap(first_sent))

        # key_metrics: aligned "Label:  Value" display
        # For public_equity, skip: (a) technical indicators (shown in tech rows below)
        #                          (b) valuation ratios (shown in VALUATION section)
        _TECH_SKIP = {"52-week", "52w", "50-day", "200-day", "moving average",
                      "50d", "200d", "rsi", "52 week"}
        _VAL_SKIP = {"p/e", "pe ratio", "pe:", "trailing pe", "forward pe",
                     "price/sales", "price-to-sales", "p/s", "ev/ebitda",
                     "price to sales", "analyst target", "analyst price"}
        km_has_employees = False
        if isinstance(key_metrics_raw, list):
            km_items = []
            for m in key_metrics_raw:
                if not _is_empty(m):
                    m_str = str(m)
                    m_lc = m_str.lower()
                    # Skip technical indicator rows (shown below in tech rows)
                    if entity_type == "public_equity" and any(
                        kw in m_lc for kw in _TECH_SKIP
                    ):
                        continue
                    # Skip valuation ratios (shown in VALUATION section)
                    if entity_type == "public_equity" and any(
                        kw in m_lc for kw in _VAL_SKIP
                    ):
                        continue
                    if ":" in m_str:
                        lbl, val = m_str.split(":", 1)
                        lbl_s, val_s = lbl.strip(), val.strip()
                        if not _is_empty(val_s):
                            if "employee" in lbl_s.lower():
                                km_has_employees = True
                            km_items.append((lbl_s, val_s))
                    else:
                        km_items.append(("", m_str))
            if km_items:
                lbl_w = max((len(lbl) for lbl, _ in km_items if lbl), default=0)
                for lbl, val in km_items:
                    if lbl:
                        print(f"   {(lbl + ':'):<{lbl_w + 1}} {val}")
                    else:
                        print(_wrap(val))
        elif not _is_empty(key_metrics_raw):
            print(_wrap(f"Key metric: {key_metrics_raw}"))

        # Only show team_size if Employees not already in key_metrics
        if not km_has_employees and not _is_empty(team_size):
            print(_wrap(f"Team size: {team_size}"))
        # HQ: skip if not available
        if not _is_empty(hq):
            print(_wrap(f"HQ: {hq}"))

        if entity_type == "venture_capital":
            peer_label = "Co-investors"
        elif entity_type == "exchange":
            peer_label = "vs exchanges"
        else:
            peer_label = "Competitors"

        if comp_peers:
            print()
            print(_wrap(f"{peer_label}: {', '.join(comp_peers[:3])}"))  # max 3

        if not _is_empty(comp_note):
            note_str = str(comp_note)
            first_line = note_str.split("\n")[0].strip()
            first_line = re.split(r'\.\s+(?=vs\b)', first_line)[0].strip()
            print(_wrap(f"vs peers: {first_line}", subsequent_indent=10))

        # Technical data (public_equity only) — shown here, no separate section
        if entity_type == "public_equity":
            tech = data.get("technical_snapshot") or {}
            if tech:
                high52 = tech.get("52w_high")
                low52 = tech.get("52w_low")
                pct_from_high = tech.get("pct_from_high")
                ma50 = tech.get("50d_ma")
                ma200 = tech.get("200d_ma")
                rsi = tech.get("rsi14")
                if any(v is not None for v in [high52, low52, ma50, ma200, rsi]):
                    print("   " + "─" * 40)
                    row1_parts = []
                    if high52 is not None:
                        row1_parts.append(f"52w High: ${high52:,.2f}")
                    if low52 is not None:
                        row1_parts.append(f"52w Low: ${low52:,.2f}")
                    if pct_from_high is not None:
                        row1_parts.append(f"vs ATH: {pct_from_high:+.1f}%")
                    if row1_parts:
                        print("   " + "  │  ".join(row1_parts))
                    row2_parts = []
                    if ma50 is not None:
                        row2_parts.append(f"50d MA:  ${ma50:,.2f}")
                    if ma200 is not None:
                        row2_parts.append(f"200d MA: ${ma200:,.2f}")
                    if rsi is not None:
                        rsi_tag = " ⚠️ overbought" if rsi > 70 else (" ⚠️ oversold" if rsi < 30 else "")
                        row2_parts.append(f"RSI(14): {_b(str(rsi), bold_mode)}{rsi_tag}")
                    if row2_parts:
                        print("   " + "  │  ".join(row2_parts))

        # Peer comparison table — inline under COMPETITIVE POSITION
        if peers and entity_type == "public_equity":
            yf_raw = (data.get("fetcher_results") or {}).get("yfinance") or {}
            yf_d = yf_raw.get("data") or {}
            target_row = {
                "ticker": yf_d.get("symbol") or name,
                "name": name,
                "price": market.get("price"),
                "market_cap": yf_d.get("market_cap"),
                "trailingPE": yf_d.get("trailingPE"),
                "priceToSalesTrailing12Months": yf_d.get("priceToSalesTrailing12Months"),
                "revenueGrowth": yf_d.get("revenue_growth"),
                "grossMargins": yf_d.get("grossMargins"),
                "_is_target": True,
            }
            all_rows = [target_row] + peers[:2]  # max 2 peers

            def _fmt_price(v):
                if v is None:
                    return "  —  "
                if v >= 1e12:
                    return f"${v/1e12:.2f}T"
                if v >= 1e9:
                    return f"${v/1e9:.1f}B"
                if v >= 1e6:
                    return f"${v/1e6:.0f}M"
                return f"${v:.2f}"

            def _fmt_mult(v):
                if v is None:
                    return " —  "
                return f"{v:.1f}x"

            def _fmt_pct(v):
                if v is None:
                    return "  — "
                return f"{v*100:+.0f}%"

            hdr = f"  {'Ticker':<8} {'Price':>7}  {'MCap':>7}  {'P/E':>6}  {'P/S':>6}  {'RevGr':>6}  {'GrMgn':>6}"
            print(hdr)
            print(f"  {'─'*8} {'─'*7}  {'─'*7}  {'─'*6}  {'─'*6}  {'─'*6}  {'─'*6}")
            for row in all_rows:
                ticker = str(row.get("ticker") or "?")[:8]
                marker = " ◀" if row.get("_is_target") else "  "
                price_s = f"${row['price']:.2f}" if row.get("price") else "  —  "
                mcap_s = _fmt_price(row.get("market_cap"))
                pe_s = _fmt_mult(row.get("trailingPE"))
                ps_s = _fmt_mult(row.get("priceToSalesTrailing12Months"))
                rev_s = _fmt_pct(row.get("revenueGrowth"))
                gm_s = _fmt_pct(row.get("grossMargins"))
                print(f"  {ticker:<8} {price_s:>7}  {mcap_s:>7}  {pe_s:>6}  {ps_s:>6}  {rev_s:>6}  {gm_s:>6}{marker}")

    # ── Valuation ────────────────────────────────────────────────────────────
    val_lines = []

    total_raised = funding.get("total_raised", "")
    last_round = funding.get("last_round", "")
    investors = funding.get("key_investors", [])

    regime_a = valuation.get("REGIME_A_public_equity_only", {})
    regime_b = valuation.get("REGIME_B_crypto_only") or valuation.get("REGIME_B_crypto") or {}

    if regime_a:
        for key, label in [
            ("pe_ratio", "P/E ratio"),
            ("price_to_sales", "Price/Sales"),
            ("analyst_target_price", "Analyst target"),
        ]:
            v = regime_a.get(key, "")
            if not _is_empty(v):
                if key == "analyst_target_price":
                    target_s = str(v).lstrip("$").split()[0].replace(",", "")
                    try:
                        target_f = float(target_s)
                        curr = market.get("price")
                        if curr:
                            upside = (target_f / float(curr) - 1) * 100
                            display = f"{_b(str(v), bold_mode)} ({upside:+.1f}% upside)"
                        else:
                            display = _b(str(v), bold_mode)
                    except (ValueError, TypeError):
                        display = _b(str(v), bold_mode)
                else:
                    display = str(v)
                val_lines.append(f"{label}: {display}")
        # vs Peers P/E — computed from peer comparison data
        if peers:
            peer_pes = [p.get("trailingPE") for p in peers[:2] if p.get("trailingPE") is not None]
            if peer_pes:
                avg_pe = sum(peer_pes) / len(peer_pes)
                peer_pe_str = " / ".join(f"{p:.1f}x" for p in peer_pes)
                val_lines.append(f"vs Peers P/E: avg {avg_pe:.1f}x ({peer_pe_str})")

    if regime_b:
        for key, label in [
            ("fully_diluted_valuation", "FDV"), ("market_cap_to_tvl", "MCap/TVL"),
            ("protocol_revenue_annual", "Protocol revenue (annual)"), ("upcoming_unlocks", "Upcoming unlocks"),
        ]:
            v = regime_b.get(key, "")
            if not _is_empty(v):
                val_lines.append(f"{label}: {v}")

    if val_lines:
        if entity_type == "public_equity":
            val_section_title = "💰 VALUATION"
        elif entity_type == "venture_capital":
            val_section_title = "💵 FUND STATUS"
        else:
            val_section_title = "💰 FUNDING"
        print(_section_header(val_section_title, bold_mode))
        for line in val_lines:
            print(_wrap(line))

    # ── Ownership & Funding ───────────────────────────────────────────────────
    ownership_lines = []
    if entity_type == "public_equity":
        if investors:
            if isinstance(investors, list):
                inv_str = ", ".join(str(i) for i in investors[:3])
            else:
                inv_str = str(investors)
            ownership_lines.append(f"Top holders: {inv_str}")
    else:
        if not _is_empty(total_raised):
            ownership_lines.append(f"Total raised: {total_raised}")
        if not _is_empty(last_round):
            ownership_lines.append(f"Last round: {last_round}")
        if investors:
            if isinstance(investors, list):
                inv_str = ", ".join(str(i) for i in investors[:3])
            else:
                inv_str = str(investors)
            ownership_lines.append(f"Investors: {inv_str}")

    if ownership_lines:
        if entity_type == "public_equity":
            own_section_title = "🏦 OWNERSHIP"
        elif entity_type == "exchange":
            own_section_title = "💵 SHAREHOLDERS"
        else:
            own_section_title = "💵 OWNERSHIP & FUNDING"
        print(_section_header(own_section_title, bold_mode))
        for line in ownership_lines:
            print(_wrap(line, subsequent_indent=10))

    # ── Catalysts & Risks (UPCOMING CATALYSTS + FORWARD LOOKING + RISKS) ────
    forward = ai.get("forward_looking", [])
    catalysts = j.get("upcoming_catalysts") or []
    risks = ai.get("risk_signals", [])

    has_catalysts_risks = (
        catalysts
        or any(not _is_empty(f) for f in forward)
        or any(not _is_empty(r) for r in risks)
    )

    if has_catalysts_risks:
        print(_section_header("📅 CATALYSTS & RISKS", bold_mode))
        # Collect catalyst event keywords for dedup
        cat_events_shown = []  # full event text for dedup
        shown_cats = 0
        for c in catalysts:
            if shown_cats >= 2:
                break
            dt = c.get("date", "TBD")
            event = c.get("event", "")
            if _is_empty(dt) or _is_empty(event):
                continue
            cat_events_shown.append(event.lower())
            why = c.get("why_it_matters", "")
            print(f"   {dt}  {event}")
            if why and not _is_empty(why):
                why_s = str(why).split("\n")[0].strip()
                print(_wrap(f"→ {why_s}", indent=10))
            shown_cats += 1
        # forward_looking: skip if it duplicates a catalyst event (word-overlap check)
        _STOP = {"the", "a", "an", "and", "or", "of", "in", "at", "to", "for", "on",
                 "is", "are", "be", "its", "with", "by", "will", "from"}
        def _kw(s):
            import re as _re
            return set(_re.findall(r'\w+', s.lower())) - _STOP
        for item in forward:
            if _is_empty(item):
                continue
            item_words = _kw(str(item))
            dup = False
            for ev in cat_events_shown:
                if len(item_words & _kw(ev)) >= 2:
                    dup = True
                    break
            if dup:
                continue
            print(_wrap(f"• {item}"))
        shown_risks = 0
        for r in risks:
            if shown_risks >= 2:
                break
            if not _is_empty(r):
                print(_wrap(f"⚠️  {r}", subsequent_indent=10))
                shown_risks += 1

    # ── Verdict ─────────────────────────────────────────────────────────────
    verdict = ai.get("verdict", {})
    # Graceful fallback: old format used recommended_action + wait_for at top level
    if not verdict:
        old_action = ai.get("recommended_action", "")
        old_wait = ai.get("wait_for", "")
        if old_action or old_wait:
            verdict = {"one_line": old_action, "wait_for": old_wait}

    if verdict:
        print(_section_header("🎯 VERDICT", bold_mode))
        one_line = verdict.get("one_line", "")
        bull = verdict.get("bull_case", "")
        bear = verdict.get("bear_case", "")
        wait = verdict.get("wait_for", "")
        if not _is_empty(one_line):
            print(_wrap(one_line))
        if not _is_empty(bull):
            print(_wrap(f"🟢 {bull}", subsequent_indent=10))
        if not _is_empty(bear):
            print(_wrap(f"🔴 {bear}", subsequent_indent=10))
        if not _is_empty(wait):
            print(_wrap(f"⏳ {wait}"))

    # ── Footer ───────────────────────────────────────────────────────────────
    succeeded = len(data.get("sources_succeeded", []))
    elapsed = data.get("elapsed_seconds", meta.get("total_time_seconds", "?"))

    print(DIVIDER)
    print(f" Sources: {succeeded} | {elapsed}s")
    print(f" 📊 Generated by DeepLook — Free AI company research | github.com/OSOJDJD/deeplook")
    print(f"{DIVIDER}\n")


def format_layer1(data: dict, bold_mode: str = "ansi") -> None:
    """Print compact Layer 1 summary (5-8 lines) for README demo / quick view."""
    j = data.get("judgment", data)
    if "error" in j:
        print(f"[ERROR] {j['error']}")
        return

    overview = j.get("overview", {})
    market = j.get("market_data", {})
    ai = j.get("ai_judgment", {})
    signals = j.get("recent_signals", [])
    verdict = ai.get("verdict", {})

    name = j.get("company_name") or data.get("company", "Unknown")
    yf_d = ((data.get("fetcher_results") or {}).get("yfinance") or {}).get("data") or {}
    ticker = yf_d.get("symbol") or overview.get("stage", "")
    phase = ai.get("company_phase", "")
    momentum = ai.get("momentum", "")

    ticker_str = f" ({ticker})" if ticker and ticker.upper() != name.upper() else ""
    phase_str = f"{phase} / {momentum}" if momentum else phase

    top3 = sorted(signals, key=lambda s: s.get("date", "") or "", reverse=True)[:3]

    print(f"\n{DIVIDER}")
    print(f"  {_b(name, bold_mode)}{ticker_str}  {phase_str}")
    print(THIN)
    for sig in top3:
        icon = _sentiment_icon(sig.get("sentiment", ""))
        summary = sig.get("summary", "")
        print(f"  {icon} {_b(summary, bold_mode)}")
    print(THIN)
    one_line = verdict.get("one_line", "")
    bull = verdict.get("bull_case", "")
    bear = verdict.get("bear_case", "")
    if not _is_empty(one_line):
        print(f"  {one_line}")
    if not _is_empty(bull):
        print(f"  {_b('🟢', bold_mode)} {bull}")
    if not _is_empty(bear):
        print(f"  {_b('🔴', bold_mode)} {bear}")
    print(DIVIDER)
    print()



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
    "verdict", "catalysts", "sources_count", "generation_time_sec",
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
        ss = sorted(sig, key=lambda s: s.get("date","") or "", reverse=True)[:5]
        signals_list = [{"date": s.get("date"), "sentiment": s.get("sentiment"), "summary": s.get("summary")} for s in ss]
    except Exception:
        pass

    # catalysts — always present as list (base schema requirement)
    catalysts_list = []
    try:
        catalysts_list = [{"date": c.get("date"), "event": c.get("event")} for c in (j.get("upcoming_catalysts") or [])[:3]]
    except Exception:
        pass

    # verdict — always present with all 4 keys (base schema requirement)
    verdict_obj = {
        "one_line": _safe(lambda: vd.get("one_line")) or None,
        "bull_case": _safe(lambda: vd.get("bull_case")) or None,
        "bear_case": _safe(lambda: vd.get("bear_case")) or None,
        "wait_for": _safe(lambda: vd.get("wait_for")) or None,
    }

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
    if ol:
        L.append("**Verdict:** " + ol)
        if bu: L.append("- \U0001F7E2 " + bu)
        if be: L.append("- \U0001F534 " + be)
        if wa: L.append("- \u23F3 " + wa)
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

def format_dual_output(data):
    import json as _json
    import os as _os
    import time as _time
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
        _lines.append(f"formatter: {_formatter_time}s")
        _lines.append(f"total_wall: {_tm.get('total_wall', '?')}s")
        timing_block = "\n\n<!-- TIMING_DEBUG\n" + "\n".join(_lines) + "\n-->"

    display_hint = """<!-- DISPLAY_HINT:
Build an HTML artifact dashboard with these sections in order:
1. HEADER: Company name, ticker, price, phase badge (green=EXPANDING, yellow=STABLE, red=CONTRACTING), momentum badge
2. METRIC CARDS ROW: 4 cards — Market Cap, P/E ratio, Revenue Growth %, FCF. Use the metrics and valuation objects.
3. VERDICT BOX: One-line verdict in bold. Bull case (green left border), Bear case (red left border), Wait for (amber left border).
4. SIGNALS TIMELINE: Recent signals with colored dots (green=positive, red=negative, yellow=neutral) and dates.
5. PEER TABLE: If peers array is non-empty, show comparison table with columns: Name, Price, Market Cap, P/E, Revenue Growth, Gross Margin.
6. CATALYSTS: Upcoming events with dates.
7. FOOTER: Sources count, generation time, DeepLook link.

Style: Clean, professional. White/light background cards with subtle shadows. Use system fonts. No excessive color.
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


if __name__ == "__main__":
    main()
