"""What a change's diff says about itself, read by the platform.

Pure functions over GitHub's own file list for a pull request (filename,
status, patch). Four findings, each of which a gate in `yaaf.server.changes`
turns into a refusal or a hold:

* **secrets** — a credential in an added line. Never waivable: a key that
  reached a branch is already exposed, and merging it only makes it permanent.
* **weakened tests** — assertions removed from a test file without as many put
  back, a test file deleted, or a test newly skipped. Agents under pressure to
  get green edit the test, not the code; this is that move, made visible.
* **new packages** — a dependency that was not there before. Held for a
  challenge seat to name it as checked: young lookalike packages are how a
  model's confident guess becomes a supply-chain hole.
* **protected paths** — files the chairman said need a person.

Heuristics, and said so. A finding that is wrong costs a reviewer one line of
explanation; a weakened test that slips through costs the guarantee.
"""

from __future__ import annotations

import fnmatch
import re

TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__|spec|specs)/|(^|/)test_[^/]+\.py$|_test\.(py|go|rb|exs?)$"
    r"|\.(test|spec)\.(js|jsx|ts|tsx|mjs|cjs)$|_spec\.rb$")
ASSERTION = re.compile(
    r"\b(assert\w*|expect\s*\(|should\b|self\.assert\w+|t\.(Error|Fatal|Fail)\w*|"
    r"require\.\w+|toBe\w*|toEqual|toMatch\w*|toHaveBeen\w*|toThrow\w*)")
SKIP = re.compile(
    r"(@pytest\.mark\.(skip|xfail)\b|\bpytest\.skip\(|@unittest\.skip|"
    r"\b(it|test|describe)\.(skip|todo)\s*\(|\bx(it|describe|test)\s*\(|"
    r"\b(it|test|describe)\.only\s*\(|\bt\.Skip\w*\(|\bskip\s*:\s*true\b)")
SECRET = re.compile(
    r"(AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,}"
    r"|sk-ant-[A-Za-z0-9_-]{20,}|sk-(proj-)?[A-Za-z0-9]{32,}|xox[abpors]-[A-Za-z0-9-]{10,}"
    r"|AIza[0-9A-Za-z_-]{35}|sk_live_[0-9A-Za-z]{20,}|rk_live_[0-9A-Za-z]{20,}"
    r"|-----BEGIN (RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----)")

_NPM_DEP = re.compile(r'^\s*"(@?[A-Za-z0-9][\w./-]*)"\s*:\s*"[\^~<>=*\dvlatestnpm:git:file:workspace:]')
_PY_REQ = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9_.-]*)\s*(\[[^\]]*\])?\s*([=<>!~]=?|@|;|$)")
_PYPROJECT_DEP = re.compile(r'^\s*"([A-Za-z0-9][A-Za-z0-9_.-]*)\s*(\[[^\]]*\])?\s*([=<>!~]=?|;|")')
_GO_DEP = re.compile(r"^\s*(?:require\s+)?([\w.-]+\.[\w.-]+/[\w./-]+)\s+v\d")
_NPM_KEYS = {"name", "version", "description", "main", "module", "types", "scripts", "license",
             "private", "type", "author", "repository", "engines", "workspaces", "homepage",
             "bugs", "keywords", "files", "bin", "exports", "packageManager"}


def _lines(patch: str | None, sign: str) -> list[str]:
    if not patch:
        return []
    other = "+++" if sign == "+" else "---"
    return [line[1:] for line in patch.splitlines()
            if line.startswith(sign) and not line.startswith(other)]


def is_test_file(filename: str) -> bool:
    return bool(TEST_PATH.search(filename))


def weakened_tests(files: list[dict]) -> list[str]:
    found = []
    for f in files:
        name = f.get("filename") or ""
        if not is_test_file(name):
            continue
        if f.get("status") == "removed":
            found.append(f"{name}: the test file is deleted")
            continue
        added, removed = _lines(f.get("patch"), "+"), _lines(f.get("patch"), "-")
        lost = sum(bool(ASSERTION.search(x)) for x in removed)
        gained = sum(bool(ASSERTION.search(x)) for x in added)
        if lost > gained:
            found.append(f"{name}: {lost - gained} assertion(s) fewer")
        skipped = [x.strip() for x in added if SKIP.search(x)]
        if skipped:
            found.append(f"{name}: newly skipped or focused — {skipped[0][:80]}")
    return found


def _package_names(filename: str, lines: list[str]) -> set[str]:
    base = filename.rsplit("/", 1)[-1]
    names: set[str] = set()
    if base == "package.json":
        for x in lines:
            m = _NPM_DEP.match(x)
            if m and m.group(1) not in _NPM_KEYS:
                names.add(m.group(1).lower())
    elif base.startswith("requirements") and base.endswith((".txt", ".in")):
        for x in lines:
            if x.strip().startswith(("#", "-")):
                continue
            m = _PY_REQ.match(x)
            if m:
                names.add(m.group(1).lower().replace("_", "-"))
    elif base == "pyproject.toml":
        for x in lines:
            m = _PYPROJECT_DEP.match(x)
            if m:
                names.add(m.group(1).lower().replace("_", "-"))
    elif base == "go.mod":
        for x in lines:
            m = _GO_DEP.match(x)
            if m:
                names.add(m.group(1))
    return names


def new_packages(files: list[dict]) -> list[str]:
    """Names added to a manifest and not merely re-pinned: a version bump
    shows the name on both sides of the diff and is not a new package."""
    found: set[str] = set()
    for f in files:
        name = f.get("filename") or ""
        added = _package_names(name, _lines(f.get("patch"), "+"))
        removed = _package_names(name, _lines(f.get("patch"), "-"))
        found |= added - removed
    return sorted(found)


def secrets(files: list[dict]) -> list[str]:
    found = []
    for f in files:
        for line in _lines(f.get("patch"), "+"):
            m = SECRET.search(line)
            if m:
                token = m.group(0)
                found.append(f"{f.get('filename')}: {token[:6]}… ({len(token)} chars)")
                break
    return found


def protected(files: list[dict], globs: list[str]) -> list[str]:
    if not globs:
        return []
    return sorted({f.get("filename") or "" for f in files
                   if any(fnmatch.fnmatch(f.get("filename") or "", g) for g in globs)})


def parse_globs(raw: str) -> list[str]:
    return [g.strip() for g in (raw or "").replace("\n", ",").split(",") if g.strip()]


def inspect(files: list[dict], protected_globs: list[str]) -> dict:
    return {"secrets": secrets(files), "weakened_tests": weakened_tests(files),
            "new_packages": new_packages(files),
            "protected": protected(files, protected_globs),
            "files": len(files)}
