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
        "llm_credentials": _llm_counts(session),
    }


def _llm_counts(session: Session) -> dict:
    """How many credentials exist, and how many of them mean anything.

    On the screen this is navigation rather than data: "12 credentials" and
    "3 in use, 9 hidden" send an administrator to different places, and the
    difference between those two numbers is the whole reason hiding exists.
    """
    from sqlalchemy import distinct, func, select

    from yaaf.core.llm_credentials import TERMINAL
    from yaaf.core.models import Project

    managed = Secret.llm_mode.is_not(None), Secret.project_id.is_(None)
    total = session.scalar(select(func.count(Secret.id)).where(*managed)) or 0
    hidden = session.scalar(select(func.count(Secret.id))
                            .where(*managed, Secret.hidden_at.is_not(None))) or 0
    in_use = session.scalar(
        select(func.count(distinct(Project.billing_secret_id)))
        .join(Secret, Secret.id == Project.billing_secret_id)
        .where(*managed, Secret.hidden_at.is_(None), Project.status.not_in(TERMINAL))) or 0
    return {"total": int(total), "hidden": int(hidden), "in_use": int(in_use),
            "offerable": int(total) - int(hidden)}


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


def _token_groups(session: Session) -> dict[str, tuple[str | None, int]]:
    """Which credentials are holding the *same* token, by digest.

    The migration that promoted per-project keys named each credential after
    the project variable it came from, and those names lie: a value pasted
    into a variable called `anthropic-api-key` is still a subscription token
    if that is what it is, and twelve rows on one install carry exactly that
    name for one `sk-ant-oat` token. So the name cannot be used to tell two
    credentials apart, and neither can the type — fourteen rows all correctly
    say "Claude subscription" because they are all the same subscription.

    What tells them apart is the value, and the value can never be shown. A
    digest of it can: it identifies without revealing, it is stable across
    reloads, and it turns "fourteen identical-looking rows" into "one
    subscription, keep one of these" — which is the only question an
    administrator is actually trying to answer on this screen.

    Admin-only, and deliberately absent from `llm_credentials.choices`: which
    of a project's options are secretly the same key is the administrator's
    inventory question, not a drafter's.
    """
    from sqlalchemy import select

    from yaaf.core.crypto import decrypt
    from yaaf.core.models import Secret
    from yaaf.core.plan_usage import fingerprint

    digests: dict[str, str | None] = {}
    for row in session.scalars(select(Secret).where(Secret.llm_mode.is_not(None),
                                                    Secret.project_id.is_(None))):
        try:
            digests[row.id] = fingerprint(decrypt(row.ciphertext))[:8]
        except Exception:
            # A value this key cannot read still deserves a row on the screen;
            # it simply cannot be grouped with anything.
            digests[row.id] = None
    sizes: dict[str, int] = {}
    for digest in digests.values():
        if digest is not None:
            sizes[digest] = sizes.get(digest, 0) + 1
    return {secret_id: (digest, sizes.get(digest or "", 1) if digest else 1)
            for secret_id, digest in digests.items()}


def _llm_summary(session: Session, row: Secret, projects: list | None = None,
                 groups: dict[str, tuple[str | None, int]] | None = None) -> dict:
    """One credential as the screen needs it: what it is, and what it is *for*.

    "Assigned" was the only fact this used to carry, and after credentials
    moved above projects it stopped being the interesting one: an install can
    hold a dozen keys every one of which is assigned, all of them to projects
    that ended months ago. So each project says whether it can still spend, and
    the credential says whether any of them can. That single boolean is what
    the page sorts on, what the delete button reads, and what hiding is refused
    over — one definition, computed once, rather than three screens each
    guessing at "in use".
    """
    from sqlalchemy import select

    from yaaf.core.llm_credentials import TERMINAL
    from yaaf.core.models import Project

    if projects is None:
        projects = session.scalars(select(Project).where(Project.billing_secret_id == row.id)
                                   .order_by(Project.name)).all()
    out = [{"id": p.id, "name": p.name, "status": p.status, "live": p.status not in TERMINAL}
           for p in projects]
    if groups is None:
        groups = _token_groups(session)
    token, sharing = groups.get(row.id, (None, 1))
    return {"id": row.id, "name": row.name, "mode": row.llm_mode,
            "created_at": row.created_at, "hidden_at": row.hidden_at,
            "projects": out, "in_use": any(p["live"] for p in out),
            "token": token, "shares_token_with": max(0, sharing - 1)}


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
    # A hidden credential holds no name: it is invisible on this page, so
    # refusing the name it is sitting on would be a refusal pointing at
    # nothing. The partial unique index draws the line in the same place.
    duplicate = session.scalar(select(Secret.id).where(
        Secret.project_id.is_(None), func.lower(Secret.name) == name.lower(),
        Secret.hidden_at.is_(None), Secret.id != row.id))
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
        # Nothing to invalidate: a plan reading is filed under the token, not
        # the credential, so the old token's meter still belongs to whatever
        # else holds it and this credential simply has none until its next run.
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
def list_llm_credentials(include_hidden: bool = False, session: Session = Depends(db)) -> list:
    """Every credential this install can still choose from.

    Hidden ones are absent by default, and that default is what every other
    caller gets: the project picker, the import matcher and the CLI all read
    this route. `include_hidden=true` is the Settings page opening the drawer
    to un-hide or finally delete one — the only screen that has a reason to
    look at a credential nobody may pick.
    """
    from sqlalchemy import select

    from yaaf.core.models import Project, Secret

    usage: dict[str, list] = {}
    for project in session.scalars(select(Project).where(Project.billing_secret_id.is_not(None))
                                  .order_by(Project.name)):
        usage.setdefault(project.billing_secret_id, []).append(project)
    listing = select(Secret).where(Secret.llm_mode.is_not(None))
    if not include_hidden:
        listing = listing.where(Secret.hidden_at.is_(None))
    # Grouped once for the whole listing rather than per row: the digests come
    # from decrypting every managed credential, which is cheap once and N²
    # if each row asks for itself.
    groups = _token_groups(session)
    return [_llm_summary(session, row, usage.get(row.id, []), groups)
            for row in session.scalars(listing.order_by(Secret.name))]


@router.post("/llm-credentials", status_code=201)
def create_llm_credential(body: LlmCredentialIn, session: Session = Depends(db)) -> dict:
    from yaaf.core import ids
    from yaaf.core.models import Secret

    row = Secret(id=ids.new_id(ids.SECRET), project_id=None, name=body.name,
                 kind="billing", role_scopes=[], llm_mode=body.mode)
    return _save_llm(session, row, body.name, body.value)


def _managed(session: Session, credential_id: str, *, lock: bool = True) -> Secret:
    """The row to edit, hide or delete, locked — or a 404, because on this
    screen an id that names nothing is a credential somebody else already
    removed, not a bad choice on a project form.

    Hidden rows come back here. `assigned_credential` refuses them because
    *assigning* one is the mistake; renaming, un-hiding and deleting one are
    the only ways out of hidden, and a lookup that could not see it would make
    hiding a one-way door.

    `lock=False` is for the reader that only wants to look. The plan meter is
    polled once a minute by every open screen, and a route that write-locks
    the credential row to answer "how full is the plan" would queue behind —
    and hold up — the writes that actually change something.
    """
    from sqlalchemy import select

    from yaaf.core.models import Secret

    query = select(Secret).where(Secret.id == credential_id)
    row = session.scalar(query.with_for_update() if lock else query)
    if row is None or row.llm_mode is None or row.project_id is not None \
            or row.kind != "billing":
        raise HTTPException(status_code=404, detail="unknown LLM credential")
    return row


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


# ── hiding: the answer to an inventory nobody chose ─────────────────────────────
#
# Deleting is refused for any credential a project ever named, and that refusal
# is right — the ledger says "this project paid with X" and a dangling id would
# make that sentence a lie. But it left no way to tidy up. Lifting per-project
# keys above projects turned every key an install had ever used into a named,
# permanent, undeletable row on one page, most of them belonging to projects
# that finished long ago, and a picker with forty entries is a picker nobody
# reads.
#
# Hiding is the missing verb. It is not deletion and does not pretend to be:
# the row stays, the receipts stay, the finished projects keep naming what paid
# for them. What goes away is the *offer* — every menu, the Settings list, the
# ability to be assigned. And because it is reversible in one click, it is safe
# to use on forty rows at once, which is the only scale at which this problem
# actually appears.


class HiddenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: list[str] = Field(min_length=1, max_length=500)


def _set_hidden(session: Session, ids: list[str], *, hide: bool) -> dict:
    """Hide or restore many credentials, refusing individually.

    Bulk, and partial. Cleaning up an inherited list means selecting most of it
    and pressing one button; an all-or-nothing call would fail on the one row
    a live project still pays with and leave the administrator to bisect
    thirty-nine others by hand. So each id is judged on its own and the ones
    that could not move come back with the reason, which the screen shows.
    """
    from sqlalchemy import func, select

    from yaaf.core.llm_credentials import live_project_names
    from yaaf.core.models import Secret, utcnow
    from yaaf.server.api.admin import _audit

    moved: list[dict] = []
    refused: list[dict] = []
    for credential_id in dict.fromkeys(ids):
        row = session.scalar(select(Secret).where(Secret.id == credential_id).with_for_update())
        if row is None or row.llm_mode is None or row.project_id is not None:
            refused.append({"id": credential_id, "name": None,
                            "reason": "no such LLM credential"})
            continue
        if hide and row.hidden_at is None:
            # The one refusal. A project that can still spend is a project
            # whose money must stay explainable on the screen that assigns it.
            live = live_project_names(session, row.id)
            if live:
                refused.append({"id": row.id, "name": row.name,
                                "reason": "still paying for " + ", ".join(live[:3])
                                          + (f" and {len(live) - 3} more" if len(live) > 3 else "")
                                          + " — reassign or stop them first"})
                continue
        if not hide and row.hidden_at is not None:
            # Its name was released the moment it was hidden, so somebody may
            # be standing on it. Say which door to use rather than letting the
            # unique index refuse the whole batch.
            taken = session.scalar(select(Secret.name).where(
                Secret.project_id.is_(None), Secret.hidden_at.is_(None),
                func.lower(Secret.name) == row.name.lower()))
            if taken:
                refused.append({"id": row.id, "name": row.name,
                                "reason": f"another credential is now called '{taken}' — "
                                          "rename this one before restoring it"})
                continue
        if (row.hidden_at is not None) != hide:
            # Only a row that actually moves is stamped. Re-hiding an already
            # hidden credential used to reset "hidden 25m ago" to "just now",
            # which is the one fact that shelf is keeping.
            row.hidden_at = utcnow() if hide else None
            session.add(row)
            _audit(session, None, f"llm_credential.{'hide' if hide else 'unhide'}",
                   "secret", row.id, {"name": row.name})
        moved.append({"id": row.id, "name": row.name})
    session.flush()
    return {"hidden" if hide else "restored": moved, "refused": refused}


@router.post("/llm-credentials/hide")
def hide_llm_credentials(body: HiddenIn, session: Session = Depends(db)) -> dict:
    return _set_hidden(session, body.ids, hide=True)


@router.post("/llm-credentials/unhide")
def unhide_llm_credentials(body: HiddenIn, session: Session = Depends(db)) -> dict:
    return _set_hidden(session, body.ids, hide=False)


@router.get("/llm-credentials/{credential_id}/usage")
def llm_credential_usage(credential_id: str, refresh: bool = False,
                         session: Session = Depends(db)) -> dict:
    """What the plan behind a subscription credential last said was left.

    A local read. Nothing here calls Anthropic: the reading was harvested from
    the credential's own runs, where the SDK volunteers it, so this route
    cannot be rate-limited, cannot be slow, and cannot fail because an
    undocumented endpoint changed. `refresh` is accepted and ignored — there is
    nothing to re-ask; the number moves when the credential is next used. It is
    still worth re-reading on a timer, and the screens do: every finished run
    files a new reading here, and a page opened at nine is otherwise showing
    nine o'clock at noon.

    API keys have no plan meter, a subscription nothing has run on yet has no
    reading, and a reading whose windows have all reset since describes a plan
    that no longer exists; all three say so rather than 404 or draw a bar,
    because the screen asking is the same screen either way.
    """
    from yaaf.core.crypto import decrypt

    row = _managed(session, credential_id, lock=False)
    credential_id, mode = row.id, row.llm_mode
    if mode != "subscription":
        return {"credential_id": credential_id, "mode": mode, "available": False,
                "reason": "API keys are metered per call in dollars, not against a plan — "
                          "the monthly budget below is the meter that applies",
                "windows": [], "fetched_at": None, "cached": False}
    found = plan_usage.stored(session, decrypt(row.ciphertext))
    if found is None:
        return {"credential_id": credential_id, "mode": mode, "available": False,
                "reason": "no reading yet — the plan meter arrives with this credential's "
                          "next agent run, which is where Anthropic reports it",
                "windows": [], "fetched_at": None, "cached": False}
    return {"credential_id": credential_id, "mode": mode, "cached": True, **found}
