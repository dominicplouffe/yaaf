"""Moving every stored secret from one master key to another.

`YAAF_MASTER_KEY` encrypts three things, and this is the one module that has
to know all of them:

- **`secrets.ciphertext`**: every credential, data source URL and token.
- **`platform_settings.value`** where `is_secret`: install-wide secrets.
- **A pending OAuth verifier** inside a project's `integrations` JSON, written
  when someone presses Connect on an OAuth data source and gone fifteen
  minutes later. Rare, but leaving one behind would fail that connect.

All or nothing. Every value is opened with the old key before any is written,
so one unreadable value stops the rotation with nothing changed, and the
caller's single transaction is the whole of it. A store that cannot be fully
read would come out of a partial rotation under two keys, which no running
server could use.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from yaaf.core.models import PlatformSetting, Project, Secret


class RotationError(Exception):
    """Nothing was changed. The message says why."""


@dataclass
class RotationReport:
    secrets: int = 0
    settings: int = 0
    verifiers: int = 0
    unreadable: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.secrets + self.settings + self.verifiers


def fernet(key: str, label: str) -> Fernet:
    try:
        return Fernet(key.strip().encode())
    except (ValueError, TypeError) as exc:
        raise RotationError(
            f"the {label} key is not a Fernet key (32 url-safe base64 bytes, with "
            f"padding — `openssl rand -base64 32` makes one): {exc}") from None


def _pending_verifiers(project: Project) -> list[dict]:
    """The `pending` dicts that hold a verifier, as live references into the
    project's JSON so they can be rewritten in place."""
    found = []
    for entry in (project.integrations or {}).get("mcp_servers", []) or []:
        pending = ((entry or {}).get("oauth") or {}).get("pending")
        if isinstance(pending, dict) and pending.get("verifier_cipher"):
            found.append(pending)
    return found


def rotate(session: Session, old_key: str, new_key: str) -> RotationReport:
    """Re-encrypt everything under `new_key`. Raises RotationError, having
    written nothing, if any stored value does not open with `old_key`."""
    old, new = fernet(old_key, "current"), fernet(new_key, "new")
    if old_key.strip() == new_key.strip():
        raise RotationError("the new key is the same as the current one")

    secrets = session.execute(select(Secret)).scalars().all()
    settings = session.execute(
        select(PlatformSetting).where(PlatformSetting.is_secret.is_(True))).scalars().all()
    projects = [p for p in session.execute(select(Project)).scalars().all()
                if _pending_verifiers(p)]

    def opened(ciphertext: str, label: str, report: RotationReport) -> bytes | None:
        try:
            return old.decrypt(ciphertext.encode())
        except InvalidToken:
            report.unreadable.append(label)
            return None

    # Pass one: read everything. Nothing is written until all of it opens.
    report = RotationReport()
    plain_secrets = [(s, opened(s.ciphertext, f"secret {s.name} ({s.id})", report))
                     for s in secrets]
    plain_settings = [(s, opened(s.value, f"setting {s.key}", report)) for s in settings]
    plain_verifiers = [(p, pending, opened(pending["verifier_cipher"],
                                           f"OAuth verifier on project {p.id}", report))
                       for p in projects for pending in _pending_verifiers(p)]

    if report.unreadable:
        stored = len(plain_secrets) + len(plain_settings) + len(plain_verifiers)
        if len(report.unreadable) == stored:
            raise RotationError(
                f"none of the {stored} stored values open with the current key. Is "
                "YAAF_MASTER_KEY the key this database was encrypted with? If a "
                "rotation already ran, the database is on the new key: set that one.")
        raise RotationError(
            f"{len(report.unreadable)} of {stored} stored values do not open with the "
            "current key, so nothing was changed. They were written under another key "
            "and are already unreadable. Delete or re-enter them, then rotate:\n  "
            + "\n  ".join(report.unreadable))

    # Pass two: write.
    for row, plain in plain_secrets:
        row.ciphertext = new.encrypt(plain).decode()
        report.secrets += 1
    for row, plain in plain_settings:
        row.value = new.encrypt(plain).decode()
        report.settings += 1
    for project, pending, plain in plain_verifiers:
        pending["verifier_cipher"] = new.encrypt(plain).decode()
        flag_modified(project, "integrations")
        report.verifiers += 1
    session.flush()
    return report
