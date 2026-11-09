"""A project mailbox's configuration: what the chairman set, and what it resolves to.

Stored as ``project.integrations["mail"]``::

    {
      "enabled": true,                  # false keeps the settings, sends and reads nothing
      "address": "ceo@acme.example",    # From:, and where replies arrive
      "display_name": "Acme",           # optional
      "smtp_host": "smtp.acme.example", "smtp_port": 587, "smtp_security": "starttls",
      "imap_host": "imap.acme.example", "imap_port": 993, "imap_folder": "INBOX",
      "username": "ceo@acme.example",
      "password_secret": "mail-password",   # a project Secret, never the value
      "recipients": ["editor@client.example"],
      "verify_sender": true,
      "accept_new": true                # a listed sender may write first
    }

The password is an ordinary envelope-encrypted project Secret with no role
scopes, so it is decrypted here, server-side, and no agent sandbox ever holds
it — the same key custody as the Slack bot token. An agent cannot email anyone
except by asking the platform to, which is where the recipient list is held.

Written only through ``PUT /admin/projects/{id}/mail``, which validates it:
the generic project PATCH refuses the key, because a recipient list nobody
checked is a recipient list that does not mean anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from email.utils import parseaddr

from sqlalchemy.orm import Session

from yaaf.core.models import Project

KEY = "mail"
PASSWORD_SECRET = "mail-password"
SMTP_SECURITY = ("starttls", "ssl")
MAX_RECIPIENTS = 100

# Deliberately plain: one local part, one dotted domain, nothing a header
# parser could read as two addresses. An address that needs more than this is
# not one the chairman should be allowlisting by typing it into a box.
_ADDRESS = re.compile(r"^[a-z0-9!#$%&'*+/=?^_`{|}~.-]+@[a-z0-9-]+(\.[a-z0-9-]+)+$")


def normalise(address: str | None) -> str | None:
    """The comparable form of an address, or None when it is not one.

    Lower-cased whole: the local part is case-sensitive on paper and on no
    mail system anyone uses, and an allowlist that missed `Editor@` for
    `editor@` would fail exactly the person it was written for.
    """
    if not address or any(c in address for c in "\r\n,;"):
        return None
    _, parsed = parseaddr(address.strip())
    parsed = parsed.strip().lower()
    return parsed if _ADDRESS.match(parsed) else None


def domain_of(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower()


def project_config(project: Project) -> dict | None:
    config = (project.integrations or {}).get(KEY)
    return config if isinstance(config, dict) else None


def recipients_of(project: Project) -> list[str]:
    return list((project_config(project) or {}).get("recipients") or [])


@dataclass(frozen=True)
class MailSettings:
    """A project's mailbox, password decrypted. Never serialised: the one thing
    on it that is not already in the project row is the reason it exists."""

    address: str
    smtp_host: str
    smtp_port: int
    smtp_security: str
    imap_host: str
    imap_port: int
    imap_folder: str
    username: str
    password: str = field(repr=False)
    display_name: str = ""
    recipients: tuple[str, ...] = ()
    verify_sender: bool = True
    # Whether a listed sender may write first. Off, mail is accepted only as a
    # reply to something the project sent.
    accept_new: bool = False

    @property
    def domain(self) -> str:
        return domain_of(self.address)

    def allows(self, address: str | None) -> bool:
        normal = normalise(address)
        return normal is not None and normal in self.recipients


def missing(project: Project) -> list[str]:
    """What the configuration still lacks, in the settings screen's words. An
    empty list is a mailbox the loop can use — once its password decrypts."""
    config = project_config(project)
    if config is None:
        return ["the mailbox is not set up"]
    gaps = []
    for key, label in (("address", "an address"), ("smtp_host", "an SMTP server"),
                       ("imap_host", "an IMAP server"), ("username", "a username"),
                       ("password_secret", "a password")):
        if not config.get(key):
            gaps.append(label)
    return gaps


def enabled(project: Project) -> bool:
    """Configured and switched on — the cheap check, no decryption. What the
    runner asks before it offers the CEO the mail tools."""
    config = project_config(project)
    return bool(config and config.get("enabled", True) and not missing(project))


def resolve(session: Session, project: Project, *,
            even_if_off: bool = False) -> MailSettings | None:
    """The usable mailbox, or None. None covers off, half-configured and a
    password that will not decrypt alike: every caller's answer to all three
    is the same — send nothing, read nothing, say why somewhere visible.

    `even_if_off` is for the settings screen's test alone, so a chairman can
    check a mailbox before he switches it on."""
    if missing(project) or not (even_if_off or enabled(project)):
        return None
    from yaaf.server.slack.bridge import project_secret

    config = project_config(project) or {}
    password = project_secret(session, project.id, config.get("password_secret"))
    if password is None:
        return None
    return MailSettings(
        address=normalise(config["address"]) or config["address"],
        display_name=str(config.get("display_name") or ""),
        smtp_host=str(config["smtp_host"]),
        smtp_port=int(config.get("smtp_port") or 587),
        smtp_security=str(config.get("smtp_security") or "starttls"),
        imap_host=str(config["imap_host"]),
        imap_port=int(config.get("imap_port") or 993),
        imap_folder=str(config.get("imap_folder") or "INBOX"),
        username=str(config["username"]),
        password=password,
        recipients=tuple(r for r in (normalise(x) for x in config.get("recipients") or [])
                         if r),
        verify_sender=bool(config.get("verify_sender", True)),
        accept_new=bool(config.get("accept_new", False)),
    )


def start_state(project: Project) -> str | None:
    """What a project that needs email lacks, as a clause — "it has no email
    address yet" — or None when nothing about email holds it back."""
    if not project.needs_mail or enabled(project):
        return None
    config = project_config(project)
    if config is None:
        return "it has no email address yet"
    if config.get("enabled", True) is False:
        return "its email is switched off"
    return f"its email still needs {', '.join(missing(project))}"


def start_refusal(project: Project) -> str | None:
    """Why a project that needs email cannot start, or resume — or None.

    The one door every start goes through (`POST .../start`, the resume of a
    paused project, the reopen of a finished one) asks this, so the web, the
    CLI and MCP refuse in the same words. A project that does not need email
    is never held back by its mailbox, whatever state the mailbox is in.
    """
    state = start_state(project)
    if state is None:
        return None
    return (f"This project's work runs through email, and {state}. Set it up in "
            "Settings → Email, then start it.")
