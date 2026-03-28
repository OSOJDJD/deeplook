"""
judgment/synthesize.py — Two-stage LLM pipeline (compress + judge)

  Call 1 (compress) — Haiku:  compress fetcher data into structured context
  Call 2 (judge)    — Sonnet: assess signals, produce verdict with stance + triggers

  Model selection (Anthropic-first, falls back through OpenAI → Gemini → DeepSeek)
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
    """Call LLM with model selected by role. Timeout: 60s per call.
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


def _build_compress_prompt(entity_type: str) -> str:
    """Build entity-specific compress prompt for Haiku (v3)."""
    base = """You are a company intelligence analyst. Compress the raw research data into a structured brief.

## Output sections

### EVENTS
Recent significant events. Max 5 items. Each must have:
- date: exact date from source (YYYY-MM-DD)
- summary: 1 sentence, must include specific numbers or names
- sentiment: "positive" / "negative" / "neutral"

Priority: events that change the company's trajectory > routine updates.

### CONTEXT
Exactly 3 insights. Each is 1-2 sentences. They must follow this structure:
1. WHAT: What is this company doing right now? Core positioning and strategy.
2. SO WHAT: What do the recent events mean? Why should someone care?
3. BUT WHAT: What risk, tension, or uncertainty should someone watch?

Do NOT prefix items with labels like "WHAT:", "SO WHAT:", or "BUT WHAT:". Write the insight directly without any prefix.
Do NOT repeat facts from EVENTS. CONTEXT interprets what events mean.

### CATALYSTS
Upcoming events or triggers. Max 4 items. Each is 1 sentence.
Must include dates when known. If no dates known, describe the trigger condition.

### DESCRIPTION
1-2 sentence summary of what this company does. For a reader who has never heard of it.
Must mention: sector/industry and primary product/service.

### FOUNDERS
List of founder names. If not found in data, return empty array [].

### VALUATION_EXTRACT (private/unlisted companies only)
If news mentions a valuation (e.g. "valued at $159B"), extract:
- value: the number as string (e.g. "$159B")
- source: "news_report" / "secondary_market" / "funding_round"
- date: when reported (YYYY-MM)
If no valuation found, return null.

### VERDICT
- one_line: 1-sentence summary judgment. Must have a clear direction, not hedge.
- momentum: "accelerating" / "steady" / "decelerating" / "uncertain"
  Decide based on: revenue trend + recent events + competitive position.
- tailwind: Primary positive force. 1 sentence with specific evidence.
- headwind: Primary negative force or risk. 1 sentence with specific evidence.
- watch_for: What specific event or data point would change the picture? 1 sentence.

## Rules
- No fixed word limit. Let content density determine length.
  Per-section limits: EVENTS max 5 items (1 sentence each), CONTEXT exactly 3 items (1-2 sentences each),
  CATALYSTS max 4 items (1 sentence each), DESCRIPTION 1-2 sentences, VERDICT 5 fields (1 sentence each).
- Only state facts from provided sources. Never hallucinate.
- Numbers must be specific. Never write "significant growth" — write "+73% YoY".
- Every claim must be traceable to the provided data.
"""

    entity_guidance = {
        "public_equity": """
For EVENTS priority: earnings results, analyst upgrades/downgrades, institutional activity (13F), M&A rumors, product launches, regulatory actions.
For CATALYSTS priority: next earnings date + what to watch, analyst consensus target, upcoming catalysts with dates.
For CONTEXT focus: management tone shift, competitive dynamics not in numbers, sector rotation signals.""",

        "crypto": """
For EVENTS priority: governance proposals/votes, security incidents (hacks/exploits), major partnerships, protocol upgrades, token unlock events, regulatory actions.
For CATALYSTS priority: upcoming governance votes with dates, token unlock schedule, roadmap milestones, regulatory deadlines.
For CONTEXT focus: TVL trend direction, developer activity signals, community sentiment shift, competitive protocol dynamics.""",

        "venture_capital": """
For EVENTS priority: new funding rounds (amount + lead investor), key executive hires/departures, notable portfolio investments or exits, fund launches, strategic pivots.
For CATALYSTS priority: expected next fund close, sector thesis signals, upcoming portfolio company events (IPO, acquisition).
For CONTEXT focus: investment thesis evolution, portfolio concentration risk, team changes that signal strategy shift.""",

        "private_or_unlisted": """
For EVENTS priority: new funding rounds (amount + lead investor), product launches, key partnerships, executive changes, regulatory milestones.
For CATALYSTS priority: expected funding round, IPO signals, product launch dates, expansion milestones.
For CONTEXT focus: competitive positioning, market traction signals, team strength.""",

        "defunct": """
For EVENTS priority: legal proceedings updates, regulatory actions, creditor recovery distributions, key personnel indictments/settlements, asset sales.
For CATALYSTS priority: next court date, expected distribution timeline, pending regulatory decisions.
For CONTEXT focus: recovery rate vs expectations, knock-on effects on industry, lessons cited by regulators.""",
    }

    guidance = entity_guidance.get(entity_type) or entity_guidance.get("private_or_unlisted", "")

    footer = """
Return valid JSON only. No markdown, no explanation.

{
  "events": [{"date": "YYYY-MM-DD", "summary": "...", "sentiment": "positive|negative|neutral"}],
  "context": ["WHAT: ...", "SO WHAT: ...", "BUT WHAT: ..."],
  "catalysts": ["..."],
  "description": "...",
  "founders": ["..."],
  "valuation_extract": {"value": "...", "source": "...", "date": "..."} | null,
  "verdict": {
    "one_line": "...",
    "momentum": "accelerating|steady|decelerating|uncertain",
    "tailwind": "...",
    "headwind": "...",
    "watch_for": "..."
  }
}"""

    return base + guidance + footer


async def compress_context(structured_data: dict) -> dict:
    """Single LLM call: compress fetcher data into structured brief (v3 prompt).
    Input: output of prepare_structured_data()
    Output: {events, context, catalysts, description, founders, valuation_extract, verdict, _model, _tokens}

    Model selection: DEEPLOOK_MODEL env var overrides all roles. If unset, uses Haiku (extract role).
    """
    import asyncio as _asyncio

    company_name = structured_data.get("company_name", "")
    entity_type = structured_data.get("entity_type", "")
    compress_system = _build_compress_prompt(entity_type)

    # DEEPLOOK_MODEL override: if set, use that model for all calls
    _override_model = os.environ.get("DEEPLOOK_MODEL")
    if _override_model:
        os.environ["DEEPLOOK_EXTRACT_MODEL"] = _override_model

    # Build text context: news + website/wiki snippets + entity-specific numbers
    extra_context = ""
    crypto_nums = structured_data.get("crypto_numbers") or {}
    vc_nums = structured_data.get("vc_numbers") or {}
    if crypto_nums:
        extra_context = f"\n=== Crypto Metrics ===\n{json.dumps(crypto_nums, indent=2)}\n"
    elif vc_nums:
        extra_context = f"\n=== VC/Fund Data ===\n{json.dumps(vc_nums, indent=2)}\n"

    user_prompt = (
        f"Company: {company_name}\nType: {entity_type}\n\n"
        f"=== News Articles ===\n"
        f"{json.dumps(structured_data.get('news_for_compression', []), indent=2)}\n\n"
        f"=== Website/Wikipedia Text ===\n"
        f"{json.dumps(structured_data.get('text_for_compression', {}), indent=2)}\n"
        f"{extra_context}\n"
        f"Respond with valid JSON only."
    )

    try:
        result, model, tokens = await _asyncio.to_thread(
            _call_llm_with_retry,
            user_prompt, compress_system, "extract", "compress_context",
            0.2, 1200,
        )
        log("compress_context", "OK", f"model={model}, tokens={tokens}")
        return {
            # v3 fields
            "events": result.get("events", []),
            "context": result.get("context", []),
            "catalysts": result.get("catalysts", []),
            "description": result.get("description"),
            "founders": result.get("founders", []),
            "valuation_extract": result.get("valuation_extract"),
            "verdict": result.get("verdict") or {},
            # v2 backward-compat aliases (so build_structured_json_v2 still works)
            "recent_news": result.get("events", []),
            "forward_looking": result.get("catalysts", []),
            "entity_context": result.get("context", []),
            "_model": model,
            "_tokens": tokens,
        }
    except Exception as e:
        log("compress_context", "FAIL", str(e))
        print(f"[compress_context] failed: {e}")
        return {
            "events": [],
            "context": [],
            "catalysts": [],
            "description": None,
            "founders": [],
            "valuation_extract": None,
            "verdict": {},
            "recent_news": [],
            "forward_looking": [],
            "entity_context": [],
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


