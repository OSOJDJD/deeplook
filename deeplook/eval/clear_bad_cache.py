"""清除 eval_cache.db 中 yfinance 失敗結果的 cache entries"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "eval_cache.db")

db = sqlite3.connect(DB_PATH)

rows = db.execute(
    "SELECT COUNT(*) FROM fetch_cache WHERE value LIKE '%yfinance%' AND value LIKE '%\"success\": false%'"
).fetchone()[0]

db.execute(
    "DELETE FROM fetch_cache WHERE value LIKE '%yfinance%' AND value LIKE '%\"success\": false%'"
)
db.commit()
db.close()

print(f"Deleted {rows} bad yfinance cache entries.")
