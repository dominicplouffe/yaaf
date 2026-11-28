"""Which API a plugin was written for, and whether this one is it.

A manifest names a range (`api = ">=1.0,<2"`) rather than one version because a
plugin written against 1.0 keeps working on 1.3: within a major version the API
only grows. The range grammar is deliberately small — comma-joined comparisons
of dotted integers — so an author cannot write a range the host reads one way
and the author meant another.
"""

from __future__ import annotations

import re

#: The API this package is. Additions bump the minor; a removal or a change of
#: meaning is a new major, and every plugin's range then has to say so.
API_VERSION = "1.0"

_CLAUSE = re.compile(r"^(>=|<=|==|>|<)\s*(\d+(?:\.\d+){0,2})$")


def parse_version(text: str) -> tuple[int, int, int]:
    parts = text.strip().split(".")
    if not 1 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"{text!r} is not a version: use dotted integers such as 1.0")
    nums = [int(p) for p in parts] + [0] * (3 - len(parts))
    return nums[0], nums[1], nums[2]


def parse_range(spec: str) -> list[tuple[str, tuple[int, int, int]]]:
    """`">=1.0,<2"` → [(">=", (1,0,0)), ("<", (2,0,0))], or ValueError naming
    the clause that is not one."""
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError("the api range is empty: write one such as \">=1.0,<2\"")
    clauses = []
    for raw in spec.split(","):
        m = _CLAUSE.match(raw.strip())
        if m is None:
            raise ValueError(f"{raw.strip()!r} is not a comparison: use >=, >, <=, < or == "
                             "and a dotted version, such as >=1.0")
        clauses.append((m.group(1), parse_version(m.group(2))))
    return clauses


def compatible(spec: str, version: str = API_VERSION) -> bool:
    have = parse_version(version)
    ops = {">=": have.__ge__, ">": have.__gt__, "<=": have.__le__,
           "<": have.__lt__, "==": have.__eq__}
    return all(ops[op](v) for op, v in parse_range(spec))
