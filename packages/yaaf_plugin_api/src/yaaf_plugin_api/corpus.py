"""The fuzz corpus every hook is asked with (MIN-09): what a careless, hostile or
broken seat, source or state could hand a plugin. A hook may refuse any of it;
it may not raise, hang or reach out. The corpus is fixed so two runs, an
author's and the install's, ask the same questions.
"""

from __future__ import annotations

import json

from yaaf_plugin_api import types as T

#: Bodies a seat could publish under a gated name.
UPLOADS: tuple[tuple[str, bytes], ...] = (
    ("empty", b""),
    ("whitespace", b"   \n\t "),
    ("not-utf8", b"\xff\xfe\x00\x81garbage"),
    ("not-json", b"{{{ this is not json"),
    ("json-null", b"null"),
    ("json-number", b"42"),
    ("json-string", b'"a string"'),
    ("json-list", b"[1, 2, 3]"),
    ("json-empty-object", b"{}"),
    ("wrong-types", json.dumps({"title": 7, "budget": "lots", "claims": {"a": None},
                                "items": "x", "id": [1]}).encode()),
    ("deeply-nested", ("[" * 2000 + "]" * 2000).encode()),
    ("huge-number", b'{"amount": 1e999999}'),
    ("unicode", json.dumps({"title": "‮\u0000퟿\U0001f4a9" * 50}).encode()),
    ("unknown-fields", json.dumps({f"field_{i}": i for i in range(500)}).encode()),
    ("large", b'{"pad": "' + b"x" * (4 * 1024 * 1024) + b'"}'),
)

#: Results a data source could return for any tool.
RESULTS: tuple[tuple[str, dict], ...] = (
    ("empty", {}),
    ("no-content", {"content": []}),
    ("prose", {"content": [{"type": "text", "text": "Account 7 answered: here are rows."}]}),
    ("prefixed-json", {"content": [{"type": "text", "text": 'rows: {"cost": 1}'}]}),
    ("structured-list", {"structuredContent": [1, "two", None]}),
    ("structured-nested", {"structuredContent": {"a": {"b": {"c": {"d": {"e": 1}}}}}}),
    ("wrong-shape", {"content": "not a list", "structuredContent": "not an object"}),
    ("error", {"isError": True, "content": [{"type": "text", "text": "rate limited"}]}),
)

#: Tool listings a source could offer.
TOOLS: tuple[tuple[str, list], ...] = (
    ("none", []),
    ("strangers", [T.ToolInfo("x", "do_everything", "deletes things"),
                   T.ToolInfo("x", "", ""), T.ToolInfo("x", "a" * 128, "\x00")]),
)

#: Owner-entered creation fields.
FIELDS: tuple[tuple[str, dict], ...] = (
    ("empty", {}),
    ("wrong-types", {"a": [1], "b": {"c": None}, "d": 1e300}),
    ("long", {"text": "x" * 100_000}),
)
