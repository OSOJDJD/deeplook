import sqlite3
import json
import hashlib
import time
import os
import logging

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "eval", "eval_cache.db")


def _get_db():
    db = sqlite3.connect(DB_PATH)
    db.execute("""
        CREATE TABLE IF NOT EXISTS fetch_cache (
            key TEXT PRIMARY KEY,
            value TEXT,
            timestamp REAL
        )
    """)
    return db


def cache_key(fetcher_name: str, company: str, **kwargs) -> str:
    raw = json.dumps({"fetcher": fetcher_name, "company": company, **kwargs}, sort_keys=True)
    return hashlib.md5(raw.encode()).hexdigest()


def get_cached(key: str, max_age_hours: int = 24):
    """回傳 cached result 或 None（過期、不存在、或 --no-cache 模式）"""
    if os.environ.get("DEEPLOOK_NO_CACHE") == "1":
        return None
    try:
        db = _get_db()
        row = db.execute("SELECT value, timestamp FROM fetch_cache WHERE key = ?", (key,)).fetchone()
        db.close()
        if row and (time.time() - row[1]) < max_age_hours * 3600:
            return json.loads(row[0])
    except Exception as e:
        logger.warning(f"Cache read error: {e}")
    return None


def set_cache(key: str, value):
    """存結果進 cache"""
    try:
        db = _get_db()
        db.execute(
            "INSERT OR REPLACE INTO fetch_cache (key, value, timestamp) VALUES (?, ?, ?)",
            (key, json.dumps(value, default=str), time.time())
        )
        db.commit()
        db.close()
    except Exception as e:
        logger.warning(f"Cache write error: {e}")
