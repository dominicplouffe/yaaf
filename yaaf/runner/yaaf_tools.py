"""Runs INSIDE the sandbox container: the yaaf platform tools as a stdio MCP server.

For every harness that reaches tools over MCP rather than in process — a model
CLI the run starts (``claude_cli_entry.py``) launches this as a subprocess and
speaks MCP to it over stdin and stdout. Imports ``sandbox_entry`` by bare name,
as ``chat_entry`` does: the image lays the entrypoints out in one directory.

It serves the very server object the Agent SDK harness serves in process —
``create_sdk_mcp_server`` over ``render_sdk(run_tools())`` — rather than a second
rendering of the catalog, so two harnesses cannot drift into describing one
tool two ways or converting one result two ways. The tools are the ones this
agent is sent and no others; the server's 403s are still the only thing that
decides what it may do (docs/RUNTIMES.md).

Nothing here may write to stdout: stdout is the protocol.
"""

from __future__ import annotations

import asyncio

import sandbox_entry as base
from mcp.server.stdio import stdio_server


async def main() -> int:
    server = base.create_sdk_mcp_server(
        name="yaaf", version="1.0.0", tools=base.render_sdk(base.run_tools()))["instance"]
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
