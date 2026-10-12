"""MCP server exposing DuckDuckGo web search.

Exists because the built-in websearch tool fails with HTTP 402 in this setup and
paid search APIs need credentials. This needs none.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool
from pydantic import BaseModel, Field

from ddg_search.client import DuckDuckGoClient, instant

logger = logging.getLogger(__name__)

SERVER_NAME = "duckduckgo-search"

TOOLS = [
    {
        "name": "web_search",
        "description": (
            "Search the web via DuckDuckGo and get titles, URLs and snippets. "
            "Use this when you need to find a page whose URL you do not know. "
            "For a page whose URL you already have, prefer fetching it directly."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search terms, e.g. 'sqlite vec0 windows error'",
                },
                "limit": {
                    "type": "integer",
                    "default": 8,
                    "minimum": 1,
                    "maximum": 20,
                    "description": "How many results to return",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "instant_answer",
        "description": (
            "Look up a short encyclopedic description of a named thing - a "
            "library, tool, language, or concept. More reliable than web_search "
            "because it uses a less restricted endpoint, but it cannot find "
            "arbitrary web pages. Try this first when a question starts with "
            "'what is'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The thing to describe, e.g. 'llama.cpp'",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_health",
        "description": (
            "Check whether web search is currently usable. DuckDuckGo rate "
            "limits bursts, so this reports whether a recent block is cooling "
            "down."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


class WebSearchParams(BaseModel):
    """Parameters for web_search."""

    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=8, ge=1, le=20)


class DuckDuckGoServer:
    """Wires the client to MCP tool calls."""

    def __init__(self, client: DuckDuckGoClient | None = None) -> None:
        self.client = client or DuckDuckGoClient()
        self.server = Server(
            SERVER_NAME,
            on_list_tools=self.list_tools,
            on_call_tool=self.call_tool,
        )

    async def list_tools(
        self, context: Any = None, params: Any = None
    ) -> ListToolsResult:
        return ListToolsResult(tools=[Tool(**t) for t in TOOLS])

    async def call_tool(
        self, context: Any = None, params: Any = None
    ) -> CallToolResult:
        name = getattr(params, "name", None)
        arguments = getattr(params, "arguments", None) or {}

        try:
            if name == "web_search":
                return await self._search(arguments)
            if name == "instant_answer":
                return await self._instant(arguments)
            if name == "search_health":
                return self._health()
            return _error(
                f"Unknown tool: {name}",
                hint=f"Available tools: {', '.join(t['name'] for t in TOOLS)}",
            )
        except Exception as e:
            logger.exception("Tool %s failed", name)
            return _error(f"{type(e).__name__}: {e}")

    async def _search(self, arguments: dict[str, Any]) -> CallToolResult:
        p = WebSearchParams(**arguments)

        # Network calls run in a worker thread so the event loop stays free.
        results = await asyncio.to_thread(self.client.search, p.query, p.limit)

        payload = {
            "query": p.query,
            "result_count": len(results),
            "results": [
                {"title": r.title, "url": r.url, "snippet": r.snippet}
                for r in results
            ],
        }
        return _json(payload)

    async def _instant(self, arguments: dict[str, Any]) -> CallToolResult:
        """Fetch an encyclopedic answer about a named thing."""
        query = str(arguments.get("query", "")).strip()
        if not query:
            return _error("query is required")

        answer = await asyncio.to_thread(instant, query)

        return _json(
            {
                "query": query,
                "heading": answer.heading,
                "answer": answer.abstract,
                "source": answer.abstract_source,
                "source_url": answer.abstract_url,
                "related": [
                    {"topic": topic, "url": url} for topic, url in answer.related[:8]
                ],
                "note": (
                    ""
                    if answer.has_answer
                    else "No encyclopedic entry for that term. "
                    "Use web_search to find web pages."
                ),
            }
        )

    def _health(self) -> CallToolResult:
        return _json(
            {
                "status": "rate_limited" if self.client.is_cooling_down else "ok",
                "cooldown_seconds_remaining": round(self.client.cooldown_remaining),
                "min_interval_seconds": self.client.min_interval,
                "endpoint": self.client.endpoint,
            }
        )

    async def run(self) -> None:
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream, write_stream, self.server.create_initialization_options()
            )


def _json(payload: Any) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, indent=2))]
    )


def _error(message: str, hint: str | None = None) -> CallToolResult:
    payload: dict[str, Any] = {"error": message}
    if hint:
        payload["hint"] = hint
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, indent=2))],
        isError=True,
    )


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    await DuckDuckGoServer().run()


if __name__ == "__main__":
    asyncio.run(main())
