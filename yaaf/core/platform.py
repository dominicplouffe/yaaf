"""Platform-level configuration and the credential that pays for it.

Everything else in this system is scoped to a project, because everything else
binds a company. This module owns the handful of things that have to be true
*before* a project exists — starting with how the Founder pays for the
conversation in which a project gets designed.

Two stores, on purpose:

- **`platform_settings`** holds plain configuration (which model the Founder
  runs, what billing mode the platform credential uses).
- **A platform-level `Secret`** (``project_id IS NULL``) holds the credential
  itself, reusing the same envelope encryption every other secret gets. This
  follows the precedent set by the platform "github" token in
  ``yaaf.server.github`` rather than inventing a second way to store a key.

The platform credential is decrypted server-side only. It is never injected
into a sandbox and never reaches a project's agents: a project still pays its
own way with its own attached billing secret.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.models import PlatformSetting, Secret, utcnow

#: Name of the platform-level Secret holding the Founder's model credential.
PLATFORM_BILLING_SECRET = "platform-billing"

#: Setting keys. Kept as constants so a typo is an ImportError, not a silent
#: read of a setting nobody ever wrote.
BILLING_MODE = "platform.billing_mode"  # "api_key" | "subscription"
FOUNDER_MODEL = "founder.model"

DEFAULT_FOUNDER_MODEL = "claude-opus-5"


class NoPlatformCredential(RuntimeError):
    """Raised when a platform-wide conversation is attempted with no key.

    Deliberately its own type: the API turns it into a 422 the chairman can
    act on, and the fire drill keeps working without one.
    """


def get_setting(session: Session, key: str, default: str | None = None) -> str | None:
    row = session.execute(
        select(PlatformSetting).where(PlatformSetting.key == key)
    ).scalar_one_or_none()
    if row is None:
        return default
    if row.is_secret:
        from yaaf.core.crypto import decrypt

        return decrypt(row.value)
    return row.value


def set_setting(session: Session, key: str, value: str, *, is_secret: bool = False) -> None:
    if is_secret:
        from yaaf.core.crypto import encrypt

        stored = encrypt(value)
    else:
        stored = value
    row = session.execute(
        select(PlatformSetting).where(PlatformSetting.key == key)
    ).scalar_one_or_none()
    if row is None:
        session.add(PlatformSetting(
            id=ids.new_id(ids.PLATFORM_SETTING), key=key, value=stored, is_secret=is_secret))
        return
    row.value = stored
    row.is_secret = is_secret
    row.updated_at = utcnow()


def billing_mode(session: Session) -> str:
    return get_setting(session, BILLING_MODE, "api_key") or "api_key"


def founder_model(session: Session) -> str:
    return get_setting(session, FOUNDER_MODEL, DEFAULT_FOUNDER_MODEL) or DEFAULT_FOUNDER_MODEL


def _credential_row(session: Session) -> Secret | None:
    return session.execute(
        select(Secret).where(
            Secret.project_id.is_(None), Secret.name == PLATFORM_BILLING_SECRET)
    ).scalar_one_or_none()


def has_credential(session: Session) -> bool:
    """Whether a platform conversation can be paid for at all.

    Cheap and side-effect free — the UI asks this to decide whether to offer
    the Founder, and it must not decrypt anything to answer.
    """
    return _credential_row(session) is not None


def credential(session: Session) -> str:
    """The decrypted platform credential, or raise :class:`NoPlatformCredential`."""
    from yaaf.core.crypto import decrypt

    row = _credential_row(session)
    if row is None:
        raise NoPlatformCredential(
            "no platform credential is configured. Set one in Settings so "
            "product-wide conversations (the Founder) can run; a project's own "
            "billing key is never used for them."
        )
    return decrypt(row.ciphertext)


def set_credential(session: Session, plaintext: str, *, mode: str = "api_key") -> None:
    """Install or rotate the platform credential and record its billing mode."""
    from yaaf.core.crypto import encrypt

    if mode not in {"api_key", "subscription"}:
        raise ValueError(f"unknown billing mode {mode!r}")
    row = _credential_row(session)
    if row is None:
        session.add(Secret(
            id=ids.new_id(ids.SECRET),
            project_id=None,
            name=PLATFORM_BILLING_SECRET,
            ciphertext=encrypt(plaintext),
            role_scopes=[],
            kind="billing",
        ))
    else:
        row.ciphertext = encrypt(plaintext)
    set_setting(session, BILLING_MODE, mode)


def clear_credential(session: Session) -> bool:
    """Remove the platform credential. Returns whether one was there."""
    row = _credential_row(session)
    if row is None:
        return False
    session.delete(row)
    return True


def founder_env(session: Session) -> dict[str, str]:
    """The credential, as the environment the Claude Code CLI reads.

    This is deliberately the same two-line split every agent already runs
    under (:func:`yaaf.runner.daemon._billing_env`): a subscription token is
    consumed by the CLI, which owns the OAuth exchange, and is not a Messages
    API credential at all. An earlier version of this module handed the token
    to the raw Messages API as a bearer token; it 401'd on every subscription,
    because that is not what the token is for.

    One credential story for the whole product is the point. A key that works
    for a company's agents works for the Founder, and vice versa.
    """
    key = credential(session)
    if billing_mode(session) == "subscription":
        return {"CLAUDE_CODE_OAUTH_TOKEN": key}
    return {"ANTHROPIC_API_KEY": key}


def shape_error(mode: str, plaintext: str) -> str | None:
    """Whether this credential can possibly be what its mode says it is.

    Shared with the per-project billing check so both say the same sentence:
    the message a chairman gets for pasting a subscription token into an
    API-key field should not depend on which form he pasted it into.
    """
    is_oauth = plaintext.startswith("sk-ant-oat")
    if mode == "subscription" and not is_oauth:
        return ("that credential is not a subscription token (sk-ant-oat…). Run "
                "`claude setup-token` and paste the result — or switch to API-key mode.")
    if mode == "api_key" and is_oauth:
        return ("that credential is a subscription OAuth token, not an API key "
                "(sk-ant-api…). Paste a key from the Anthropic console — or switch "
                "to subscription mode.")
    return None
