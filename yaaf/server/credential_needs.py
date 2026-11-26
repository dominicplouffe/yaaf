"""Access the chairman promised a project, from the promise to the proof.

"I'll give you my WordPress login and a Google Analytics service account"
used to survive founding as a sentence in the brief and nothing else: the
setup step that could have caught it ticked itself off for any single secret,
and a credential that was given but wrong was discovered when an agent's run
failed, days later, in a log. Here each promise is a `CredentialNeed`:

* **what it asks for** — the catalog's fields when yaaf knows the service, the
  Founder's suggestion when it does not, and anything the chairman adds;
* **the values** — stored as ordinary project Secrets, so the agents receive
  them exactly as they receive any other credential;
* **how far it is proven** — a verified test, a suggested call he chose to
  run, or an agent's report of using it (yaaf cannot see agents' calls to
  outside services, so it asks them to say, and labels it as theirs).

A need is "needed" until it is given or he says the project does without it,
and the setup checklist names each one until then.
"""

from __future__ import annotations

import contextlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.models import Agent, CredentialNeed, Project, Secret, utcnow
from yaaf.server import credential_checks as checks

#: States a need can be in. "saved" is given but not yet proven.
SETTLED = ("ok", "saved", "waived")


def _field(raw: dict, origin: str) -> dict | None:
    key = checks.env_key(str(raw.get("key") or raw.get("label") or ""))
    if not key:
        return None
    return {"key": key, "label": str(raw.get("label") or key.replace("_", " ").title())[:80],
            "help": str(raw.get("help") or "")[:240], "secret": bool(raw.get("secret", True)),
            "file": bool(raw.get("file", False)), "origin": origin, "secret_id": None}


def promise(session: Session, project: Project, promised: list[dict]) -> list[CredentialNeed]:
    """Turn what the chairman promised while founding into needs."""
    made = []
    for item in promised or []:
        name = str(item.get("service") or "").strip()
        if not name:
            continue
        entry = checks.known(name)
        check = None
        if entry is not None:
            fields = [f.view("catalog") | {"secret_id": None} for f in entry.fields]
            service, label = entry.service, entry.label
        else:
            fields = [f for f in (_field(raw, "suggested") for raw in item.get("fields") or []) if f]
            service, label = checks.slug(name), str(item.get("label") or name)[:120]
            try:
                check = checks.validate(item.get("check"))
            except checks.Refused:
                check = None  # a check that could change something is not kept
        need = CredentialNeed(
            id=ids.new_id(ids.CREDENTIAL_NEED), project_id=project.id, service=service,
            label=label, purpose=str(item.get("purpose") or "")[:500],
            source=str(item.get("source") or "creation"), fields=fields,
            roles=[str(r) for r in item.get("roles") or []], check=check, status="needed")
        session.add(need)
        made.append(need)
    return made


def _secrets(session: Session, project_id: str) -> dict[str, Secret]:
    return {s.name: s for s in session.execute(
        select(Secret).where(Secret.project_id == project_id)).scalars()}


def values_of(session: Session, need: CredentialNeed) -> dict[str, str]:
    """The stored values, decrypted for a check. Never leaves the server."""
    from yaaf.core import credential_files
    from yaaf.core.crypto import decrypt

    held = _secrets(session, need.project_id)
    out: dict[str, str] = {}
    for f in need.fields:
        secret = held.get(f["key"])
        if secret is None:
            continue
        value = decrypt(secret.ciphertext)
        if secret.filename:
            # A check reads what it can; an undecodable file fails its check.
            with contextlib.suppress(Exception):
                value = credential_files.decode(value).decode(errors="replace")
        out[f["key"]] = value
    return out


def _role_names(session: Session, project: Project, slugs: list[str]) -> list[str]:
    """The teams that receive it, by name: "Used by Writers, Reviewers"."""
    from yaaf.core.models import Role, RoleVersion, Team

    if not slugs:
        return []
    names = list(session.execute(
        select(Team.name).join(RoleVersion, Team.role_version_id == RoleVersion.id)
        .join(Role, RoleVersion.role_id == Role.id)
        .where(Team.project_id == project.id, Role.slug.in_(slugs))).scalars())
    return names or [s.replace("-", " ").title() for s in slugs]


def view(session: Session, project: Project) -> list[dict]:
    held = _secrets(session, project.id)
    out = []
    for need in session.execute(
            select(CredentialNeed).where(CredentialNeed.project_id == project.id)
            .order_by(CredentialNeed.created_at)).scalars():
        fields = [{k: v for k, v in f.items() if k != "secret_id"} | {"given": f["key"] in held}
                  for f in need.fields]
        known = checks.known(need.service) is not None
        plain = {f["key"]: _plain(held.get(f["key"])) for f in need.fields
                 if not f["secret"] and f["key"] in held}
        out.append({
            "id": need.id, "service": need.service, "label": need.label, "purpose": need.purpose,
            "source": need.source, "status": need.status, "known": known, "fields": fields,
            "roles": need.roles, "used_by": _role_names(session, project, need.roles),
            "check": checks.shown(need.check, plain) if need.check and not known else None,
            "last_check": need.last_check, "last_use": need.last_use,
            "complete": bool(fields) and all(f["given"] for f in fields
                                             if f["origin"] != "user"),
        })
    return out


def _plain(secret: Secret | None) -> str:
    from yaaf.core.crypto import decrypt

    if secret is None or secret.filename:
        return ""
    try:
        return decrypt(secret.ciphertext)
    except Exception:  # noqa: BLE001
        return ""


def add_fields(need: CredentialNeed, extra: list[dict]) -> None:
    keys = {f["key"] for f in need.fields}
    fields = list(need.fields)
    for raw in extra:
        f = _field(raw, "user")
        if f and f["key"] not in keys:
            fields.append(f)
            keys.add(f["key"])
    need.fields = fields


def record_check(need: CredentialNeed, result: dict) -> None:
    need.last_check = {"at": utcnow().isoformat(), **result}
    need.status = "ok" if result["ok"] else "failed"


def run_check(session: Session, need: CredentialNeed) -> dict:
    """Test it now: the catalog's verified test, or the stored suggested call."""
    result, _ = checks.test(need.service, values_of(session, need), need.check)
    record_check(need, result)
    return result


def report_use(session: Session, project: Project, agent: Agent, name: str,
               worked: bool, detail: str) -> CredentialNeed | None:
    """An agent says it used a credential and whether that worked.

    Its word, labelled as its word: yaaf cannot see agents' calls to outside
    services. A refusal turns the need red at once — the chairman would rather
    hear "the researcher says Google refused it" than nothing.
    """
    wanted = checks.env_key(name)
    for need in session.execute(
            select(CredentialNeed).where(CredentialNeed.project_id == project.id)).scalars():
        names = {f["key"] for f in need.fields} | {checks.env_key(need.service),
                                                   checks.env_key(need.label)}
        if wanted in names:
            need.last_use = {"at": utcnow().isoformat(), "ok": bool(worked), "by": agent.name,
                             "detail": (detail or "")[:300]}
            if need.status != "waived":
                need.status = "ok" if worked else "failed"
            return need
    return None


def open_for_ceo(session: Session, project_id: str) -> list[dict]:
    """What the CEO must plan around: promised access not yet given or
    working. Settled ones say nothing — they are just credentials."""
    out = []
    for need in session.execute(
            select(CredentialNeed).where(CredentialNeed.project_id == project_id,
                                         CredentialNeed.status.in_(("needed", "failed")))
            .order_by(CredentialNeed.created_at)).scalars():
        out.append({"service": need.label, "for": need.purpose,
                    "state": "not given yet" if need.status == "needed" else "given, but not working",
                    "variables": [f["key"] for f in need.fields]})
    return out
