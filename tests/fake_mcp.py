"""A data source's server, faked at the transport: the account an agent's calls land on.

Every request the platform makes to a data source — the save's check, discovery,
a forwarded call — goes through `datasource_tools.TRANSPORT`, so one of these
stood in there is the far end of every door. It records what reached it, which
is the half of every permission test that matters: a refused call is one that
never arrives here.
"""

from __future__ import annotations

import copy
import json

import httpx

READ_TOOLS = [
    {"name": "get_stats", "description": "Read campaign statistics.",
     "inputSchema": {"type": "object", "properties": {"days": {"type": "integer"}}}},
    {"name": "get_table_fields", "description": "List the fields of a table.",
     "annotations": {"readOnlyHint": True}},
]
WRITE_TOOLS = [
    {"name": "create_campaigns", "description": "Create campaigns on the account.",
     "inputSchema": {"type": "object", "properties": {"budget": {"type": "number"}}}},
    {"name": "pricing_actions", "description": "Apply pricing changes.",
     "annotations": {"readOnlyHint": False}},
]


class FakeVendor:
    def __init__(self, tools: list[dict] | None = None):
        # Deep: a test that rewords a tool must not reword it for the next test.
        self.tools = copy.deepcopy(tools if tools is not None else READ_TOOLS + WRITE_TOOLS)
        #: (tool, arguments, Authorization header) for every tools/call that arrived.
        self.calls: list[tuple[str, dict, str | None]] = []
        self.lists = 0
        self.results: dict[str, dict] = {}

    @property
    def called(self) -> list[str]:
        return [tool for tool, _, _ in self.calls]

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return httpx.Response(405)
        body = json.loads(request.content)
        if "id" not in body:
            return httpx.Response(202)
        method = body.get("method")
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "Vendor", "version": "1"}}
        elif method == "tools/list":
            self.lists += 1
            result = {"tools": self.tools}
        elif method == "tools/call":
            name = body["params"]["name"]
            self.calls.append((name, body["params"].get("arguments") or {},
                               request.headers.get("authorization")))
            result = self.results.get(name) or {
                "content": [{"type": "text", "text": json.dumps({"tool": name, "ok": True})}]}
        else:
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"],
                                             "error": {"code": -32601, "message": "no"}})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def install(monkeypatch, vendor: FakeVendor | None = None) -> FakeVendor:
    from yaaf.server import datasource_tools

    vendor = vendor or FakeVendor()
    monkeypatch.setattr(datasource_tools, "TRANSPORT", vendor.transport())
    return vendor


def rpc(client, source: str, headers: dict, method: str, params: dict | None = None,
        extra: dict | None = None) -> dict:
    """One JSON-RPC message to the gateway, as a harness would send it."""
    response = client.post(f"/agent/mcp/{source}", headers={**headers, **(extra or {})},
                           json={"jsonrpc": "2.0", "id": 1, "method": method,
                                 "params": params or {}})
    assert response.status_code == 200, response.text
    return response.json()
