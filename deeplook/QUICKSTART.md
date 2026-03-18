# DeepLook Quickstart

## 1. Install

```bash
git clone https://github.com/yourname/personal-os.git && cd personal-os && pip install -e ".[deeplook]"
```

Or if you're already in the repo:

```bash
pip install -e .
```

## 2. Set up .env

Copy and fill in:

```bash
cp .env.example .env
```

Required — LLM key, pick one (Anthropic recommended):

```
ANTHROPIC_API_KEY=sk-ant-...      # claude-haiku-4-5 (recommended)
OPENAI_API_KEY=sk-...             # gpt-4o-mini
GEMINI_API_KEY=...                # gemini-2.0-flash-lite
DEEPSEEK_API_KEY=...              # deepseek-chat
```

Optional (enables more data sources):

```
FIRECRAWL_API_KEY=fc-...          # website content fetching (fallback scraper)
COINGECKO_API_KEY=...             # crypto market data
ROOTDATA_SKILL_KEY=...            # Web3 project funding data
```

## 3. Run CLI

```bash
python -m deeplook.research "Nvidia"
```

Add `--no-youtube` to skip YouTube (faster):

```bash
python -m deeplook.research "Nvidia" --no-youtube
```

Output is saved to `deeplook/output/<company>_<date>.json`.

## 4. Start MCP Server

**stdio mode** (local, default):
```bash
python -m deeplook.mcp_server
```

**HTTP mode** (for remote / multi-user hosting):
```bash
python -m deeplook.mcp_server --http
# Serving on http://0.0.0.0:8819/mcp

# Custom host/port:
python -m deeplook.mcp_server --http --host 127.0.0.1 --port 9000
```

## 5. Claude Desktop Config

### Local install (stdio)

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "deeplook": {
      "command": "/path/to/deeplook-public/venv/bin/python",
      "args": ["-m", "deeplook.mcp_server"],
      "cwd": "/path/to/deeplook-public",
      "env": {
        "ANTHROPIC_API_KEY": "sk-ant-...",
        "FIRECRAWL_API_KEY": "fc-..."
      }
    }
  }
}
```

Replace `/path/to/deeplook-public` with your actual clone path.

After saving, restart Claude Desktop. You should see the `deeplook_research` and `deeplook_lookup` tools available.

### Remote MCP (no local install needed)

If someone is hosting DeepLook for you, just add the URL to your Claude Desktop config:

```json
{
  "mcpServers": {
    "deeplook": {
      "url": "https://your-host-url/mcp"
    }
  }
}
```

No Python, no git clone, no `.env` needed on your machine.

**Usage in Claude Desktop:**
> Research Nvidia for me

Claude will call `deeplook_research("Nvidia")` and return a full intelligence report.
