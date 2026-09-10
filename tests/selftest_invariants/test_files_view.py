"""Invariant selftests: the chairman's Files view — everything agents wrote,
nothing the platform staged, and no way to read past the project's edges."""

from __future__ import annotations

import os
from pathlib import Path

from tests.conftest import ADMIN, make_project
from yaaf.core.artifacts import store
from yaaf.core.config import settings


def _workspace(slug: str) -> Path:
    root = Path(settings().workspaces_root) / slug
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_workspace_listing_hides_platform_plumbing(client):
    project = make_project(client, start=False)
    root = _workspace("acme")
    (root / "report-draft.md").write_text("# draft")
    (root / "data").mkdir(exist_ok=True)
    (root / "data" / "extract.csv").write_text("a,b\n1,2")
    (root / ".yaaf").mkdir(exist_ok=True)
    (root / ".yaaf" / "system_prompt.txt").write_text("SECRETish")
    (root / ".git").mkdir(exist_ok=True)
    (root / ".git" / "config").write_text("[core]")

    listing = client.get(f"/observe/projects/{project['id']}/files", headers=ADMIN).json()
    paths = [f["path"] for f in listing["workspace"]]
    assert "report-draft.md" in paths and "data/extract.csv" in paths
    assert not any(p.startswith((".yaaf", ".git")) for p in paths)
    # Nothing was cut, and the screen is told so rather than left to guess.
    assert listing["workspace_truncated"] == 0

    served = client.get(
        f"/observe/projects/{project['id']}/files/content?path=data/extract.csv",
        headers=ADMIN)
    assert served.status_code == 200 and served.content == b"a,b\n1,2"
    # Always a download: agent-written HTML must never render on the origin
    # that holds the chairman's admin token.
    assert served.headers["content-type"].startswith("application/octet-stream")
    assert "attachment" in served.headers["content-disposition"]


def test_file_reads_cannot_escape_the_workspace(client):
    project = make_project(client, start=False)
    _workspace("acme")
    outside = Path(settings().workspaces_root) / "not-this-project.txt"
    outside.write_text("other project's business")

    for evil in ("../not-this-project.txt", "/etc/passwd", ".yaaf/system_prompt.txt",
                 "../../../../etc/passwd"):
        response = client.get(
            f"/observe/projects/{project['id']}/files/content",
            params={"path": evil}, headers=ADMIN)
        assert response.status_code == 404, f"{evil!r} must not be served"


def test_artifacts_are_listed_and_scoped_per_project(client):
    project = make_project(client, start=False)
    mine = f"projects/{project['id']}/run_abc123/final-report.html"
    store().put_bytes(mine, b"<h1>done</h1>")
    store().put_bytes("projects/proj_SOMEONEELSE/run_x/loot.txt", b"nope")

    listing = client.get(f"/observe/projects/{project['id']}/files", headers=ADMIN).json()
    assert [a["key"] for a in listing["artifacts"]] == [mine]
    assert listing["artifacts"][0]["name"] == "final-report.html"
    assert listing["artifacts"][0]["run_id"] == "run_abc123"

    served = client.get(
        f"/observe/projects/{project['id']}/files/content",
        params={"artifact": mine}, headers=ADMIN)
    assert served.status_code == 200 and served.content == b"<h1>done</h1>"
    assert "attachment" in served.headers["content-disposition"]

    # Another project's key, or a traversal-shaped key, is a flat 404.
    for evil in ("projects/proj_SOMEONEELSE/run_x/loot.txt",
                 f"projects/{project['id']}/../proj_SOMEONEELSE/run_x/loot.txt"):
        refused = client.get(
            f"/observe/projects/{project['id']}/files/content",
            params={"artifact": evil}, headers=ADMIN)
        assert refused.status_code == 404, f"{evil!r} must not be served"


def test_listing_cuts_the_oldest_and_says_so(client, monkeypatch):
    """Past the cap the listing keeps the NEWEST files and reports what it
    dropped. The old walk sorted by path and stopped at the cap, so a busy
    project lost whatever sorted last — including the report written this
    morning — and the screen said nothing about it."""
    from yaaf.server.api import observe

    # Its own slug, so the workspace holds only this test's files: the root is
    # a per-worker directory that outlives any one test.
    project = make_project(client, slug="capped", start=False)
    root = _workspace("capped")
    # Names deliberately anti-correlated with time: alphabetically last is
    # oldest, so a path-ordered cut would keep exactly the wrong three.
    for i, name in enumerate(["e.md", "d.md", "c.md", "b.md", "a.md"]):
        path = root / name
        path.write_text(name)
        os.utime(path, (1_700_000_000 + i * 3600, 1_700_000_000 + i * 3600))
    monkeypatch.setattr(observe, "MAX_FILE_LISTING", 3)

    listing = client.get(f"/observe/projects/{project['id']}/files", headers=ADMIN).json()
    assert [f["path"] for f in listing["workspace"]] == ["a.md", "b.md", "c.md"]
    assert listing["workspace_truncated"] == 2
