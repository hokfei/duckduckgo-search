"""DuckDuckGo search as an MCP server.

No API key, no browser, no paid dependency. DuckDuckGo's no-JavaScript endpoint
is rate limited, so this server paces itself and reports blocks clearly instead
of returning empty results.
"""

from ddg_search.client import (
    DuckDuckGoClient,
    Result,
    SearchBlocked,
    SearchUnavailable,
)

__all__ = [
    "DuckDuckGoClient",
    "Result",
    "SearchBlocked",
    "SearchUnavailable",
]
