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


def test_a_render_too_big_to_buffer_still_downloads(client):
    """The video desk could not fetch its own work.

    Every workspace file was read into memory on the request thread, so a 20 MB
    ceiling stood in front of the endpoint and answered 413 above it. That
    number was a fact about the server, not a decision about what the chairman
    may have: his cuts are 40-124 MB each, so the page listed every render and
    served none of them, while the notes beside them downloaded fine. Streaming
    off disk removes the reason for the ceiling, so the ceiling goes.

    Deliberately slow-ish: it writes a file past the old limit, because a test
    that stays under it would pass against the bug it is here to catch.
    """
    project = make_project(client, start=False)
    root = _workspace("acme")
    (root / "renders").mkdir(exist_ok=True)
    big = root / "renders" / "c09.mov"
    big.write_bytes(b"\0" * (24 * 1024 * 1024))

    served = client.get(
        f"/observe/projects/{project['id']}/files/content",
        params={"path": "renders/c09.mov"}, headers=ADMIN)

    assert served.status_code == 200, served.text
    assert len(served.content) == 24 * 1024 * 1024
    # Streaming must not cost the property the buffered path was protecting.
    assert served.headers["content-type"].startswith("application/octet-stream")
    assert "attachment" in served.headers["content-disposition"]
    assert "c09.mov" in served.headers["content-disposition"]


def test_a_streamed_download_cannot_escape_the_workspace_either(client):
    """The traversal guards run before the response type is chosen, so the new
    path is covered by them — asserted rather than assumed, because a refactor
    that returns early is exactly how a check gets stepped over."""
    project = make_project(client, start=False)
    _workspace("acme")
    outside = Path(settings().workspaces_root) / "elsewhere.mov"
    outside.write_bytes(b"\0" * 1024)

    for evil in ("../elsewhere.mov", "../../etc/passwd", ".git/config"):
        response = client.get(
            f"/observe/projects/{project['id']}/files/content",
            params={"path": evil}, headers=ADMIN)
        assert response.status_code == 404, f"{evil!r} must not be served"


# ── the browse index: the Deliverables screen asks, a screenful at a time ──────


def _at(path: Path, iso: str) -> None:
    from datetime import datetime

    stamp = datetime.fromisoformat(iso).timestamp()
    os.utime(path, (stamp, stamp))


def _files(root: Path, spec: dict[str, str]) -> None:
    for rel, iso in spec.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rel)
        _at(path, iso)


def _browse(client, project: dict, **params) -> dict:
    response = client.get(f"/observe/projects/{project['id']}/files/browse",
                          params=params, headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def _publish(client, token: str, filename: str, body: bytes):
    import base64

    return client.post(
        "/agent/artifacts",
        json={"filename": filename, "content_base64": base64.b64encode(body).decode()},
        headers={"Authorization": f"Bearer {token}"},
    )


def test_folders_are_the_readers_days_and_only_open_ones_carry_rows(client):
    """A file written at 6pm in California belongs in that evening's folder, so
    the server folders by the zone the screen names. And a closed folder sends
    its count, not its rows: that is the whole difference between a screen that
    loads and one that ships the project to the browser every fifteen seconds."""
    project = make_project(client, slug="browse-days", start=False)
    _files(_workspace("browse-days"), {
        "late.md": "2024-03-05T02:00:00+00:00",    # the evening of the 4th in LA
        "mid.csv": "2024-03-03T18:00:00+00:00",
        "old.md": "2024-03-01T12:00:00+00:00",
    })

    la = _browse(client, project, tz="America/Los_Angeles")
    assert [d["key"] for d in la["days"]] == ["2024-03-04", "2024-03-03", "2024-03-01"]
    assert _browse(client, project, tz="UTC")["days"][0]["key"] == "2024-03-05"

    # The newest folder opens by itself; the rest say how much is in them.
    newest, middle, oldest = la["days"]
    assert newest["open"] and [r["name"] for r in newest["rows"]] == ["late.md"]
    assert not middle["open"] and middle["rows"] is None and middle["count"] == 1
    assert la["matched"] == 3 and la["total"] == 3

    # What the reader toggled is honoured, and a row knows where it sits in the
    # whole filtered list — the reader walks from it with nothing else in hand.
    toggled = _browse(client, project, tz="America/Los_Angeles",
                      expand="2024-03-01", collapse="2024-03-04")
    by_key = {d["key"]: d for d in toggled["days"]}
    assert by_key["2024-03-04"]["rows"] is None
    assert [(r["name"], r["pos"]) for r in by_key["2024-03-01"]["rows"]] == [("old.md", 2)]


def test_each_filter_counts_what_it_would_show_leaving_itself_out(client):
    """A type list reading "md (0)" because you are on Published is a broken
    list. Moving the counting to the server must not quietly change what each
    count means."""
    project = make_project(client, slug="browse-counts", start=False)
    _files(_workspace("browse-counts"), {
        "notes/a.md": "2024-03-01T10:00:00+00:00",
        "notes/b.md": "2024-03-01T11:00:00+00:00",
        "data/x.csv": "2024-03-01T12:00:00+00:00",
    })
    store().put_bytes(f"projects/{project['id']}/run_1/report.md", b"# report")

    published = _browse(client, project, state="published")
    assert published["states"] == {"all": 4, "published": 1, "working": 3}
    assert published["types"] == [["md", 1]]
    assert published["matched"] == 1

    markdown = _browse(client, project, type="md")
    assert markdown["states"] == {"all": 3, "published": 1, "working": 2}
    assert dict(map(tuple, markdown["types"])) == {"md": 3, "csv": 1}

    # Every term has to hit, in any order, anywhere in the path.
    hit = _browse(client, project, q="a notes")
    assert hit["matched"] == 1
    assert hit["days"][0]["rows"][0]["path"] == "notes/a.md"


def test_a_filter_opens_folders_until_its_budget_not_every_one_it_touched(client,
                                                                          monkeypatch):
    """Opening every folder a search touched was the right instinct and the
    wrong ceiling: a two-letter search over a year of files opened hundreds of
    them at once, which is the screen not loading by another route."""
    from yaaf.server import file_index

    monkeypatch.setattr(file_index, "FILTERED_OPEN_ROWS", 2)
    project = make_project(client, slug="browse-budget", start=False)
    _files(_workspace("browse-budget"),
           {f"x{day}.md": f"2024-03-0{day}T12:00:00+00:00" for day in range(1, 6)})

    plain = _browse(client, project)
    assert [d["open"] for d in plain["days"]] == [True, False, False, False, False]

    searched = _browse(client, project, q="x")
    assert [d["open"] for d in searched["days"]] == [True, True, False, False, False]
    assert all(d["count"] == 1 for d in searched["days"]), "closed folders still count"


def test_a_republished_name_is_one_row_with_its_versions_and_their_receipts(client):
    """Five copies of three files are three deliverables. Each copy keeps its
    own receipt, matched by key for exactly the rows sent — the old screen read
    the newest two hundred receipts and left every older deliverable looking
    unreceipted."""
    from tests.conftest import live_token

    project = make_project(client, slug="browse-versions")
    keys = []
    for body in (b"first", b"second"):
        _run, token = live_token(project["id"], project["ceo_agent_id"])
        posted = _publish(client, token, "memo.md", body)
        assert posted.status_code == 200, posted.text
        keys.append(f"projects/{project['id']}/{_run}/memo.md")
    # The first copy older on disk, as it would be: same-second writes would
    # leave which one is "latest" to chance.
    _at(Path(settings().artifacts_root) / keys[0], "2024-03-01T12:00:00+00:00")

    row = _browse(client, project, state="published")["days"][0]["rows"][0]
    assert (row["name"], row["version"], row["key"]) == ("memo.md", 2, keys[1])
    assert row["receipt"]["version"] == 2
    assert [(o["version"], o["key"], o["receipt"]["version"]) for o in row["older"]] == [
        (1, keys[0], 1)]

    # The Overview links to a deliverable by key — an earlier version too.
    url = f"/observe/projects/{project['id']}/files/browse/entry"
    older = client.get(url, params={"id": keys[0]}, headers=ADMIN).json()
    assert older["entry"]["version"] == 1 and older["entry"]["pos"] is None
    latest = client.get(url, params={"id": keys[1]}, headers=ADMIN).json()
    assert latest["entry"]["pos"] == 0
    gone = client.get(url, params={"id": "projects/proj_ELSEWHERE/run_x/loot.md"},
                      headers=ADMIN)
    assert gone.status_code == 404


def test_rows_walk_the_list_in_the_order_the_folders_show_it(client):
    """The reader's ← → and the export both ask for runs of the list by
    position. Those positions must be the screen's, or → lands on a file the
    reader never saw next."""
    project = make_project(client, slug="browse-rows", start=False)
    _files(_workspace("browse-rows"), {
        "b.md": "2024-03-02T09:00:00+00:00",
        "a.md": "2024-03-02T09:00:00+00:00",     # same minute: name breaks the tie
        "big.csv": "2024-03-02T08:00:00+00:00",
        "z.txt": "2024-03-01T12:00:00+00:00",
    })
    days = _browse(client, project, sort="size", desc="true",
                   expand=["2024-03-02", "2024-03-01"])["days"]
    on_screen = [r["id"] for d in days for r in d["rows"]]

    url = f"/observe/projects/{project['id']}/files/browse/rows"
    walked = client.get(url, params={"sort": "size", "desc": "true", "limit": 10},
                        headers=ADMIN).json()
    assert [r["id"] for r in walked["rows"]] == on_screen
    assert [r["pos"] for r in walked["rows"]] == list(range(4))

    third = client.get(url, params={"sort": "size", "desc": "true", "offset": 2, "limit": 1},
                       headers=ADMIN).json()
    assert [r["id"] for r in third["rows"]] == [on_screen[2]] and third["matched"] == 4

    lean = client.get(url, params={"lean": "true"}, headers=ADMIN).json()["rows"][0]
    assert "older" not in lean and "receipt" not in lean


def test_the_index_keeps_the_newest_and_says_what_it_left_out(client, monkeypatch):
    from yaaf.server import file_index

    monkeypatch.setattr(file_index, "MAX_INDEXED_FILES", 3)
    project = make_project(client, slug="browse-capped", start=False)
    _files(_workspace("browse-capped"),
           {name: f"2024-03-0{i + 1}T12:00:00+00:00"
            for i, name in enumerate(["e.md", "d.md", "c.md", "b.md", "a.md"])})

    view = _browse(client, project)
    assert view["total"] == 3
    assert (view["working_kept"], view["working_dropped"], view["walk_stopped"]) == (3, 2, False)
    assert [d["key"] for d in view["days"]] == ["2024-03-05", "2024-03-04", "2024-03-03"]


def test_the_walk_never_enters_the_directories_it_hides(monkeypatch, tmp_path):
    """rglob walked all of `.git` and threw it away afterwards; on a real
    project that was most of the cost of the screen."""
    from yaaf.server import file_index

    _files(tmp_path, {"report.md": "2024-03-01T12:00:00+00:00",
                      ".git/objects/ab/cdef": "2024-03-01T12:00:00+00:00",
                      ".yaaf/prompt.txt": "2024-03-01T12:00:00+00:00"})
    opened: list[str] = []
    real = os.scandir

    def watching(path):
        opened.append(str(path))
        return real(path)

    monkeypatch.setattr(file_index.os, "scandir", watching)
    found, stopped = file_index._walk(tmp_path)

    assert [rel for _, rel, _ in found] == ["report.md"] and not stopped
    assert not any(".git" in p or ".yaaf" in p for p in opened), opened


def test_a_slow_walk_is_paid_once_and_never_by_the_reader(monkeypatch, tmp_path):
    """The failure this index exists for: a walk slower than the poll. Every
    tab's every tick started its own, they overlapped, and the screen stopped
    loading. Now a stale index is served at once while ONE rebuild runs behind
    it, and the next read gets the new one."""
    import threading
    import time

    from yaaf.server import file_index

    monkeypatch.setattr(file_index, "FRESH_S", 0.0)
    monkeypatch.setattr(file_index, "MAX_WALK_SHARE", 1e9)
    monkeypatch.setattr(file_index, "PATIENCE_S", 0.05)
    _files(tmp_path, {"first.md": "2024-03-01T12:00:00+00:00"})
    held = file_index.snapshot("proj_slowwalk", tmp_path, "projects/proj_slowwalk/")
    assert [i.name for i in held.items] == ["first.md"]

    gate, walks = threading.Event(), []
    real_build = file_index.build

    def slow_build(root, prefix):
        walks.append(root)
        gate.wait(5)
        return real_build(root, prefix)

    monkeypatch.setattr(file_index, "build", slow_build)
    _files(tmp_path, {"second.md": "2024-03-02T12:00:00+00:00"})

    started = time.monotonic()
    served = [file_index.snapshot("proj_slowwalk", tmp_path, "projects/proj_slowwalk/")
              for _ in range(3)]
    assert time.monotonic() - started < 1.0, "the reader waited on the walk"
    assert all(s is held for s in served), "the held index is what a reader gets meanwhile"
    assert len(walks) == 1, "three stale reads, one walk"

    pending = file_index._inflight["proj_slowwalk"]
    gate.set()
    pending.result(timeout=5)
    monkeypatch.setattr(file_index, "FRESH_S", 60.0)
    fresh = file_index.snapshot("proj_slowwalk", tmp_path, "projects/proj_slowwalk/")
    assert sorted(i.name for i in fresh.items) == ["first.md", "second.md"]
