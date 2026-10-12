"""Command-line entry point.

Serves the MCP protocol on stdio, which is how OpenCode runs this.

    python -m ddg_search --help    show usage and exit
    python -m ddg_search           serve on stdio (the usual case)

Without a subcommand the server waits for an MCP client, so a bare invocation
blocks by design. Anything other than an explicit --help or --version is
treated as "serve".
"""

import argparse
import asyncio
import sys

from ddg_search.server import SERVER_NAME, main

__version__ = "0.1.0"


def build_parser() -> argparse.ArgumentParser:
    """Parse the handful of flags that do not require a running server."""
    parser = argparse.ArgumentParser(
        prog="ddg_search",
        description=(
            "Web search MCP server backed by DuckDuckGo's no-JS endpoint. "
            "With no arguments it serves on stdio and waits for a client."
        ),
    )
    parser.add_argument("--version", action="version", version=f"{SERVER_NAME} {__version__}")
    return parser


def main_() -> int:
    """Handle flags, then serve."""
    build_parser().parse_args()
    asyncio.run(main())
    return 0


if __name__ == "__main__":
    sys.exit(main_())
