"""Invariant selftests: answers travel compressed — except a file's own bytes,
which are an agent's output and are handed over exactly as written."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import ADMIN, make_project
from yaaf.core.config import settings


def _workspace(slug: str) -> Path:
    root = Path(settings().workspaces_root) / slug
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_the_api_answers_compressed_to_a_client_that_asks(client):
    project = make_project(client, slug="squeezed", start=False)
    root = _workspace("squeezed")
    for i in range(200):
        (root / f"note-{i:03d}.md").write_text("x")
    url = f"/observe/projects/{project['id']}/files/browse"

    packed = client.get(url, headers={**ADMIN, "Accept-Encoding": "gzip"})
    assert packed.status_code == 200, packed.text
    assert packed.headers["content-encoding"] == "gzip"
    assert packed.json()["total"] == 200, "and it still reads as the same answer"

    plain = client.get(url, headers={**ADMIN, "Accept-Encoding": "identity"})
    assert "content-encoding" not in plain.headers
    assert plain.json() == packed.json()


def test_a_files_own_bytes_are_never_recompressed(client):
    """A render is already compressed and can be a hundred megabytes; gzipping
    it again on a request thread costs CPU and buys nothing. So the download
    route is left exactly as it was: the bytes, their length, as an attachment."""
    project = make_project(client, slug="squeezed-raw", start=False)
    body = b"the same line, over and over\n" * 4096    # would compress superbly
    (_workspace("squeezed-raw") / "log.txt").write_bytes(body)

    served = client.get(f"/observe/projects/{project['id']}/files/content",
                        params={"path": "log.txt"},
                        headers={**ADMIN, "Accept-Encoding": "gzip"})

    assert served.status_code == 200, served.text
    assert "content-encoding" not in served.headers
    assert served.headers["content-length"] == str(len(body))
    assert served.content == body
    assert served.headers["content-type"].startswith("application/octet-stream")
