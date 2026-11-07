"""Invariant selftests: rotating the master key moves everything, or nothing.

Before `yaaf rotate-master-key` existed, changing YAAF_MASTER_KEY stranded
every stored credential, and the only way to "rotate" was to re-enter all of
them by hand. The rules held here:

- every place a ciphertext lives is rotated (secrets, secret platform
  settings, a pending OAuth verifier inside a project's JSON);
- one value that does not open with the current key stops the rotation with
  nothing written, because a store under two keys is unusable;
- the native install's key file is replaced and the old key kept, because
  backups taken before the rotation still need it.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified
from typer.testing import CliRunner

from tests.conftest import make_project
from yaaf.cli.main import app as cli
from yaaf.core import ids
from yaaf.core.config import settings
from yaaf.core.crypto import encrypt
from yaaf.core.db import db_session, engine
from yaaf.core.key_rotation import RotationError, rotate
from yaaf.core.models import AuditLog, PlatformSetting, Project, Secret
from yaaf.core.platform import set_setting

NEW = Fernet.generate_key().decode()


def _store(project_id: str) -> None:
    """One of each kind of ciphertext, all under the suite's current key."""
    with db_session() as session:
        session.add(Secret(id=ids.new_id(ids.SECRET), project_id=project_id,
                           name="api-key", ciphertext=encrypt("sk-live-123"),
                           role_scopes=[], kind="service"))
        set_setting(session, "slack_token", "xoxb-456", is_secret=True)
        set_setting(session, "not-a-secret", "plain")
        project = session.get(Project, project_id)
        project.integrations = {"mcp_servers": [
            {"name": "crm", "oauth": {"pending": {"verifier_cipher": encrypt("pkce-789")}}}]}
        flag_modified(project, "integrations")


def _opened_with(key: str) -> dict[str, str]:
    f = Fernet(key.encode())
    with db_session() as session:
        secret = session.execute(select(Secret).where(Secret.name == "api-key")).scalar_one()
        setting = session.execute(
            select(PlatformSetting).where(PlatformSetting.key == "slack_token")).scalar_one()
        plain = session.execute(
            select(PlatformSetting).where(PlatformSetting.key == "not-a-secret")).scalar_one()
        project = session.execute(select(Project)).scalars().first()
        verifier = project.integrations["mcp_servers"][0]["oauth"]["pending"]["verifier_cipher"]
        return {"secret": f.decrypt(secret.ciphertext.encode()).decode(),
                "setting": f.decrypt(setting.value.encode()).decode(),
                "verifier": f.decrypt(verifier.encode()).decode(),
                "plain": plain.value}


@pytest.fixture
def stored(client):
    project_id = make_project(client)["id"]
    _store(project_id)
    return project_id


def test_every_kind_of_ciphertext_moves_to_the_new_key(stored):
    with db_session() as session:
        report = rotate(session, settings().master_key, NEW)
    assert (report.secrets, report.settings, report.verifiers) == (1, 1, 1)
    assert _opened_with(NEW) == {"secret": "sk-live-123", "setting": "xoxb-456",
                                 "verifier": "pkce-789", "plain": "plain"}
    with pytest.raises(InvalidToken):
        _opened_with(settings().master_key)


def test_one_unreadable_value_changes_nothing(stored):
    stray = Fernet.generate_key().decode()
    with db_session() as session:
        session.add(Secret(id=ids.new_id(ids.SECRET), project_id=stored, name="orphan",
                           ciphertext=Fernet(stray.encode()).encrypt(b"x").decode(),
                           role_scopes=[], kind="service"))
    with pytest.raises(RotationError, match="secret orphan"), db_session() as session:
        rotate(session, settings().master_key, NEW)
    assert _opened_with(settings().master_key)["secret"] == "sk-live-123"


def test_the_wrong_current_key_says_so(stored):
    with (pytest.raises(RotationError, match="none of the 3 stored values"),
          db_session() as session):
        rotate(session, Fernet.generate_key().decode(), NEW)


@pytest.mark.parametrize("new, why", [("not-a-key", "not a Fernet key"),
                                      (None, "same as the current one")])
def test_a_bad_new_key_is_refused(stored, new, why):
    with pytest.raises(RotationError, match=why), db_session() as session:
        rotate(session, settings().master_key, new or settings().master_key)


def test_the_cli_rotates_a_server_install_and_records_it(stored):
    done = CliRunner().invoke(cli, ["rotate-master-key", "--yes"],
                              env={"YAAF_NEW_MASTER_KEY": NEW})
    assert done.exit_code == 0, done.output
    assert "Re-encrypted 3 value(s)" in done.output
    assert NEW not in done.output, "a key it was given is not echoed back"
    assert _opened_with(NEW)["secret"] == "sk-live-123"
    with db_session() as session:
        audit = session.execute(
            select(AuditLog).where(AuditLog.action == "master_key.rotate")).scalar_one()
    assert audit.actor == "system:shell" and audit.detail["secrets"] == 1


def test_the_cli_replaces_a_native_key_file_and_keeps_the_old_one(stored, tmp_path,
                                                                   monkeypatch):
    old = settings().master_key
    (tmp_path / "master.key").write_text(old + "\n")
    monkeypatch.setattr(settings(), "master_key", "")
    done = CliRunner().invoke(cli, ["rotate-master-key", "--yes", "--home", str(tmp_path)],
                              env={"YAAF_NEW_MASTER_KEY": NEW,
                                   # str(url) masks a Postgres password as ***.
                                   "YAAF_DATABASE_URL": engine().url.render_as_string(
                                       hide_password=False)})
    assert done.exit_code == 0, done.output
    assert (tmp_path / "master.key").read_text().strip() == NEW
    kept = list(tmp_path.glob("master.key.*.old"))
    assert len(kept) == 1 and kept[0].read_text().strip() == old
    assert oct(kept[0].stat().st_mode & 0o777) == "0o600"
    assert _opened_with(NEW)["verifier"] == "pkce-789"


def test_the_cli_asks_before_it_rotates(stored):
    done = CliRunner().invoke(cli, ["rotate-master-key"], input="n\n",
                              env={"YAAF_NEW_MASTER_KEY": NEW})
    assert done.exit_code == 1 and "Stop yaaf" in done.output
    assert _opened_with(settings().master_key)["secret"] == "sk-live-123"
