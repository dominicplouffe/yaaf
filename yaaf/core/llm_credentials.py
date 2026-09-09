"""Named, administrator-managed credentials explicitly assigned to projects."""
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yaaf.core.models import Project, Secret

MODE_LABELS = {"api_key": "Anthropic API key, billed in USD",
               "subscription": "Claude subscription, metered in tokens"}


def assigned_credential(session: Session, credential_id: str) -> Secret:
    # Assignment and deletion lock the same row, so usage cannot appear between
    # the deletion check and commit. The FK also protects non-HTTP writers.
    row = session.scalar(select(Secret).where(Secret.id == credential_id).with_for_update())
    if row is not None and row.kind != "billing":
        raise HTTPException(status_code=422, detail="A service credential cannot be assigned for billing")
    if row is None or row.llm_mode is None or row.project_id is not None:
        raise HTTPException(status_code=422, detail="Select an LLM credential configured in Settings")
    return row


def resolve(session: Session, *, credential_id: str | None = None,
            name: str | None = None) -> Secret | None:
    """The managed credential an id or a name points at, or None.

    Names match case-insensitively because that is how the unique index sees
    them: two credentials cannot differ only in case, so a chairman who types
    "production" for "Production" means the one that exists.
    """
    query = select(Secret).where(Secret.llm_mode.is_not(None), Secret.project_id.is_(None))
    if credential_id:
        return session.scalar(query.where(Secret.id == credential_id))
    if name:
        return session.scalar(query.where(func.lower(Secret.name) == name.strip().lower()))
    return None


def describe(row: Secret) -> dict:
    """A credential as a choice: what it is called and how it bills. Never a value."""
    return {"id": row.id, "name": row.name, "mode": row.llm_mode,
            "bills": MODE_LABELS.get(row.llm_mode or "", row.llm_mode)}


def choices(session: Session, *, with_plan: bool = False) -> list[dict]:
    """Every credential a project may pay with, as a drafter needs to see them.

    Names, modes and how many projects already use each — never a value, and
    never which projects, because the drafter may be a client scoped to one
    project and the inventory of the rest is not its business. With
    `with_plan`, a subscription credential's plan meter rides along when a
    screen has read it in the last minute — for the Founder, which drafts in
    the administrator's own screen; not for an MCP client, which the usage
    route is withheld from for the same reason as the inventory. The choice is
    never worth a network call of its own.
    """
    from yaaf.core import plan_usage

    counts = dict(session.execute(
        select(Project.billing_secret_id, func.count(Project.id))
        .where(Project.billing_secret_id.is_not(None)).group_by(Project.billing_secret_id)
    ).all())
    rows = session.scalars(
        select(Secret).where(Secret.llm_mode.is_not(None), Secret.project_id.is_(None))
        .order_by(Secret.name)).all()
    out = []
    for row in rows:
        entry = {**describe(row), "projects_using_it": counts.get(row.id, 0)}
        plan = plan_usage.cached(row.id) if with_plan else None
        if plan and plan.get("available"):
            entry["plan"] = [{"window": w["label"], "used_percent": round(w["utilization"] * 100),
                              "resets_at": w["resets_at"]} for w in plan["windows"]]
        out.append(entry)
    return out


def menu(session: Session) -> str:
    """The choices as one sentence, for a refusal or a preview to quote."""
    rows = choices(session)
    if not rows:
        return "no LLM credentials exist yet — an administrator adds one in Settings"
    return "available: " + "; ".join(f"{r['name']} ({r['bills']})" for r in rows)
