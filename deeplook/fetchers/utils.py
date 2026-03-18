import asyncio
import os

import httpx
from duckduckgo_search import AsyncDDGS
import logging

logger = logging.getLogger(__name__)


async def _collect(gen_or_list) -> list:
    """Handle both async generators and plain lists from DDGS."""
    if hasattr(gen_or_list, '__aiter__'):
        results = []
        async for r in gen_or_list:
            results.append(r)
        return results
    return list(gen_or_list)


async def ddgs_with_timeout(query: str, search_type: str = "text", max_results: int = 5, timeout: int = 10) -> list:
    async def _fetch():
        try:
            async with AsyncDDGS() as ddgs:
                if search_type == "text":
                    return await _collect(ddgs.text(query, max_results=max_results))
                elif search_type == "news":
                    return await _collect(ddgs.news(query, max_results=max_results))
                else:
                    return []
        except Exception as e:
            logger.warning(f"DDGS error for '{query}': {e}")
            return []

    try:
        return await asyncio.wait_for(_fetch(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.error(f"DDGS timeout ({timeout}s) for: '{query}'")
        return []


def _normalize_ddg(results: list) -> list[dict]:
    """Normalize DDG results to unified format."""
    normalized = []
    for r in results:
        normalized.append({
            "title": r.get("title", ""),
            "url": r.get("url", "") or r.get("href", ""),
            "snippet": r.get("body", ""),
            "date": r.get("date", ""),
        })
    return normalized


async def _search_tavily(query: str, search_type: str, max_results: int, timeout: int, api_key: str) -> list[dict]:
    topic = "news" if search_type == "news" else "general"
    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": "basic",
        "topic": topic,
        "max_results": max_results,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post("https://api.tavily.com/search", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("content", ""),
                    "date": r.get("published_date", ""),
                }
                for r in data.get("results", [])
            ]
    except Exception as e:
        logger.warning(f"Tavily error for '{query}': {e}")
        return []


async def robust_search(query: str, search_type: str = "text", max_results: int = 5, timeout: int = 10) -> list[dict]:
    """DuckDuckGo → Tavily fallback. Returns list of {title, url, snippet, date}."""
    ddg_results = await ddgs_with_timeout(query, search_type, max_results, timeout)
    if ddg_results:
        return _normalize_ddg(ddg_results)

    print(f"[search] '{query}' -> DDG failed, trying Tavily...", flush=True)
    tavily_key = os.getenv("TAVILY_API_KEY")
    if not tavily_key:
        print(f"[search] Tavily skipped (TAVILY_API_KEY not set)", flush=True)
        return []

    results = await _search_tavily(query, search_type, max_results, timeout, tavily_key)
    print(f"[search] Tavily result: {len(results)} items", flush=True)
    return results
