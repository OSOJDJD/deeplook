"""
Cost protection for DeepLook MCP server.

SQLite-backed per-IP daily limits:
  - lookup:   5 per IP per day
  - research: 2 per IP per day

Resets at UTC 00:00.
"""

import os
import sqlite3
import threading
from contextvars import ContextVar
from datetime import datetime, timezone

# Passed from ASGI middleware to tool handlers via context variable
client_ip_var: ContextVar[str] = ContextVar("client_ip", default="unknown")

# ── Config (env-overridable) ───────────────────────────────────────────────────
DAILY_LOOKUP_LIMIT: int = int(os.environ.get("DEEPLOOK_DAILY_LOOKUP_LIMIT", "5"))
DAILY_RESEARCH_LIMIT: int = int(os.environ.get("DEEPLOOK_DAILY_RESEARCH_LIMIT", "2"))

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = os.path.join(_PROJECT_ROOT, "data")
_DB_PATH = os.path.join(_DATA_DIR, "rate_limit.db")

_LIMIT_REACHED_MSG = (
    '{"error": "Daily free limit reached (5 lookups + 2 research reports per day). '
    'DeepLook Pro coming soon \u2014 go deeper. '
    'Join waitlist: https://deeplook.dev/waitlist"}'
)

_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    os.makedirs(_DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ip_usage (
            ip TEXT NOT NULL,
            date TEXT NOT NULL,
            lookup_count INTEGER NOT NULL DEFAULT 0,
            research_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (ip, date)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS waitlist (
            email TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


# Module-level connection (created lazily, protected by _lock)
_conn: sqlite3.Connection | None = None


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = _get_conn()
    return _conn


class RateLimiter:
    def check_and_record(self, ip: str, tool_type: str = "research") -> tuple[bool, str]:
        """
        Check per-IP daily limit for the given tool_type ("lookup" or "research").
        Returns (allowed, error_message). error_message is "" when allowed.
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        col = "lookup_count" if tool_type == "lookup" else "research_count"
        limit = DAILY_LOOKUP_LIMIT if tool_type == "lookup" else DAILY_RESEARCH_LIMIT

        with _lock:
            try:
                db = _db()
                db.execute(
                    """
                    INSERT INTO ip_usage (ip, date, lookup_count, research_count)
                    VALUES (?, ?, 0, 0)
                    ON CONFLICT(ip, date) DO NOTHING
                    """,
                    (ip, today),
                )
                row = db.execute(
                    f"SELECT {col} FROM ip_usage WHERE ip = ? AND date = ?",
                    (ip, today),
                ).fetchone()
                current = row[0] if row else 0

                if current >= limit:
                    return False, _LIMIT_REACHED_MSG

                db.execute(
                    f"UPDATE ip_usage SET {col} = {col} + 1 WHERE ip = ? AND date = ?",
                    (ip, today),
                )
                db.commit()
                return True, ""

            except Exception:
                # DB failure → fail open so a bad disk doesn't kill the service
                return True, ""


def add_to_waitlist(email: str) -> None:
    """Insert an email into the waitlist table."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _lock:
        try:
            db = _db()
            db.execute("INSERT INTO waitlist (email, created_at) VALUES (?, ?)", (email, now))
            db.commit()
        except Exception:
            pass
