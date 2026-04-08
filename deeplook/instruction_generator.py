"""
deeplook/instruction_generator.py — Deterministic analysis guide from data conditions.
No LLM required. Tells the host LLM HOW to interpret the data, not WHAT to conclude.
Used when use_llm=False (the default pipeline).
"""


def generate_analysis_guide(structured_data: dict, entity_type: str) -> str:
    """Generate conditional analysis instructions from structured data."""
    instructions = []

    try:
        if entity_type == "public_equity":
            instructions.extend(_equity_instructions(structured_data))
        elif entity_type == "crypto":
            instructions.extend(_crypto_instructions(structured_data))
        elif entity_type in ("private_or_unlisted", "venture_capital", "foundation"):
            instructions.extend(_private_instructions(structured_data))
        elif entity_type == "defunct":
            instructions.extend(_defunct_instructions(structured_data))

        instructions.extend(_news_instructions(structured_data))
        instructions.extend(_peer_instructions(structured_data))
    except Exception:
        pass

    if not instructions:
        return ""

    lines = ["**Analysis Guide** (how to interpret this data):"]
    for i, inst in enumerate(instructions, 1):
        lines.append(f"{i}. {inst}")
    return "\n".join(lines)


# ── Entity-specific instruction builders ─────────────────────────────────────

def _equity_instructions(sd: dict) -> list[str]:
    tech = sd.get("technicals") or {}
    fin = sd.get("financials") or {}
    val = sd.get("valuation") or {}
    peers = sd.get("peers") or []

    rsi = tech.get("rsi_14")
    ma200_signal = tech.get("ma200_signal")
    ma200_dist = tech.get("ma200_distance_pct")
    pe_ratio = val.get("pe_ratio")
    peg_ratio = val.get("peg_ratio")
    gross_margin = fin.get("gross_margin")
    operating_margin = fin.get("operating_margin")
    analyst_target = val.get("analyst_target")
    price_current = (sd.get("price") or {}).get("current")
    vol_ratio = tech.get("volume_ratio")
    position_52w = tech.get("position_52w_pct")

    instructions = []

    # RSI
    if rsi is not None:
        if rsi < 30:
            instructions.append(
                f"RSI at {rsi:.1f} is technically oversold — historically a mean-reversion entry zone. "
                "Confirm with volume and news catalyst before acting."
            )
        elif rsi > 70:
            instructions.append(
                f"RSI at {rsi:.1f} is technically overbought — watch for pullback or continuation. "
                "High RSI alone is not a sell signal in strong uptrends."
            )

    # MA200
    if ma200_signal and ma200_dist is not None:
        if ma200_signal == "below" and ma200_dist < -5:
            instructions.append(
                f"Price is {abs(ma200_dist):.1f}% below the 200-day MA — critical support breach. "
                "Sustained break often signals structural weakness; recovery above MA200 would shift the intermediate trend."
            )
        elif ma200_signal == "above" and ma200_dist > 30:
            instructions.append(
                f"Price is {ma200_dist:.1f}% above MA200 — significantly extended. "
                "Mean-reversion risk increases at these levels; assess whether valuation justifies the premium."
            )
        elif ma200_signal == "above":
            instructions.append(
                "Price above MA200 — intermediate uptrend intact. Use MA200 as a dynamic support reference."
            )

    # PE vs peer median
    if pe_ratio is not None and peers:
        peer_pes = [p.get("pe") for p in peers if p.get("pe") is not None]
        if peer_pes:
            try:
                import statistics
                peer_median = statistics.median(peer_pes)
                if pe_ratio < peer_median * 0.8:
                    instructions.append(
                        f"P/E of {pe_ratio:.1f}x is significantly below peer median ({peer_median:.1f}x) — "
                        "either a value opportunity or the market is pricing in a structural issue. Investigate the discount."
                    )
                elif pe_ratio > peer_median * 1.3:
                    instructions.append(
                        f"P/E of {pe_ratio:.1f}x commands a {((pe_ratio/peer_median)-1)*100:.0f}% premium over "
                        f"peer median ({peer_median:.1f}x) — justified only if growth rate meaningfully exceeds peers."
                    )
            except Exception:
                pass

    # Margin quality
    if gross_margin is not None and operating_margin is not None:
        if gross_margin > 0.6 and operating_margin > 0.15:
            instructions.append(
                f"High-quality margin structure: {gross_margin*100:.0f}% gross, {operating_margin*100:.0f}% operating. "
                "Indicates pricing power and operating leverage."
            )
        elif gross_margin > 0.6 and operating_margin < 0.05:
            instructions.append(
                f"Gross margin is strong ({gross_margin*100:.0f}%) but operating margin is thin "
                f"({operating_margin*100:.0f}%) — investigate where margin is absorbed (R&D, SG&A, or competitive pressure)."
            )
        elif gross_margin < 0.3:
            instructions.append(
                f"Low gross margin ({gross_margin*100:.0f}%) indicates commodity-like pricing dynamics. "
                "Revenue growth alone may not translate to earnings expansion."
            )

    # Analyst target upside/downside
    if analyst_target is not None and price_current is not None:
        try:
            upside = (float(analyst_target) - float(price_current)) / float(price_current) * 100
            if upside > 20:
                instructions.append(
                    f"Analyst consensus target ({analyst_target:.2f}) implies {upside:.0f}% upside — significant. "
                    "Review analyst assumptions vs current guidance."
                )
            elif upside < -10:
                instructions.append(
                    f"Analyst consensus target ({analyst_target:.2f}) implies {abs(upside):.0f}% downside — "
                    "bearish consensus. Evaluate whether recent data warrants re-rating."
                )
        except Exception:
            pass

    # Volume ratio
    if vol_ratio is not None:
        try:
            vr = float(vol_ratio)
            if vr > 2.0:
                instructions.append(
                    f"Volume at {vr:.1f}x the 20-day average — unusual activity. "
                    "Correlate with price direction and news to assess institutional intent."
                )
            elif vr < 0.5:
                instructions.append(
                    f"Volume at {vr:.1f}x average — thin trading. "
                    "Price moves on low volume are less reliable; wait for volume confirmation."
                )
        except Exception:
            pass

    # 52W position
    if position_52w is not None:
        if position_52w > 85:
            instructions.append(
                "Trading near 52-week highs — strong momentum but watch for resistance and potential mean reversion."
            )
        elif position_52w < 15:
            instructions.append(
                "Trading near 52-week lows — assess whether this is a value entry or a falling knife. "
                "Check for fundamental deterioration before acting."
            )

    # PEG
    if peg_ratio is not None:
        try:
            peg = float(peg_ratio)
            if peg < 1.0:
                instructions.append(
                    f"PEG ratio of {peg:.2f} suggests growth is underpriced relative to earnings trajectory — "
                    "classic growth-at-reasonable-price signal."
                )
            elif peg > 3.0:
                instructions.append(
                    f"PEG ratio of {peg:.2f} indicates premium growth pricing — "
                    "requires sustained high growth to justify current valuation."
                )
        except Exception:
            pass

    return instructions


def _crypto_instructions(sd: dict) -> list[str]:
    cn = sd.get("crypto_numbers") or {}
    mcap_tvl = cn.get("mcap_tvl_ratio")
    price_change_30d = cn.get("price_change_30d")

    instructions = []

    if mcap_tvl is not None:
        try:
            ratio = float(mcap_tvl)
            if ratio < 1.0:
                instructions.append(
                    f"MCap/TVL of {ratio:.2f} means market cap is below locked protocol value — "
                    "speculative discount or early-stage undervaluation. Compare to similar protocols."
                )
            elif ratio > 10.0:
                instructions.append(
                    f"MCap/TVL of {ratio:.2f} is elevated — market cap significantly exceeds locked value. "
                    "Typical of narrative/momentum-driven assets; higher volatility risk."
                )
        except Exception:
            pass

    if price_change_30d is not None:
        try:
            change = float(price_change_30d)
            if change > 50:
                instructions.append(
                    f"30-day price change of +{change:.0f}% is parabolic — assess whether on-chain fundamentals "
                    "(TVL, fees, active addresses) support the move or if it's purely speculative."
                )
            elif change < -40:
                instructions.append(
                    f"30-day price change of {change:.0f}% is a severe drawdown — check for protocol-specific issues "
                    "(exploit, governance failure, liquidity crisis) vs broad market correction."
                )
        except Exception:
            pass

    return instructions


def _private_instructions(sd: dict) -> list[str]:
    funding = sd.get("funding") or {}
    instructions = [
        "Private company: no public market price or valuation data. "
        "Use funding rounds, revenue signals, and industry comparables to assess trajectory."
    ]
    if funding.get("key_investors"):
        instructions.append(
            "Notable investors are listed — consider their track record and portfolio strategy "
            "as a signal of company quality and sector conviction."
        )
    return instructions


def _defunct_instructions(sd: dict) -> list[str]:
    return [
        "Defunct entity: focus analysis on recovery proceedings, creditor recovery rate, and legal/regulatory timeline.",
        "Avoid extrapolating historical performance — assess only what recovery value remains.",
    ]


def _news_instructions(sd: dict) -> list[str]:
    news = sd.get("news_for_compression") or []
    if len(news) >= 5:
        return [
            "High news volume — prioritize events from the last 30 days. "
            "Look for a pattern: is the news predominantly operational, strategic, or regulatory?"
        ]
    if len(news) == 0:
        return [
            "No recent news found — treat this as a snapshot without recent narrative context. "
            "Focus on structural and financial fundamentals."
        ]
    return []


def _peer_instructions(sd: dict) -> list[str]:
    peers = sd.get("peers") or []
    if peers:
        return [
            "Peer table provided — compare revenue growth and margin profile first, then valuation multiples. "
            "Discount peer comparisons if business models differ materially."
        ]
    return []
