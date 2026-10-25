"""Named, administrator-managed credentials explicitly assigned to projects."""
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yaaf.core.models import ProjectStatus, Secret
from yaaf.runtimes.providers import PROVIDERS

#: How each kind of credential bills, in the provider's own words — for a row
#: whose provider this install has lost, the first provider's (Anthropic's).
MODE_LABELS = {kind.mode: kind.bills for p in reversed(PROVIDERS.values()) for kind in p.kinds}


def bills(row: Secret) -> str:
    """How this credential bills, in its own provider's words."""
    seller = PROVIDERS.get(row.provider or "")
    if seller is None:
        return MODE_LABELS.get(row.llm_mode or "", row.llm_mode or "")
    return seller.kind(row.llm_mode).bills

#: A project that has ended can neither start again nor spend again, so the
#: credential it named is history rather than a live dependency. Every
#: "is anything actually using this?" question in the product asks it this way
#: — the Desk's `live=true` listing draws the same line.
TERMINAL = (ProjectStatus.STOPPED, ProjectStatus.COMPLETED)


def offerable():
    """The SELECT behind every menu: managed, shared, and not hidden.

    One query so that hiding cannot be honoured on the Settings page and
    forgotten on the project picker, the Founder's list or an MCP spec. Every
    door that lets somebody *choose* a credential goes through here.
    """
    return select(Secret).where(Secret.llm_mode.is_not(None), Secret.project_id.is_(None),
                                Secret.hidden_at.is_(None))


def live_project_names(session: Session, credential_id: str) -> list[str]:
    """Projects still able to spend on this credential, by name.

    Empty is the licence to hide: nothing that can run tomorrow is pointing
    here, so taking the name off the menus cannot strand a project mid-flight.
    """
    from yaaf.core.project_credentials import projects_using

    return [p.name for p in projects_using(session, credential_id) if p.status not in TERMINAL]


def assigned_credential(session: Session, credential_id: str) -> Secret:
    # Assignment and deletion lock the same row, so usage cannot appear between
    # the deletion check and commit. The FK also protects non-HTTP writers.
    row = session.scalar(select(Secret).where(Secret.id == credential_id).with_for_update())
    if row is not None and row.kind != "billing":
        raise HTTPException(status_code=422, detail="A service credential cannot be assigned for billing")
    if row is not None and row.hidden_at is not None:
        # Its own sentence, because this one is undoable in ten seconds and the
        # generic refusal would send a chairman hunting for a credential that
        # is sitting in Settings under "Hidden".
        raise HTTPException(
            status_code=422,
            detail=f"'{row.name}' is hidden and cannot be assigned — un-hide it under "
                   "Settings → Providers, or pick another")
    if row is None or row.llm_mode is None or row.project_id is not None:
        raise HTTPException(status_code=422, detail="Select an LLM credential configured in Settings")
    return row


def resolve(session: Session, *, credential_id: str | None = None,
            name: str | None = None) -> Secret | None:
    """The managed credential an id or a name points at, or None.

    Names match case-insensitively because that is how the unique index sees
    them: two credentials cannot differ only in case, so a chairman who types
    "production" for "Production" means the one that exists. A hidden
    credential resolves to nothing at all — the caller then says "no such
    credential; here is the menu", which is the truthful answer.
    """
    query = offerable()
    if credential_id:
        return session.scalar(query.where(Secret.id == credential_id))
    if name:
        return session.scalar(query.where(func.lower(Secret.name) == name.strip().lower()))
    return None


def describe(row: Secret) -> dict:
    """A credential as a choice: what it is called, which provider it pays and
    how it bills. Never a value."""
    return {"id": row.id, "name": row.name, "mode": row.llm_mode, "provider": row.provider,
            "bills": bills(row)}


def choices(session: Session, *, with_plan: bool = False) -> list[dict]:
    """Every credential a project may pay with, as a drafter needs to see them.

    Names, modes and how many *live* projects use each — never a value, and
    never which projects, because the drafter may be a client scoped to one
    project and the inventory of the rest is not its business. The count is
    live-only for the same reason the list is hidden-aware: a credential last
    touched by a project that finished in March is not "in use", and a drafter
    told otherwise picks the busiest-looking key rather than the right one.
    With `with_plan`, a subscription credential's last harvested plan meter
    rides along — for the Founder, which drafts in the administrator's own
    screen; not for an MCP client, which the usage route is withheld from for
    the same reason as the inventory. It is a local row read: the meter is
    collected from the credential's own runs, so this costs nothing and there
    is no network call for it to be worth avoiding.
    """
    from yaaf.core import plan_usage
    from yaaf.core.project_credentials import usage

    counts = {secret_id: sum(1 for p in projects if p.status not in TERMINAL)
              for secret_id, projects in usage(session).items()}
    rows = session.scalars(offerable().order_by(Secret.name)).all()
    out = []
    for row in rows:
        entry = {**describe(row), "projects_using_it": counts.get(row.id, 0)}
        plan = None
        if with_plan and row.llm_mode == "subscription":
            from yaaf.core.crypto import decrypt

            plan = plan_usage.stored(session, decrypt(row.ciphertext))
        if plan and plan.get("available"):
            # Expired windows are dropped rather than listed: this menu exists
            # to talk an agent out of a credential that is about to refuse its
            # run, and a window that reset an hour ago argues against a
            # credential which is in fact wide open.
            entry["plan"] = [{"window": w["label"], "used_percent": round(w["utilization"] * 100),
                              "resets_at": w["resets_at"]}
                             for w in plan["windows"] if not w.get("expired")]
        out.append(entry)
    return out


def menu(session: Session) -> str:
    """The choices as one sentence, for a refusal or a preview to quote."""
    rows = choices(session)
    if not rows:
        return "no LLM credentials exist yet — an administrator adds one in Settings"
    return "available: " + "; ".join(f"{r['name']} ({r['bills']})" for r in rows)
