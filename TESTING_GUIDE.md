# DeepLook Testing Guide
## Get started in 5 minutes

---

## What You Need

- Mac or Linux (terminal access)
- Python 3.11+ (`python3 --version` to check)
- At least one LLM API key — Claude, GPT, Gemini, or DeepSeek
- Claude Desktop app (https://claude.ai/download) if testing MCP

### Cost (per research report)

| Provider | Model | ~Cost |
|----------|-------|-------|
| Anthropic | Haiku + Sonnet | ~$0.02-0.05 |
| OpenAI | GPT-4o-mini | ~$0.01-0.03 |
| Gemini | Flash Lite | ~$0.01-0.02 |
| DeepSeek | Chat | ~$0.005-0.01 |

Sign up and add credit before testing:
- Anthropic: https://console.anthropic.com
- OpenAI: https://platform.openai.com
- Google AI: https://aistudio.google.com
- DeepSeek: https://platform.deepseek.com

### Install Python (if you don't have it)

Mac:
```bash
brew install python@3.11
```

Or download from https://www.python.org/downloads/

After install, verify:
```bash
python3 --version
```

---

## Installation (3 minutes)

### Step 1: Clone
```bash
git clone https://github.com/OSOJDJD/deeplook.git
cd deeplook
```

### Step 2: Set up environment
```bash
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

### Step 3: Configure API key
```bash
cp .env.example .env
```
Open `.env` and add at least one LLM key. Anthropic recommended but any will work.

### Step 4: Test with CLI
```bash
python -m deeplook "NVIDIA"
```
If you see a full report with price, signals, and verdict — you're good.

### Step 5: Connect to Claude Desktop (optional)

Open or create this file:
```bash
# macOS
nano ~/Library/Application\ Support/Claude/claude_desktop_config.json
```

Paste this (change the two paths):
```json
{
  "mcpServers": {
    "deeplook": {
      "command": "/full/path/to/deeplook/venv/bin/python",
      "args": ["-m", "deeplook.mcp_server"],
      "cwd": "/full/path/to/deeplook",
      "env": {
        "ANTHROPIC_API_KEY": "sk-ant-your-key"
      }
    }
  }
}
```

Fully quit and restart Claude Desktop. Then ask: "Research NVIDIA for me"

---

## Companies to Test

| Company | Type | What it tests |
|---------|------|---------------|
| NVIDIA | Mega cap stock | Financial metrics, analyst targets |
| Tesla | Controversial stock | Signal detection |
| Aave | DeFi / Crypto | Entity auto-detection |
| Anthropic | Private company | Funding rounds + news |
| Your choice | Any | Edge case handling |

---

## Feedback We Need

After testing, please tell us:

1. **Did installation go smoothly?** If not, where did it break?
2. **Are the numbers accurate?** Spot-check price and market cap
3. **Any signal that made you go "wow, that's useful"?**
4. **Any signal that felt wrong or irrelevant?**
5. **Does the bull/bear verdict make sense?**
6. **Is the speed acceptable?** (typically 6-15 seconds)
7. **Would you recommend this to someone who does investment research?**
8. **If you could add one feature, what would it be?**

Screenshots + quick notes are fine. No need to be formal.

---

## Troubleshooting

**Rate limit error:** DuckDuckGo has free tier limits. Wait a few minutes or add `TAVILY_API_KEY` to `.env` as fallback.

**Crypto reports missing data:** Add `COINGECKO_API_KEY` to `.env` for better coverage. Free tier works.

**Claude Desktop doesn't detect MCP server:** Make sure paths in config.json are absolute (no `~/`). Fully quit and restart Claude Desktop.
