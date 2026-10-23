"""Platform-level configuration and the credential that pays for it.

Everything else in this system is scoped to a project, because everything else
binds a project. This module owns the handful of things that have to be true
*before* a project exists — starting with how the Founder pays for the
conversation in which a project gets designed.

Two stores, on purpose:

- **`platform_settings`** holds plain configuration (which model the Founder
  runs, what billing mode the platform credential uses, the app-wide Slack
  connection's chairman user and channel-naming convention).
- **Platform-level `Secret` rows** (``project_id IS NULL``) hold the
  credentials themselves — the Founder's model key, the app-wide Slack tokens,
  the GitHub provisioning token — reusing the same envelope encryption every
  other secret gets, rather than inventing a second way to store a key.

The platform credential is decrypted server-side only. **It is never injected
into a sandbox and never reaches a project's agents, under any circumstances:
there is no inheritance and no fallback.** The other direction is allowed, by
an administrator's explicit choice: the Founder may *borrow* one of the LLM
credentials projects pay with (`use_llm_credential`), so an install that holds
one Anthropic key does not have to be given it twice. That moves nothing into
a project — the Founder still runs server-side, on no project's budget. A project that has not attached its
own billing secret cannot run, and that is the intended answer — the Founder's
key pays for product-wide conversations and nothing else. Slack tokens are
different in kind: they are the *platform's* outward wiring, decrypted
server-side by the mirror and never handed to an agent either, so one workspace
can serve every project.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.models import PlatformSetting, Secret, utcnow

#: Name of the platform-level Secret holding the Founder's model credential.
PLATFORM_BILLING_SECRET = "platform-billing"

#: Names of the platform-level Secrets holding the app-wide Slack tokens. A
#: project that names its own secrets overrides these field by field; one that
#: names none inherits all three, which is the whole point — the same workspace
#: serves every project and only the channels differ.
SLACK_BOT_SECRET = "platform-slack-bot"
SLACK_SIGNING_SECRET = "platform-slack-signing"
SLACK_APP_SECRET = "platform-slack-app"

#: Setting keys. Kept as constants so a typo is an ImportError, not a silent
#: read of a setting nobody ever wrote.
BILLING_MODE = "platform.billing_mode"  # "api_key" | "subscription"
FOUNDER_MODEL = "founder.model"
#: The LLM credential the Founder borrows when it holds no key of its own.
FOUNDER_LLM_CREDENTIAL = "founder.llm_credential"
SLACK_CHAIRMAN_USER = "slack.chairman_user"
SLACK_CHANNEL_PREFIX = "slack.channel_prefix"  # "" => the project slug
SLACK_CHANNEL_VISIBILITY = "slack.channel_visibility"  # "private" | "public"
SLACK_AUTO_CREATE = "slack.auto_create_channels"  # "true" | "false"

DEFAULT_FOUNDER_MODEL = "claude-opus-5-5"
DEFAULT_SLACK_VISIBILITY = "private"


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
    # A borrowed credential bills the way it says it does; only the Founder's
    # own key keeps its mode in a setting beside it.
    if _own_row(session) is None and (borrowed := _borrowed_row(session)) is not None:
        return borrowed.llm_mode or "api_key"
    return get_setting(session, BILLING_MODE, "api_key") or "api_key"


def founder_model(session: Session) -> str:
    return get_setting(session, FOUNDER_MODEL, DEFAULT_FOUNDER_MODEL) or DEFAULT_FOUNDER_MODEL


def _own_row(session: Session) -> Secret | None:
    return session.execute(
        select(Secret).where(
            Secret.project_id.is_(None), Secret.name == PLATFORM_BILLING_SECRET)
    ).scalar_one_or_none()


def _borrowed_row(session: Session) -> Secret | None:
    """The LLM credential the Founder was told to use, while it still exists.

    Read by id, not through the menus: hiding a credential takes it off the
    pickers, and must not silently take the Founder offline with it. A deleted
    one simply resolves to nothing, and the Founder says it has no key.
    """
    credential_id = get_setting(session, FOUNDER_LLM_CREDENTIAL)
    if not credential_id:
        return None
    return session.execute(
        select(Secret).where(Secret.id == credential_id, Secret.project_id.is_(None),
                             Secret.llm_mode.is_not(None))
    ).scalar_one_or_none()


def _credential_row(session: Session) -> Secret | None:
    """What pays for the Founder: its own key when it has one, else the LLM
    credential it borrows. One function, so every reader agrees."""
    return _own_row(session) or _borrowed_row(session)


def credential_source(session: Session) -> dict:
    """Where the Founder's key comes from, for a screen to say so. No value."""
    if _own_row(session) is not None:
        return {"source": "own"}
    borrowed = _borrowed_row(session)
    if borrowed is not None:
        return {"source": "llm_credential", "credential_id": borrowed.id,
                "name": borrowed.name}
    return {"source": None}


def use_llm_credential(session: Session, credential_id: str) -> Secret:
    """Have the Founder pay with this LLM credential instead of a key of its own.

    Replaces the Founder's own key if it had one: two places a key could come
    from is how an install ends up rotating one and running on the other. The
    credential stays exactly what it was — a project still has to be assigned
    it to spend on it.
    """
    from yaaf.core import llm_credentials

    row = llm_credentials.resolve(session, credential_id=credential_id)
    if row is None:
        raise NoPlatformCredential(
            "no such LLM credential — pick one listed under Settings → LLM credentials")
    own = _own_row(session)
    if own is not None:
        session.delete(own)
    set_setting(session, FOUNDER_LLM_CREDENTIAL, row.id)
    return row


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
            "no platform credential is configured — point the Founder at one of your "
            "LLM credentials, or give it a key of its own, in Settings → Founder. It "
            "pays for drafting and is never handed to a project's agents."
        )
    return decrypt(row.ciphertext)


def set_credential(session: Session, plaintext: str, *, mode: str = "api_key",
                   provider: str | None = None, base_url: str | None = None) -> None:
    """Install or rotate the platform credential and record its billing mode,
    the provider it pays and — for a server the chairman runs — its address."""
    from yaaf.core.crypto import encrypt
    from yaaf.runtimes.providers import DEFAULT_PROVIDER
    from yaaf.runtimes.providers import provider as seller

    provider = provider or DEFAULT_PROVIDER
    if mode not in {k.mode for k in seller(provider).kinds}:
        raise ValueError(f"unknown billing mode {mode!r}")
    # A key of its own ends any borrowing, so there is one answer to "what does
    # the Founder run on?".
    _forget_borrowed(session)
    row = _own_row(session)
    if row is None:
        row = Secret(
            id=ids.new_id(ids.SECRET),
            project_id=None,
            name=PLATFORM_BILLING_SECRET,
            ciphertext=encrypt(plaintext),
            role_scopes=[],
            kind="billing",
        )
        session.add(row)
    else:
        row.ciphertext = encrypt(plaintext)
    row.provider, row.base_url = provider, base_url
    set_setting(session, BILLING_MODE, mode)


def credential_provider(session: Session) -> str:
    """Which provider the platform credential pays — Anthropic until one says
    otherwise, which is what every row written before there was a choice says."""
    from yaaf.runtimes.providers import DEFAULT_PROVIDER

    row = _credential_row(session)
    return (row.provider if row is not None else None) or DEFAULT_PROVIDER


def _forget_borrowed(session: Session) -> bool:
    row = session.execute(
        select(PlatformSetting).where(PlatformSetting.key == FOUNDER_LLM_CREDENTIAL)
    ).scalar_one_or_none()
    if row is None:
        return False
    session.delete(row)
    return True


def clear_credential(session: Session) -> bool:
    """Remove the platform credential, or stop borrowing one. Returns whether
    there was anything to remove. The borrowed LLM credential itself is never
    touched — projects may be paying with it."""
    forgot = _forget_borrowed(session)
    row = _own_row(session)
    if row is None:
        return forgot
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
    for a project's agents works for the Founder, and vice versa.
    """
    from yaaf.runtimes.providers import credential_env

    row = _credential_row(session)
    env = credential_env(row.provider if row is not None else None,
                         billing_mode(session), credential(session))
    if row is not None and row.base_url:
        # A server the chairman runs: its address travels with its key
        # (yaaf.founder.loop reads it).
        from yaaf.founder.loop import BASE_URL_ENV

        env[BASE_URL_ENV] = row.base_url
    return env


def shape_error(mode: str, plaintext: str, provider: str | None = None) -> str | None:
    """Whether this credential can possibly be what its mode says it is.

    Shared with the per-project billing check so both say the same sentence:
    the message a chairman gets for pasting a subscription token into an
    API-key field should not depend on which form he pasted it into. The
    sentences themselves belong to the provider (yaaf.runtimes.providers).
    """
    from yaaf.runtimes.providers import shape_error as provider_shape_error

    return provider_shape_error(provider, mode, plaintext)


# ── platform-level secrets ──────────────────────────────────────────────────────
#
# The Founder's key above is one of these with a dedicated door, because its
# billing mode travels with it. The rest — the Slack tokens, the GitHub
# provisioning token — are ordinary named secrets at platform scope, and every
# one of them keeps empty `role_scopes`: no agent sandbox ever holds one. That
# is not a convention, it is the reason a single workspace can serve every
# project without widening what an agent can reach.


def secret_row(session: Session, name: str) -> Secret | None:
    return session.execute(
        select(Secret).where(Secret.project_id.is_(None), Secret.name == name)
    ).scalar_one_or_none()


def secret_value(session: Session, name: str) -> str | None:
    """The decrypted platform secret, or None when it was never installed or
    will not decrypt. Callers pick their own failure mode — the admin API turns
    None into a 422, the Slack mirror degrades to mirroring nothing."""
    import logging

    from yaaf.core.crypto import decrypt

    row = secret_row(session, name)
    if row is None:
        return None
    try:
        return decrypt(row.ciphertext)
    except Exception:
        logging.getLogger("yaaf.platform").warning(
            "platform secret %r failed to decrypt", name)
        return None


def set_secret(session: Session, name: str, plaintext: str) -> Secret:
    """Install or rotate a platform secret. Always ungranted: `role_scopes` is
    empty and stays empty, so the value is reachable only server-side."""
    from yaaf.core.crypto import encrypt

    row = secret_row(session, name)
    if row is None:
        row = Secret(
            id=ids.new_id(ids.SECRET),
            project_id=None,
            name=name,
            ciphertext=encrypt(plaintext),
            role_scopes=[],
            kind="service",
        )
        session.add(row)
        return row
    row.ciphertext = encrypt(plaintext)
    row.role_scopes = []
    return row


def clear_secret(session: Session, name: str) -> bool:
    """Remove a platform secret. Returns whether one was there."""
    row = secret_row(session, name)
    if row is None:
        return False
    session.delete(row)
    return True
