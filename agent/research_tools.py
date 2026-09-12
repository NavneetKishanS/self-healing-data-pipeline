"""Exa Search API adapter; one bounded request, no automatic retries."""

import os
import httpx

from .settings import configured, positive_number


def search_repair_docs(query: str) -> dict:
    if not configured("EXA_API_KEY"):
        return {"status": "unavailable", "sources": [], "error": "missing EXA_API_KEY"}
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        return {"status": "unavailable", "sources": [], "error": "invalid query"}
    try:
        response = httpx.post(
            "https://api.exa.ai/search",
            headers={"x-api-key": os.environ["EXA_API_KEY"]},
            json={"query": query, "type": "auto", "numResults": 3,
                  "contents": {"text": {"maxCharacters": 2000}}},
            timeout=positive_number("EXA_TIMEOUT_SECONDS", 15), follow_redirects=False,
        )
        response.raise_for_status()
        sources = []
        for item in response.json()["results"][:3]:
            title, url, excerpt = item.get("title", ""), item.get("url", ""), item.get("text", "")
            if not all(isinstance(value, str) for value in (title, url, excerpt)):
                raise ValueError("Invalid Exa source")
            if url.startswith(("https://", "http://")):
                sources.append({"title": title[:300], "url": url[:2048], "excerpt": excerpt[:2000]})
        return {"status": "ok", "sources": sources, "error": None}
    except Exception as exc:
        code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else type(exc).__name__
        return {"status": "unavailable", "sources": [], "error": f"Exa request failed ({code})"}
