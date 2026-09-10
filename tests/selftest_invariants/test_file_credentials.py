"""Invariant selftests: a credential can be a FILE, and the platform is the only
thing that ever writes one.

A service-account key, a PEM, a kubeconfig, a .p12 is only usable as a file. The
tempting shortcut — hand the agent the bytes and let it save them — writes key
material into /workspace, which is a git clone an agent commits and pushes from.
So the runner sends the bytes under a carrier variable, the sandbox entrypoint
writes the file outside the workspace at 0600 and repoints the credential's own
variable at the path, and the carrier is deleted before the model exists.

What must stay true, and is asserted below:

- the credential's own variable NEVER holds the bytes, in either lane;
- the file lands outside /workspace, mode 0600, byte-exact including binary;
- the carrier is gone from the environment afterwards;
- a file pasted into the string door is refused, with the fix named;
- a name that is really a path cannot aim a private key anywhere;
- being a file is fixed at creation, exactly as `kind` is;
- role grants still gate the whole thing.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest

from tests.conftest import ADMIN, make_project
from yaaf.core.db import db_session
from yaaf.core.models import Agent, Project
from yaaf.core.naming import FILE_CRED_PREFIX, env_var_name, file_cred_carrier
from yaaf.runner.daemon import _role_secret_env
from yaaf.server.contextpack import role_version_for

SERVICE_ACCOUNT = json.dumps({
    "type": "service_account", "project_id": "demo",
    "private_key": "-----BEGIN PRIVATE KEY-----\nMIIB\n-----END PRIVATE KEY-----\n",
    "client_email": "robot@demo.iam.gserviceaccount.com",
})


def _store(client, project_id, name, body: bytes, filename="key.json", **extra):
    return client.post(
        "/admin/secrets",
        json={"project_id": project_id, "name": name,
              "value": base64.b64encode(body).decode(), "filename": filename, **extra},
        headers=ADMIN,
    )


def _worker_env(project: dict) -> dict:
    """The environment one granted worker's next run would receive."""
    worker_id = project["teams"][0]["agents"][0]["id"]
    with db_session() as session:
        agent = session.get(Agent, worker_id)
        return _role_secret_env(session, session.get(Project, project["id"]),
                                role_version_for(session, agent))


# ── the door ───────────────────────────────────────────────────────────────────

def test_a_file_pasted_into_the_string_door_is_refused(client):
    """The failure this replaces: a service-account JSON stored as a string
    arrives as a variable holding JSON where every Google library expects a
    path, and the error the agent reports names a missing file."""
    project = make_project(client, start=False)

    for body in (SERVICE_ACCOUNT, "-----BEGIN PRIVATE KEY-----\nMIIB\n"):
        refused = client.post("/admin/secrets", headers=ADMIN, json={
            "project_id": project["id"], "name": "google-sa", "value": body})
        assert refused.status_code == 422, refused.text
        # The refusal has to name the fix, or it is just an obstacle.
        assert "file credential" in refused.json()["detail"]
        assert "--from-file" in refused.json()["detail"]

    # And the mirror: the same bytes through the file door are stored.
    stored = _store(client, project["id"], "google-sa", SERVICE_ACCOUNT.encode())
    assert stored.status_code == 200, stored.text
    assert stored.json()["filename"] == "key.json"


def test_an_ordinary_string_credential_is_untouched(client):
    """The narrowness is the point: only the two unambiguous file shapes are
    refused. A multi-line string credential is a legitimate thing to store."""
    project = make_project(client, start=False)
    for value in ("sk_test_abc", "line one\nline two", "[1, 2, 3]", "not json {"):
        ok = client.post("/admin/secrets", headers=ADMIN, json={
            "project_id": project["id"], "name": f"cred-{abs(hash(value))}",
            "value": value})
        assert ok.status_code == 200, (value, ok.text)
        assert ok.json()["filename"] is None


def test_a_filename_that_is_really_a_path_is_refused_not_sanitized(client):
    project = make_project(client, start=False)
    for bad in ("../../etc/passwd", "a/b.json", ".hidden", "x" * 121):
        refused = _store(client, project["id"], "sa", b"{}", filename=bad)
        assert refused.status_code == 422, (bad, refused.text)


def test_an_unnamed_file_is_named_from_the_credential_and_its_shape(client):
    """An empty name means "you name it" rather than an error — a chairman who
    pasted a key should not have to invent a filename to get past the form."""
    project = make_project(client, start=False)

    assert _store(client, project["id"], "google-sa", b"{}",
                  filename="").json()["filename"] == "google_sa.json"
    assert _store(client, project["id"], "deploy-key", b"-----BEGIN OPENSSH KEY-----\n",
                  filename=" ").json()["filename"] == "deploy_key.pem"
    # Nothing recognisable: no extension is the honest answer, not a guess.
    assert _store(client, project["id"], "blob", b"\x00\x01",
                  filename="").json()["filename"] == "blob"


def test_being_a_file_is_fixed_at_creation(client):
    """The twin of the rule that `kind` is fixed. A credential that changed
    shape under a role would change what its variable MEANS mid-flight."""
    project = make_project(client, start=False)
    client.post("/admin/secrets", headers=ADMIN, json={
        "project_id": project["id"], "name": "token", "value": "abc"})

    refused = _store(client, project["id"], "token", b"{}")
    assert refused.status_code == 422
    assert "fixed at creation" in refused.json()["detail"]


def test_a_file_too_large_for_the_environment_is_refused_at_the_door(client):
    """It travels in the run's environment, which execve bounds. Refusing here
    beats a container that will not start."""
    project = make_project(client, start=False)
    refused = _store(client, project["id"], "bundle", b"x" * (200 * 1024))
    assert refused.status_code == 422
    assert "KB" in refused.json()["detail"]


def test_a_credential_cannot_claim_a_platform_variable(client):
    """Role credentials are merged into the run environment last, so a
    credential named `yaaf-api-token` would replace the run's own token — and
    one named for the file carrier would be handed to the materializer."""
    project = make_project(client, start=False)
    for name in ("yaaf-api-token", "yaaf-file-cred--google-sa"):
        refused = client.post("/admin/secrets", headers=ADMIN, json={
            "project_id": project["id"], "name": name, "value": "x"})
        assert refused.status_code == 422, (name, refused.text)
        assert "YAAF_" in refused.json()["detail"]


def test_a_file_credential_is_still_an_ordinary_service_credential(client):
    """Being a file says nothing about who may hold it. That is why it is a
    column and not a `kind`: the export, the roster and the grant checks all
    filter on kind == 'service' and must keep finding it."""
    project = make_project(client, start=False)
    stored = _store(client, project["id"], "google-sa", b"{}", role_scopes=["worker"]).json()
    assert stored["kind"] == "service"

    listed = client.get(f"/admin/secrets?project_id={project['id']}", headers=ADMIN).json()
    row = next(r for r in listed if r["name"] == "google-sa")
    assert row["filename"] == "key.json" and row["role_scopes"] == ["worker"]
    assert "value" not in row


# ── reveal ─────────────────────────────────────────────────────────────────────

def test_the_chairman_reads_a_text_file_back_and_is_told_when_it_is_binary(client):
    project = make_project(client, start=False)
    text = _store(client, project["id"], "google-sa", SERVICE_ACCOUNT.encode()).json()
    binary = _store(client, project["id"], "keystore", b"\x00\x01\x02\xff",
                    filename="store.p12").json()

    shown = client.get(f"/admin/secrets/{text['id']}/value", headers=ADMIN).json()
    # The decoded file, not the base64 — that is the value they recognise.
    assert shown["value"] == SERVICE_ACCOUNT
    assert shown["filename"] == "key.json"

    refused = client.get(f"/admin/secrets/{binary['id']}/value", headers=ADMIN).json()
    assert refused["value"] is None and refused["binary"] is True
    assert refused["size_bytes"] == 4


# ── injection ──────────────────────────────────────────────────────────────────

def test_the_bytes_never_reach_the_credentials_own_variable(client):
    """The leak this whole design exists to prevent. GOOGLE_SA must hold a path
    at the moment the model can read it, and nothing else, ever."""
    project = make_project(client, start=False)
    _store(client, project["id"], "google-sa", SERVICE_ACCOUNT.encode(),
           role_scopes=["worker"])

    env = _worker_env(project)

    assert "GOOGLE_SA" not in env
    carrier = file_cred_carrier("GOOGLE_SA")
    assert carrier in env
    # The key is in no variable in readable form — not even the carrier, which
    # holds it base64'd — and the one that does carry it is the one the
    # entrypoint pops before the model exists.
    assert not [k for k, v in env.items() if "gserviceaccount" in v]
    spec = json.loads(env[carrier])
    assert base64.b64decode(spec["b64"]).decode() == SERVICE_ACCOUNT
    assert spec["filename"] == "key.json"


def test_a_string_credential_is_injected_exactly_as_before(client):
    project = make_project(client, start=False)
    client.post("/admin/secrets", headers=ADMIN, json={
        "project_id": project["id"], "name": "stripe-key", "value": "sk_test_1",
        "role_scopes": ["worker"]})

    env = _worker_env(project)
    assert env["STRIPE_KEY"] == "sk_test_1"
    assert not [k for k in env if k.startswith(FILE_CRED_PREFIX)]


def test_an_ungranted_role_gets_neither_the_path_nor_the_bytes(client):
    project = make_project(client, start=False)
    _store(client, project["id"], "google-sa", SERVICE_ACCOUNT.encode(), role_scopes=[])

    env = _worker_env(project)
    assert "GOOGLE_SA" not in env
    assert file_cred_carrier("GOOGLE_SA") not in env


def test_the_prompt_tells_the_agent_it_holds_a_path(client):
    """A capability is only a capability if the agent knows what it is. And the
    standing rule — never write a credential to a file — must survive intact,
    because the platform did the writing."""
    from yaaf.core.naming import announced_credentials
    from yaaf.server.recognizers import credentials_prompt

    project = make_project(client, start=False)
    _store(client, project["id"], "google-sa", SERVICE_ACCOUNT.encode(),
           role_scopes=["worker"])

    text = credentials_prompt(*announced_credentials(_worker_env(project)))
    assert "GOOGLE_SA" in text
    assert FILE_CRED_PREFIX not in text  # never announce a variable that is deleted
    assert "PATH to key.json" in text
    assert "never print, log, or write them to files" in text
    assert "gserviceaccount" not in text  # the prompt is stored; the value is not


# ── materialization, inside the sandbox ────────────────────────────────────────

@pytest.fixture()
def sandbox(monkeypatch, tmp_path):
    """The entrypoint, with its credential directory pointed somewhere writable.

    Importing it needs the run variables it reads at module scope; this is the
    same trick test_data_fetch.py uses to exercise sandbox code without Docker.
    """
    for key, value in {"YAAF_API_BASE_URL": "http://localhost", "YAAF_API_TOKEN": "t",
                       "YAAF_RUN_ID": "run_1"}.items():
        monkeypatch.setenv(key, value)
    import yaaf.runner.sandbox_entry as entry

    monkeypatch.setattr(entry, "CREDENTIAL_DIR", tmp_path / "credentials")
    entry.CREDENTIAL_PROBLEMS.clear()
    return entry


def _carry(monkeypatch, name: str, body: bytes, filename: str) -> None:
    monkeypatch.setenv(file_cred_carrier(name), json.dumps(
        {"filename": filename, "b64": base64.b64encode(body).decode()}))


def test_the_file_is_written_outside_the_workspace_at_0600(sandbox, monkeypatch):
    _carry(monkeypatch, "GOOGLE_SA", SERVICE_ACCOUNT.encode(), "key.json")

    written = sandbox.materialize_file_credentials()

    path = Path(written["GOOGLE_SA"])
    assert os.environ["GOOGLE_SA"] == str(path)
    assert path.read_text() == SERVICE_ACCOUNT
    # ssh refuses a key anyone else can read, and so does this.
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert path.parent.parent == sandbox.CREDENTIAL_DIR
    # The git-clone leak, stated as an assertion rather than a comment.
    assert not path.is_relative_to("/workspace")
    assert path.name == "key.json"


def test_the_carrier_is_gone_before_the_model_exists(sandbox, monkeypatch):
    """Popped, not merely unused: every Bash subprocess the agent spawns
    inherits this environment, and `env_list` reads it directly."""
    _carry(monkeypatch, "GOOGLE_SA", SERVICE_ACCOUNT.encode(), "key.json")

    sandbox.materialize_file_credentials()

    assert file_cred_carrier("GOOGLE_SA") not in os.environ
    assert not [k for k in os.environ if k.startswith(FILE_CRED_PREFIX)]


def test_binary_and_trailing_newlines_survive_byte_for_byte(sandbox, monkeypatch):
    """A .p12 is not text and a PEM's trailing newline is load-bearing — which
    is why the value is stored as base64 rather than as a string."""
    keystore = bytes(range(256))
    _carry(monkeypatch, "KEYSTORE", keystore, "store.p12")
    _carry(monkeypatch, "DEPLOY_KEY", b"-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n", "id_ed25519")

    written = sandbox.materialize_file_credentials()

    assert Path(written["KEYSTORE"]).read_bytes() == keystore
    assert Path(written["DEPLOY_KEY"]).read_bytes().endswith(b"\n")


def test_a_filename_that_escapes_is_reduced_to_its_basename(sandbox, monkeypatch):
    """The door already refuses this. Attack it from the other side — a
    hand-edited row — because the string is about to become a path and the door
    is a different process on a different machine."""
    _carry(monkeypatch, "DEPLOY_KEY", b"key", "../../workspace/id_rsa")

    written = sandbox.materialize_file_credentials()

    path = Path(written["DEPLOY_KEY"])
    assert path.parent.parent == sandbox.CREDENTIAL_DIR
    assert path.name == "id_rsa"
    assert not path.is_relative_to("/workspace")


def test_a_credential_that_cannot_be_written_says_so_in_the_run_summary(sandbox, monkeypatch):
    """A silent absence sends an agent hunting for causes in the wrong place —
    the same reason a data source that will not mount announces itself."""
    monkeypatch.setenv(file_cred_carrier("BROKEN"), "not json at all")
    _carry(monkeypatch, "NAMELESS", b"x", "..")

    sandbox.materialize_file_credentials()

    assert any("BROKEN" in line for line in sandbox.CREDENTIAL_PROBLEMS)
    assert any("NAMELESS" in line for line in sandbox.CREDENTIAL_PROBLEMS)
    assert "BROKEN" not in os.environ and "NAMELESS" not in os.environ


def test_the_carrier_prefix_matches_the_platforms(sandbox):
    """sandbox_entry is copied into the image alone and cannot import the yaaf
    package, so it mirrors this constant. A mirror that drifts injects nothing
    and explains nothing — hold the two together here."""
    assert sandbox.FILE_CRED_PREFIX == FILE_CRED_PREFIX


# ── the needs loop, end to end ─────────────────────────────────────────────────

def test_a_need_is_fulfilled_with_a_file_and_the_ceo_is_told_it_is_a_path(client):
    """'I need a Google service account' is a need agents actually file, and
    the answer to it is a file. One motion on the chairman's side."""
    from tests.conftest import bearer, live_token

    project = make_project(client)
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])
    filed = client.post("/agent/needs", headers=bearer(ceo_token), json={
        "title": "Google Sheets access", "roles": ["worker"],
        "why": "The reporting agent cannot read the sheet without a service account.",
        "suggested_secret_name": "google-sa"})
    assert filed.status_code == 200, filed.text
    approval_id = filed.json()["need_id"]

    done = client.post(f"/admin/approvals/{approval_id}/fulfill", headers=ADMIN, json={
        "secret_name": "google-sa", "roles": ["worker"], "filename": "key.json",
        "value": base64.b64encode(SERVICE_ACCOUNT.encode()).decode()})
    assert done.status_code == 200, done.text
    assert done.json()["env_var_hint"] == env_var_name("google-sa") == "GOOGLE_SA"
    assert done.json()["filename"] == "key.json"

    # The variable the CEO is told about is the one the runner will inject, and
    # the CEO is told it holds a path — not the key.
    env = _worker_env(project)
    assert file_cred_carrier("GOOGLE_SA") in env and "GOOGLE_SA" not in env

    roster = client.get(f"/admin/secrets?project_id={project['id']}", headers=ADMIN).json()
    assert next(r for r in roster if r["name"] == "google-sa")["filename"] == "key.json"

    # Fulfilling again without the file would leave the granted worker reading a
    # variable whose meaning changed under it. Same refusal as the /secrets door.
    downgrade = client.post(f"/admin/approvals/{approval_id}/fulfill", headers=ADMIN, json={
        "secret_name": "google-sa", "roles": ["worker"], "value": "just-a-token"})
    assert downgrade.status_code in (409, 422)


# ── it survives the things that copy a project ─────────────────────────────────

def test_export_carries_the_fact_and_never_the_bytes(client):
    project = make_project(client, start=False)
    _store(client, project["id"], "google-sa", SERVICE_ACCOUNT.encode(),
           role_scopes=["worker"])

    bundle = client.get(f"/admin/projects/{project['id']}/export", headers=ADMIN).json()
    row = next(s for s in bundle["secrets"] if s["name"] == "google-sa")
    # What you have to re-supply on import is a FILE. Saying only "google-sa"
    # sends the chairman looking for a value to retype.
    assert row["file"] is True
    # The file's own name stays behind with its bytes: a key file is usually
    # named after the account it opens, and a setup file gets shared.
    assert "key.json" not in json.dumps(bundle)
    assert "gserviceaccount" not in json.dumps(bundle)


def test_a_full_backup_carries_the_fact_along_with_the_value(client):
    """The name-only export carries `file`; the full backup carries the VALUE,
    and without the filename beside it a file credential restores as a string
    holding base64 — which nothing downstream can detect, since base64 begins
    with neither `{` nor `-----BEGIN`."""
    project = make_project(client, start=False)
    _store(client, project["id"], "google-sa", SERVICE_ACCOUNT.encode())

    bundle = client.get(f"/admin/projects/{project['id']}/export?include_values=true",
                        headers=ADMIN).json()
    row = next(s for s in bundle["secrets"] if s["name"] == "google-sa")
    assert row["filename"] == "key.json"
    assert base64.b64decode(row["value"]).decode() == SERVICE_ACCOUNT


def test_a_corrupt_file_row_is_refused_with_a_reason_not_a_500(client):
    """Only a hand-edited row reaches this, but a deliberate read answering with
    a stack trace tells the chairman nothing about what to do."""
    from yaaf.core.crypto import encrypt
    from yaaf.core.models import Secret

    project = make_project(client, start=False)
    stored = _store(client, project["id"], "google-sa", b"{}").json()
    with db_session() as session:
        session.get(Secret, stored["id"]).ciphertext = encrypt("not base64 at all!!")

    answered = client.get(f"/admin/secrets/{stored['id']}/value", headers=ADMIN)
    assert answered.status_code == 422
    assert "store it again" in answered.json()["detail"]


def test_a_clone_stays_a_file_credential(client):
    """Missing this would silently downgrade the twin's credential to a string,
    and the first run on it would hand an agent base64 where a path belongs."""
    project = make_project(client, start=False)
    _store(client, project["id"], "google-sa", SERVICE_ACCOUNT.encode(),
           role_scopes=["worker"])

    twin = client.post(f"/admin/projects/{project['id']}/clone", headers=ADMIN,
                       json={"slug": "acme-two", "name": "Acme Two"})
    assert twin.status_code == 200, twin.text

    listed = client.get(f"/admin/secrets?project_id={twin.json()['id']}",
                        headers=ADMIN).json()
    assert next(r for r in listed if r["name"] == "google-sa")["filename"] == "key.json"


def test_two_credentials_naming_the_same_file_do_not_collide(sandbox, monkeypatch):
    """A chairman with a production and a staging service account calls both
    key.json. Written into one directory the second silently overwrote the
    first, and $GOOGLE_SA served the staging key — a credential quietly becoming
    a different credential. Each gets a directory named for its variable, which
    the runner guarantees is unique."""
    _carry(monkeypatch, "GOOGLE_SA", b"PROD KEY", "key.json")
    _carry(monkeypatch, "GOOGLE_SA_STAGING", b"STAGING KEY", "key.json")

    written = sandbox.materialize_file_credentials()

    assert written["GOOGLE_SA"] != written["GOOGLE_SA_STAGING"]
    assert Path(written["GOOGLE_SA"]).read_bytes() == b"PROD KEY"
    assert Path(written["GOOGLE_SA_STAGING"]).read_bytes() == b"STAGING KEY"
    # The file keeps its own name — the extension is why it was given one.
    assert Path(written["GOOGLE_SA"]).name == "key.json"
