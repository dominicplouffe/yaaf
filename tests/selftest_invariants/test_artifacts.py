"""Invariant selftests: the artifact store — durable bytes with a uri.

Evidence snapshots, run traces, and agent deliverables must outlive sandboxes
and workspaces; rows record the store uri, and the observe API serves both
store uris and the plain paths older rows still hold."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest

from tests.conftest import ADMIN, bearer, live_token, make_project
from yaaf.core.artifacts import LocalStore, store
from yaaf.core.db import db_session
from yaaf.core.models import Evidence, Run


def test_local_store_roundtrip():
    backend = store()
    assert isinstance(backend, LocalStore)  # tests run without an S3 endpoint

    uri = backend.put_bytes("tests/roundtrip.txt", b"hello artifacts")
    assert uri.startswith("file://")
    assert backend.exists("tests/roundtrip.txt")
    assert backend.get_bytes("tests/roundtrip.txt") == b"hello artifacts"
    assert not backend.exists("tests/never-written.bin")
    # The uri is the durable record: it resolves on its own, without the store.
    assert Path(uri[len("file://"):]).read_bytes() == b"hello artifacts"

    # Keys never escape the store root.
    with pytest.raises(ValueError):
        backend.put_bytes("../outside.txt", b"nope")


def test_verifier_snapshot_lands_in_store_and_is_served(client):
    from yaaf.server.verifier import verify_pending_evidence

    project = make_project(client)
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])
    evidence = client.post(
        "/agent/evidence",
        json={"kind": "external", "description": "a stranger reviewed us",
              "source_url": "https://independent.example.org/review"},
        headers=bearer(ceo_token),
    ).json()

    page = b"<html>a stranger's page about the project</html>"

    def fake_fetch(url: str) -> tuple[int, bytes]:
        return 200, page

    with db_session() as session:
        verify_pending_evidence(session, fetch=fake_fetch)

    with db_session() as session:
        row = session.get(Evidence, evidence["id"])
        assert row.verification == "verified"
        # snapshot_path is a store uri now; the sha256 stays the authority.
        assert row.snapshot_path.startswith("file://")
        assert row.snapshot_sha256 == hashlib.sha256(page).hexdigest()
    assert store().get_bytes(f"evidence/{evidence['id']}.snapshot") == page

    served = client.get(f"/observe/evidence/{evidence['id']}/snapshot", headers=ADMIN)
    assert served.status_code == 200
    assert served.text == page.decode()

    # Absent snapshots are a 404, not a stack trace.
    assert client.get("/observe/evidence/evid_missing/snapshot",
                      headers=ADMIN).status_code == 404


def test_agent_artifact_put_stores_deliverables(client):
    project = make_project(client)
    run_id, ceo_token = live_token(project["id"], project["ceo_agent_id"])

    content = b"# Final report\n\nEverything shipped."
    response = client.post(
        "/agent/artifacts",
        json={"filename": "report.md",
              "content_base64": base64.b64encode(content).decode()},
        headers=bearer(ceo_token),
    )
    assert response.status_code == 200, response.text
    uri = response.json()["uri"]
    assert uri.startswith("file://")
    # Stored under the platform-chosen key, never an agent-chosen path.
    assert store().get_bytes(f"projects/{project['id']}/{run_id}/report.md") == content


def test_key_for_uri_resolves_a_citation_back_to_the_store(tmp_path):
    from yaaf.core.artifacts import key_for_uri

    uri = store().put_bytes("projects/p1/r1/deck.html", b"<html>charts</html>")
    assert key_for_uri(uri) == "projects/p1/r1/deck.html"
    # A path outside the store root is not a key, however file-like it looks.
    assert key_for_uri(f"file://{tmp_path / 'elsewhere.html'}") is None
    assert key_for_uri("s3://not-our-bucket/projects/p1/r1/deck.html") is None
    assert key_for_uri("https://example.org/deck.html") is None


def test_a_non_author_can_read_the_shipped_bytes(client):
    """The independent-check invariant: an agent that did not write a deliverable
    can read what was actually shipped, addressing it by the uri it was cited
    under. Without this a shipped artifact is unfalsifiable — the sandbox mounts
    only the workspace, so the uri's path resolves to nothing."""
    project = make_project(client)
    _, author_token = live_token(project["id"], project["ceo_agent_id"])

    content = b"<html><!-- revenue: $4.2M --><svg id='chart'/></html>"
    put = client.post(
        "/agent/artifacts",
        json={"filename": "deck.html", "content_base64": base64.b64encode(content).decode()},
        headers=bearer(author_token),
    ).json()
    assert put["sha256"] == hashlib.sha256(content).hexdigest()

    # A different run, holding only the uri the author published.
    _, checker_token = live_token(project["id"], project["ceo_agent_id"])
    read = client.get("/agent/artifacts/content", params={"ref": put["uri"]},
                      headers=bearer(checker_token))
    assert read.status_code == 200, read.text
    body = read.json()
    assert body["text"] == content.decode()
    assert body["sha256"] == put["sha256"] and body["size"] == len(content)
    assert body["truncated"] is False

    # The key from artifact_list addresses the same bytes as the uri.
    listed = client.get("/agent/artifacts", headers=bearer(checker_token)).json()["artifacts"]
    assert [row["name"] for row in listed] == ["deck.html"]
    assert listed[0]["key"] == put["key"]
    by_key = client.get("/agent/artifacts/content", params={"ref": put["key"]},
                        headers=bearer(checker_token)).json()
    assert by_key["sha256"] == put["sha256"]


def test_artifact_get_pages_a_large_file_and_keeps_the_whole_file_digest(client):
    project = make_project(client)
    _, token = live_token(project["id"], project["ceo_agent_id"])
    content = ("line\n" * 40_000).encode()  # 200 KB, past the 64 KB read cap
    client.post(
        "/agent/artifacts",
        json={"filename": "report.md", "content_base64": base64.b64encode(content).decode()},
        headers=bearer(token),
    )

    # A bare filename is not a key; the store is addressed, not browsed.
    assert client.get("/agent/artifacts/content", params={"ref": "report.md"},
                      headers=bearer(token)).status_code == 404

    key = client.get("/agent/artifacts", headers=bearer(token)).json()["artifacts"][0]["key"]
    pages, offset = [], 0
    while True:
        body = client.get("/agent/artifacts/content",
                          params={"ref": key, "offset": offset},
                          headers=bearer(token)).json()
        assert body["sha256"] == hashlib.sha256(content).hexdigest()  # whole file, every page
        assert body["returned"] <= 64 * 1024
        pages.append(body["text"])
        if not body["truncated"]:
            break
        offset += body["returned"]
    assert "".join(pages) == content.decode()


def test_artifact_get_refuses_other_projects_and_missing_bytes(client):
    """A run token reads its own project's store, and a uri that was cited is not
    evidence the bytes exist."""
    mine = make_project(client)
    theirs = make_project(client, slug="neighbour")
    _, their_token = live_token(theirs["id"], theirs["ceo_agent_id"])
    my_run, my_token = live_token(mine["id"], mine["ceo_agent_id"])

    put = client.post(
        "/agent/artifacts",
        json={"filename": "secret.md", "content_base64": base64.b64encode(b"theirs").decode()},
        headers=bearer(their_token),
    ).json()

    for ref in (put["uri"], put["key"],
                f"projects/{mine['id']}/../{theirs['id']}/r/secret.md"):
        blocked = client.get("/agent/artifacts/content", params={"ref": ref},
                             headers=bearer(my_token))
        assert blocked.status_code == 404, ref
        assert "artifact_list" in blocked.json()["error"]

    # A well-formed key for this project whose bytes were never written is an
    # ANSWER, not a wall: "it isn't there" is the finding a verifier is sent to
    # produce, and refusals are capped and revoke the token at the cap.
    with db_session() as session:
        before = session.get(Run, my_run).refusal_count
    absent = client.get(
        "/agent/artifacts/content",
        params={"ref": f"projects/{mine['id']}/run_ghost/never-shipped.html"},
        headers=bearer(my_token),
    )
    assert absent.status_code == 200
    assert absent.json()["exists"] is False
    assert "not proof" in absent.json()["detail"]
    with db_session() as session:
        assert session.get(Run, my_run).refusal_count == before


def test_agent_artifact_put_refuses_traversal_and_oversize(client):
    project = make_project(client)
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])
    payload = base64.b64encode(b"attack").decode()

    traversal = client.post(
        "/agent/artifacts",
        json={"filename": "../x", "content_base64": payload},
        headers=bearer(ceo_token),
    )
    assert traversal.status_code == 422
    assert "path" in traversal.json()["error"]

    oversize = base64.b64encode(b"x" * (5 * 1024 * 1024 + 1)).decode()
    too_big = client.post(
        "/agent/artifacts",
        json={"filename": "big.bin", "content_base64": oversize},
        headers=bearer(ceo_token),
    )
    assert too_big.status_code == 413


def test_stash_trace_writes_store_uri_and_observe_serves_it(client, tmp_path):
    from yaaf.runner.daemon import _stash_trace
    from yaaf.runner.types import RunJob

    project = make_project(client)
    run_id, _ = live_token(project["id"], project["ceo_agent_id"])
    workspace = tmp_path / "ws"
    (workspace / ".yaaf").mkdir(parents=True)
    (workspace / ".yaaf" / f"trace-{run_id}.jsonl").write_text(
        '{"type": "text", "text": "thinking out loud"}\n'
    )
    job = RunJob(run_id=run_id, project_id=project["id"], agent_id=project["ceo_agent_id"],
                 kind="agent_run", trigger={}, task_id=None, attempt=1,
                 workspace_dir=str(workspace))

    uri = _stash_trace(job)
    assert uri is not None and uri.startswith("file://")
    assert store().exists(f"traces/{run_id}.jsonl")

    with db_session() as session:
        session.get(Run, run_id).trace_path = uri
    served = client.get(f"/observe/runs/{run_id}/trace", headers=ADMIN)
    assert served.status_code == 200
    assert "thinking out loud" in served.text


def test_trace_endpoint_still_serves_plain_path_rows(client, tmp_path):
    """Rows from before the store hold a bare filesystem path; they must keep
    working without a migration."""
    project = make_project(client)
    run_id, _ = live_token(project["id"], project["ceo_agent_id"])
    old_trace = tmp_path / "old-trace.jsonl"
    old_trace.write_text('{"type": "text", "text": "from the before times"}\n')
    with db_session() as session:
        session.get(Run, run_id).trace_path = str(old_trace)

    served = client.get(f"/observe/runs/{run_id}/trace", headers=ADMIN)
    assert served.status_code == 200
    assert "from the before times" in served.text

    # A recorded path whose file is gone is a 404, not a stack trace.
    with db_session() as session:
        session.get(Run, run_id).trace_path = str(tmp_path / "vanished.jsonl")
    assert client.get(f"/observe/runs/{run_id}/trace", headers=ADMIN).status_code == 404
