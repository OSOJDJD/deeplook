"""
Search Intelligence Layer — 7 decision layers before calling external resources.

Pure Python, no LLM, no external API calls.
"""

from datetime import datetime, timezone


# ─────────────────────────────────────────────
# Layer 1: Search Query Strategy（搜什麼）
# ─────────────────────────────────────────────

def build_search_queries(company_name: str, company_type: str,
                          resolved_ticker: str | None,
                          company_full_name: str | None = None) -> dict:
    """為每個 fetcher 生成優化的 search query"""
    current_year = datetime.now().year
    ticker = resolved_ticker or company_name
    # Use full company name for natural-language queries (e.g. "Coherent Corp" not just "COHR")
    display_name = company_full_name or company_name

    print(f"[strategy] build_search_queries: type={company_type} ticker={ticker} display={display_name}")

    if company_type == "public_equity":
        return {
            "news": [
                f"{ticker} earnings revenue {current_year}",
                f"{display_name} technology product growth market share",
                f"{display_name} guidance analyst price target {current_year}",
            ],
            "youtube": f"{display_name} earnings call {current_year}",
            "website": company_name,
            "wikipedia": display_name,
        }

    elif company_type == "crypto":
        return {
            "news": [
                f"{company_name} TVL token {current_year}",
                f"{company_name} partnership integration {current_year}",
                f"{company_name} upgrade development roadmap",
            ],
            "youtube": f"{company_name} ecosystem update {current_year}",
            "website": company_name,
            "wikipedia": company_name,
        }

    elif company_type in ("private", "private_or_unlisted"):
        return {
            "news": [
                f"{company_name} funding valuation round {current_year}",
                f"{company_name} product launch revenue users",
                f"{company_name} IPO merger acquisition listing",
            ],
            "youtube": f"{company_name} product demo {current_year}",
            "website": company_name,
            "wikipedia": company_name,
        }

    elif company_type == "defunct":
        return {
            "news": [
                f"{company_name} collapse bankruptcy what happened",
                f"{company_name} history timeline rise fall",
            ],
            "youtube": f"{company_name} collapse explained",
            "website": company_name,
            "wikipedia": company_name,
        }

    elif company_type == "venture_capital":
        return {
            "news": [
                f"{company_name} investment portfolio {current_year}",
                f"{company_name} fund raise exit token launch",
                f"{company_name} LP returns performance fund close",
            ],
            "youtube": f"{company_name} partner interview {current_year}",
            "website": company_name,
            "wikipedia": company_name,
        }

    elif company_type == "exchange":
        return {
            "news": [
                f"{company_name} trading volume market share {current_year}",
                f"{company_name} license regulation fine {current_year}",
                f"{company_name} investors shareholders funding history",
            ],
            "youtube": f"{company_name} CEO interview {current_year}",
            "website": company_name,
            "wikipedia": company_name,
        }

    elif company_type == "foundation":
        # Search blockchain name (not foundation entity) for on-chain data
        blockchain_name = (company_name
                           .replace(" Foundation", "")
                           .replace(" Protocol", "")
                           .strip())
        return {
            "news": [
                f"{blockchain_name} TVL ecosystem {current_year}",
                f"{blockchain_name} developer dApp growth {current_year}",
                f"{company_name} board leadership change",
            ],
            "youtube": f"{blockchain_name} ecosystem update {current_year}",
            "website": company_name,
            "wikipedia": blockchain_name,  # search blockchain, not foundation
        }

    # fallback
    print(f"[strategy] unknown company_type={company_type!r}, using fallback queries")
    return {
        "news": [f"{company_name} {current_year}"],
        "youtube": f"{company_name} {current_year}",
        "website": company_name,
        "wikipedia": company_name,
    }


# ─────────────────────────────────────────────
# Layer 2: Source Selection（問誰）
# ─────────────────────────────────────────────

def get_active_fetchers(company_type: str) -> dict:
    """回傳每個 fetcher 是否啟用"""
    config = {
        "public_equity": {
            "website":    True,
            "news":       True,
            "wikipedia":  True,
            "yfinance":   True,
            "youtube":    True,
            "coingecko":  False,
            "rootdata":   False,
            "defillama":  False,
            "sec_edgar":  True,
            "finnhub":    True,
        },
        "crypto": {
            "website":    True,
            "news":       True,
            "wikipedia":  True,
            "yfinance":   False,
            "youtube":    True,
            "coingecko":  True,
            "rootdata":   True,
            "defillama":  True,
            "sec_edgar":  False,
            "finnhub":    False,
        },
        "private": {
            "website":    True,
            "news":       True,
            "wikipedia":  True,
            "yfinance":   False,
            "youtube":    True,
            "coingecko":  False,
            "rootdata":   True,
            "defillama":  False,
            "sec_edgar":  False,
            "finnhub":    False,
        },
        "private_or_unlisted": {
            "website":    True,
            "news":       True,
            "wikipedia":  True,
            "yfinance":   False,
            "youtube":    True,
            "coingecko":  False,
            "rootdata":   True,
            "defillama":  False,
            "sec_edgar":  False,
            "finnhub":    False,
        },
        "defunct": {
            "website":    False,
            "news":       True,
            "wikipedia":  True,
            "yfinance":   False,
            "youtube":    False,
            "coingecko":  False,
            "rootdata":   False,
            "defillama":  False,
            "sec_edgar":  False,
            "finnhub":    False,
        },
        "venture_capital": {
            "website":    True,
            "news":       True,
            "wikipedia":  True,
            "yfinance":   False,
            "youtube":    True,
            "coingecko":  False,
            "rootdata":   True,
            "defillama":  False,
            "sec_edgar":  False,
            "finnhub":    False,
        },
        "exchange": {
            "website":    True,
            "news":       True,
            "wikipedia":  True,
            "yfinance":   False,
            "youtube":    True,
            "coingecko":  True,
            "rootdata":   False,
            "defillama":  False,
            "sec_edgar":  False,
            "finnhub":    False,
        },
        "foundation": {
            "website":    True,
            "news":       True,
            "wikipedia":  True,
            "yfinance":   False,
            "youtube":    True,
            "coingecko":  True,
            "rootdata":   True,
            "defillama":  True,
            "sec_edgar":  False,
            "finnhub":    False,
        },
    }
    result = config.get(company_type, {
        "website": True, "news": True, "wikipedia": True,
        "yfinance": True, "youtube": True, "coingecko": True, "rootdata": True,
        "sec_edgar": False, "finnhub": False,
    })
    active_list = [k for k, v in result.items() if v]
    print(f"[strategy] active fetchers for {company_type}: {active_list}")
    return result


# ─────────────────────────────────────────────
# Layer 3: Time Filtering（要多新的）
# ─────────────────────────────────────────────

def get_time_limits() -> dict:
    """每個 fetcher 的時間限制（天），None = 不限"""
    return {
        "news":      90,    # 最多 3 個月
        "youtube":   180,   # 最多 6 個月
        "wikipedia": None,
        "website":   None,
        "yfinance":  None,
        "coingecko":  None,
        "rootdata":   None,
        "defillama":  None,
        "sec_edgar":  None,
        "finnhub":    None,
    }


# ─────────────────────────────────────────────
# Layer 4: Result Validation（回來的對不對）
# ─────────────────────────────────────────────

def validate_result(fetcher_name: str, company_name: str, result: dict) -> bool:
    """驗證 fetcher 結果是否跟查詢公司相關"""
    if fetcher_name == "yfinance":
        data = result.get("data") or {}
        short_name = data.get("shortName") or data.get("longName") or ""
        if not short_name:
            return True  # 沒有 shortName，無法驗證，保留
        # 如果公司名本身就是 ticker（全大寫且 <= 5 字），跳過驗證
        if company_name.isupper() and len(company_name) <= 5:
            return True
        company_words = set(company_name.lower().split())
        name_words = set(short_name.lower().replace(".", " ").split())
        if not company_words.intersection(name_words):
            print(f"[strategy] REJECTED yfinance: '{company_name}' resolved to '{short_name}' — no name overlap")
            return False
        print(f"[strategy] VALIDATED yfinance: '{company_name}' -> '{short_name}'")
    return True


# ─────────────────────────────────────────────
# Layer 5: Timeout & Cost Control（花多少資源）
# ─────────────────────────────────────────────

def get_fetcher_limits() -> dict:
    """每個 fetcher 的資源限制"""
    return {
        "website":   {"timeout_seconds": 10, "max_items": 1},
        "news":      {"timeout_seconds": 15, "max_items": 15},
        "wikipedia": {"timeout_seconds": 10, "max_items": 1},
        "yfinance":  {"timeout_seconds": 10, "max_items": 1},
        "youtube":   {"timeout_seconds": 15, "max_items": 3,
                      "transcript_timeout": 10},
        "coingecko":  {"timeout_seconds": 10, "max_items": 1},
        "rootdata":   {"timeout_seconds": 10, "max_items": 1},
        "defillama":  {"timeout_seconds": 10, "max_items": 1},
        "sec_edgar":  {"timeout_seconds": 30, "max_items": 1},
        "finnhub":    {"timeout_seconds": 15, "max_items": 1},
    }


# ─────────────────────────────────────────────
# Layer 6: Deduplication（重複的怎麼辦）
# ─────────────────────────────────────────────

def deduplicate_news(articles: list[dict]) -> list[dict]:
    """合併講同一件事的新聞（title 字重疊 >60% 視為重複）"""
    seen = []
    for article in sorted(articles, key=lambda x: x.get("date", ""), reverse=False):
        is_dup = False
        for s in seen:
            words_a = set(article.get("title", "").lower().split())
            words_b = set(s.get("title", "").lower().split())
            if len(words_a) > 0 and len(words_b) > 0:
                overlap = len(words_a & words_b) / min(len(words_a), len(words_b))
                if overlap > 0.6:
                    s["also_reported_by"] = s.get("also_reported_by", 1) + 1
                    is_dup = True
                    break
        if not is_dup:
            seen.append(article)

    deduped = len(articles) - len(seen)
    if deduped > 0:
        print(f"[strategy] dedup: removed {deduped} duplicate articles, kept {len(seen)}")
    return seen


# ─────────────────────────────────────────────
# Layer 7: Priority Ranking（先用哪個結果）
# 3D scoring: Source Tier + Recency + Relevance
# ─────────────────────────────────────────────

SOURCE_TIERS = {
    # Tier 1: 頂級財經媒體 + 官方來源 (1.0)
    "sec.gov": 1.0, "investor.": 1.0, "ir.": 1.0,
    "bloomberg.com": 1.0, "reuters.com": 1.0,
    # Tier 2: Crypto 一手媒體 (0.7)
    "coindesk.com": 0.7, "theblock.co": 0.7,
    # Tier 3: 區域財經 + 通用科技 / Crypto 二手媒體 (0.5)
    "asia.nikkei.com": 0.5, "digitimes.com": 0.5,
    "techcrunch.com": 0.5, "venturebeat.com": 0.5,
    "decrypt.co": 0.5, "cointelegraph.com": 0.5,
    # Tier 4: 聚合 / 二手來源 (0.2)
    "msn.com": 0.2, "yahoo.com": 0.2,
    "analyticsinsight.net": 0.2, "invezz.com": 0.2,
}


def score_article(article: dict, company_name: str) -> float:
    """三維評分：Source Tier (0.3) + Recency (0.4) + Relevance (0.3)"""

    # Dimension 1: Source Tier (weight: 0.3)
    url = article.get("url", "").lower()
    source_score = 0.3  # 未知來源 default
    for domain, score in SOURCE_TIERS.items():
        if domain in url:
            source_score = score
            break

    # Dimension 2: Recency (weight: 0.4)
    # 今天=1.0, 一週前=0.7, 一個月前=0.3, 更久=0.1
    try:
        date_str = article.get("date", "").replace("Z", "+00:00")
        article_date = datetime.fromisoformat(date_str)
        # Make timezone-aware if naive
        if article_date.tzinfo is None:
            article_date = article_date.replace(tzinfo=timezone.utc)
        days_ago = (datetime.now(timezone.utc) - article_date).days
        if days_ago <= 1:
            recency_score = 1.0
        elif days_ago <= 7:
            recency_score = 0.7
        elif days_ago <= 30:
            recency_score = 0.3
        else:
            recency_score = 0.1
    except Exception:
        recency_score = 0.3  # 無法解析日期

    # Dimension 3: Relevance (weight: 0.3)
    # 標題包含公司名=1.0, 內容提到=0.5, 都沒有=0.1
    title = article.get("title", "").lower()
    content = article.get("content", "").lower()
    company_lower = company_name.lower()
    if company_lower in title:
        relevance_score = 1.0
    elif company_lower in content:
        relevance_score = 0.5
    else:
        relevance_score = 0.1

    final = 0.3 * source_score + 0.4 * recency_score + 0.3 * relevance_score
    return round(final, 3)


def rank_articles(articles: list[dict], company_name: str) -> list[dict]:
    """按三維評分排序新聞（高分在前）"""
    for a in articles:
        a["_priority_score"] = score_article(a, company_name)

    ranked = sorted(articles, key=lambda a: a["_priority_score"], reverse=True)

    for a in ranked[:5]:
        print(f"[rank] {a['_priority_score']:.2f} | {a.get('title', '')[:60]}")

    return ranked
