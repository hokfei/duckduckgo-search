# DuckDuckGo Search MCP

Web search for OpenCode without an API key, browser, or paid dependency.

Built because the built-in `websearch` tool fails with HTTP 402 in this setup,
and the usual alternatives both need credentials. This uses DuckDuckGo's
no-JavaScript endpoint instead.

## Tools

| Tool | Purpose |
| ---- | ------- |
| `web_search` | Full web search: titles, URLs, snippets. **Rate limited** — see below. |
| `instant_answer` | Short encyclopedic description of a named thing. Much more reliable. |
| `search_health` | Whether search is usable, or how long a block lasts. |

## Rate limiting — read this

DuckDuckGo rate limits the HTML endpoint aggressively, and the block is
**IP-level**.

Measured on this machine: the first two queries succeeded at ~0.7s each. The
third, one second later, returned an HTTP 202 anomaly page. The block then
persisted for **over three minutes** and survived `curl` as well as this client,
confirming it is not a client bug.

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

For a URL you already know, prefer OpenCode's built-in `fetch`. This exists for
the gap `fetch` cannot cover: finding a page you don't have.

## Setup

Already installed. To rebuild:

```bash
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

Registered in `~/.opencode/mcp.json`:

```json
"duckduckgo-search": {
  "command": "C:\\Users\\you\\opencode-tools\\duckduckgo-search\\.venv\\Scripts\\python.exe",
  "args": ["-m", "ddg_search"]
}
```

## Development

```bash
pytest tests            # 19 tests, offline
pytest tests -m integration   # hits the live endpoint; can be blocked
ruff check src tests
python probe_server.py  # drive a real server over stdio
```

Parsing is unit tested against captured HTML; the network is only touched by
the `integration` marked tests, which are excluded by default.