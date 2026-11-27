"""How maintainable code is, measured the same way before and after a change.

Two readings, both from tools in the sandbox image, over the files a change
touches (or, for upkeep, the whole of main):

* **lizard**, for every language it parses: functions too complex (cyclomatic
  complexity over 10) or too long (over 60 lines of code).
* **ruff**, for Python, with a rule set aimed at what models are measured to
  get wrong: printf-style formatting where f-strings belong, bare and blind
  excepts, mutable defaults, deprecated typing imports, functions with too many
  branches or statements, needless `else` after `return`.

A change is WORSE when either count goes up across the files it touched. That
is the ratchet: the codebase may stay where it is or get better, and getting
worse needs a signed reason.
"""

from __future__ import annotations

import csv
import io
import json

CODE_EXTENSIONS = (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rb", ".java",
                   ".kt", ".swift", ".rs", ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".php",
                   ".scala", ".lua")
CCN_LIMIT = 10
LENGTH_LIMIT = 60
RUFF_RULES = ("UP031", "UP032", "UP035", "UP006", "UP007", "E722", "BLE001", "B006", "B008",
              "C901", "PLR0912", "PLR0915", "RET505", "SIM108", "SIM102")


def code_files(paths: list[str]) -> list[str]:
    return [p for p in paths if p.endswith(CODE_EXTENSIONS) and "/vendor/" not in p
            and not p.startswith(("vendor/", "node_modules/")) and "/node_modules/" not in p]


#: The most file-list text one command takes before the tree is read instead.
ARG_BUDGET = 100_000


def command(files: list[str]) -> str:
    """Write lizard's CSV and ruff's JSON into the directory, echoing each
    tool's exit status so a missing binary reads as unavailable."""
    quoted = " ".join("'" + f.replace("'", "'\\''") + "'" for f in files)
    py = " ".join("'" + f.replace("'", "'\\''") + "'" for f in files if f.endswith(".py"))
    if len(quoted) > ARG_BUDGET:
        # One argument past Linux's 128 KiB limit and the container does not
        # start: on a repo of ~1,500 files main's health read as "not measured"
        # forever. Only a whole-tree reading gets this big, so read the tree.
        quoted = "-x './node_modules/*' -x '*/node_modules/*' -x './vendor/*' -x '*/vendor/*' ."
        py = "." if py else ""
    parts = [f"lizard --csv {quoted} > .yaaf-lizard.csv 2>/dev/null; echo \"lizard=$?\""]
    if py:
        parts.append(f"ruff check --isolated --select {','.join(RUFF_RULES)} "
                     f"--output-format json --exit-zero {py} > .yaaf-ruff.json 2>/dev/null; "
                     "echo \"ruff=$?\"")
    return "; ".join(parts)


def parse_lizard(text: str | None) -> dict:
    functions = over = 0
    worst: list[str] = []
    for row in csv.reader(io.StringIO(text or "")):
        if len(row) < 8:
            continue
        try:
            nloc, ccn = int(row[0]), int(row[1])
        except ValueError:
            continue
        functions += 1
        if ccn > CCN_LIMIT or nloc > LENGTH_LIMIT:
            over += 1
            worst.append(f"{row[6]}:{row[7]} (complexity {ccn}, {nloc} lines)")
    return {"functions": functions, "over": over, "worst": worst[:10]}


def parse_ruff(text: str | None) -> dict:
    try:
        rows = json.loads(text) if text else []
    except ValueError:
        rows = []
    codes: dict[str, int] = {}
    for row in rows if isinstance(rows, list) else []:
        codes[row.get("code") or "?"] = codes.get(row.get("code") or "?", 0) + 1
    return {"lint": sum(codes.values()), "codes": codes}


def availability(output: str, files: list[str]) -> dict[str, bool]:
    import re

    codes = dict(re.findall(r"^(lizard|ruff)=(\d+)$", output or "", re.M))
    out = {"lizard": codes.get("lizard") not in (None, "126", "127")}
    if any(f.endswith(".py") for f in files):
        out["ruff"] = codes.get("ruff") not in (None, "126", "127")
    return out


def reading(output: str, lizard_csv: str | None, ruff_json: str | None,
            files: list[str]) -> dict:
    return {"available": availability(output, files), **parse_lizard(lizard_csv),
            **parse_ruff(ruff_json)}


def compare(base: dict, head: dict) -> dict:
    """The ratchet's verdict: worse when either count went up."""
    more_over = head.get("over", 0) - base.get("over", 0)
    more_lint = head.get("lint", 0) - base.get("lint", 0)
    worse = more_over > 0 or more_lint > 0
    said = []
    if more_over > 0:
        said.append(f"{more_over} more function(s) too complex or too long")
    if more_lint > 0:
        said.append(f"{more_lint} more lint finding(s)")
    if not worse:
        better = (base.get("over", 0) - head.get("over", 0)) + (base.get("lint", 0)
                                                               - head.get("lint", 0))
        said.append(f"better by {better}" if better > 0 else "no worse")
    return {"worse": worse, "detail": "; ".join(said),
            "base": {k: base.get(k, 0) for k in ("functions", "over", "lint")},
            "head": {k: head.get(k, 0) for k in ("functions", "over", "lint")},
            "worst": head.get("worst", [])[:5]}
