"""Has a key been seen to work, and when.

The readiness report says what yaaf itself would refuse: a class with no model,
a model with no price, a browsing role with nothing to search through. It
cannot say whether the provider takes the key, and a key nobody has called
reads exactly like a good one. Settings drew three keys no provider would
accept in green, under the word Ready.

So this reads the record instead. Four things are an answer from the provider
on a key: a Test that came back, the model list the key returned, a graded
check in which a model answered, and a finished run of a project the key pays.
Two things are a refusal: a Test, or a run, that the provider turned away for
the key itself ("auth", the kind three of which in a row pause a project). A
call that failed for any other reason (a timeout, a model name the provider
does not know) says nothing about the key and is not counted either way.

Only what happened after the key's value last changed counts. An answer on the
old value says nothing about the new one, which is also why a rotation turns a
key back to "never" until something calls it.

It also keeps the last result of the three connection tests that are not an LLM
key (Slack, web search, the Founder), so those sections can say "not tested"
rather than "connected" for a token that was only saved.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import platform
from yaaf.core.models import AuditLog, Project, ProviderCheck, Run, RunStatus, Secret, utcnow

#: + a credential id: the last deliberate call on that key (a Test, or a model
#: list that could not be read). A model list that was read is kept by
#: yaaf.core.model_upkeep already.
CALL = "answer."
#: + a subject (`slack`, `web_search`, `founder`): its last connection test.
TESTED = "tested."
#: The failure kinds that are the provider refusing the key itself.
REFUSED_KINDS = frozenset({"auth"})
SUBJECTS = frozenset({"slack", "web_search", "founder"})


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _when(text: str | None) -> datetime | None:
    try:
        return _aware(datetime.fromisoformat(text)) if text else None
    except (TypeError, ValueError):
        return None


def _load(session: Session, key: str) -> dict:
    raw = platform.get_setting(session, key)
    try:
        value = json.loads(raw) if raw else {}
    except ValueError:
        value = {}
    return value if isinstance(value, dict) else {}


def note_call(session: Session, secret_id: str, *, ok: bool, via: str,
              kind: str | None = None, detail: str = "") -> None:
    """Remember a deliberate call on a key and how it came back."""
    platform.set_setting(session, CALL + secret_id, json.dumps(
        {"at": utcnow().isoformat(), "ok": ok, "via": via, "kind": kind,
         "detail": (detail or "")[:300]}))


def changed_at(session: Session, secret: Secret) -> datetime:
    """When the key's value last changed: its newest rotation, else its creation.
    A rename is a save too, and is not a new value, so it does not count."""
    rows = session.scalars(
        select(AuditLog).where(AuditLog.action == "llm_credential.save",
                               AuditLog.entity_id == secret.id)
        .order_by(AuditLog.at.desc()).limit(50))
    for row in rows:
        if (row.detail or {}).get("rotated"):
            return _aware(row.at)
    return _aware(secret.created_at or utcnow())


def _event(at: datetime, via: str, detail: str, project: str | None = None) -> dict:
    return {"at": at, "via": via, "detail": detail, "project": project}


def of(session: Session, secret: Secret) -> dict:
    """What the record says about one key: `state` is `answered`, `refused`
    or `never`, with when, through what (`test`, `model_list`, `check`, `run`),
    and the project whose run it was."""
    from yaaf.core import model_upkeep, project_credentials

    since = changed_at(session, secret)
    answered: list[dict] = []
    refused: list[dict] = []

    call = _load(session, CALL + secret.id)
    at = _when(call.get("at"))
    if at is not None and at >= since:
        if call.get("ok"):
            answered.append(_event(at, call.get("via") or "test", call.get("detail") or ""))
        elif call.get("kind") in REFUSED_KINDS:
            refused.append(_event(at, call.get("via") or "test", call.get("detail") or ""))

    listed = model_upkeep.discovered(session, secret.id) or {}
    at = _when(listed.get("at"))
    if at is not None and at >= since:
        answered.append(_event(at, "model_list",
                               f"listed {len(listed.get('models') or [])} models"))

    for check in session.scalars(
            select(ProviderCheck).where(ProviderCheck.secret_id == secret.id,
                                        ProviderCheck.finished_at.is_not(None),
                                        ProviderCheck.finished_at >= since)
            .order_by(ProviderCheck.finished_at.desc()).limit(5)):
        if any(o.get("ok") is True for o in check.outcomes or []):
            answered.append(_event(_aware(check.finished_at), "check",
                                   f"{check.model} answered a graded check"))
            break

    projects = {p.id: p.name for p in project_credentials.projects_using(session, secret.id)}
    if projects:
        runs = session.execute(
            select(Run.status, Run.error_kind, Run.finished_at, Run.project_id)
            .where(Run.project_id.in_(projects), Run.provider == secret.provider,
                   Run.finished_at.is_not(None), Run.finished_at >= since,
                   Run.status.in_((RunStatus.DONE, RunStatus.FAILED)))
            .order_by(Run.finished_at.desc()).limit(40)).all()
        done = next((r for r in runs if r.status == RunStatus.DONE), None)
        if done is not None:
            answered.append(_event(_aware(done.finished_at), "run", "a run finished",
                                   projects.get(done.project_id)))
        turned = next((r for r in runs if r.status == RunStatus.FAILED
                       and r.error_kind in REFUSED_KINDS), None)
        if turned is not None:
            refused.append(_event(_aware(turned.finished_at), "run",
                                  "the provider refused the key on a run",
                                  projects.get(turned.project_id)))

    yes = max(answered, key=lambda e: e["at"], default=None)
    no = max(refused, key=lambda e: e["at"], default=None)
    if no is not None and (yes is None or no["at"] > yes["at"]):
        state, event = "refused", no
    elif yes is not None:
        state, event = "answered", yes
    else:
        return {"state": "never", "at": None, "via": None, "detail": None, "project": None,
                "since": since.isoformat()}
    return {"state": state, "at": event["at"].isoformat(), "via": event["via"],
            "detail": event["detail"], "project": event["project"], "since": since.isoformat()}


def summary(evidence: list[dict]) -> dict:
    """One provider's evidence from its keys': a refusal on any key leads, else
    the newest answer, else never."""
    refused = [e for e in evidence if e["state"] == "refused"]
    if refused:
        return max(refused, key=lambda e: e["at"])
    answered = [e for e in evidence if e["state"] == "answered"]
    if answered:
        return max(answered, key=lambda e: e["at"])
    return {"state": "never", "at": None, "via": None, "detail": None, "project": None}


def status(ready: bool, issues: list[dict], evidence: dict) -> str:
    """The provider's state in a word, for the badge: `blocked` (yaaf would
    refuse its seats), `failing` (the provider refused its key), `attention`
    (it runs, with something worth a look), `untested` (nothing has answered
    on it yet) or `ready`. Green is never drawn without an answer on record."""
    if not ready:
        return "blocked"
    if evidence["state"] == "refused":
        return "failing"
    if issues:
        return "attention"
    if evidence["state"] == "never":
        return "untested"
    return "ready"


# ── connection tests that are not an LLM key ───────────────────────────────────


def record_test(session: Session, subject: str, *, ok: bool, detail: str) -> None:
    platform.set_setting(session, TESTED + subject, json.dumps(
        {"at": utcnow().isoformat(), "ok": bool(ok), "detail": (detail or "")[:300]}))


def last_test(session: Session, subject: str) -> dict | None:
    """The newest test of `subject`, or None when it has not been tested since
    it was last changed."""
    found = _load(session, TESTED + subject)
    return found if found.get("at") else None


def forget_test(session: Session, subject: str) -> None:
    """A changed connection is an untested one."""
    platform.set_setting(session, TESTED + subject, "")


def live_projects_paid(session: Session, secret: Secret) -> list[Project]:
    from yaaf.core import project_credentials

    return [p for p in project_credentials.projects_using(session, secret.id)
            if p.status in ("running", "paused", "draft")]
