"""
judgment/synthesize.py — Four-stage LLM pipeline

  Call 1 (extract)   — Haiku:   extract facts + format display fields
  Call 2 (judge)     — Sonnet:  assess phase / momentum / risks
  Call 3 (act)       — Haiku:   produce actionable verdict
  Call 4 (validate)  — Sonnet:  sharpen verdict with stance + triggers

Model selection (Anthropic-first, falls back through OpenAI → Gemini → DeepSeek):
  Override per-role via env vars:
    DEEPLOOK_EXTRACT_MODEL    (default: claude-haiku-4-5-20251001)
    DEEPLOOK_JUDGE_MODEL      (default: claude-sonnet-4-5-20250929)
    DEEPLOOK_ACT_MODEL        (default: claude-sonnet-4-5-20250929)
    DEEPLOOK_VALIDATE_MODEL   (default: claude-sonnet-4-5-20250929)
"""

import os
import time
import re
import json
from datetime import date

from ..debug_log import log


# ── Model defaults ────────────────────────────────────────────────────────────

_ANTHROPIC_DEFAULTS = {
    "extract":  "claude-haiku-4-5-20251001",
    "judge":    "claude-sonnet-4-5-20250929",
    "act":      "claude-haiku-4-5-20251001",
    "validate": "claude-sonnet-4-5-20250929",
}
_OPENAI_DEFAULTS = {
    "extract":  "gpt-4o-mini",
    "judge":    "gpt-4o",
    "act":      "gpt-4o",
    "validate": "gpt-4o",
}
_GEMINI_DEFAULTS = {
    "extract":  "gemini-2.0-flash-lite",
    "judge":    "gemini-2.0-flash",
    "act":      "gemini-2.0-flash",
    "validate": "gemini-2.0-flash",
}
_DEEPSEEK_DEFAULTS = {
    "extract":  "deepseek-chat",
    "judge":    "deepseek-chat",
    "act":      "deepseek-chat",
    "validate": "deepseek-chat",
}

def _llm_timeout() -> float:
    """Timeout per LLM call in seconds. Override via DEEPLOOK_llm_timeout() env var.
    Default 60s — LLM generation typically takes 15-45s for large contexts.
    """
    return float(os.environ.get("DEEPLOOK_llm_timeout()", "60"))


def _model_for(role: str, provider: str) -> str:
    env_val = os.environ.get(f"DEEPLOOK_{role.upper()}_MODEL")
    if env_val:
        return env_val
    mapping = {
        "anthropic": _ANTHROPIC_DEFAULTS,
        "openai":    _OPENAI_DEFAULTS,
        "gemini":    _GEMINI_DEFAULTS,
        "deepseek":  _DEEPSEEK_DEFAULTS,
    }
    return mapping.get(provider, {}).get(role, "")


# ── JSON helpers ──────────────────────────────────────────────────────────────

def _clean_json_text(raw: str) -> tuple[str, str]:
    text = raw.strip()
    match = re.match(r"^```(?:json)?\s*\n?(.*?)```$", text, re.DOTALL)
    if match:
        text = match.group(1).strip()
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1 and last > first:
        text = text[first : last + 1]
    return text, raw


# ── LLM caller ────────────────────────────────────────────────────────────────

def get_llm_response(
    prompt: str, system_prompt: str = None, model_role: str = "extract",
    temperature: float = 0, max_tokens: int = 4096,
) -> tuple[str, str, int]:
    """Call LLM with model selected by role. Timeout: 10s per call.
    Returns (response_text, model_name, tokens_used).
    Priority: Anthropic → OpenAI → Gemini → DeepSeek.
    """
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        try:
            import anthropic as _anthropic
            model = _model_for(model_role, "anthropic")
            client = _anthropic.Anthropic(api_key=anthropic_key)
            kwargs = dict(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=_llm_timeout(),
                messages=[{"role": "user", "content": prompt}],
            )
            if system_prompt:
                kwargs["system"] = system_prompt
            response = client.messages.create(**kwargs)
            tokens = response.usage.input_tokens + response.usage.output_tokens
            return response.content[0].text, model, tokens
        except ImportError:
            pass
        except Exception as e:
            log("get_llm_response", "ANTHROPIC_ERROR", str(e))

    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        try:
            import openai as _openai
            model = _model_for(model_role, "openai")
            client = _openai.OpenAI(api_key=openai_key, timeout=_llm_timeout())
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            response = client.chat.completions.create(
                model=model, max_tokens=max_tokens, temperature=temperature, messages=messages
            )
            tokens = response.usage.total_tokens if response.usage else 0
            return response.choices[0].message.content, model, tokens
        except ImportError:
            pass
        except Exception as e:
            log("get_llm_response", "OPENAI_ERROR", str(e))

    gemini_key = os.environ.get("GEMINI_API_KEY")
    if gemini_key:
        try:
            from google import genai
            model = _model_for(model_role, "gemini")
            client = genai.Client(api_key=gemini_key)
            full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
            response = client.models.generate_content(model=model, contents=full_prompt)
            return response.text, model, 0
        except ImportError:
            pass
        except Exception as e:
            log("get_llm_response", "GEMINI_ERROR", str(e))

    deepseek_key = os.environ.get("DEEPSEEK_API_KEY")
    if deepseek_key:
        try:
            import openai as _openai
            model = _model_for(model_role, "deepseek")
            client = _openai.OpenAI(
                api_key=deepseek_key,
                base_url="https://api.deepseek.com",
                timeout=_llm_timeout(),
            )
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            response = client.chat.completions.create(
                model=model, max_tokens=max_tokens, temperature=temperature, messages=messages
            )
            tokens = response.usage.total_tokens if response.usage else 0
            return response.choices[0].message.content, model, tokens
        except ImportError:
            pass
        except Exception as e:
            log("get_llm_response", "DEEPSEEK_ERROR", str(e))

    raise RuntimeError(
        "No LLM API key available. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, "
        "GEMINI_API_KEY, or DEEPSEEK_API_KEY."
    )


# ── Prompts ───────────────────────────────────────────────────────────────────

EXTRACT_SYSTEM = """You are a financial data extraction agent. Your ONLY job is to extract and organize facts from raw company research data. Do NOT assign phases, predict outcomes, or recommend actions. Only report what the data says.

## Strict Rules
- Only state facts found in the provided sources. Never hallucinate.
- All numbers must be specific. Never write "significant growth", "tens of billions", "massive adoption", etc. Use exact figures from the data, or write "Not available".
- Every claim must cite a source. If a fact is not in the data, add it to missing_data[].
- Use official brand names (e.g. "Anthropic" not "anthropic").
- Dates: use exact dates from sources. Do not round or approximate.
- If a data source returned info about a DIFFERENT entity, discard that data and note the mismatch in missing_data[].

## Entity Type & Regime
Look at the Type field to determine which data to extract:
- REGIME A (public_equity): Extract stock price, market cap, P/E, revenue/earnings growth, analyst targets from yfinance.
- REGIME B (crypto/foundation): Extract token price, market cap, FDV, TVL, 24h volume, TPS from coingecko/defillama.
- REGIME C (private/private_or_unlisted/venture_capital): Extract funding stage, rounds, investors from rootdata/news.
- REGIME D (exchange): Extract spot+derivatives volume (separately), users, licenses.
- REGIME E (defunct): Extract collapse timeline. Set overview.stage = "DEFUNCT [year]". Set upcoming_catalysts = [].

## Entity-Specific Rules

### venture_capital
- overview.one_liner = investment thesis in one sentence
- market_data.key_metrics = ["AUM: $X", "Focus: Stage/Sector", "Portfolio: N companies", "Fund: name + year"]
- funding.section_label = "Fund Status"
- competitive_landscape.main_competitors = co-investors (ONLY from fetched data, not general knowledge)

### exchange
- market_data.key_metrics must include SEPARATE entries for Spot Volume and Derivatives Volume
- Format: ["Users: Xm", "Spot Volume (24h): $X", "Derivatives Volume (24h): $X", "Market Share: X%", "Licenses: N jurisdictions"]

### foundation / crypto
- Must report: token symbol, market cap, FDV. If TVL not found, write "TVL: Not available from sources".
- For blockchain foundations, look for the chain's metrics (TVL, TPS, txns), not the foundation entity's corporate data.
- OWN METRICS FIRST RULE: When analyzing any crypto entity, ALWAYS state the subject entity's OWN key metrics (TVL, fees, DEX volume, active addresses) BEFORE mentioning any competitor's specific numbers. If you cite a competitor metric (e.g. "Ethereum generated $263M in DeFi fees"), you MUST first state the subject entity's equivalent figure in the same section. If the subject's data is not available, write "[subject] equivalent: Not available from sources".
- LAYER 1 REQUIRED FIELDS: For major Layer 1 blockchains (Solana, Ethereum, Bitcoin, and similar L1s), MUST extract ALL of the following if they exist anywhere in the provided data: (1) TVL from DeFiLlama data, (2) ETF filing or approval status if mentioned in any source, (3) on-chain revenue or protocol fees for a recent period. Failing to extract these when the data is present is a critical omission — add them to missing_data[] if not found.

### defunct
- valuation = {"note": "Defunct company — ceased operations"}
- recent_signals = collapse events only
- upcoming_catalysts = []

## Funding / Ownership Field Rules
- public_equity: set total_raised=null, last_round=null. Populate key_investors with top 3-5 institutional holders by % ownership (e.g. ["Vanguard Group (7.2%)", "BlackRock (5.9%)", "Fidelity (3.1%)"]). Source from yfinance or SEC 13F data.
- private/private_or_unlisted/venture_capital: populate total_raised (latest known figure), last_round (most recent round with date + lead), key_investors (lead VCs or angels).
- crypto/foundation: populate key_investors with top token holders or foundation backers if known; total_raised = ICO/fundraise amount if available.
- defunct: set all funding fields to null.

## Peer Comparison
If "Peer Comparison" data is in the input, add a relative valuation sentence to competitive_landscape.comparison_note:
"Relative valuation: [target] trades at [X]x P/S vs [peer1] [Y]x and [peer2] [Z]x — [cheaper/pricier/in-line] given [revenue growth differential]."
Only add this if peer P/S or P/E data is actually present. Do NOT fabricate peer multiples.

## Technical Snapshot
If "Technical Snapshot" data is in the input:
- RSI > 70: add to recent_signals as negative sentiment signal "RSI(14) [value] — technically overbought"
- RSI < 30: add to recent_signals as positive sentiment signal "RSI(14) [value] — technically oversold"
- Price below both 50d MA and 200d MA: add bearish technical signal to recent_signals

## Output Format
Return valid JSON only. No markdown, no explanation.

{
  "company_name": "string",
  "entity_type": "string",
  "price": {
    "current": "number as string, e.g. '142.50', or null",
    "change_30d": "e.g. '+5.2%' or null",
    "market_cap": "e.g. '$1.2B' or null"
  },
  "financials": {
    "revenue_growth": "e.g. '+23% YoY' or null",
    "earnings_growth": "e.g. '+45% YoY' or null",
    "fcf": "e.g. '$2.1B TTM' or null",
    "margin": "e.g. 'gross 65%, operating 22%' or null"
  },
  "signals": [
    {"date": "YYYY-MM-DD", "event": "one sentence", "sentiment": "positive|negative|neutral", "source": "source name"}
  ],
  "upcoming_events": [
    {"event": "description", "date": "YYYY-MM-DD or quarter or 'TBD'", "source": "source name"}
  ],
  "competitors": ["name1", "name2"],
  "missing_data": ["list of facts not found in any source"],
  "overview": {
    "one_liner": "company description from data",
    "founded": "year",
    "hq_location": "city, country",
    "team_size": "number or range or null",
    "sector": "sector name",
    "stage": "e.g. Series B, Public, Seed, DEFUNCT 2023"
  },
  "funding": {
    "section_label": "Ownership | Funding | Fund Status",
    "total_raised": "total amount or null",
    "last_round": "most recent round details or null",
    "key_investors": ["investor1", "investor2"],
    "source": "source name"
  },
  "market_data": {
    "type": "crypto|public_equity|private",
    "price": <number or null>,
    "market_cap": "string",
    "30d_trend": "string",
    "key_metrics": ["Label: Value", "Label: Value"]
  },
  "valuation": {
    "REGIME_A_public_equity_only": {
      "pe_ratio": "from yfinance or 'Insufficient data'",
      "ev_to_ebitda": "from yfinance or 'Insufficient data'",
      "price_to_sales": "from yfinance or 'Insufficient data'",
      "analyst_target_price": "from yfinance or 'Insufficient data'"
    }
  },
  "competitive_landscape": {
    "main_competitors": ["name1", "name2"],
    "comparison_note": "one line per competitor: 'vs [Name]: [one sentence comparison]'",
    "peer_tickers": ["TICK1", "TICK2", "TICK3"]
  },
  "recent_signals": [
    {
      "date": "YYYY-MM-DD",
      "type": "event type",
      "summary": "what happened",
      "so_what": "[consequence] → Watch: [specific observable indicator]",
      "source_url": "url or empty string",
      "sentiment": "positive|negative|neutral",
      "alert_type": "critical|normal"
    }
  ],
  "upcoming_catalysts": [
    {"date": "YYYY-MM-DD or quarter", "event": "description", "why_it_matters": "one sentence"}
  ],
  "data_sources_used": ["sources with status ok"],
  "data_sources_failed": ["sources with status error or timeout"]
}

IMPORTANT for valuation field:
- public_equity: populate REGIME_A, omit REGIME_B
- crypto/foundation/exchange: populate REGIME_B (fields: fully_diluted_valuation, market_cap_to_tvl, protocol_revenue_annual, upcoming_unlocks), omit REGIME_A
- private/venture_capital: set valuation = {"note": "Private company — no public valuation data available"}
- defunct: set valuation = {"note": "Defunct company — ceased operations"}

IMPORTANT for upcoming_events:
upcoming_events has TWO categories:
a) Events with an explicit date (earnings date, product launch date, conference date) — use exact date.
b) Expected milestones without a confirmed date (funding round closing, regulatory decision, partnership implementation result, token unlock, governance vote outcome) — set date = "TBD".
Always populate both categories if evidence exists. Do NOT leave upcoming_events empty just because no exact date is available.

IMPORTANT for market_data.key_metrics:
- Array of strings, max 6 items, format "Label: Value"
- Specific numbers only. If unavailable, omit the entry entirely.

IMPORTANT for recent_signals:
- Generate 5 to 8 signals ordered by date descending (most recent first).
- alert_type = "critical" for: M&A activity, CEO/leadership changes, earnings miss >10%, major regulatory actions, significant lawsuits, going-concern warnings, or delisting risks. All other signals use alert_type = "normal".
- If earnings surprise data is available and magnitude exceeds 20% (positive or negative), MUST extract both actual EPS and consensus estimate EPS. Format the signal as: "EPS $X.XX vs estimate $Y.YY (±Z% surprise)". Do not report only one figure.

IMPORTANT for competitive_landscape.peer_tickers:
- Add up to 3 ticker symbols of the most comparable publicly-traded competitors.
- Use only real, active tickers. For private/crypto companies, use the closest public proxy (e.g. COIN for a crypto exchange, ABNB for a private travel startup).
- If no comparable public peers exist, set peer_tickers = [].
- For Layer 1 blockchains: peer_tickers MUST include ETH plus at least one other comparable L1 (e.g., AVAX, SUI, NEAR, or the most relevant competitor from the data). Never leave peer_tickers empty for L1 crypto entities.
- For DeFi protocols: peer_tickers MUST include at least 2 comparable protocol proxies (e.g., AAVE, UNI, CRV, COMP). Never leave peer_tickers empty for DeFi entities.

IMPORTANT for business segments:
- If the fetched data contains segment-level breakdowns (e.g. from SEC filings, earnings call transcripts, news), extract 3-6 segments as an array:
  "segments": [{"name": "Segment Name", "metric": "+X% YoY or $XB", "context": "brief note"}]
- If no segment data is available, set "segments": [].
- Add the segments field to the root-level JSON output.

REMINDER: Only state facts found in sources. Never hallucinate. Return valid JSON only.
"""

JUDGE_SYSTEM = """You are a company phase assessment agent. You receive structured facts about a company. Assign phase and momentum ONLY based on the facts provided. Do not use general knowledge to fill gaps.

## Hard Rules
1. Phase must reflect the company's CURRENT operating status, not historical events alone.
2. positive revenue_growth AND positive operating margin = CANNOT assign DISTRESS.
3. bull_case and bear_case MUST each contain at least one specific number that appears in the facts JSON.
4. DISTRESS requires 2+ confirmed signals: bankruptcy/Chapter 11/defunct confirmed in Wikipedia OR layoffs >20% workforce OR SEC/DOJ enforcement OR delisting OR ceased operations confirmed by multiple sources. A single negative article is NOT sufficient.
5. If len(missing_data) > 50% of total fact fields, add "low_confidence": true.
6. You MUST reference every important quantitative figure from the facts in bull_case, bear_case, or risks. Do not omit any revenue, ARR, deal size, analyst target price, TVL, mcap/TVL ratio, or other numeric data present in the facts. If facts contain 10 numbers, your output must cite at least 8 of them across bull_case + bear_case + risks combined.
7. bear_case MUST include at least one macro or geopolitical risk factor relevant to this company. Examples: tariffs, export controls, interest rate policy, commodity price risk, regulatory changes, geopolitical tensions. If no obvious macro risk applies, briefly state why the company is relatively insulated (e.g. "domestic-only revenue base insulates from tariff risk").
8. If PEG ratio < 1.0 but your phase assessment suggests caution or deceleration, you MUST note this discrepancy in phase_reasoning. Explain why low PEG may be misleading (e.g., forward earnings estimates may be overly optimistic, or growth is expected to decelerate beyond the forecast period).

## Phase Definitions
- EXPANDING: Active growth — revenue up, new markets, recent fundraising, strong positive catalysts
- STABLE: Operating normally, no dramatic changes in either direction (default for mature companies)
- PIVOTING: Business model change in progress, documented in sources
- DISTRESS: 2+ confirmed collapse signals (see Rule 4)
- UNKNOWN: Data sources comprehensively failed — use sparingly

## Phase Assignment by Entity Type

### Public Equity (REGIME A — yfinance data present)
Determine market cap tier first:
- MEGA CAP (>$500B): EXPANDING if revenue_growth > 15% AND at least one of: earnings_growth > 30%, OR entry into major new market, OR recommendation = strong_buy + analyst_target > current_price * 1.3
- LARGE CAP ($50B–$500B): EXPANDING if revenue_growth > 20% OR earnings_growth > 30%, with news showing growth catalysts
- MID/SMALL CAP (<$50B): EXPANDING if revenue_growth > 15% OR significant positive catalyst in news
- STABLE = default for mature public companies with steady positive metrics and no major shifts
- Exception: revenue_growth > 50% = EXPANDING at any scale

### Crypto (REGIME B)
EXPANDING if 2+ of: recent funding >$10M / TVL or mcap growing / major protocol upgrade / new product/chain launch
STABLE: established project with steady operations, no dramatic growth or decline
DISTRESS only: rug pull / exploit losing >50% funds / regulatory shutdown / token death spiral >90% drop

### Private (REGIME C)
Use funding stage as primary proxy:
- Seed/Angel/Pre-seed → EXPANDING
- Series A / Series B → EXPANDING
- Series C+ / Late Stage / Pre-IPO → EXPANDING + ACCELERATING
- No funding >18 months AND no growth signals in news → STABLE
- No funding history AND no growth signals → STABLE (default)

## Momentum Rules
- ACCELERATING: revenue_growth > 30% AND positive earnings surprise AND 3+ positive signals in 90 days. OR for private/crypto: funding + partnerships + user growth all within 90 days.
- STEADY: consistent metrics, no dramatic changes
- DECELERATING: growth rate declining QoQ, negative earnings revisions, cooling news sentiment
- CRISIS: active collapse, exploit aftermath, regulatory enforcement

## Output (valid JSON only):
{
  "phase": "EXPANDING|STABLE|PIVOTING|DISTRESS|UNKNOWN",
  "phase_reasoning": "cite specific facts: e.g. revenue_growth +23% YoY + FCF $2.1B + 3 positive signals in 90 days",
  "momentum": "ACCELERATING|STEADY|DECELERATING|CRISIS",
  "bull_case": "ONE sentence, max 25 words, must contain a specific number from the facts",
  "bear_case": "ONE sentence, max 25 words, must contain a specific number or named risk from the facts",
  "risks": [
    {"risk": "description", "severity": "high|medium|low", "evidence": "which fact supports this"}
  ],
  "low_confidence": false
}

REMINDER: Phase must reflect CURRENT status from provided facts only. Return valid JSON only.
"""

ACT_SYSTEM = """You are an action recommendation agent. You receive company facts and a judgment. Produce a concise, evidence-based action recommendation.

## Hard Rules
1. wait_for uses two fallback tiers — work through them in order:
   TIER A (preferred): If facts.upcoming_events contains any entry (including date="TBD"), use the most decision-relevant one. Set wait_for_source to that event's text.
   TIER B (fallback): If upcoming_events is empty, scan judgment.risks and facts.signals for the single most concrete next event that would change your assessment (e.g. "sPENDLE adoption rate after governance migration", "Ch.11 emergence announcement", "next fundraise round valuation"). Write it as a specific observable event. Set wait_for_source = null.
   ONLY if both tiers yield nothing meaningful, write exactly: "No confirmed catalyst in current sources"
2. Do NOT predict or infer dates beyond what sources state. For Tier B events, describe the event, not a guessed date.
3. wait_for_source: exact text of the upcoming_events entry used (Tier A), or null (Tier B or no catalyst).
4. confidence:
   - "high" if missing_data has < 3 items AND judgment.low_confidence is false
   - "medium" if missing_data has 3–6 items OR judgment.low_confidence is true
   - "low" if missing_data has > 6 items
5. one_line: max 15 words, direct judgment, no filler
6. action rules:
   - EXPANDING + ACCELERATING → "research_deeper"
   - EXPANDING + STEADY → "research_deeper"
   - STABLE + any → "monitor"
   - PIVOTING + any → "wait_for_catalyst"
   - DISTRESS + any → "avoid"
   - UNKNOWN + any → "research_deeper"
7. bull_case and bear_case MUST include every important quantitative figure from judgment.bull_case and judgment.bear_case. Additionally, if judgment.risks or facts contain analyst target prices, mcap/TVL ratios, revenue growth rates, ARR figures, or deal sizes that were not in judgment.bull_case/bear_case, incorporate the most investment-relevant ones. Do not replace specific numbers with vague qualitative descriptions.

## Output (valid JSON only):
{
  "one_line": "max 15 words, direct assessment, no filler",
  "bull_case": "(copy exactly from judgment.bull_case)",
  "bear_case": "(copy exactly from judgment.bear_case)",
  "wait_for": "ONE specific event with timeline from upcoming_events, or 'No confirmed catalyst in current sources'",
  "wait_for_source": "exact text of the upcoming_events entry used, or null",
  "action": "research_deeper|monitor|avoid|wait_for_catalyst",
  "confidence": "high|medium|low",
  "guidance": {
    "period": "FY2026 or Q1 2026",
    "items": [
      {"metric": "Revenue", "guidance": "$34-35B", "sentiment": "in-line"},
      {"metric": "EPS", "guidance": "Mid-single-digit decline", "sentiment": "weak"}
    ]
  },
  "segments": [
    {"name": "Segment Name", "metric": "+X% YoY or $XB revenue", "context": "brief note"}
  ]
}

IMPORTANT for guidance: Extract forward guidance from earnings calls, press releases, or investor presentations found in the fetched data. Use sentiment "strong" (beats/raises), "in-line" (meets), or "weak" (misses/cuts). If no forward guidance is available in the sources, set guidance = null.

IMPORTANT for segments: Extract 3-6 business segment metrics from SEC filings, earnings transcripts, or news if available. If no segment data exists in the sources, set segments = [].

IMPORTANT for defense/government contractors: Identify contract type when possible. IDIQ (Indefinite Delivery/Indefinite Quantity) contract ceilings represent maximum potential value, NOT guaranteed revenue. State this distinction clearly in one_line or bear_case when relevant. Do not treat a contract ceiling as confirmed backlog.

IMPORTANT for private/pre-IPO companies: entry and exit criteria must include a liquidity caveat. Note that shares cannot be freely traded on public markets. Specify whether criteria apply to secondary market transactions, future IPO pricing, or are hypothetical benchmarks for monitoring only.

REMINDER: Every claim must cite specific data. No generic statements. Return valid JSON only.
"""


VALIDATE_SYSTEM = """You are a verdict validation and refinement agent. You receive a preliminary verdict, structured facts, and peer data. Produce a sharper, opinionated verdict with explicit stance and actionable triggers.

## Hard Rules
1. Take a clear stance — bullish, bearish, or neutral. Do not hedge.
2. entry_trigger: ONE specific, observable condition that would justify entering a position (e.g. "Revenue growth re-accelerates above 20% YoY in next earnings", "Stock pulls back below $X support"). Must be concrete.
3. risk_trigger: ONE specific, observable condition that would force exit or avoidance (e.g. "Operating margin falls below 10%", "Key customer contract lost"). Must be concrete.
4. peer_context: ONE sentence comparing valuation to peers using real numbers from peer data. If no peer data available, set to null.
5. Rewrite one_line to have a clear directional stance. Max 15 words. No filler.
6. If bull_case narrative contradicts signals (e.g. bull says accelerating but signals show deceleration), note the gap in analysis_context.narrative_gap and side with the data.
7. FORBIDDEN phrases — never output: "demands scrutiny", "mixed signals", "investors should monitor", "presents both opportunities and challenges", "warrants attention", "remains to be seen", "complex landscape".
8. analysis_context: compute from provided data. Set any field to null if the data to compute it is absent.
9. Before finalizing, verify all entry/exit conditions are logically and mathematically consistent. Specific checks: (a) If entry_price is below a stated support level (e.g. MA), the support is broken — do not say "while maintaining support above [higher price]". (b) If a per-unit cost is derived by division, verify numerator and denominator are from the same business segment. (c) If an event appears in signals as completed, it cannot also appear as an upcoming catalyst.
10. Check signal timeline for contradictions: if an event is described as completed/launched in the timeline (recent_signals), it MUST NOT also appear as an upcoming catalyst (upcoming_catalysts). Flag and fix any such contradiction before finalizing output.

## Output (valid JSON only):
{
  "one_line": "max 15 words, directional stance, no filler",
  "bull_case": "copy from input or sharpen with specific numbers from facts",
  "bear_case": "copy from input or sharpen with specific numbers from facts",
  "wait_for": "copy exactly from preliminary_verdict.wait_for",
  "stance": "bullish|bearish|neutral",
  "entry_trigger": "ONE specific observable condition to enter",
  "risk_trigger": "ONE specific observable condition to exit or avoid",
  "peer_context": "ONE sentence vs peers with real numbers, or null",
  "analysis_context": {
    "peer_relative_pe": "e.g. 'Trades at 45x vs peer avg 32x — 41% premium' or null",
    "peer_relative_ps": "e.g. 'P/S 18x vs peers 12x avg' or null",
    "growth_vs_valuation": "e.g. 'Revenue +28% YoY but PE premium suggests market already pricing in growth' or null",
    "technical_summary": "e.g. 'Below 50d MA, RSI 42 — not oversold yet' or null",
    "narrative_gap": "e.g. 'Bull cites AI growth but 3 of last 5 signals show margin compression' or null"
  }
}

REMINDER: Take a clear stance. Do not hedge. Check all numbers for mathematical consistency. Return valid JSON only.
"""


# ── Query generation (Round 1.5) ──────────────────────────────────────────────

QUERY_GEN_SYSTEM = """You are a research assistant. Given structured data about a company from initial sources (financial data, SEC filings, Wikipedia), generate targeted search queries to find the most relevant recent news and video content.

Output valid JSON only:
{
  "youtube_queries": ["specific query 1", "specific query 2"],
  "news_queries": ["specific query 1", "specific query 2"]
}

Rules:
- Use specific names, dates, events found in the data — NOT generic queries
- BAD: "NVIDIA latest news" | GOOD: "NVIDIA earnings miss analyst reaction February 2026"
- BAD: "Apple YouTube" | GOOD: "Tim Cook Apple Q1 FY26 earnings call revenue guidance"
- youtube_queries: 2-3 queries covering (1) latest earnings call or quarterly results, (2) recent product launches/demos/keynotes, (3) CEO/founder interviews or analyst day presentations. Use official company name (not ticker). Prioritize content from the last 6 months.
- news_queries: 2 queries targeting reactions to specific events found in Round 1 data (e.g. earnings miss → analyst reaction, product launch → market impact)
- If no specific events found, use company name + most recent quarter/year
"""


COMPRESS_SYSTEM = """You are a data compression and lightweight analysis agent. You receive pre-structured company research data. Your job is to (1) compress text into concise summaries, (2) generate analysis hooks, and (3) produce a lightweight verdict based strictly on the provided data.

## Your 4 tasks:

### Task 1 — Company Overview
Compress the website text and Wikipedia text into 3-5 sentences covering:
- What the company does (one sentence)
- Key products/services
- Recent strategic direction (if evident from text)
Use only facts from the provided text. English only.

### Task 2 — News Summary
For each news article provided, produce ONE bullet point (max 2 sentences):
- What happened + when + quantitative impact if available
- Drop articles that are duplicates or pure opinion with no data
Output as array, ordered by date descending. Max 8 items.

### Task 3 — Analysis Hooks
Generate 3-5 analysis hooks — these are QUESTIONS or OBSERVATIONS for a senior analyst to investigate, NOT conclusions. Format: each hook identifies a data point and frames it as something worth examining.

Good hooks:
- "Revenue grew 23% but operating margin compressed from 28% to 22% — worth examining whether growth is profitable"
- "3 of top 5 institutional holders reduced positions in Q4 — may signal valuation concern at current levels"
- "RSI at 72 while price is 15% above 200d MA — technically extended, watch for mean reversion"

Bad hooks (too conclusory):
- "NVIDIA is a strong buy based on AI demand"
- "The stock is overvalued"
- "Investors should accumulate on dips"

### Task 4 — Lightweight Verdict
Produce a short, evidence-based verdict from the provided data. This helps downstream LLMs (especially smaller models) that may not synthesize well from raw data alone. Stronger models will form their own judgment and may override this.

Rules for verdict:
- one_line: Maximum 15 words. Direct assessment with a number from the data.
- stance: bullish / bearish / neutral — pick one, do not hedge
- bull_case: Maximum 35 words. ONE sentence.
- bear_case: Maximum 35 words. ONE sentence.
- wait_for: ONE specific upcoming event or catalyst from the data. If none exists, write "No confirmed catalyst in current data"
- action: research_deeper / monitor / avoid / wait_for_catalyst
Your output will be programmatically validated. Any field exceeding the word limit will be truncated.
  - revenue_growth > 20% AND positive signals → research_deeper
  - steady metrics, no dramatic change → monitor
  - major negative signals or data gaps → avoid or wait_for_catalyst

IMPORTANT: The verdict must be derived ONLY from the structured data provided (financials, technicals, valuation, news, peers). Do not use general knowledge. If data is insufficient for a confident verdict, set confidence = "low" and explain in one_line.

## Output (valid JSON only):
{
  "overview": "3-5 sentence company overview",
  "news_bullets": [
    {"date": "YYYY-MM-DD", "summary": "1-2 sentence summary", "sentiment": "positive|negative|neutral", "source": "source name"}
  ],
  "analysis_hooks": [
    "hook 1 — observation + question",
    "hook 2 — observation + question",
    "hook 3 — observation + question"
  ],
  "verdict": {
    "one_line": "max 15 words",
    "stance": "bullish|bearish|neutral",
    "bull_case": "max 35 words, ONE sentence",
    "bear_case": "max 35 words, ONE sentence",
    "wait_for": "ONE specific upcoming event, or 'No confirmed catalyst in current data'",
    "action": "research_deeper|monitor|avoid|wait_for_catalyst",
    "confidence": "high|medium|low"
  }
}

RULES:
- English only, even if source text is in other languages
- Numbers must be exact (from source data). Never write "significant" or "substantial".
- If website/wiki text is empty or irrelevant, write overview as "No company description available from sources."
- Do not add information not present in the provided data.
- Return valid JSON only. No markdown, no explanation."""


def _enforce_word_limits(verdict: dict) -> dict:
    """Truncate verdict fields that exceed word limits, cutting at the nearest punctuation."""
    limits = {
        "one_line": 20,
        "bull_case": 40,
        "bear_case": 40,
        "wait_for": 30,
    }
    for field, max_words in limits.items():
        text = verdict.get(field, "")
        if not text:
            continue
        # wait_for: strip everything after the first semicolon
        if field == "wait_for" and ";" in text:
            text = text[:text.index(";")].strip()
            verdict[field] = text
        words = text.split()
        if len(words) <= max_words:
            continue
        truncated = " ".join(words[:max_words])
        # walk back to nearest sentence-ending punctuation
        for i in range(len(truncated) - 1, -1, -1):
            if truncated[i] in ".;,":
                truncated = truncated[:i + 1]
                break
        else:
            truncated = truncated + "."
        verdict[field] = truncated
    return verdict


async def compress_context(structured_data: dict) -> dict:
    """Single Haiku call: compress text + generate analysis hooks + lightweight verdict.
    Input: output of prepare_structured_data()
    Output: {overview, news_bullets, analysis_hooks, verdict, _model, _tokens}
    """
    import asyncio as _asyncio

    company_name = structured_data.get("company_name", "")
    entity_type = structured_data.get("entity_type", "")

    user_prompt = (
        f"Company: {company_name}\nType: {entity_type}\n\n"
        f"=== Website Text ===\n"
        f"{structured_data.get('text_for_compression', {}).get('website', '') or 'No website text available.'}\n\n"
        f"=== Wikipedia Text ===\n"
        f"{structured_data.get('text_for_compression', {}).get('wikipedia', '') or 'No Wikipedia text available.'}\n\n"
        f"=== News Articles (pre-filtered, priority > 0.65) ===\n"
        f"{json.dumps(structured_data.get('news_for_compression', []), indent=2)}\n\n"
        f"=== Key Financials (for context in hooks) ===\n"
        f"{json.dumps(structured_data.get('financials', {}), indent=2)}\n\n"
        f"=== Technicals (for context in hooks) ===\n"
        f"{json.dumps(structured_data.get('technicals', {}), indent=2)}\n\n"
        f"=== Valuation (for context in hooks) ===\n"
        f"{json.dumps(structured_data.get('valuation', {}), indent=2)}\n\n"
        f"Respond with valid JSON only."
    )

    try:
        result, model, tokens = await _asyncio.to_thread(
            _call_llm_with_retry,
            user_prompt, COMPRESS_SYSTEM, "extract", "compress_context",
            0.2, 2048,
        )
        log("compress_context", "OK", f"model={model}, tokens={tokens}")
        return {
            "overview": result.get("overview", ""),
            "news_bullets": result.get("news_bullets", []),
            "analysis_hooks": result.get("analysis_hooks", []),
            "verdict": _enforce_word_limits(result.get("verdict", {})),
            "_model": model,
            "_tokens": tokens,
        }
    except Exception as e:
        log("compress_context", "FAIL", str(e))
        print(f"[compress_context] failed: {e}")
        return {
            "overview": "No company description available from sources.",
            "news_bullets": [],
            "analysis_hooks": [],
            "verdict": {
                "one_line": "Compression step failed — insufficient data",
                "stance": "neutral",
                "bull_case": "",
                "bear_case": "",
                "wait_for": "No confirmed catalyst in current data",
                "action": "research_deeper",
                "confidence": "low",
            },
            "_model": "failed",
            "_tokens": 0,
        }


def generate_search_queries(company_name: str, entity_type: str, round1_data: dict) -> dict:
    """Haiku call between Round 1 and Round 2: generates context-aware search queries."""
    compact = {}
    for k, v in round1_data.items():
        s = json.dumps(v, default=str)
        compact[k] = s[:1500] + "..." if len(s) > 1500 else s

    prompt = (
        f"Company: {company_name}\nType: {entity_type}\n\n"
        f"Round 1 data:\n{json.dumps(compact, indent=2)}\n\n"
        f"Generate search queries. Respond with valid JSON only."
    )
    try:
        result, _, tokens = _call_llm_with_retry(
            prompt, QUERY_GEN_SYSTEM, "extract", "generate_search_queries", temperature=0.3, max_tokens=1024
        )
        log("generate_search_queries", "OK", f"tokens={tokens}")
        return result if isinstance(result, dict) else {"youtube_queries": [company_name], "news_queries": [f"{company_name} latest news"]}
    except Exception as e:
        log("generate_search_queries", "FAIL", str(e))
        return {"youtube_queries": [company_name], "news_queries": [f"{company_name} latest news"]}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _call_llm_with_retry(
    prompt: str, system_prompt: str, model_role: str, call_name: str,
    temperature: float = 0, max_tokens: int = 4096,
) -> tuple[dict, str, int]:
    """Call LLM, parse JSON, retry once on failure. Returns (result_dict, model, tokens)."""
    raw_text, model, tokens = get_llm_response(prompt, system_prompt, model_role, temperature, max_tokens)
    log(call_name, "API_RESPONSE", f"model={model}, tokens={tokens}, length={len(raw_text)}")

    text, _ = _clean_json_text(raw_text)
    try:
        return json.loads(text), model, tokens
    except json.JSONDecodeError as e:
        log(call_name, "JSON_PARSE_FAIL", raw_text[:200])
        print(f"[{call_name}] JSON parse failed: {e} — retrying")
        retry_prompt = prompt + "\n\nYour previous response was not valid JSON. Respond with valid JSON only, no markdown."
        raw_text2, model2, tokens2 = get_llm_response(retry_prompt, system_prompt, model_role, temperature, max_tokens)
        text2, _ = _clean_json_text(raw_text2)
        try:
            return json.loads(text2), model2, tokens + tokens2
        except json.JSONDecodeError as e2:
            raise ValueError(f"{call_name} JSON parse failed after retry: {e2}") from e2


def _validate_case_numbers(judgment: dict, facts: dict) -> None:
    """Warn if bull/bear case contains numbers not found in facts (simple string check)."""
    facts_str = json.dumps(facts)
    for field in ("bull_case", "bear_case"):
        text = judgment.get(field, "")
        numbers = re.findall(r'\$[\d,.]+[BMKTbmkt%]?|\d+(?:\.\d+)?%|\b\d{4}\b', text)
        for num in numbers:
            num_clean = num.replace("$", "").replace(",", "")
            if num_clean not in facts_str:
                log("validate", "NUMBER_NOT_IN_FACTS", f"{field}: '{num}' not found in facts")


def _validate_wait_for_source(verdict: dict, facts: dict) -> None:
    """Only reset wait_for when it is an empty string (LLM left it blank)."""
    if verdict.get("wait_for", "") == "":
        verdict["wait_for"] = "No confirmed catalyst in current sources"
        verdict["wait_for_source"] = None


def _assemble(
    company_name: str,
    facts: dict,
    judgment: dict,
    verdict: dict,
    total_time: float,
    api_calls: int,
    models_used: list[str],
    total_tokens: int,
) -> dict:
    """Combine three-call outputs into formatter-compatible JSON."""
    ai_judgment = {
        "company_phase": judgment.get("phase", "UNKNOWN"),
        "momentum": judgment.get("momentum", "STEADY"),
        "risk_signals": [
            r.get("risk", str(r)) if isinstance(r, dict) else str(r)
            for r in judgment.get("risks", [])
        ],
        "forward_looking": [
            e["event"] for e in facts.get("upcoming_events", []) if e.get("event")
        ],
        "verdict": {
            "one_line": verdict.get("one_line", ""),
            "bull_case": verdict.get("bull_case", ""),
            "bear_case": verdict.get("bear_case", ""),
            "wait_for": verdict.get("wait_for", ""),
            "stance": verdict.get("stance"),
            "entry_trigger": verdict.get("entry_trigger"),
            "risk_trigger": verdict.get("risk_trigger"),
            "peer_context": verdict.get("peer_context"),
        },
        "analysis_context": verdict.get("analysis_context"),
        "guidance": verdict.get("guidance"),
        "segments": verdict.get("segments") or facts.get("segments") or [],
    }
    return {
        "company_name": facts.get("company_name", company_name),
        "research_date": date.today().isoformat(),
        "data_sources_used": facts.get("data_sources_used", []),
        "data_sources_failed": facts.get("data_sources_failed", []),
        "overview": facts.get("overview", {}),
        "funding": facts.get("funding", {}),
        "market_data": facts.get("market_data", {}),
        "valuation": facts.get("valuation", {}),
        "competitive_landscape": facts.get("competitive_landscape", {}),
        "recent_signals": facts.get("recent_signals", []),
        "upcoming_catalysts": facts.get("upcoming_catalysts", []),
        "ai_judgment": ai_judgment,
        "metadata": {
            "total_time_seconds": round(total_time, 2),
            "total_api_calls": api_calls,
            "llm_model_used": " → ".join(models_used),
            "llm_tokens_used": total_tokens,
        },
    }


def _low_data_report(
    company_name: str,
    facts: dict,
    total_time: float,
    api_calls: int,
    models_used: list[str],
    total_tokens: int,
) -> dict:
    """Minimal report returned when data coverage is insufficient for judgment."""
    return {
        "company_name": facts.get("company_name", company_name),
        "research_date": date.today().isoformat(),
        "data_sources_used": facts.get("data_sources_used", []),
        "data_sources_failed": facts.get("data_sources_failed", []),
        "overview": facts.get("overview", {}),
        "funding": {},
        "market_data": facts.get("market_data", {}),
        "valuation": {"note": "Insufficient data coverage — most sources returned no data"},
        "competitive_landscape": {},
        "recent_signals": [],
        "upcoming_catalysts": [],
        "ai_judgment": {
            "company_phase": "UNKNOWN",
            "momentum": "STEADY",
            "risk_signals": ["Insufficient data coverage — most sources returned no data"],
            "forward_looking": [],
            "verdict": {
                "one_line": "Insufficient data — manual research required",
                "bull_case": "",
                "bear_case": "",
                "wait_for": "No confirmed catalyst in current sources",
            },
        },
        "metadata": {
            "total_time_seconds": round(total_time, 2),
            "total_api_calls": api_calls,
            "llm_model_used": " → ".join(models_used),
            "llm_tokens_used": total_tokens,
            "low_data_coverage": True,
        },
    }


# ── Pipeline steps ────────────────────────────────────────────────────────────

def extract_facts(
    company_name: str, entity_type: str, fetcher_results: dict
) -> tuple[dict, str, int]:
    """Call 1 (Haiku): Extract structured facts + display fields from raw fetcher data."""
    truncated = {}
    for k, v in fetcher_results.items():
        s = json.dumps(v, indent=2, default=str)
        if len(s) > 4000:
            s = s[:4000] + "... [TRUNCATED]"
        truncated[k] = s

    sections = [
        f"Company: {company_name}",
        f"Type: {entity_type}",
        f"Research Date: {date.today().isoformat()}",
        "",
    ]
    for source_name, data_str in truncated.items():
        label = source_name.replace("_", " ").title()
        sections.append(f"=== {label} Data ===")
        sections.append(data_str)
        sections.append("")

    _SKIP_INSTRUCTIONS = {
        "public_equity": (
            "Skip these fields entirely (do not include in output):\n"
            "- funding.total_raised, funding.last_round (not applicable for public companies)\n"
            "- valuation REGIME_B fields: fully_diluted_valuation, market_cap_to_tvl, protocol_revenue_annual, upcoming_unlocks\n"
            "- overview.team_size (rarely available, skip)\n"
            "- valuation.ev_to_ebitda (skip if not available, do not output \"Insufficient data\")\n"
            "Keep output focused on: financials, valuation REGIME_A, signals, peers."
        ),
        "crypto": (
            "Skip these fields entirely (do not include in output):\n"
            "- financials.earnings_growth, financials.fcf, financials.margin (not applicable for crypto)\n"
            "- funding.last_round (omit unless explicitly known)\n"
            "- valuation REGIME_A fields: pe_ratio, ev_to_ebitda, price_to_sales, analyst_target_price\n"
            "- segments (not applicable for crypto)\n"
            "Keep output focused on: market_data.key_metrics (TVL, DAU, fees), signals, competitive_landscape."
        ),
        "private": (
            "Skip these fields entirely (do not include in output):\n"
            "- financials.earnings_growth, financials.fcf (not publicly available)\n"
            "- valuation REGIME_A fields: pe_ratio, ev_to_ebitda, price_to_sales, analyst_target_price\n"
            "- valuation REGIME_B fields: fully_diluted_valuation, market_cap_to_tvl, protocol_revenue_annual, upcoming_unlocks\n"
            "- price.change_30d (no public price)\n"
            "- segments (rarely available)\n"
            "Keep output focused on: funding, overview, signals, competitive_landscape, upcoming_catalysts."
        ),
        "private_or_unlisted": (
            "Skip these fields entirely (do not include in output):\n"
            "- financials.earnings_growth, financials.fcf (not publicly available)\n"
            "- valuation REGIME_A fields: pe_ratio, ev_to_ebitda, price_to_sales, analyst_target_price\n"
            "- valuation REGIME_B fields: fully_diluted_valuation, market_cap_to_tvl, protocol_revenue_annual, upcoming_unlocks\n"
            "- price.change_30d (no public price)\n"
            "- segments (rarely available)\n"
            "Keep output focused on: funding, overview, signals, competitive_landscape, upcoming_catalysts."
        ),
    }
    skip_note = _SKIP_INSTRUCTIONS.get(entity_type, "")

    user_message = "\n".join(sections)
    skip_block = f"\n\n{skip_note}\n" if skip_note else ""
    prompt = user_message + skip_block + "\nRespond with valid JSON only. No markdown, no explanation."
    log("extract_facts", "START", f"input_chars={len(user_message)}")

    result, model, tokens = _call_llm_with_retry(prompt, EXTRACT_SYSTEM, "extract", "extract_facts", temperature=0.2, max_tokens=4096)
    print(f"[extract_facts] model={model}, tokens={tokens}")
    return result, model, tokens


def judge(facts: dict) -> tuple[dict, str, int]:
    """Call 2 (Sonnet): Assess phase/momentum/risks from structured facts."""
    facts_for_judge = {
        "company_name": facts.get("company_name"),
        "entity_type": facts.get("entity_type"),
        "price": facts.get("price"),
        "financials": facts.get("financials"),
        "signals": facts.get("signals"),
        "upcoming_events": facts.get("upcoming_events"),
        "competitors": facts.get("competitors"),
        "missing_data": facts.get("missing_data"),
    }
    prompt = json.dumps(facts_for_judge, indent=2) + "\n\nRespond with valid JSON only."
    log("judge", "START", f"input_chars={len(prompt)}")

    result, model, tokens = _call_llm_with_retry(prompt, JUDGE_SYSTEM, "judge", "judge", temperature=0.4, max_tokens=2048)
    print(f"[judge] model={model}, tokens={tokens}")
    return result, model, tokens


def recommend_action(facts: dict, judgment: dict) -> tuple[dict, str, int]:
    """Call 3 (Sonnet): Produce actionable verdict from facts + judgment."""
    input_data = {
        "facts": {
            "company_name": facts.get("company_name"),
            "entity_type": facts.get("entity_type"),
            "price": facts.get("price"),
            "financials": facts.get("financials"),
            "signals": facts.get("signals"),
            "upcoming_events": facts.get("upcoming_events"),
            "missing_data": facts.get("missing_data"),
        },
        "judgment": judgment,
    }
    prompt = json.dumps(input_data, indent=2) + "\n\nRespond with valid JSON only."
    log("recommend_action", "START", f"input_chars={len(prompt)}")

    result, model, tokens = _call_llm_with_retry(prompt, ACT_SYSTEM, "act", "recommend_action", temperature=0.1, max_tokens=3072)
    print(f"[recommend_action] model={model}, tokens={tokens}")
    return result, model, tokens


def validate_verdict(facts: dict, judgment: dict, verdict: dict) -> tuple[dict, str, int]:
    """Call 4 (Sonnet): Sharpen verdict with stance, entry/risk triggers, and peer context."""
    input_data = {
        "preliminary_verdict": verdict,
        "judgment": {
            "phase": judgment.get("phase"),
            "momentum": judgment.get("momentum"),
            "bull_case": judgment.get("bull_case"),
            "bear_case": judgment.get("bear_case"),
            "risks": judgment.get("risks"),
        },
        "facts": {
            "company_name": facts.get("company_name"),
            "entity_type": facts.get("entity_type"),
            "price": facts.get("price"),
            "financials": facts.get("financials"),
            "valuation": facts.get("valuation"),
            "competitive_landscape": facts.get("competitive_landscape"),
            "recent_signals": facts.get("recent_signals", [])[:5],
        },
    }
    prompt = json.dumps(input_data, indent=2) + "\n\nRespond with valid JSON only."
    log("validate_verdict", "START", f"input_chars={len(prompt)}")

    result, model, tokens = _call_llm_with_retry(prompt, VALIDATE_SYSTEM, "validate", "validate_verdict", temperature=0.3, max_tokens=2048)
    print(f"[validate_verdict] model={model}, tokens={tokens}")
    return result, model, tokens


# ── Main entry point ──────────────────────────────────────────────────────────

def synthesize(
    company_name: str,
    company_type: str,
    fetcher_results: dict,
    total_time: float,
    api_call_count: int,
) -> dict:
    """Run the four-stage pipeline: extract → judge → act → validate.
    Returns formatter-compatible JSON.
    """
    total_tokens = 0
    models_used: list[str] = []

    # ── Call 1: Extract ────────────────────────────────────────────────────
    _t1 = time.time()
    try:
        facts, model1, tokens1 = extract_facts(company_name, company_type, fetcher_results)
        models_used.append(model1)
        total_tokens += tokens1
    except Exception as e:
        log("synthesize", "EXTRACT_FAIL", str(e))
        print(f"[synthesize] extract_facts failed: {e}")
        return {
            "error": f"Extract step failed: {e}",
            "metadata": {
                "total_time_seconds": round(total_time, 2),
                "total_api_calls": api_call_count,
                "llm_model_used": "failed-extract",
                "llm_tokens_used": total_tokens,
            },
        }
    _timing_extract = round(time.time() - _t1, 2)

    # Validation 1: early exit if data coverage too low
    signals_empty = not facts.get("signals")
    price_empty = not (facts.get("price") or {}).get("current")
    missing_count = len(facts.get("missing_data") or [])
    if signals_empty and price_empty and missing_count > 5:
        log("synthesize", "LOW_DATA_COVERAGE", f"signals=0, price=null, missing={missing_count}")
        print(f"[synthesize] low data coverage (missing={missing_count}) — returning minimal report")
        return _low_data_report(company_name, facts, total_time, api_call_count, models_used, total_tokens)

    # ── Call 2: Judge ──────────────────────────────────────────────────────
    _t2 = time.time()
    try:
        judgment, model2, tokens2 = judge(facts)
        models_used.append(model2)
        total_tokens += tokens2
    except Exception as e:
        log("synthesize", "JUDGE_FAIL", str(e))
        print(f"[synthesize] judge failed: {e} — using fallback judgment")
        judgment = {
            "phase": "UNKNOWN",
            "phase_reasoning": f"Judge step failed: {e}",
            "momentum": "STEADY",
            "bull_case": "",
            "bear_case": "",
            "risks": [],
            "low_confidence": True,
        }
    _timing_judge = round(time.time() - _t2, 2)

    # Validation 2: check bull/bear numbers are grounded in facts
    _validate_case_numbers(judgment, facts)

    # ── Call 3: Act ────────────────────────────────────────────────────────
    _t3 = time.time()
    try:
        verdict, model3, tokens3 = recommend_action(facts, judgment)
        models_used.append(model3)
        total_tokens += tokens3
    except Exception as e:
        log("synthesize", "ACT_FAIL", str(e))
        print(f"[synthesize] recommend_action failed: {e} — using fallback verdict")
        verdict = {
            "one_line": "Analysis incomplete — action step failed",
            "bull_case": judgment.get("bull_case", ""),
            "bear_case": judgment.get("bear_case", ""),
            "wait_for": "No confirmed catalyst in current sources",
            "wait_for_source": None,
            "action": "research_deeper",
            "confidence": "low",
        }
    _timing_act = round(time.time() - _t3, 2)

    # Validation 3: ensure wait_for references a real upcoming_event
    _validate_wait_for_source(verdict, facts)

    # ── Call 4: Validate ───────────────────────────────────────────────────
    _t4 = time.time()
    try:
        validated, model4, tokens4 = validate_verdict(facts, judgment, verdict)
        models_used.append(model4)
        total_tokens += tokens4
        # Merge: original verdict as base, validated fields override
        verdict = {**verdict, **validated}
        if not verdict.get("wait_for"):
            verdict["wait_for"] = "No confirmed catalyst in current sources"
    except Exception as e:
        log("synthesize", "VALIDATE_FAIL", str(e))
        print(f"[synthesize] validate_verdict failed: {e} — using original verdict")
    verdict = _enforce_word_limits(verdict)
    _timing_validate = round(time.time() - _t4, 2)

    result = _assemble(
        company_name, facts, judgment, verdict,
        total_time, api_call_count, models_used, total_tokens
    )
    # Stash LLM timings in metadata for run_research to extract
    if "metadata" in result:
        result["metadata"]["_timing_llm_extract"] = _timing_extract
        result["metadata"]["_timing_llm_judge"] = _timing_judge
        result["metadata"]["_timing_llm_act"] = _timing_act
        result["metadata"]["_timing_llm_validate"] = _timing_validate
    return result
