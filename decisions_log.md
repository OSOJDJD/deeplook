# DeepLook Decisions Log

---

## 2026-05-01 — REST API v1

### Decision
Added `deeplook/api.py` — a FastAPI app exposing `/api/v1/health`, `/api/v1/lookup/{query}`, and `/api/v1/research/{query}` endpoints for non-MCP clients (curl, Python requests, trading bots, Discord bots).

### Architecture
- FastAPI runs in the same uvicorn process as the MCP server (no new port, no new service).
- `_ClientIPMiddleware` in `mcp_server.py` routes `/api/*` paths to the FastAPI app; all other paths continue to the MCP Starlette app unchanged.
- MCP endpoint (`/mcp`) behavior is 100% unchanged.

### Scope decisions
| Feature | Decision | Reason |
|---|---|---|
| `research_with_judgment` REST endpoint | Not added | Requires server-side LLM key; deferred to later |
| POST endpoints | Not added | GET with path param is sufficient |
| Batch endpoints | Not added | No demand yet |
| Authentication | Not added | Open access for now; add if abuse |

### Additional features (added beyond initial spec)
- **SQLite request log** (`data/api_requests.db`): timestamp, IP, endpoint, query, entity_type, status, elapsed_seconds per call
- **Per-IP rate limit**: 10 req/min in-memory sliding window; 429 + `Retry-After` header. Configurable via `DEEPLOOK_REST_RATE_LIMIT` env var.
- **CORS**: `allow_origins=["*"]` — needed for browser-based tools

### Files changed
- `deeplook/api.py` — new file
- `deeplook/mcp_server.py` — `_ClientIPMiddleware` accepts `rest_app`; `main()` wires it in
- `pyproject.toml` — added `fastapi>=0.111.0`, `uvicorn[standard]>=0.29.0`

---

## Decision entries — 2026-05-01

- **Entity detection 簡化**：移除 parallel check + disambiguation，改 format-based routing（ticker→stock, name→crypto）+ `entity_type` 參數 | 減少 false disambiguation，降低用戶困惑 | 放棄 disambiguation response type
- **REST API alongside MCP**（`/api/v1/lookup`, `/api/v1/research`, `/api/v1/health`）| 開放給非 MCP 用戶（台灣 builder 社群）| 不開 `research_with_judgment`，per-IP 10/min rate limit
- **Request logging to SQLite**（`api_requests.db`）| Usage data 追蹤，未來 product 決策用 | N/A
- **VPS deploy 規則：pip install 必須用 `/root/deeplook/venv/bin/pip`** | 之前用 system pip 導致 `ModuleNotFoundError` crash | 寫入 `ops.md`
