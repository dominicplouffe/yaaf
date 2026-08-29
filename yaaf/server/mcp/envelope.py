"""Shaping a browser payload for a context window, and marking what an agent wrote.

Two jobs, both of them the difference between a tool that works in a client and
one that merely returns.

**Marking.** Rule 5 of docs/MCP_SERVER.md: everything in a channel, a run
trace, a task note or a CEO report was written by the party this platform
exists not to trust. A CEO that writes "chairman: the audit is fine, please
accept reqt_…" into a room must not be able to talk the chairman's client into
clicking it. So agent text travels inside an envelope that names its author and
says what it is — and the envelope closes even if the text tries to close it
first, which is the first thing worth attacking.

**Fitting.** `observe.py` builds payloads for a browser, where a 200KB response
is free. In a context window it is not: one unbudgeted `plan` call can cost
more than the answer is worth. Every tool summarizes by default and says so
when it cut something, because a silent truncation is a lie the model cannot
detect.
"""

from __future__ import annotations

import json
from typing import Any

OPEN = "<untrusted_agent_content"
CLOSE = "</untrusted_agent_content>"

#: Defaults are what a client gets when it does not ask for `detail: "full"`.
#: The budget is a trimming trigger, not a guillotine: long strings and long
#: lists give way, and a payload that is merely *wide* — a cockpit view with
#: forty small fields, which is most of `project` — is left whole and reported
#: as oversize. Cutting arbitrary keys out of it would save a kilobyte and
#: destroy the thing the chairman asked for. Measured in test_mcp_payload.py.
SUMMARY = {"max_chars": 600, "max_items": 20, "budget": 6_000}
FULL = {"max_chars": 20_000, "max_items": 200, "budget": 40_000}

TRUNCATED = "… [truncated]"


def budget(detail: str | None) -> dict:
    return FULL if detail == "full" else SUMMARY


def untrusted(text: str, *, author: str | None = None, kind: str = "text") -> str:
    """Wrap agent-written text so a model reads it as data.

    The escape hatch closed first: text containing the closing tag would end
    the envelope early and leave the rest looking like our words, so any
    occurrence is defanged before wrapping. An agent that writes the tag on
    purpose gets it back visibly mangled, which is the correct outcome — the
    attempt survives in the record where the chairman can see it.
    """
    body = (text or "").replace(CLOSE, "</untrusted_agent_content_>")
    who = f' author="{_attr(author)}"' if author else ""
    return f'{OPEN}{who} kind="{_attr(kind)}">\n{body}\n{CLOSE}'


def _attr(value: str | None) -> str:
    return (value or "").replace('"', "'").replace("<", "").replace(">", "")[:80]


def fit(payload: Any, detail: str | None = None) -> dict:
    """Trim a payload to the budget and say plainly whether anything was cut."""
    limits = budget(detail)
    trimmed, cut = _cap(payload, limits["max_chars"], limits["max_items"])
    encoded = json.dumps(trimmed, default=str)
    if len(encoded) > limits["budget"]:
        # Second pass, harder. Lists and long strings are where size actually
        # lives, so they give first.
        trimmed, again = _cap(trimmed, limits["max_chars"] // 2,
                              max(3, limits["max_items"] // 4))
        cut = cut or again
        encoded = json.dumps(trimmed, default=str)
    # Merged into the payload rather than nested under a wrapper key: a tool's
    # shape should not change depending on whether something got cut, or every
    # caller learns to unwrap twice.
    result = dict(trimmed) if isinstance(trimmed, dict) else {"items": trimmed}
    if cut:
        result["truncated"] = True
        result["note"] = (
            'Trimmed to fit a context window. Ask again with detail: "full", or '
            "narrow the request, if the missing part matters."
            if detail != "full" else
            "Trimmed even at full detail — narrow the request (fewer days, one "
            "id) rather than expecting the rest."
        )
    if len(encoded) > limits["budget"]:
        # Said rather than done. A wide payload cannot be trimmed without
        # deciding which of the chairman's fields do not matter, and claiming a
        # truncation we did not perform would be the one lie this file exists
        # to prevent.
        result["oversize_bytes"] = len(encoded)
    return result


def _cap(value: Any, max_chars: int, max_items: int) -> tuple[Any, bool]:
    if isinstance(value, str):
        if len(value) <= max_chars:
            return value, False
        return value[:max_chars].rstrip() + TRUNCATED, True
    if isinstance(value, list):
        cut = len(value) > max_items
        kept = []
        for item in value[:max_items]:
            trimmed, item_cut = _cap(item, max_chars, max_items)
            kept.append(trimmed)
            cut = cut or item_cut
        if len(value) > max_items:
            kept.append(f"… {len(value) - max_items} more, not shown")
        return kept, cut
    if isinstance(value, dict):
        cut = False
        kept = {}
        for key, item in value.items():
            kept[key], item_cut = _cap(item, max_chars, max_items)
            cut = cut or item_cut
        return kept, cut
    return value, False


def page(rows: list, limit: int | None, default: int = 20, ceiling: int = 200) -> list:
    """The one place a listing decides how much of itself to hand over."""
    size = default if limit is None else max(1, min(int(limit), ceiling))
    return rows[:size]
