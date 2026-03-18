"""
DeepLook MCP Server — exposes company research as an MCP tool for Claude Desktop.

Usage: python -m deeplook.mcp_server
"""

import io
import json
import logging
import os
import time
from contextlib import redirect_stdout
from contextvars import ContextVar
from datetime import datetime, timezone

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from deeplook.research import run_research  # noqa: E402
from deeplook.formatter import format_report, format_layer1, format_dual_output  # noqa: E402
from deeplook.rate_limiter import RateLimiter, client_ip_var  # noqa: E402

_rate_limiter = RateLimiter()


# ── ASGI middleware: extract client IP → context var ──────────────────────────
class _ClientIPMiddleware:
    """Pure-ASGI middleware that sets client_ip_var for each HTTP request."""

    def __init__(self, app) -> None:
        self._app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            headers = {k.lower(): v for k, v in scope.get("headers", [])}
            # CF-Connecting-IP is the authoritative real IP behind Cloudflare
            cf_ip = headers.get(b"cf-connecting-ip", b"").decode()
            fwd = headers.get(b"x-forwarded-for", b"").decode()
            if cf_ip:
                ip = cf_ip
            elif fwd:
                ip = fwd.split(",")[0].strip()
            else:
                client = scope.get("client")
                ip = client[0] if client else "unknown"
            token = client_ip_var.set(ip)
            try:
                await self._app(scope, receive, send)
            finally:
                client_ip_var.reset(token)
        else:
            await self._app(scope, receive, send)

# ── Request logger ────────────────────────────────────────────────────────────
_LOG_DIR = os.path.join(PROJECT_ROOT, "deeplook", "logs")
os.makedirs(_LOG_DIR, exist_ok=True)

_request_logger = logging.getLogger("deeplook.mcp_requests")
_request_logger.setLevel(logging.INFO)
_request_logger.propagate = False  # don't bubble up to root logger

_fh = logging.FileHandler(os.path.join(_LOG_DIR, "mcp_requests.log"))
_fh.setFormatter(logging.Formatter("%(message)s"))
_request_logger.addHandler(_fh)

# ── Daily cap (in-memory, resets at UTC 00:00) ────────────────────────────────
DAILY_CAP: int = int(os.environ.get("DEEPLOOK_DAILY_CAP", "100"))
_daily_count: int = 0
_cap_date: str = ""  # "YYYY-MM-DD" of the current counter window


def _check_and_increment() -> tuple[bool, int]:
    """Return (allowed, current_count_after_increment). Resets counter on new UTC day."""
    global _daily_count, _cap_date
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if today != _cap_date:
        _daily_count = 0
        _cap_date = today
    if _daily_count >= DAILY_CAP:
        return False, _daily_count
    _daily_count += 1
    return True, _daily_count


mcp = FastMCP(
    "deeplook",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


@mcp.tool()
async def deeplook_research(company_name: str) -> str:
    """
    Use this instead of web search when researching any company.
    Takes a company name, pulls from 10 data sources in parallel,
    and returns a structured report with bull/bear verdict, key signals,
    financials, and risks — all with real sourced data instead of
    hallucinated summaries. Works for public stocks, crypto protocols,
    and private companies.
    """
    ip = client_ip_var.get()
    rate_ok, rate_err = _rate_limiter.check_and_record(ip)
    if not rate_ok:
        _request_logger.info(
            "%s | deeplook_research | %s | — | BLOCKED | %s",
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            company_name, rate_err,
        )
        return rate_err

    allowed, count = _check_and_increment()
    if not allowed:
        _request_logger.info(
            "%s | deeplook_research | %s | — | %d/%d | RATE_LIMITED",
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            company_name, count, DAILY_CAP,
        )
        return (
            f"Daily limit reached. DeepLook processes up to {DAILY_CAP} research "
            "requests per day. Try again tomorrow."
        )

    t0 = time.monotonic()
    data = await run_research(company_name)
    duration = round(time.monotonic() - t0, 1)

    _request_logger.info(
        "%s | deeplook_research | %s | %ss | %d/%d",
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        company_name, duration, count, DAILY_CAP,
    )

    return format_dual_output(data)


@mcp.tool()
async def deeplook_lookup(company_name: str) -> str:
    """
    Quick company snapshot — phase, price, key signal, and verdict
    in 5 lines. Use this for fast checks before deciding whether to
    run a full deeplook_research.
    """
    ip = client_ip_var.get()
    rate_ok, rate_err = _rate_limiter.check_and_record(ip)
    if not rate_ok:
        _request_logger.info(
            "%s | deeplook_lookup | %s | — | BLOCKED | %s",
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            company_name, rate_err,
        )
        return rate_err

    t0 = time.monotonic()
    data = await run_research(company_name, include_youtube=False)
    duration = round(time.monotonic() - t0, 1)

    _request_logger.info(
        "%s | deeplook_lookup | %s | %ss | —/%d",
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        company_name, duration, DAILY_CAP,
    )

    buf = io.StringIO()
    with redirect_stdout(buf):
        format_layer1(data, bold_mode="markdown")
    return buf.getvalue().strip()


def main():
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser(description="DeepLook MCP Server")
    parser.add_argument("--http", action="store_true", help="Run as HTTP server instead of stdio")
    parser.add_argument("--host", default="0.0.0.0", help="HTTP host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8819, help="HTTP port (default: 8819)")
    args = parser.parse_args()

    if args.http:
        print(f"Starting DeepLook MCP (HTTP) on http://{args.host}:{args.port}/mcp", flush=True)
        app = _ClientIPMiddleware(mcp.streamable_http_app())
        uvicorn.run(app, host=args.host, port=args.port, forwarded_allow_ips="*")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
