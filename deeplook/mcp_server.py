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
from urllib.parse import parse_qs, urlencode, urlparse

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

def _load_static(filename: str) -> bytes:
    with open(os.path.join(_STATIC_DIR, filename), "rb") as f:
        return f.read()

_FAVICON_ICO = _load_static("favicon.ico")
_FAVICON_PNG = _load_static("favicon-64.png")

from deeplook.research import run_research  # noqa: E402
from deeplook.formatter import format_report, format_layer1, format_dual_output  # noqa: E402
from deeplook.rate_limiter import RateLimiter, client_ip_var, add_to_waitlist  # noqa: E402

_rate_limiter = RateLimiter()


# ── ASGI middleware: extract client IP → context var ──────────────────────────
_PRIVACY_HTML = b"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>DeepLook Privacy Policy</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 680px; margin: 60px auto; padding: 0 24px; line-height: 1.7; color: #222; }
    h1 { font-size: 1.6rem; margin-bottom: 0.25em; }
    h2 { font-size: 1.1rem; margin-top: 2em; color: #444; }
    ul { padding-left: 1.4em; }
    a { color: #0066cc; }
    footer { margin-top: 3em; font-size: 0.85rem; color: #888; }
  </style>
</head>
<body>
  <h1>DeepLook Privacy Policy</h1>
  <p><strong>Last updated: March 2026</strong></p>

  <h2>Data Collection</h2>
  <ul>
    <li>DeepLook does not store, log, or sell user queries.</li>
    <li>No personal data is collected through the MCP server.</li>
    <li>Request counts are tracked in-memory only (for rate limiting) and reset daily. No query content is retained.</li>
  </ul>

  <h2>Data Sources</h2>
  <ul>
    <li>Reports are generated exclusively from publicly available data sources: yfinance, DuckDuckGo News, CoinGecko, DeFiLlama, SEC EDGAR, Wikipedia, YouTube, and Finnhub.</li>
    <li>DeepLook does not scrape, store, or redistribute any proprietary financial data.</li>
  </ul>

  <h2>Third-Party APIs</h2>
  <ul>
    <li>DeepLook calls third-party APIs (listed above) on your behalf. Each provider's own privacy policy applies to those calls.</li>
    <li>No user-identifying information is forwarded to third-party APIs.</li>
  </ul>

  <h2>Contact</h2>
  <p>For questions or concerns, open an issue at:
    <a href="https://github.com/OSOJDJD/deeplook/issues">github.com/OSOJDJD/deeplook/issues</a>
  </p>

  <footer>DeepLook is open-source software released under the MIT License.</footer>
</body>
</html>"""


_WAITLIST_HTML = b"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>DeepLook Pro &#8212; Coming Soon</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: system-ui, -apple-system, sans-serif; background: #0a0a0a; color: #e8e8e8; min-height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 24px; }
    .card { max-width: 520px; width: 100%; text-align: center; }
    .logo { font-size: 1.5rem; font-weight: 700; letter-spacing: -0.5px; color: #fff; margin-bottom: 2rem; }
    .logo span { color: #2563eb; }
    h1 { font-size: 2rem; font-weight: 700; color: #fff; margin-bottom: 0.75rem; }
    .tagline { font-size: 1.15rem; color: #2563eb; font-weight: 600; margin-bottom: 1.5rem; }
    .body-text { color: #999; line-height: 1.7; margin-bottom: 2rem; font-size: 0.95rem; }
    .body-text strong { color: #ccc; }
    form { display: flex; gap: 10px; margin-bottom: 1.5rem; }
    input[type="email"] { flex: 1; padding: 12px 16px; border-radius: 8px; border: 1px solid #333; background: #1a1a1a; color: #fff; font-size: 1rem; outline: none; }
    input[type="email"]:focus { border-color: #2563eb; }
    input[type="email"]::placeholder { color: #555; }
    button { padding: 12px 24px; border-radius: 8px; border: none; background: #2563eb; color: #fff; font-size: 1rem; font-weight: 600; cursor: pointer; white-space: nowrap; }
    button:hover { background: #1d4ed8; }
    .footer-link { font-size: 0.85rem; color: #555; }
    .footer-link a { color: #444; text-decoration: none; }
    .footer-link a:hover { color: #888; }
  </style>
</head>
<body>
  <div class="card">
    <div class="logo">Deep<span>Look</span></div>
    <h1>DeepLook Pro</h1>
    <div class="tagline">Go deeper. Get the full picture.</div>
    <p class="body-text">
      <strong>Free DeepLook</strong> gives you a quick research snapshot.<br>
      <strong>Pro</strong> gives you the depth to make decisions.<br><br>
      Be the first to know when it launches.
    </p>
    <form action="/waitlist" method="POST">
      <input type="email" name="email" placeholder="your@email.com" required>
      <button type="submit">Join Waitlist</button>
    </form>
    <div class="footer-link">
      Currently free and open source &rarr;
      <a href="https://github.com/OSOJDJD/deeplook">github.com/OSOJDJD/deeplook</a>
    </div>
  </div>
</body>
</html>"""

_WAITLIST_CONFIRMED_HTML = b"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>You're on the list!</title>
  <style>
    body { font-family: system-ui, -apple-system, sans-serif; background: #0a0a0a; color: #e8e8e8; min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 24px; text-align: center; }
    .card { max-width: 420px; }
    .logo { font-size: 1.5rem; font-weight: 700; color: #fff; margin-bottom: 2rem; }
    .logo span { color: #2563eb; }
    .check { font-size: 3rem; margin-bottom: 1rem; }
    h1 { font-size: 1.6rem; font-weight: 700; color: #fff; margin-bottom: 0.75rem; }
    p { color: #888; line-height: 1.6; }
    a { color: #2563eb; text-decoration: none; }
  </style>
</head>
<body>
  <div class="card">
    <div class="logo">Deep<span>Look</span></div>
    <div class="check">&#10003;</div>
    <h1>You're on the list!</h1>
    <p>We'll reach out when DeepLook Pro launches.<br><br>
    In the meantime, explore the open-source version at<br>
    <a href="https://github.com/OSOJDJD/deeplook">github.com/OSOJDJD/deeplook</a></p>
  </div>
</body>
</html>"""

_OAUTH_ACCESS_TOKEN = "deeplook_free_v1"
_OAUTH_CODE = "deeplook_auth_code"


def _make_authorize_html(redirect_uri: str, state: str) -> bytes:
    """Generate the OAuth authorize page with the redirect baked into the button URL."""
    do_auth_params = urlencode({"redirect_uri": redirect_uri, "state": state})
    action_url = f"/oauth/do_authorize?{do_auth_params}"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Authorize DeepLook</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 480px; margin: 100px auto; padding: 0 24px; text-align: center; color: #222; }}
    .logo {{ font-size: 2rem; font-weight: 700; letter-spacing: -1px; margin-bottom: 0.25em; }}
    .logo span {{ color: #2563eb; }}
    p {{ color: #555; line-height: 1.6; margin: 1.2em 0; }}
    .btn {{ display: inline-block; background: #16a34a; color: #fff; padding: 12px 36px; border-radius: 8px;
            text-decoration: none; font-size: 1rem; font-weight: 600; margin-top: 1em; }}
    .btn:hover {{ background: #15803d; }}
    .privacy {{ font-size: 0.8rem; color: #aaa; margin-top: 2em; }}
    .privacy a {{ color: #888; }}
  </style>
</head>
<body>
  <div class="logo">Deep<span>Look</span></div>
  <h2>Connect with Claude</h2>
  <p>DeepLook provides free company research reports powered by 10 public data sources.<br>
     By authorizing, you agree to our
     <a href="/privacy">Privacy Policy</a>.</p>
  <p style="font-size:0.9rem;color:#888;">No account required. No data stored.</p>
  <a class="btn" href="{action_url}">Authorize</a>
  <div class="privacy">
    <a href="/privacy">Privacy Policy</a> &middot;
    <a href="https://github.com/OSOJDJD/deeplook">GitHub</a>
  </div>
</body>
</html>"""
    return html.encode()


async def _asgi_send_bytes(send, status: int, content_type: bytes, body: bytes):
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", content_type),
            (b"content-length", str(len(body)).encode()),
        ],
    })
    await send({"type": "http.response.body", "body": body})


async def _asgi_send_redirect(send, location: str):
    loc_bytes = location.encode()
    await send({
        "type": "http.response.start",
        "status": 302,
        "headers": [
            (b"location", loc_bytes),
            (b"content-length", b"0"),
        ],
    })
    await send({"type": "http.response.body", "body": b""})


class _ClientIPMiddleware:
    """Pure-ASGI middleware that sets client_ip_var for each HTTP request."""

    def __init__(self, app) -> None:
        self._app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            path = scope.get("path", "")
            qs = scope.get("query_string", b"").decode()
            params = parse_qs(qs)

            if path == "/privacy":
                await _asgi_send_bytes(send, 200, b"text/html; charset=utf-8", _PRIVACY_HTML)
                return

            if path == "/favicon.ico":
                await _asgi_send_bytes(send, 200, b"image/x-icon", _FAVICON_ICO)
                return

            if path == "/favicon.png":
                await _asgi_send_bytes(send, 200, b"image/png", _FAVICON_PNG)
                return

            if path == "/oauth/authorize":
                redirect_uri = params.get("redirect_uri", [""])[0]
                state = params.get("state", [""])[0]
                if not redirect_uri:
                    await _asgi_send_bytes(send, 400, b"text/plain", b"Missing redirect_uri")
                    return
                html = _make_authorize_html(redirect_uri, state)
                await _asgi_send_bytes(send, 200, b"text/html; charset=utf-8", html)
                return

            if path == "/oauth/do_authorize":
                redirect_uri = params.get("redirect_uri", [""])[0]
                state = params.get("state", [""])[0]
                if not redirect_uri:
                    await _asgi_send_bytes(send, 400, b"text/plain", b"Missing redirect_uri")
                    return
                sep = "&" if "?" in redirect_uri else "?"
                location = f"{redirect_uri}{sep}code={_OAUTH_CODE}&state={state}"
                await _asgi_send_redirect(send, location)
                return

            if path == "/oauth/token":
                # Consume request body (required by ASGI protocol), then return token
                while True:
                    msg = await receive()
                    if msg["type"] == "http.request" and not msg.get("more_body"):
                        break
                token_response = json.dumps({
                    "access_token": _OAUTH_ACCESS_TOKEN,
                    "token_type": "bearer",
                    "expires_in": 31536000,
                }).encode()
                await _asgi_send_bytes(send, 200, b"application/json", token_response)
                return

            method = scope.get("method", "GET").upper()

            if path == "/waitlist" and method == "GET":
                await _asgi_send_bytes(send, 200, b"text/html; charset=utf-8", _WAITLIST_HTML)
                return

            if path == "/waitlist" and method == "POST":
                body = b""
                while True:
                    msg = await receive()
                    body += msg.get("body", b"")
                    if not msg.get("more_body"):
                        break
                form = parse_qs(body.decode("utf-8", errors="ignore"))
                email = (form.get("email") or [""])[0].strip()
                if email:
                    add_to_waitlist(email)
                await _asgi_send_bytes(send, 200, b"text/html; charset=utf-8", _WAITLIST_CONFIRMED_HTML)
                return

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

mcp = FastMCP(
    "deeplook",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


@mcp.tool(annotations=ToolAnnotations(title="Full company research report", readOnlyHint=True, destructiveHint=False))
async def deeplook_research(company_name: str) -> str:
    """
    Research a company and return structured financial data with analysis context.

    Returns: Markdown report + structured JSON (between <!-- STRUCTURED_DATA_START --> and <!-- STRUCTURED_DATA_END --> tags) with:
    - Real-time price, financials, valuation metrics (from market data APIs)
    - Peer comparison table (up to 3 comparable companies)
    - Technical indicators (RSI, moving averages, 52-week range)
    - Filtered recent news (priority-ranked, max 8 items)
    - Upcoming catalysts with dates
    - Analysis hooks (observations and questions for deeper investigation)
    - Lightweight verdict (stance, bull/bear case, suggested action)

    A lightweight verdict is included as a starting point, but you should
    form your own judgment from the structured data — especially if you
    notice the data tells a different story than the verdict suggests.

    IMPORTANT FOR AI ASSISTANTS:
    - Always extract and parse the JSON block
    - Present the data as a rich visual dashboard (HTML artifact preferred)
    - Dashboard: header with price, metric cards, verdict box, analysis hooks, news timeline, peer table, catalysts
    - If any JSON field is null or missing, skip that section gracefully
    - The verdict is a lightweight starting point — refine or override with your own analysis
    """
    ip = client_ip_var.get()
    rate_ok, rate_err = _rate_limiter.check_and_record(ip, tool_type="research")
    if not rate_ok:
        _request_logger.info(
            "%s | deeplook_research | %s | — | BLOCKED | %s",
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            company_name, rate_err,
        )
        return rate_err

    t0 = time.monotonic()
    data = await run_research(company_name)
    duration = round(time.monotonic() - t0, 1)

    _request_logger.info(
        "%s | deeplook_research | %s | %ss",
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        company_name, duration,
    )

    return format_dual_output(data)


@mcp.tool(annotations=ToolAnnotations(title="Quick company snapshot", readOnlyHint=True, destructiveHint=False))
async def deeplook_lookup(company_name: str) -> str:
    """
    Quick company snapshot — phase, price, key signal, and verdict
    in 5 lines. Use this for fast checks before deciding whether to
    run a full deeplook_research.

    TIP FOR AI ASSISTANTS: For the best user experience, call deeplook_lookup first to show an instant snapshot while the user waits, then call deeplook_research for the full interactive dashboard.
    """
    ip = client_ip_var.get()
    rate_ok, rate_err = _rate_limiter.check_and_record(ip, tool_type="lookup")
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
        "%s | deeplook_lookup | %s | %ss",
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        company_name, duration,
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
