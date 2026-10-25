"""Runs INSIDE the sandbox container: the yaaf platform tools as a stdio MCP server.

For every harness that reaches tools over MCP rather than in process — a model
CLI the run starts (``claude_cli_entry.py``, ``codex_cli_entry.py``,
``gemini_cli_entry.py``, ``opencode_cli_entry.py``) launches
this as a subprocess and speaks MCP to it over stdin and stdout. Imports ``sandbox_entry`` by bare name,
as ``chat_entry`` does: the image lays the entrypoints out in one directory.

It serves the very server object the Agent SDK harness serves in process —
``create_sdk_mcp_server`` over ``render_sdk(run_tools())`` — rather than a second
rendering of the catalog, so two harnesses cannot drift into describing one
tool two ways or converting one result two ways. The tools are the ones this
agent is sent and no others; the server's 403s are still the only thing that
decides what it may do (docs/RUNTIMES.md).

It also serves yaaf's own file tools (``loop_harness.BUILTINS`` — Read, Write,
Edit, Glob, Grep) to a CLI that has none apart from its shell, when the run
asks for them (``YAAF_SERVE_FILE_TOOLS``): a role granted files but not a shell
is honoured that way on the Codex CLI, rather than by handing it a shell it was
not hired with. And it serves the install's own search (``web_search``) to a
CLI with no search of its own when the run was promised `web` and handed the
backend (``YAAF_SEARCH``; docs/PROVIDERS.md §6) — each query charged to the
run's search ledger, which the driver bills as it goes.

Nothing here may write to stdout: stdout is the protocol.
"""

from __future__ import annotations

import asyncio
import os

import sandbox_entry as base
from mcp.server.stdio import stdio_server


def served() -> list:
    """The tools this server offers: the run's, then the file tools if asked,
    then the install's search where the run was promised it."""
    tools = base.run_tools()
    if os.environ.get("YAAF_SERVE_FILE_TOOLS"):
        import loop_harness

        tools += [loop_harness.BUILTINS[name] for name in loop_harness.FILE_TOOLS]
    if base.SEARCH and base.PERMS.get("web"):
        tools.append(base.Tool(
            "web_search", "Search the web. Returns the top results — titles, addresses and "
            "snippets — to read further with your fetch tool.",
            {"type": "object", "properties": {"query": {"type": "string"}},
             "required": ["query"]}, web_search))
    return tools


async def web_search(args: dict) -> dict:
    query = str(args.get("query", "")).strip()
    if not query:
        return {"content": [{"type": "text", "text": "query is empty"}], "is_error": True}
    try:
        text, bill = await base.platform_search(query)
    except Exception as exc:  # noqa: BLE001 — a failed search is the model's to read
        return {"content": [{"type": "text", "text": f"search failed: {exc}"}],
                "is_error": True}
    base.SearchLedger.charge(bill)
    return {"content": [{"type": "text", "text": text}]}


async def main() -> int:
    server = base.create_sdk_mcp_server(
        name="yaaf", version="1.0.0", tools=base.render_sdk(served()))["instance"]
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
