"""The project-home section a plugin may draw, without one line of browser code.

A plugin declares its section in `ui/view.json` as an ordered list of
components the dashboard already knows how to render, and fills them from its
`view` hook. No plugin JavaScript reaches the dashboard: a richer page waits for
a new component here, reviewed once, rather than running a stranger's script in
the chairman's browser (docs/plans/factory-plugins.md §4.9, §11.3).

Both halves are checked: the declaration when the plugin is installed, the data
every time the hook answers. Data that does not fit its component is a host
failure, and the section is drawn as "could not be read" — never half-drawn.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

VIEW_VERSION = 1

#: component → (required keys, optional keys). Each value's type is checked in `_shape`.
COMPONENTS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "status": (frozenset({"state", "text"}), frozenset()),
    "metric": (frozenset({"label", "value"}), frozenset({"unit", "note"})),
    "markdown": (frozenset({"text"}), frozenset()),
    "link": (frozenset({"label"}), frozenset({"artifact", "href"})),
    "artifact": (frozenset({"name"}), frozenset({"version", "note"})),
    "checklist": (frozenset({"items"}), frozenset()),
    "table": (frozenset({"columns", "rows"}), frozenset({"note"})),
    "coverage": (frozenset({"areas"}), frozenset()),
}
STATES = ("ok", "info", "warn", "blocked")
MAX_SECTIONS = 20
MAX_TEXT = 20_000
MAX_ROWS = 500
MAX_COLUMNS = 20
_ID = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def validate_doc(doc: Any) -> list[str]:
    """Every problem with a `ui/view.json`, or [] when it is one the dashboard can draw."""
    if not isinstance(doc, Mapping):
        return ["the view must be one JSON object with version and sections"]
    out: list[str] = []
    if doc.get("version") != VIEW_VERSION:
        out.append(f"version must be {VIEW_VERSION}")
    extra = set(doc) - {"version", "sections"}
    if extra:
        out.append(f"unknown keys in the view: {', '.join(sorted(extra))}")
    sections = doc.get("sections")
    if not isinstance(sections, list) or not sections:
        return [*out, "sections must be a non-empty list"]
    if len(sections) > MAX_SECTIONS:
        out.append(f"at most {MAX_SECTIONS} sections")
    seen: set[str] = set()
    for i, s in enumerate(sections):
        where = f"sections[{i}]"
        if not isinstance(s, Mapping):
            out.append(f"{where} must be an object")
            continue
        sid = s.get("id")
        if not isinstance(sid, str) or not _ID.match(sid):
            out.append(f"{where}.id must match {_ID.pattern}")
        elif sid in seen:
            out.append(f"{where}.id {sid!r} is used twice")
        else:
            seen.add(sid)
        if s.get("component") not in COMPONENTS:
            out.append(f"{where}.component is one of {', '.join(COMPONENTS)}")
        if not isinstance(s.get("title"), str) or not s["title"].strip() or len(s["title"]) > 80:
            out.append(f"{where}.title is required (≤80 chars)")
        extra = set(s) - {"id", "component", "title", "help"}
        if extra:
            out.append(f"{where} has unknown keys: {', '.join(sorted(extra))}")
    return out


def validate_data(doc: Mapping, data: Any) -> list[str]:
    """Every problem with what `view(ctx)` returned for this declaration. A
    section with no data is drawn empty; data for an undeclared section is refused."""
    if not isinstance(data, Mapping):
        return ["view data must be an object: section id → that section's data"]
    by_id = {s["id"]: s["component"] for s in doc.get("sections") or []
             if isinstance(s, Mapping) and isinstance(s.get("id"), str)}
    out: list[str] = []
    for sid, value in data.items():
        if sid not in by_id:
            out.append(f"view data names section {sid!r}, which view.json does not declare")
            continue
        out += [f"section {sid}: {p}" for p in _shape(by_id[sid], value)]
    return out


def _text(v: Any, limit: int = 500) -> bool:
    return isinstance(v, str) and len(v) <= limit


def _scalar(v: Any) -> bool:
    return v is None or isinstance(v, (str, int, float, bool))


def _shape(component: str, value: Any) -> list[str]:
    required, optional = COMPONENTS[component]
    if not isinstance(value, Mapping):
        return [f"a {component} takes an object"]
    out = []
    missing = required - set(value)
    if missing:
        out.append(f"a {component} needs {', '.join(sorted(missing))}")
    extra = set(value) - required - optional
    if extra:
        out.append(f"a {component} has no {', '.join(sorted(extra))}")
    if out:
        return out
    if component == "status":
        if value["state"] not in STATES:
            out.append(f"state is one of {', '.join(STATES)}")
        if not _text(value["text"]):
            out.append("text is a string of at most 500 chars")
    elif component == "metric":
        if not _text(value["label"], 80):
            out.append("label is a string of at most 80 chars")
        if not (isinstance(value["value"], (int, float, str)) and not isinstance(value["value"], bool)):
            out.append("value is a number or a short string")
    elif component == "markdown":
        if not _text(value["text"], MAX_TEXT):
            out.append(f"text is a string of at most {MAX_TEXT} chars")
    elif component == "link":
        targets = [k for k in ("artifact", "href") if value.get(k)]
        if len(targets) != 1:
            out.append("a link names exactly one of artifact or href")
        elif "href" in targets and not str(value["href"]).startswith("https://"):
            out.append("href must be https://")
    elif component == "artifact":
        if not _text(value["name"], 200):
            out.append("name is an artifact name")
        if "version" in value and not (isinstance(value["version"], int) and value["version"] > 0):
            out.append("version is a positive integer")
    elif component == "checklist":
        items = value["items"]
        if not isinstance(items, list) or not all(
                isinstance(i, Mapping) and _text(i.get("text")) and isinstance(i.get("done"), bool)
                for i in items):
            out.append("items is a list of {text, done}")
    elif component == "table":
        cols, rows = value["columns"], value["rows"]
        if not isinstance(cols, list) or not cols or len(cols) > MAX_COLUMNS or \
                not all(_text(c, 80) for c in cols):
            out.append(f"columns is a list of 1-{MAX_COLUMNS} short strings")
        elif not isinstance(rows, list) or len(rows) > MAX_ROWS or not all(
                isinstance(r, list) and len(r) == len(cols) and all(map(_scalar, r))
                for r in rows):
            out.append(f"rows is a list of at most {MAX_ROWS} rows, each one scalar per column")
    elif component == "coverage":
        areas = value["areas"]
        if not isinstance(areas, list) or not all(
                isinstance(a, Mapping) and _text(a.get("key"), 80)
                and isinstance(a.get("available"), bool) for a in areas):
            out.append("areas is a list of {key, available, note?}")
    return out
