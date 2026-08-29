"""App-wide settings: the things that are true above every project.

Two of them, and they are here for opposite reasons.

**The model credential** was already stored (``yaaf.core.platform``) and had
exactly one door: a gate on the Founding screen that appeared when no key was
set and vanished the moment one was. There was no way to see what was
installed, rotate it, test it, or take it away. That is the gap this router
closes; the rule it enforces is unchanged and deliberately restated in the
response — **the platform key never reaches a project's agents.** No
inheritance, no fallback. A project pays its own way or does not run.

**The Slack connection** is new, and exists because of an observation about how
these installs are actually used: one workspace, every project, different
channels. Storing the tokens once and letting a project inherit them turns
"connect Slack" from a per-project chore into a per-project *channel map* —
which the platform can then create for you (see
:mod:`yaaf.server.slack.provision`).

Both stores are the ones that already existed: plain values in
``platform_settings``, credentials as platform-scope ``Secret`` rows with empty
``role_scopes``. Nothing here is injected into a sandbox.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from yaaf.core import platform
from yaaf.server.deps import admin_auth, db
from yaaf.server.slack import provision as slack_provision
from yaaf.server.slack import settings as slack_settings

router = APIRouter(dependencies=[Depends(admin_auth)])


@router.get("")
def get_settings(session: Session = Depends(db)) -> dict:
    """Everything the app-wide settings screen draws, in one call.

    No secret value ever appears here — only whether one is installed. A
    settings page that can show you a bot token is a settings page that can
    leak one into a screenshot, and rotating is the only thing anyone needs.
    """
    return {
        "anthropic": {
            "configured": platform.has_credential(session),
            "billing_mode": platform.billing_mode(session),
            "model": platform.founder_model(session),
            "note": ("This key pays for product-wide conversations — the Founder. It is "
                     "never injected into a sandbox and never reaches a project's agents: "
                     "every project pays its own way with its own billing credential."),
        },
        "slack": slack_settings.platform_summary(session),
    }


# ── the platform model credential ───────────────────────────────────────────────


class CredentialIn(BaseModel):
    value: str = Field(min_length=8)
    mode: str = Field(default="api_key", pattern="^(api_key|subscription)$")


def install_credential(session: Session, value: str, mode: str) -> dict:
    """Store the platform credential after refusing an impossible one.

    The shape check happens before the write on the same promise the
    per-project billing form already makes — *if the key is bad you find out
    now, not at 9am tomorrow* — and says the same sentence, because which form
    a chairman pasted into should not change the answer.

    Shared with ``/admin/founder/credential``, which predates this router and
    stays where the Founding screen expects it.
    """
    value = value.strip()
    problem = platform.shape_error(mode, value)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    platform.set_credential(session, value, mode=mode)
    return {"ok": True, "billing_mode": mode}


def check_credential(session: Session) -> dict:
    """Prove the stored credential reaches a model, by running the cheapest
    real turn there is. Nothing else can be right about a credential."""
    from yaaf.founder import agent as founder_agent
    from yaaf.founder.session import FounderModelError

    if not platform.has_credential(session):
        raise HTTPException(status_code=422, detail="no platform credential is configured")
    try:
        founder_agent.check_credential(session)
    except platform.NoPlatformCredential as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FounderModelError as exc:
        return {"ok": False, "kind": exc.kind, "detail": str(exc)}
    return {"ok": True, "detail": "the credential reached a model."}


@router.put("/anthropic")
def put_anthropic(body: CredentialIn, session: Session = Depends(db)) -> dict:
    return install_credential(session, body.value, body.mode)


@router.post("/anthropic/test")
def test_anthropic(session: Session = Depends(db)) -> dict:
    return check_credential(session)


@router.delete("/anthropic")
def delete_anthropic(session: Session = Depends(db)) -> dict:
    """Remove the platform key. Nothing else breaks: projects never used it,
    and the fire drill and the New Project form never needed it — only the
    Founder conversation stops being offered."""
    return {"removed": platform.clear_credential(session)}


class ModelIn(BaseModel):
    model: str = Field(min_length=3, max_length=64)


@router.put("/anthropic/model")
def put_anthropic_model(body: ModelIn, session: Session = Depends(db)) -> dict:
    platform.set_setting(session, platform.FOUNDER_MODEL, body.model.strip())
    return {"model": body.model.strip()}


# ── the app-wide Slack connection ───────────────────────────────────────────────


class SlackIn(BaseModel):
    """Every field optional, and blank means *keep what is stored*.

    A settings form that cannot be saved without re-pasting three tokens is a
    settings form nobody uses to change the one field they came for.
    """

    bot_token: str | None = Field(default=None, max_length=500)
    signing_secret: str | None = Field(default=None, max_length=500)
    app_token: str | None = Field(default=None, max_length=500)
    chairman_slack_user: str | None = Field(default=None, max_length=40)
    channel_prefix: str | None = Field(default=None, max_length=60)
    channel_visibility: str | None = Field(default=None, pattern="^(private|public)$")
    auto_create_channels: bool | None = None


@router.put("/slack")
def put_slack(body: SlackIn, session: Session = Depends(db)) -> dict:
    """Install or update the Slack connection every project inherits.

    The bot token is shape-checked here for the same reason the model key is:
    ``xoxb-`` is a bot token and ``xapp-`` is an app-level one, they are not
    interchangeable, and swapping them fails later with an error message
    ("invalid_auth") that says nothing about which field was wrong.
    """
    for value, prefix, field_name in (
        (body.bot_token, "xoxb-", "bot token"),
        (body.app_token, "xapp-", "app token"),
    ):
        if value and value.strip() and not value.strip().startswith(prefix):
            raise HTTPException(
                status_code=422,
                detail=f"that does not look like a Slack {field_name} — it should start "
                       f"'{prefix}'. Bot tokens come from OAuth & Permissions; app tokens "
                       "from Basic Information → App-Level Tokens.",
            )
    for value, name in (
        (body.bot_token, platform.SLACK_BOT_SECRET),
        (body.signing_secret, platform.SLACK_SIGNING_SECRET),
        (body.app_token, platform.SLACK_APP_SECRET),
    ):
        if value and value.strip():
            platform.set_secret(session, name, value.strip())
    if body.chairman_slack_user is not None:
        platform.set_setting(session, platform.SLACK_CHAIRMAN_USER,
                             body.chairman_slack_user.strip())
    if body.channel_prefix is not None:
        platform.set_setting(session, platform.SLACK_CHANNEL_PREFIX,
                             slack_settings.slugify(body.channel_prefix)
                             if body.channel_prefix.strip() else "")
    if body.channel_visibility is not None:
        platform.set_setting(session, platform.SLACK_CHANNEL_VISIBILITY, body.channel_visibility)
    if body.auto_create_channels is not None:
        platform.set_setting(session, platform.SLACK_AUTO_CREATE,
                             "true" if body.auto_create_channels else "false")
    session.flush()
    return slack_settings.platform_summary(session)


@router.post("/slack/test")
def test_slack(session: Session = Depends(db)) -> dict:
    """Ask Slack who this token is. Free, unscoped, and the only check that
    can be right about whether the connection actually works."""
    token = platform.secret_value(session, platform.SLACK_BOT_SECRET)
    if token is None:
        raise HTTPException(status_code=422, detail="no app-wide Slack bot token is configured")
    return slack_provision.workspace_check(token)


@router.delete("/slack")
def delete_slack(session: Session = Depends(db)) -> dict:
    """Forget the Slack connection. Projects that named their own tokens are
    untouched; projects that were inheriting stop mirroring and keep every
    message — the bus was always the canonical copy."""
    removed = [
        name for name in (platform.SLACK_BOT_SECRET, platform.SLACK_SIGNING_SECRET,
                          platform.SLACK_APP_SECRET)
        if platform.clear_secret(session, name)
    ]
    platform.set_setting(session, platform.SLACK_CHAIRMAN_USER, "")
    return {"removed": removed,
            "note": "Projects with their own Slack tokens are unaffected. Nothing was "
                    "deleted in Slack, and no message was lost — the bus is canonical."}
