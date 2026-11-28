"""One adapter per MCP vendor: its answers in, `yaaf.ppc.data` tables out.

An adapter is the only code that knows a vendor's tool names and column names.
It asks for Amazon's numbers and nothing else, so a vendor's own fields (its
goals, tags, bid previews, cost of goods) never reach a check: they are not
requested, and there is no table column to put them in.

`for_tools` picks the adapter whose tools a source offers. A source with no
adapter can still be audited by the crew from its tools, under the same field
rule; it just cannot have platform-computed checks until one is written.
"""

from __future__ import annotations

import json
from collections.abc import Callable

Call = Callable[[str, dict], object]


def parse(result) -> object:
    """An MCP tool result as data: structured content when the server sends it,
    otherwise the first text block that parses as JSON (vendors often prefix a
    sentence such as which account answered)."""
    if not isinstance(result, dict):
        return result
    if result.get("isError"):
        texts = [c.get("text", "") for c in result.get("content") or [] if isinstance(c, dict)]
        raise AdapterError(" ".join(t for t in texts if t)[:400] or "the tool returned an error")
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    for block in result.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            try:
                return json.loads(block.get("text") or "")
            except ValueError:
                continue
    raise AdapterError("the tool returned no data the platform could read")


class AdapterError(Exception):
    pass


def for_tools(names: set[str]):
    from yaaf.ppc.adapters import qore

    for adapter in (qore,):
        if adapter.matches(names):
            return adapter
    return None
