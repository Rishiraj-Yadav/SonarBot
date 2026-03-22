"""Web search tools."""

from __future__ import annotations

from typing import Any

import httpx

from assistant.tools.registry import ToolDefinition


def build_search_tools(config) -> list[ToolDefinition]:
    async def web_search(payload: dict[str, Any]) -> dict[str, Any]:
        query = str(payload["query"])
        limit = int(payload.get("limit", 5))
        if config.tools.brave_api_key:
            results = await _brave_search(config.tools.brave_api_key, query, limit)
        else:
            results = await _duckduckgo_search(query, limit)
        return {"query": query, "results": results}

    return [
        ToolDefinition(
            name="web_search",
            description="Search the web and return concise result snippets.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "default": 5},
                },
                "required": ["query"],
            },
            handler=web_search,
        )
    ]


async def _brave_search(api_key: str, query: str, limit: int) -> list[str]:
    headers = {"X-Subscription-Token": api_key, "Accept": "application/json"}
    params = {"q": query, "count": limit}
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get("https://api.search.brave.com/res/v1/web/search", params=params, headers=headers)
        response.raise_for_status()

    data = response.json()
    results = data.get("web", {}).get("results", [])
    return [
        f"{item.get('title', 'Untitled')} - {item.get('url', '')}\n{item.get('description', '').strip()}"
        for item in results[:limit]
    ]


async def _duckduckgo_search(query: str, limit: int) -> list[str]:
    try:
        from duckduckgo_search import DDGS  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("duckduckgo-search is not installed.") from exc

    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=limit))
    return [
        f"{item.get('title', 'Untitled')} - {item.get('href', '')}\n{item.get('body', '').strip()}"
        for item in results[:limit]
    ]
