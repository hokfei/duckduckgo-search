# DuckDuckGo Search MCP

Web search for AI assistants without an API key, browser, or paid dependency.

Built because the built-in `websearch` tool fails with HTTP 402 in many setups,
and the usual alternatives (Brave, Tavily, Serper) all require credentials.
This uses DuckDuckGo's no-JavaScript HTML endpoint instead.

## Tools

| Tool | Purpose |
| ---- | ------- |
| `web_search` | Full web search: titles, URLs, snippets. **Rate limited** — see below. |
| `instant_answer` | Short encyclopedic description of a named entity. Much more reliable. |
| `search_health` | Whether search is usable, or how long a block lasts. |

## Rate limiting

DuckDuckGo rate limits the HTML endpoint aggressively, and the block is **IP-level**.

Typical behavior: the first 1-2 queries succeed quickly. A third query within a few seconds returns an HTTP 202 anomaly page. The block then persists for **several minutes** and affects all clients from the same IP (confirmed with `curl`).

Consequences:

- Space queries. The client enforces a **3-second minimum interval** and a
  30-second backoff after any block.
- A block raises `SearchBlocked` and surfaces as a tool error. It is never
  reported as "no results" — those are very different things to a caller.
- An agent that fires several searches in one turn will get blocked. Treat
  `web_search` as best-effort.

`instant_answer` uses a different endpoint that **kept answering while the HTML
endpoint was blocked**. It only knows named entities — libraries, tools,
concepts — but it is the more dependable of the two.

For a URL you already know, prefer your environment's built-in `fetch`. This
exists for the gap `fetch` cannot cover: finding a page you don't have.

## Setup

Requires Python 3.11+.

```bash
# 1. Create virtual environment
python -m venv .venv

# 2. Install in development mode
.venv/bin/python -m pip install -e ".[dev]"
# or on Windows:
.venv\Scripts\python -m pip install -e ".[dev]"
```

Register the server in your MCP client configuration (e.g., `~/.opencode/mcp.json`):

```json
"duckduckgo-search": {
  "command": "/path/to/duckduckgo-search/.venv/bin/python",
  "args": ["-m", "ddg_search"]
}
```

Adjust the command path to your environment (use `.venv/bin/python` on Unix,
`.venv\Scripts\python.exe` on Windows).

## Development

```bash
pytest tests            # 19 tests, offline
pytest tests -m integration   # hits the live endpoint; can be blocked
ruff check src tests
python probe_server.py  # drive a real server over stdio
```

Parsing is unit tested against captured HTML; the network is only touched by
the `integration` marked tests, which are excluded by default.

## License

MIT