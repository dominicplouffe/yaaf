"""The repo-level half of 'built in the right shape': the files a manifest
points at exist, parse, and sit where the spec says.

These checks need nothing but the checkout, so an author's editor, an AI
factory and the install all run the same ones before any code is imported.
What only core can judge — a role against the role taxonomy, a crew against the
roster — is checked by core at install (MIN-07); here a role is only required to
exist and be one JSON object.
"""

from __future__ import annotations

import json
from pathlib import Path

from yaaf_plugin_api import view
from yaaf_plugin_api.manifest import FILENAME, Manifest, ManifestProblem, load_path

SKILL_MAX_BYTES = 64_000
AGENT_FILE_MAX_BYTES = 1_000_000
FIXTURES = "fixtures"
EXPECT_SUFFIX = ".expect.json"


def gated(manifest: Manifest) -> list[str]:
    """Every artifact name a gate answers for: what MIN-13 needs fixtures of."""
    return [*manifest.artifacts, *([manifest.review.artifact] if manifest.review else [])]


Problem = tuple[str, str]  # (requirement id, what is wrong)


def check_repo(root: str | Path) -> tuple[Manifest | None, list[Problem]]:
    """The manifest, and every problem with the repo around it, each tagged with
    the requirement it breaks (yaaf_plugin_api.requirements)."""
    root = Path(root)
    try:
        manifest = load_path(root)
    except ManifestProblem as exc:
        return None, [(_manifest_requirement(p), f"{FILENAME}: {p}") for p in exc.problems]
    out: list[Problem] = [("MIN-01", p) for p in _links(root)]
    if manifest.lead:
        out += [("MIN-01", p) for p in _json_file(root, f"roles/{manifest.lead}.json",
                                                    "kind.lead")]
    for crew in manifest.crews:
        out += [("MIN-01", p) for p in _json_file(root, f"crews/{crew}.json", "kind.crews")]
    for name, rel in manifest.agent_files.items():
        path = root / rel
        if not path.is_file():
            out.append(("MIN-01", f"capabilities.agent_files.{name}: {rel} is not a file in "
                                  "the repo"))
        elif path.stat().st_size > AGENT_FILE_MAX_BYTES:
            out.append(("MIN-01", f"capabilities.agent_files.{name}: {rel} is larger than "
                                  f"{AGENT_FILE_MAX_BYTES} bytes"))
    if manifest.view:
        path = root / manifest.view
        if not path.is_file():
            out.append(("EXT-VIEW-1", f"capabilities.view: {manifest.view} is not a file in "
                                      "the repo"))
        else:
            try:
                doc = json.loads(path.read_text("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                out.append(("EXT-VIEW-1", f"{manifest.view} is not valid JSON ({exc})"))
            else:
                out += [("EXT-VIEW-1", f"{manifest.view}: {p}") for p in view.validate_doc(doc)]
    skills = root / "skills"
    if skills.is_dir():
        for path in sorted(skills.glob("*.md")):
            if path.stat().st_size > SKILL_MAX_BYTES:
                out.append(("MIN-07", f"skills/{path.name} is larger than {SKILL_MAX_BYTES} "
                                      "bytes: split it, and let the runbook point to the parts"))
    out += [("MIN-13", p) for p in _fixtures(root, manifest)]
    tests = root / "tests"
    if not tests.is_dir() or not any(tests.rglob("test_*.py")):
        out.append(("MIN-16", "tests/ has no test_*.py: every gate ships an attack test "
                              "(house rule 2)"))
    return manifest, out


def _manifest_requirement(problem: str) -> str:
    """A manifest problem is MIN-06 when it is about the modes, MIN-01 otherwise."""
    return "MIN-06" if problem.startswith("kind.modes") else "MIN-01"


def _links(root: Path) -> list[str]:
    """No symlink anywhere: one pointing out of the tree would let a manifest
    path read a file the install never fetched."""
    return [f"{p.relative_to(root)} is a symlink; a plugin repo has none"
            for p in sorted(root.rglob("*")) if p.is_symlink() and ".git" not in p.parts]


def _json_file(root: Path, rel: str, what: str) -> list[str]:
    path = root / rel
    if not path.is_file():
        return [f"{what} names {path.stem}, but {rel} is not in the repo"]
    try:
        body = json.loads(path.read_text("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return [f"{rel} is not valid JSON ({exc})"]
    return [] if isinstance(body, dict) else [f"{rel} must be one JSON object"]


def _fixtures(root: Path, manifest: Manifest) -> list[str]:
    """MIN-13: each gate has at least one case it accepts and one it refuses,
    each with the verdict it expects beside it."""
    out = []
    for name in gated(manifest):
        for verdict in ("accept", "refuse"):
            rel = f"{FIXTURES}/artifacts/{verdict}/{name}"
            folder = root / rel
            cases = [p for p in sorted(folder.iterdir()) if p.is_file()
                     and not p.name.endswith(EXPECT_SUFFIX)] if folder.is_dir() else []
            if not cases:
                out.append(f"{rel}/ has no case: a gate needs at least one artifact it "
                           f"{'accepts' if verdict == 'accept' else 'refuses'}")
            for case in cases:
                expect = case.with_name(case.name + EXPECT_SUFFIX)
                if verdict == "refuse" and not expect.is_file():
                    out.append(f"{rel}/{case.name} has no {case.name + EXPECT_SUFFIX}: say which "
                               'words the refusal must contain ({"contains": [...]})')
                elif expect.is_file():
                    out += _expect(expect, f"{rel}/{expect.name}")
    if manifest.data_actions or manifest.receipts:
        sources = root / FIXTURES / "sources"
        if not sources.is_dir() or not any(p.is_dir() for p in sources.iterdir()):
            out.append(f"{FIXTURES}/sources/ has no recorded source: data actions and receipts "
                       "are tested against a fake MCP source built from one")
    return out


def _expect(path: Path, rel: str) -> list[str]:
    try:
        body = json.loads(path.read_text("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return [f"{rel} is not valid JSON ({exc})"]
    contains = body.get("contains", []) if isinstance(body, dict) else None
    if not isinstance(contains, list) or not all(isinstance(c, str) and c for c in contains):
        return [f'{rel} is {{"contains": ["words the refusal must say", ...]}}']
    return []
