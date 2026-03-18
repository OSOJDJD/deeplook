"""
Cost protection for DeepLook MCP server.

Layer 1: Per-IP rate limit  — 10 requests / IP / hour (in-memory)
Layer 2: Monthly spending cap — $50/month tracked in data/monthly_usage.json
"""

import json
import os
import time
from collections import defaultdict
from contextvars import ContextVar
from datetime import datetime, timezone

# Passed from ASGI middleware to tool handlers via context variable
client_ip_var: ContextVar[str] = ContextVar("client_ip", default="unknown")

# ── Config (env-overridable) ───────────────────────────────────────────────────
_RATE_LIMIT_PER_IP: int = int(os.environ.get("DEEPLOOK_RATE_LIMIT_PER_IP", "10"))
_MONTHLY_CAP_USD: float = float(os.environ.get("DEEPLOOK_MONTHLY_CAP_USD", "50"))
_COST_PER_REPORT: float = float(os.environ.get("DEEPLOOK_COST_PER_REPORT", "0.05"))

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = os.path.join(_PROJECT_ROOT, "data")
_USAGE_FILE = os.path.join(_DATA_DIR, "monthly_usage.json")


class RateLimiter:
    def __init__(self) -> None:
        # IP → list of request timestamps (epoch seconds, last 1 hour)
        self._ip_timestamps: dict[str, list[float]] = defaultdict(list)

    def check_and_record(self, ip: str) -> tuple[bool, str]:
        """
        Check both rate limits and record the request on success.
        Returns (allowed, error_message). error_message is "" when allowed.
        """
        # Layer 1: per-IP hourly limit (free, in-memory)
        allowed, err = self._check_ip(ip)
        if not allowed:
            return False, err

        # Layer 2: monthly spending cap (file-backed)
        allowed, err = self._check_and_record_monthly()
        if not allowed:
            # Roll back the IP timestamp we just appended
            if self._ip_timestamps[ip]:
                self._ip_timestamps[ip].pop()
            return False, err

        return True, ""

    # ── Private ────────────────────────────────────────────────────────────────

    def _check_ip(self, ip: str) -> tuple[bool, str]:
        now = time.time()
        cutoff = now - 3600.0

        # Evict timestamps older than 1 hour
        self._ip_timestamps[ip] = [t for t in self._ip_timestamps[ip] if t > cutoff]

        if len(self._ip_timestamps[ip]) >= _RATE_LIMIT_PER_IP:
            return False, (
                f"Rate limited: max {_RATE_LIMIT_PER_IP} requests per hour per IP. "
                "Please wait."
            )

        self._ip_timestamps[ip].append(now)
        return True, ""

    def _check_and_record_monthly(self) -> tuple[bool, str]:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        try:
            os.makedirs(_DATA_DIR, exist_ok=True)

            try:
                with open(_USAGE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                data = {}

            # Reset counter when month rolls over
            if data.get("month") != month:
                data = {"month": month, "count": 0, "estimated_spend": 0.0}

            if data["estimated_spend"] >= _MONTHLY_CAP_USD:
                return False, (
                    "Monthly capacity reached. Resets on the 1st. "
                    "Self-host for unlimited use: github.com/OSOJDJD/deeplook"
                )

            data["count"] += 1
            data["estimated_spend"] = round(data["count"] * _COST_PER_REPORT, 2)

            with open(_USAGE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f)

            return True, ""

        except Exception:
            # File I/O failure → fail open so a bad disk doesn't kill the service
            return True, ""
