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
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from yaaf.core import plan_usage, platform
from yaaf.core.models import Secret
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


class LlmCredentialIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    mode: str = Field(pattern="^(api_key|subscription)$")
    value: str = Field(min_length=8, max_length=10000)


class LlmCredentialPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    value: str | None = Field(default=None, min_length=8, max_length=10000)


def _llm_summary(session: Session, row: Secret, projects: list | None = None) -> dict:
    from sqlalchemy import select

    from yaaf.core.models import Project

    if projects is None:
        projects = session.scalars(select(Project).where(Project.billing_secret_id == row.id)
                                   .order_by(Project.name)).all()
    return {"id": row.id, "name": row.name, "mode": row.llm_mode,
            "created_at": row.created_at,
            "projects": [{"id": p.id, "name": p.name} for p in projects]}


def _save_llm(session: Session, row: Secret, name: str, value: str | None) -> dict:
    from sqlalchemy import func, select
    from sqlalchemy.exc import IntegrityError

    from yaaf.core.crypto import encrypt
    from yaaf.core.models import Secret
    from yaaf.server.api.admin import _audit

    name = name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Enter a credential name")
    if name.lower().startswith("platform-") or name.lower() == "github":
        raise HTTPException(status_code=422, detail="This name is reserved for a platform connection")
    duplicate = session.scalar(select(Secret.id).where(
        Secret.project_id.is_(None), func.lower(Secret.name) == name.lower(), Secret.id != row.id))
    if duplicate:
        raise HTTPException(status_code=409, detail="A credential with this name already exists")
    if value is not None:
        value = value.strip()
        if len(value) < 8:
            raise HTTPException(status_code=422, detail="Enter a credential of at least 8 characters")
        problem = platform.shape_error(row.llm_mode, value)
        if problem:
            raise HTTPException(status_code=422, detail=problem)
        row.ciphertext = encrypt(value)
        plan_usage.forget(row.id)
    row.name = name
    session.add(row)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="A credential with this name already exists") from exc
    _audit(session, None, "llm_credential.save", "secret", row.id,
           {"name": name, "rotated": value is not None})
    return _llm_summary(session, row)


@router.get("/llm-credentials")
def list_llm_credentials(session: Session = Depends(db)) -> list:
    from sqlalchemy import select

    from yaaf.core.models import Project, Secret

    usage: dict[str, list] = {}
    for project in session.scalars(select(Project).where(Project.billing_secret_id.is_not(None))
                                  .order_by(Project.name)):
        usage.setdefault(project.billing_secret_id, []).append(project)
    return [_llm_summary(session, row, usage.get(row.id, [])) for row in session.scalars(
        select(Secret).where(Secret.llm_mode.is_not(None)).order_by(Secret.name))]


@router.post("/llm-credentials", status_code=201)
def create_llm_credential(body: LlmCredentialIn, session: Session = Depends(db)) -> dict:
    from yaaf.core import ids
    from yaaf.core.models import Secret

    row = Secret(id=ids.new_id(ids.SECRET), project_id=None, name=body.name,
                 kind="billing", role_scopes=[], llm_mode=body.mode)
    return _save_llm(session, row, body.name, body.value)


def _managed(session: Session, credential_id: str) -> Secret:
    """The row to edit or delete, locked — or a 404, because on this screen an
    id that names nothing is a credential somebody else already removed, not
    a bad choice on a project form."""
    from yaaf.core.llm_credentials import assigned_credential, resolve

    if resolve(session, credential_id=credential_id) is None:
        raise HTTPException(status_code=404, detail="unknown LLM credential")
    return assigned_credential(session, credential_id)


@router.patch("/llm-credentials/{credential_id}")
def update_llm_credential(credential_id: str, body: LlmCredentialPatch,
                          session: Session = Depends(db)) -> dict:
    row = _managed(session, credential_id)
    return _save_llm(session, row, body.name, body.value)


@router.delete("/llm-credentials/{credential_id}")
def delete_llm_credential(credential_id: str, session: Session = Depends(db)) -> dict:
    from yaaf.server.api.admin import delete_secret

    _managed(session, credential_id)
    return delete_secret(credential_id, session)


@router.get("/llm-credentials/{credential_id}/usage")
def llm_credential_usage(credential_id: str, refresh: bool = False,
                         session: Session = Depends(db)) -> dict:
    """What Anthropic says is left on the plan behind a subscription credential.

    Read-only and unaudited: this is polled by screens, and the value it reads
    is the same one the token's owner sees under `/usage`. The token never
    leaves the server — it is decrypted here, sent to Anthropic, and the
    response carries only percentages and reset times. `refresh=true` skips
    the minute-long cache for a reader who just watched a run finish.

    API keys have no plan meter; the reading for one says so rather than 404,
    because the screen asking is the same screen either way.
    """
    from yaaf.core.crypto import decrypt
    from yaaf.core.llm_credentials import resolve

    row = resolve(session, credential_id=credential_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown LLM credential")
    credential_id, mode = row.id, row.llm_mode
    token = decrypt(row.ciphertext) if mode == "subscription" else ""
    # Everything this needs from the database is in hand. Anthropic may take
    # eight seconds to answer, and a pooled connection held open for that long
    # by every polling screen is how an outage over there becomes one here.
    session.close()
    return {"credential_id": credential_id, "mode": mode,
            **plan_usage.reading(credential_id, mode, token, refresh=refresh)}
