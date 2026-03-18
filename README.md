# 🔍 DeepLook

**Free Bloomberg Terminal for AI Agents** — open-source MCP server that researches any company in ~10 seconds.

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue) ![MIT License](https://img.shields.io/badge/license-MIT-green) ![MCP Compatible](https://img.shields.io/badge/MCP-compatible-purple)

LLMs hallucinate financial data. Other finance MCP servers return raw data from a single source — you still do the research yourself. DeepLook runs the full workflow: 10 sources in parallel, cross-referenced, with a structured bull/bear verdict. One call, ~10 seconds, no API keys needed.

[![deeplook MCP server](https://glama.ai/mcp/servers/OSOJDJD/deeplook/badges/card.svg)](https://glama.ai/mcp/servers/OSOJDJD/deeplook)

---

## ⚡ Connect in 30 Seconds

1. Claude.ai → **Settings → Connectors → Add MCP Server**
2. Paste: `https://mcp.deeplook.dev/mcp`
3. Try: *"Use DeepLook to research NVIDIA"*

Works with Claude Desktop, Cursor, Windsurf, or any MCP-compatible client.

---

## What You Get

```
NVIDIA Corporation — $181.93 | EXPANDING / ACCELERATING
Key Signals:

🟢 Jensen Huang projects $1T AI chip revenue by 2027
🟢 Vera Rubin platform with 7 new chips in production
🔴 Earnings surprise: -55.03%

Verdict: Mega-cap AI leader with 73% revenue growth, $1T opportunity

🟢 Revenue +73.2% YoY, earnings +95.6%, $58.1B FCF
🔴 RSI 37.2 oversold, $4.42T valuation limits upside
⏳ Wait for: Q1 FY2027 earnings on 2026-05-20
```

Embedded structured JSON with precise metrics, peer comparison, technicals
→ AI clients auto-render as interactive dashboards

---

## Features

- 10+ data sources in parallel (yfinance, news, CoinGecko, DeFiLlama, SEC EDGAR, Wikipedia, YouTube, etc.)
- Works for public stocks, crypto, and private companies
- Dual output: human-readable summary + structured JSON for AI agents
- Bull/bear verdict with catalyst timeline
- Peer comparison with financial metrics
- ~10 second research time
- Two tools: `deeplook_research` (full report) and `deeplook_lookup` (quick snapshot)

## Supported Entity Types

Public Equity · Crypto/DeFi · Private Companies · Exchanges · VCs · Foundations

---

## Self-Host

**1. Clone and install:**

```bash
git clone https://github.com/OSOJDJD/deeplook.git
cd deeplook
python3 -m venv venv && source venv/bin/activate
pip install -e .
cp .env.example .env   # add at least one LLM key
```

**2. Run as HTTP MCP server:**

```bash
python -m deeplook.mcp_server --http --port 8819
```

**3. Or add to Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "deeplook": {
      "command": "/full/path/to/deeplook/venv/bin/python",
      "args": ["-m", "deeplook.mcp_server"],
      "cwd": "/full/path/to/deeplook",
      "env": { "ANTHROPIC_API_KEY": "sk-ant-..." }
    }
  }
}
```

**CLI (no MCP):**

```bash
python -m deeplook "NVIDIA"
python -m deeplook "Aave"
python -m deeplook "Anthropic"
```

---

## API Keys

Pick at least one LLM provider:

| Variable | Provider |
|----------|----------|
| `ANTHROPIC_API_KEY` | Claude — Haiku + Sonnet (recommended) |
| `OPENAI_API_KEY` | GPT-4o-mini |
| `GEMINI_API_KEY` | Gemini 2.0 Flash Lite |
| `DEEPSEEK_API_KEY` | DeepSeek Chat |

Optional (for deeper research):

| Variable | Description |
|----------|-------------|
| `TAVILY_API_KEY` | Search fallback when DDG is rate-limited |
| `COINGECKO_API_KEY` | CoinGecko Pro for crypto data |
| `ROOTDATA_SKILL_KEY` | RootData for crypto project data |

Cost per report: ~$0.02–0.05 (Anthropic) · ~$0.01–0.03 (OpenAI) · ~$0.01–0.02 (Gemini) · ~$0.005–0.01 (DeepSeek)

---

## Data Sources

| Source | Used For |
|--------|----------|
| yFinance | Price, financials, analyst targets, technicals |
| DuckDuckGo News | Recent signals, headlines |
| Wikipedia | Company background |
| YouTube | Earnings calls, CEO interviews |
| CoinGecko | Token price, market cap, volume |
| RootData | Crypto funding, team data |
| DefiLlama | TVL, chain metrics |
| SEC EDGAR | 10-K, 10-Q, 8-K filings |
| Finnhub | Earnings, news, sentiment |
| Website | Investor relations, product pages |

---

## How It Works

```
Company Name
    ↓
Entity Type Router  (public equity / crypto / private / exchange / VC / foundation)
    ↓
10 Parallel Fetchers  (DDG News, yFinance, CoinGecko, SEC EDGAR, ...)
    ↓
3-Call LLM Pipeline:  Extract (Haiku)  →  Judge (Sonnet)  →  Act (Sonnet)
    ↓
Structured Report + Embedded JSON
```

---

## Eval

Tested across 37 companies (equities, crypto, private):

| Metric | Score |
|--------|-------|
| Overall | 3.78 / 5.0 |
| Risk detection | 4.36 / 5.0 |
| Signal quality | 3.94 / 5.0 |
| Actionability | 3.38 / 5.0 |

Eval framework ships in `/eval` — run it yourself, contribute ground truth data.

---

## License

MIT — use it however you want.

Built by [@OSOJDJD](https://github.com/OSOJDJD) · [Open an issue](https://github.com/OSOJDJD/deeplook/issues) if something breaks or a report looks wrong.
