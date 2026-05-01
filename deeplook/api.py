"""
DeepLook REST API — FastAPI app exposing research as HTTP endpoints.

Mounted alongside the MCP server in the same uvicorn process.
Routes: /api/v1/health, /api/v1/lookup/{query}, /api/v1/research/{query}
"""

import asyncio
import os
import sqlite3
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from fastapi import FastAPI, Path, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from deeplook.formatter import build_structured_json_v3, format_output_v3
from deeplook.research import run_research

# ── SQLite request log ────────────────────────────────────────────────────────
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DB_PATH = os.path.join(_PROJECT_ROOT, "data", "api_requests.db")
_db_lock = threading.Lock()
_db_conn: sqlite3.Connection | None = None


def _get_db() -> sqlite3.Connection:
    global _db_conn
    if _db_conn is None:
        os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
        conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_requests (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp    TEXT NOT NULL,
                ip           TEXT,
                endpoint     TEXT,
                query        TEXT,
                entity_type  TEXT,
                status       TEXT,
                elapsed_seconds REAL
            )
        """)
        conn.commit()
        _db_conn = conn
    return _db_conn


def _write_log(
    ip: str, endpoint: str, query: str,
    entity_type: str | None, status: str, elapsed: float,
) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _db_lock:
        try:
            db = _get_db()
            db.execute(
                "INSERT INTO api_requests "
                "(timestamp, ip, endpoint, query, entity_type, status, elapsed_seconds) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ts, ip, endpoint, query, entity_type, status, elapsed),
            )
            db.commit()
        except Exception:
            pass


# ── In-memory per-IP rate limiter (10 req/min) ────────────────────────────────
_rl_lock = threading.Lock()
_rl_windows: dict[str, deque] = defaultdict(deque)
_RL_MAX = int(os.environ.get("DEEPLOOK_REST_RATE_LIMIT", "10"))
_RL_WINDOW = 60  # seconds


def _check_rate_limit(ip: str) -> tuple[bool, int]:
    """Returns (allowed, retry_after_seconds). Thread-safe sliding window."""
    now = time.time()
    cutoff = now - _RL_WINDOW
    with _rl_lock:
        dq = _rl_windows[ip]
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= _RL_MAX:
            retry_after = max(1, int(_RL_WINDOW - (now - dq[0])) + 1)
            return False, retry_after
        dq.append(now)
        return True, 0


# ── Client IP extraction (Cloudflare-aware) ───────────────────────────────────
def _get_ip(request: Request) -> str:
    cf = request.headers.get("cf-connecting-ip", "").strip()
    if cf:
        return cf
    fwd = request.headers.get("x-forwarded-for", "").strip()
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ── App ───────────────────────────────────────────────────────────────────────
_START_TIME = time.time()

app = FastAPI(
    title="DeepLook API",
    description="Company research API — any company, one call",
    version="3.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/v1/health")
async def health():
    return {
        "status": "ok",
        "version": "3.0",
        "uptime_seconds": round(time.time() - _START_TIME),
    }


@app.get("/api/v1/lookup/{query:path}")
async def lookup(
    request: Request,
    query: str = Path(..., description="Company name or ticker"),
    type: str = Query("auto", pattern="^(stock|crypto|auto)$"),
):
    ip = _get_ip(request)
    allowed, retry_after = _check_rate_limit(ip)
    if not allowed:
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(retry_after)},
            content={
                "status": "error",
                "error": "Rate limit exceeded",
                "retry_after_seconds": retry_after,
            },
        )

    entity_type = None if type == "auto" else type
    start = time.time()
    resolved_entity_type: str | None = None
    try:
        result = await run_research(query, include_youtube=False, use_llm=False, entity_type=entity_type)
        elapsed = round(time.time() - start, 1)
        resolved_entity_type = result.get("entity_type")
        asyncio.create_task(asyncio.to_thread(
            _write_log, ip, "lookup", query, resolved_entity_type, "ok", elapsed,
        ))
        return {
            "status": "ok",
            "query": query,
            "entity_type": resolved_entity_type,
            "ticker": result.get("ticker"),
            "elapsed_seconds": elapsed,
            "data": build_structured_json_v3(result),
        }
    except Exception as e:
        elapsed = round(time.time() - start, 1)
        asyncio.create_task(asyncio.to_thread(
            _write_log, ip, "lookup", query, resolved_entity_type, "error", elapsed,
        ))
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "query": query,
                "error": str(e),
                "elapsed_seconds": elapsed,
            },
        )


@app.get("/api/v1/research/{query:path}")
async def research(
    request: Request,
    query: str = Path(..., description="Company name or ticker"),
    type: str = Query("auto", pattern="^(stock|crypto|auto)$"),
    format: str = Query("json", pattern="^(json|markdown|full)$"),
):
    ip = _get_ip(request)
    allowed, retry_after = _check_rate_limit(ip)
    if not allowed:
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(retry_after)},
            content={
                "status": "error",
                "error": "Rate limit exceeded",
                "retry_after_seconds": retry_after,
            },
        )

    entity_type = None if type == "auto" else type
    start = time.time()
    resolved_entity_type: str | None = None
    try:
        result = await run_research(query, use_llm=False, entity_type=entity_type)
        elapsed = round(time.time() - start, 1)
        resolved_entity_type = result.get("entity_type")
        asyncio.create_task(asyncio.to_thread(
            _write_log, ip, "research", query, resolved_entity_type, "ok", elapsed,
        ))
        response: dict = {
            "status": "ok",
            "query": query,
            "entity_type": resolved_entity_type,
            "ticker": result.get("ticker"),
            "elapsed_seconds": elapsed,
        }
        if format in ("json", "full"):
            response["data"] = build_structured_json_v3(result)
        if format in ("markdown", "full"):
            response["markdown"] = format_output_v3(result)
        return response
    except Exception as e:
        elapsed = round(time.time() - start, 1)
        asyncio.create_task(asyncio.to_thread(
            _write_log, ip, "research", query, resolved_entity_type, "error", elapsed,
        ))
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "query": query,
                "error": str(e),
                "elapsed_seconds": elapsed,
            },
        )
