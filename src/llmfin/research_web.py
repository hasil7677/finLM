"""
research_web.py
───────────────
The "why is it moving" layer — news/catalyst lookup via the Tavily search API.

This is the one job the LLM genuinely does better than pandas: a stock gapping
4% on an earnings beat is a different trade than one gapping 4% on nothing.
The scanner finds WHAT is moving; this finds WHY.

Requires TAVILY_API_KEY (free tier: https://tavily.com). Degrades with a
clear, actionable error when unset.
"""

from __future__ import annotations

import os
from typing import Any

import requests

TAVILY_URL = "https://api.tavily.com/search"


def research_symbol(symbol: str, company_name: str = "", days: int = 3, max_results: int = 5) -> dict[str, Any]:
    """Search recent news for an NSE symbol. Returns Tavily's synthesized
    answer plus the top sources, ready for the LLM to weigh."""
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        return {
            "error": (
                "TAVILY_API_KEY is not set. Get a free key at https://tavily.com "
                "and add it to your .env — the research_symbol tool needs it. "
                "The scanner and signal tools work without it."
            )
        }

    query = f"{company_name or symbol} NSE stock news why moving"
    resp = requests.post(
        TAVILY_URL,
        json={
            "api_key": api_key,
            "query": query,
            "topic": "news",
            "days": days,
            "search_depth": "basic",
            "max_results": max_results,
            "include_answer": True,
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    return {
        "symbol": symbol.upper(),
        "query": query,
        "synthesized_answer": data.get("answer"),
        "sources": [
            {
                "title": r.get("title"),
                "url": r.get("url"),
                "published": r.get("published_date"),
                "snippet": (r.get("content") or "")[:400],
            }
            for r in data.get("results", [])
        ],
    }
