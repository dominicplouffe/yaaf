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

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from yaaf.core import plan_usage, platform
from yaaf.core.models import Secret
from yaaf.runtimes.providers import DEFAULT_PROVIDER, MODE_PATTERN, PROVIDERS
from yaaf.server import authority
from yaaf.server.deps import db
from yaaf.server.identity import Person, person_auth, signed_in
from yaaf.server.slack import provision as slack_provision
from yaaf.server.slack import settings as slack_settings

router = APIRouter(dependencies=[Depends(signed_in)])


@router.get("", dependencies=[authority.INSTANCE_ADMIN])
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
            # Which provider it pays — the section keeps its old name, and
            # the Founder runs on whichever one this says (docs/RUNTIMES.md M6).
            "provider": platform.credential_provider(session),
            "model": platform.founder_model(session),
            # As set: a class resolves on whatever the credential pays.
            "model_ref": platform.founder_model_ref(session),
            # Its own key, or one of the LLM credentials below, borrowed.
            "source": platform.credential_source(session),
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
    from sqlalchemy import func, select

    from yaaf.core.llm_credentials import TERMINAL

    managed = Secret.llm_mode.is_not(None), Secret.project_id.is_(None)
    total = session.scalar(select(func.count(Secret.id)).where(*managed)) or 0
    hidden = session.scalar(select(func.count(Secret.id))
                            .where(*managed, Secret.hidden_at.is_not(None))) or 0
    from yaaf.core.project_credentials import usage

    visible = set(session.scalars(select(Secret.id).where(*managed, Secret.hidden_at.is_(None))))
    in_use = sum(1 for secret_id, projects in usage(session).items()
                 if secret_id in visible and any(p.status not in TERMINAL for p in projects))
    return {"total": int(total), "hidden": int(hidden), "in_use": int(in_use),
            "offerable": int(total) - int(hidden)}


# ── the platform model credential ───────────────────────────────────────────────


class CredentialIn(BaseModel):
    #: Blank only for a local server, which takes no key (`_key_value`).
    value: str = Field(default="", max_length=10000)
    mode: str = Field(default="api_key", pattern=MODE_PATTERN)
    provider: str = DEFAULT_PROVIDER
    base_url: str | None = Field(default=None, max_length=500)
    #: The Founder's model, given with a credential of another provider: the
    #: two are one decision, and the door refuses them apart.
    model: str | None = Field(default=None, min_length=3, max_length=200)


def _founder_model_problem(session: Session, ref: str, provider_name: str) -> str | None:
    """A Founder's model the platform credential does not pay for. The model
    picks the road (`yaaf.founder.session.on_sdk`); the credential pays it. A
    class resolves on the credential's provider — which is why a credential
    of any provider with a lineup needs no model named at all."""
    from yaaf.core import model_policy
    from yaaf.runtimes import catalog

    bad = catalog.valid_ref(ref)
    if bad:
        return bad
    seller = PROVIDERS[provider_name]
    model = model_policy.resolve(session, ref, provider=provider_name)
    if model is None:
        label = catalog.class_label(ref)
        return (f"the Founder runs on the {label} class, and {seller.label} has no {label} "
                f"model set — name the Founder's model ({seller.name}/<model>), or set "
                f"{seller.label}'s lineup in Settings → Models")
    if catalog.provider_of(model) == seller.name:
        return None
    owner = PROVIDERS[catalog.provider_of(model)].label
    return (f"the Founder runs on {model}, which is {owner}'s, and the platform credential "
            f"pays {seller.label} — name one of {seller.label}'s models for it "
            f"({seller.name}/<model> for one the catalog does not list)")


def install_credential(session: Session, value: str, mode: str,
                       provider: str = DEFAULT_PROVIDER, base_url: str | None = None,
                       model: str | None = None) -> dict:
    """Store the platform credential after refusing an impossible one.

    The shape check happens before the write on the same promise the
    per-project billing form already makes — *if the key is bad you find out
    now, not at 9am tomorrow* — and says the same sentence, because which form
    a chairman pasted into should not change the answer.

    Shared with ``/admin/founder/credential``, which predates this router and
    stays where the Founding screen expects it.

    Any provider the Founder can run on (`yaaf.founder.session.refusal`), with
    a model of that provider's — the one already set, or one given here.
    """
    from yaaf.founder.session import refusal

    base_url = _check_provider(provider, mode, base_url)
    value = _key_value(provider, value)
    problem = (refusal(provider, mode) or platform.shape_error(mode, value, provider)
               or _founder_model_problem(session,
                                         (model or platform.founder_model_ref(session)).strip(),
                                         provider))
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    platform.set_credential(session, value, mode=mode, provider=provider, base_url=base_url)
    if model:
        platform.set_setting(session, platform.FOUNDER_MODEL, model.strip())
    return {"ok": True, "billing_mode": mode, "provider": provider,
            "model": platform.founder_model(session)}


def borrow_llm_credential(session: Session, credential_id: str) -> dict:
    """Have the Founder pay with an LLM credential projects already use.

    The same refusals as installing a key of its own — a provider or mode the
    Founder cannot run on, a model the credential does not pay for — because
    which door a key came through should not change whether it works.
    """
    from yaaf.core import llm_credentials
    from yaaf.founder.session import refusal

    row = llm_credentials.resolve(session, credential_id=credential_id)
    if row is None:
        raise HTTPException(status_code=422, detail=(
            "no such LLM credential — pick one listed under Settings → LLM credentials"))
    provider = row.provider or DEFAULT_PROVIDER
    problem = (refusal(provider, row.llm_mode)
               or _founder_model_problem(session, platform.founder_model_ref(session), provider))
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    platform.use_llm_credential(session, row.id)
    return {"ok": True, **platform.credential_source(session),
            "billing_mode": row.llm_mode, "provider": provider,
            "model": platform.founder_model(session)}


def set_founder_model(session: Session, model: str) -> dict:
    """The Founder's model, held to the provider the platform credential pays.
    Shared with ``/admin/founder/model``."""
    from yaaf.runtimes import catalog

    model = model.strip()
    problem = (_founder_model_problem(session, model, platform.credential_provider(session))
               if platform.has_credential(session) else catalog.valid_ref(model))
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    platform.set_setting(session, platform.FOUNDER_MODEL, model)
    return {"model": platform.founder_model(session), "ref": model}


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


@router.put("/anthropic", dependencies=[authority.INSTANCE_ADMIN])
def put_anthropic(body: CredentialIn, session: Session = Depends(db)) -> dict:
    return install_credential(session, body.value, body.mode, body.provider, body.base_url,
                              body.model)


@router.post("/anthropic/test", dependencies=[authority.INSTANCE_ADMIN])
def test_anthropic(session: Session = Depends(db)) -> dict:
    return check_credential(session)


@router.delete("/anthropic", dependencies=[authority.INSTANCE_ADMIN])
def delete_anthropic(session: Session = Depends(db)) -> dict:
    """Remove the platform key. Nothing else breaks: projects never used it,
    and the fire drill and the New Project form never needed it — only the
    Founder conversation stops being offered."""
    return {"removed": platform.clear_credential(session)}


class ModelIn(BaseModel):
    model: str = Field(min_length=3, max_length=200)


@router.put("/anthropic/model", dependencies=[authority.INSTANCE_ADMIN])
def put_anthropic_model(body: ModelIn, session: Session = Depends(db)) -> dict:
    return set_founder_model(session, body.model)


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


@router.put("/slack", dependencies=[authority.INSTANCE_ADMIN])
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


@router.post("/slack/test", dependencies=[authority.INSTANCE_ADMIN])
def test_slack(session: Session = Depends(db)) -> dict:
    """Ask Slack who this token is. Free, unscoped, and the only check that
    can be right about whether the connection actually works."""
    token = platform.secret_value(session, platform.SLACK_BOT_SECRET)
    if token is None:
        raise HTTPException(status_code=422, detail="no app-wide Slack bot token is configured")
    return slack_provision.workspace_check(token)


@router.delete("/slack", dependencies=[authority.INSTANCE_ADMIN])
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
    mode: str = Field(pattern=MODE_PATTERN)
    #: Blank only for a local server, which takes no key (`_key_value`).
    value: str = Field(default="", max_length=10000)
    #: Which provider it pays (yaaf.runtimes.providers). Absent is Anthropic,
    #: which every credential was before there was a second.
    provider: str = Field(default=DEFAULT_PROVIDER, max_length=32)
    #: Where the provider is reached, for one whose address is the
    #: credential's (a custom OpenAI-compatible server). Refused otherwise.
    base_url: str | None = Field(default=None, max_length=500)


class LlmCredentialPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    value: str | None = Field(default=None, min_length=8, max_length=10000)
    base_url: str | None = Field(default=None, max_length=500)


def _check_provider(provider_name: str, mode: str, base_url: str | None) -> str | None:
    """The credential's provider, its kind and its address, against the
    registry — the shape check reads the provider, so it comes first."""
    seller = PROVIDERS.get(provider_name)
    if seller is None:
        raise HTTPException(status_code=422, detail=(
            f"unknown provider '{provider_name}' — one of: {', '.join(PROVIDERS)}"))
    if mode not in {k.mode for k in seller.kinds}:
        raise HTTPException(status_code=422, detail=(
            f"{seller.label} takes {', '.join(k.bills for k in seller.kinds)} — not '{mode}'"))
    base_url = (base_url or "").strip() or None
    if seller.credential_base_url:
        # A local server is plain HTTP on the sandbox network; everything
        # else carries a key, and a key goes over TLS or not at all.
        schemes = ("http://", "https://") if seller.local or seller.name == "custom" \
            else ("https://",)
        if not base_url or not base_url.startswith(schemes) or "{" in base_url:
            example = (seller.base_url_template or "").replace("{base_url}",
                                                               "http://ollama:11434/v1")
            raise HTTPException(status_code=422, detail=(
                f"a {seller.label} credential carries the server's address — give its "
                f"base URL, e.g. {example or 'http://ollama:11434/v1'}"))
        return base_url.rstrip("/")
    if base_url:
        raise HTTPException(status_code=422, detail=(
            f"{seller.label} is reached at its own address; a base URL is only for a "
            "provider whose address is the credential's own"))
    return None


#: What a local server's credential holds when it was given no key: the wire
#: sends nothing for it (its auth is "none"), and the column is not nullable.
NO_KEY = "no-key"


def _key_value(provider_name: str, value: str | None) -> str:
    """The value to store: as given, or — for a local server, which takes no
    key — a stand-in when it was left blank. Anything else needs a key."""
    value = (value or "").strip()
    seller = PROVIDERS.get(provider_name)
    if not value and seller is not None and seller.local:
        return NO_KEY
    if len(value) < 8:
        raise HTTPException(status_code=422, detail="Enter a credential of at least 8 characters")
    return value


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

    from yaaf.core.llm_credentials import TERMINAL

    if projects is None:
        from yaaf.core.project_credentials import projects_using

        projects = projects_using(session, row.id)
    out = [{"id": p.id, "name": p.name, "status": p.status, "live": p.status not in TERMINAL}
           for p in projects]
    if groups is None:
        groups = _token_groups(session)
    token, sharing = groups.get(row.id, (None, 1))
    from yaaf.core.llm_credentials import bills

    return {"id": row.id, "name": row.name, "mode": row.llm_mode,
            "provider": row.provider, "bills": bills(row), "base_url": row.base_url,
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
        value = _key_value(row.provider, value)
        problem = (None if value == NO_KEY
                   else platform.shape_error(row.llm_mode, value, row.provider))
        if problem:
            raise HTTPException(status_code=422, detail=problem)
        row.ciphertext = encrypt(value)
        # Nothing to invalidate: a plan reading is filed under the token, not
        # the credential, so the old token's meter still belongs to whatever
        # else holds it and this credential simply has none until its next run.
        #
        # One thing does have to be cleared. A rejected token is kept quiet for
        # fifteen minutes so a dead credential cannot earn a call per press, and
        # somebody who re-pastes the same value — which is what you do when you
        # believe the stored copy is the problem — would land inside that quiet
        # and be told nothing, by the button they pressed to check the fix. A
        # new value is a new fingerprint and clears itself; this is for the
        # value that is not new.
        from yaaf.server import plan_probe

        plan_probe.forget(value)
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


@router.get("/llm-credentials", dependencies=[authority.SIGNED_IN])
def list_llm_credentials(include_hidden: bool = False, session: Session = Depends(db),
                         person: Person = Depends(person_auth)) -> list:
    """Every credential this install can still choose from.

    Hidden ones are absent by default, and that default is what every other
    caller gets: the project picker, the import matcher and the CLI all read
    this route. `include_hidden=true` is the Settings page opening the drawer
    to un-hide or finally delete one — the only screen that has a reason to
    look at a credential nobody may pick.
    """
    from sqlalchemy import select

    from yaaf.core.models import Secret
    from yaaf.core.project_credentials import usage as credential_usage

    usage = credential_usage(session)
    # Anybody founding a project picks from this list, so anybody signed in may
    # read it — but which projects a credential pays for is only theirs to see
    # for the projects they could open anyway.
    if not person.is_admin:
        visible = {p.id for p in authority.reachable(session, person.id, False)}
        usage = {sid: [p for p in projects if p.id in visible]
                 for sid, projects in usage.items()}
    listing = select(Secret).where(Secret.llm_mode.is_not(None))
    if not include_hidden:
        listing = listing.where(Secret.hidden_at.is_(None))
    # Grouped once for the whole listing rather than per row: the digests come
    # from decrypting every managed credential, which is cheap once and N²
    # if each row asks for itself.
    groups = _token_groups(session)
    return [_llm_summary(session, row, usage.get(row.id, []), groups)
            for row in session.scalars(listing.order_by(Secret.name))]


@router.post("/llm-credentials", status_code=201, dependencies=[authority.INSTANCE_ADMIN])
def create_llm_credential(body: LlmCredentialIn, session: Session = Depends(db)) -> dict:
    from yaaf.core import ids
    from yaaf.core.models import Secret

    base_url = _check_provider(body.provider, body.mode, body.base_url)
    row = Secret(id=ids.new_id(ids.SECRET), project_id=None, name=body.name,
                 kind="billing", role_scopes=[], llm_mode=body.mode,
                 provider=body.provider, base_url=base_url)
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


@router.patch("/llm-credentials/{credential_id}", dependencies=[authority.INSTANCE_ADMIN])
def update_llm_credential(credential_id: str, body: LlmCredentialPatch,
                          session: Session = Depends(db)) -> dict:
    row = _managed(session, credential_id)
    if body.base_url is not None:
        row.base_url = _check_provider(row.provider, row.llm_mode or "api_key", body.base_url)
    return _save_llm(session, row, body.name, body.value)


@router.delete("/llm-credentials/{credential_id}", dependencies=[authority.INSTANCE_ADMIN])
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


@router.post("/llm-credentials/hide", dependencies=[authority.INSTANCE_ADMIN])
def hide_llm_credentials(body: HiddenIn, session: Session = Depends(db)) -> dict:
    return _set_hidden(session, body.ids, hide=True)


@router.post("/llm-credentials/unhide", dependencies=[authority.INSTANCE_ADMIN])
def unhide_llm_credentials(body: HiddenIn, session: Session = Depends(db)) -> dict:
    return _set_hidden(session, body.ids, hide=False)


def _no_reading_reason(outcome) -> str:
    """Why this credential has no meter, said so the reader knows what to do.

    Only one of these sentences is about patience. The rest name something that
    has to change first, which is the point: the row that reaches this branch is
    usually a token nothing can spend on, and "press refresh" — the only answer
    it ever gave — is advice to repeat the press that just failed.
    """
    if outcome is None or outcome.state == "floored":
        return ("no reading yet — the meter arrives with this credential's next "
                "agent run, or now if you check it")
    return {
        "rejected": "Anthropic refused this token when asked just now — it may have "
                    "expired or been revoked. Replace the credential's value to fix it.",
        "throttled": "Anthropic is rate-limiting this token, so it could not be asked "
                     "just now — the meter also arrives with this credential's next "
                     "agent run",
        "unreachable": "Anthropic could not be reached just now — the meter also "
                       "arrives with this credential's next agent run",
        "unreadable": "Anthropic answered with something this could not read — the "
                      "meter also arrives with this credential's next agent run",
    }.get(outcome.state, "no reading yet — the meter arrives with this credential's "
                         "next agent run, or now if you check it")


def _require_meter_reader(session: Session, row: Secret) -> None:
    """The plan meter is the instance admin's — and a project's own readers'
    for the credential that project pays with, since its Overview draws it."""
    from sqlalchemy import select

    from yaaf.core.models import Project

    person = authority.acting_person(session)
    if person is not None and person.is_admin:
        return
    billed = session.scalars(select(Project).where(Project.billing_secret_id == row.id)).all()
    for project in billed:
        if person is not None and authority.person_level(session, person, project):
            return
    authority.require_admin_here(session)


@router.get("/llm-credentials/{credential_id}/usage", dependencies=[authority.IN_HANDLER])
def llm_credential_usage(credential_id: str, refresh: bool = False,
                         session: Session = Depends(db)) -> dict:
    """What the plan behind a subscription credential last said was left.

    A local row read by default: the reading is written by every agent run on
    the credential, where the SDK volunteers the windows for free. An ordinary
    load of the screen makes no request, so it cannot be rate-limited, cannot be
    slow, and cannot fail because an undocumented endpoint changed.

    `refresh` is the exception, and it is a person pressing a button — the only
    thing in this platform that asks Anthropic for a meter, and the only way to
    get one for a credential no run is covering. It is still floored at one call
    per token every fifteen seconds and still honours a throttle, and a press
    that cannot ask still answers with the stored row rather than an error,
    because the reader wanted the number.

    What it no longer does is keep the reason to itself. `asked` says which of
    the six things happened, so the screen can tell a press that worked from one
    that was floored, throttled, or refused outright — those were one answer
    here for as long as the button existed, and a button whose every outcome
    looks the same is a button nobody can trust.

    API keys have no plan meter, a subscription nothing has reported on yet has
    no reading, and a reading whose windows have all reset since describes a
    plan that no longer exists; all three say so rather than 404 or draw a bar,
    because the screen asking is the same screen either way.
    """
    from yaaf.core.crypto import decrypt
    from yaaf.core.plan_usage import fingerprint
    from yaaf.server import plan_probe

    row = _managed(session, credential_id, lock=False)
    _require_meter_reader(session, row)
    credential_id, mode = row.id, row.llm_mode
    if mode != "subscription":
        return {"credential_id": credential_id, "mode": mode, "available": False,
                "reason": "API keys are metered per call in dollars, not against a plan — "
                          "the monthly budget below is the meter that applies",
                "windows": [], "fetched_at": None, "cached": False,
                "token_digest": None}
    token = decrypt(row.ciphertext)
    outcome = plan_probe.refresh(session, token) if refresh else None
    found = plan_usage.stored(session, token)
    # The same truncated digest `_llm_summary` puts on the list row, so a screen
    # showing several rows of one subscription can tell that they are one meter
    # and move them together. A digest, never the value, on an admin-only route.
    head = {"credential_id": credential_id, "mode": mode,
            "token_digest": fingerprint(token)[:8],
            "cached": outcome is None or not outcome.ok}
    if outcome is not None:
        head["asked"] = {"state": outcome.state, "retry_after_s": outcome.retry_after_s}
    if found is None:
        # A press that failed must not be answered with "press refresh". That
        # sentence is true for a credential nobody has asked about yet and a
        # dead end for the one case it gets read in: somebody pressed, Anthropic
        # refused the token, and the screen sent them back to the same button.
        return {**head, "available": False,
                "reason": _no_reading_reason(outcome),
                "windows": [], "fetched_at": None}
    return {**head, **found}


# ── model prices ────────────────────────────────────────────────────────────────
#
# What a model costs on this install, over the shipped catalog (yaaf.core.prices).
# The meter of every harness that prices its own runs reads it, so a model with
# no price here or in the catalog cannot be hired onto one (R3). Setting one is
# how a DeepSeek seat, an OpenRouter model, or a local server becomes hireable —
# the last at zero, if the chairman's electricity is free.


class ModelPriceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: USD per million tokens. Zero is a price — a local server's.
    input: float = Field(ge=0, le=10_000)
    output: float = Field(ge=0, le=10_000)
    cache_read: float = Field(default=0.0, ge=0, le=10_000)
    cache_write: float = Field(default=0.0, ge=0, le=10_000)


@router.get("/model-prices", dependencies=[authority.INSTANCE_ADMIN])
def list_model_prices(session: Session = Depends(db)) -> list:
    from yaaf.core import prices

    return prices.table(session)


@router.put("/model-prices/{model:path}", dependencies=[authority.INSTANCE_ADMIN])
def put_model_price(model: str, body: ModelPriceIn, session: Session = Depends(db)) -> dict:
    from yaaf.core.models import ModelPrice, utcnow
    from yaaf.runtimes import catalog
    from yaaf.server.api.admin import _audit

    model = model.strip()
    if not model or len(model) > 128:
        raise HTTPException(status_code=422, detail="Name the model, as a seat names it")
    if catalog.is_class(model):
        raise HTTPException(status_code=422, detail=(
            f"{model} is a class, not a model — price the model it resolves to "
            "(Settings → Models shows each provider's lineup)"))
    row = session.get(ModelPrice, model) or ModelPrice(model=model)
    row.input, row.output = body.input, body.output
    row.cache_read, row.cache_write = body.cache_read, body.cache_write
    row.updated_at = utcnow()
    # Typed by an administrator: whatever a feed said before, this is theirs.
    row.source = None
    session.add(row)
    session.flush()
    _audit(session, None, "model_price.set", "model", model, body.model_dump())
    # Which provider the name resolves to, said back: a typo in the provider
    # part lands on the default provider, and the screen should show that.
    return {"model": model, "provider": catalog.provider_of(model), **body.model_dump()}


@router.delete("/model-prices/{model:path}", dependencies=[authority.INSTANCE_ADMIN])
def delete_model_price(model: str, session: Session = Depends(db)) -> dict:
    """Back to the catalog's price, or to none. A seat that needs one is not
    moved: the lease refuses its next run with the reason (R3)."""
    from yaaf.core.models import ModelPrice
    from yaaf.server.api.admin import _audit

    row = session.get(ModelPrice, model)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no price of this install's own for {model}")
    session.delete(row)
    _audit(session, None, "model_price.clear", "model", model, {})
    return {"model": model, "removed": True}


# ── doctor: is this install able to do its job? ─────────────────────────────────


#: How long a queued run may wait, with nothing started anywhere, before the
#: doctor says the runner looks down. Long enough to sit out a concurrency cap.
_RUNNER_QUIET = timedelta(minutes=10)


def _check(name: str, ok: bool, detail: str, fix: str, *, problem: bool = True) -> dict:
    return {"name": name, "ok": ok, "level": "problem" if problem else "suggestion",
            "detail": detail, "fix": None if ok else fix}


@router.get("/doctor", dependencies=[authority.INSTANCE_ADMIN])
def doctor(session: Session = Depends(db)) -> dict:
    """Every way this install can look alive and do nothing, checked at once,
    each with the command or the page that fixes it.

    The dev path alone has four steps that, missed, leave "a project that looks
    alive and never moves" (docs/DEPLOYMENT.md), and a fresh box met them one
    at a time: an empty crew picker, a 500 on the first saved secret, runs
    queued forever, a Founder asking for a second key. `yaaf doctor` reads this.
    """
    from sqlalchemy import func, select

    from yaaf.core import llm_credentials
    from yaaf.core.config import settings as config
    from yaaf.core.models import Crew, Role, Run, RunQueue, utcnow
    from yaaf.server.app import _web_dist

    cfg = config()
    roles = session.execute(select(func.count(Role.id))).scalar_one()
    crews = session.execute(select(func.count(Crew.id))).scalar_one()
    keys = len(session.execute(llm_credentials.offerable()).scalars().all())
    quiet_since = utcnow() - _RUNNER_QUIET
    waiting = session.execute(select(func.count(RunQueue.id)).where(
        RunQueue.created_at < quiet_since,
        (RunQueue.not_before.is_(None)) | (RunQueue.not_before < quiet_since),
    )).scalar_one()
    started = session.execute(select(func.count(Run.id)).where(
        Run.started_at >= quiet_since)).scalar_one()
    checks = [
        _check("dashboard", _web_dist() is not None,
               "the web dashboard is built" if _web_dist() else "the dashboard is not built",
               "cd web && npm install && npm run build — the API serves only a JSON note "
               "at /app without it"),
        _check("master key", bool(cfg.master_key),
               "secrets can be stored" if cfg.master_key else "YAAF_MASTER_KEY is unset",
               "set YAAF_MASTER_KEY (a Fernet key) — without it saving any credential fails"),
        _check("roles", roles > 0, f"{roles} role(s) in the library",
               "yaaf seed-roles roles/ — nobody can be hired from an empty library"),
        _check("crews", crews > 0, f"{crews} crew(s) on the hiring desk",
               "yaaf seed-crews crews/ — the 'start from a crew' picker is empty without it",
               problem=False),
        _check("a key to pay with", keys > 0, f"{keys} LLM credential(s)",
               "add one under Settings → LLM credentials, or `yaaf create-llm-credential "
               "NAME` — a project with none is created unable to start"),
        _check("the Founder's key", platform.has_credential(session),
               "the Founder can draft" if platform.has_credential(session)
               else "the Founder cannot draft",
               "`yaaf set-platform-key --from-credential NAME`, or one click under "
               "Settings → Founder", problem=False),
        _check("runner", waiting == 0 or started > 0,
               f"{waiting} run(s) waiting over 10 minutes, none started" if waiting and not started
               else "runs are being picked up",
               "start yaaf-runner — the server queues runs but never executes them"),
        _check("public address", bool(cfg.public_base_url),
               cfg.public_base_url or "YAAF_PUBLIC_BASE_URL is unset",
               "set YAAF_PUBLIC_BASE_URL to the address people open — notices link to "
               "the Inbox only with it, and claude.ai cannot connect without it",
               problem=False),
        _check("email", bool(cfg.smtp_host),
               "mail is configured" if cfg.smtp_host else "no SMTP host",
               "set YAAF_SMTP_HOST — without it decisions and alarms reach only the "
               "Inbox and any webhook", problem=False),
    ]
    return {"ok": all(c["ok"] for c in checks if c["level"] == "problem"),
            "checks": checks}
