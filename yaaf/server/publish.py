"""Publishing a project or a role to a library source.

Three steps, and this module is the first and the last of them:

1. **Draft** (`project_draft`, `role_draft`). The plain design export, never
   the backup with values, turned into a registry listing: the chairman's
   account, repo, domains and every data-source address that is his are left
   out; keys and data sources become declared `needs`; standing orders ride in
   the goal; roles that came from a listing are referenced, not copied.
2. **Generalize** (yaaf.founder.publish). The Founder turns what is left of
   the publisher's specifics into `{{blanks}}` and asks only real doubts.
3. **Check** (`leaks`). Before anything leaves, the draft is compared with
   every value this install knows is the publisher's own — each key's value
   (plain, base64, and with whitespace taken out), the chairman's email, the
   project's domains, repo, data-source hosts and name. One match and the
   draft does not leave. `submit` runs it again on the server; nothing the
   browser or the Founder says can skip it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core.models import Project, Role, RoleVersion, Secret, User
from yaaf.server import library

#: A value shorter than this matches too much ordinary text to mean anything.
MIN_PRIVATE_LEN = 5
SLUG = re.compile(r"^[a-z0-9-]{2,64}$")


class PublishRefusal(Exception):
    def __init__(self, text: str, status: int = 422, detail: dict | None = None):
        super().__init__(text)
        self.text = text
        self.status = status
        self.detail = detail


# ── what is the publisher's own ────────────────────────────────────────────────


def _secret_values(session: Session, rows) -> list[tuple[str, str]]:
    from yaaf.core.crypto import decrypt

    out = []
    for secret in rows:
        try:
            value = decrypt(secret.ciphertext)
        except Exception:  # an unreadable row is nobody's to publish
            continue
        if value and len(value) >= MIN_PRIVATE_LEN:
            out.append((f"the value of the key '{secret.name}'", value))
    return out


def project_private(session: Session, project: Project) -> list[tuple[str, str]]:
    """Every value this install knows is the publisher's, with a label that
    names it without repeating it."""
    values: list[tuple[str, str]] = []
    values += _secret_values(session, session.execute(
        select(Secret).where(Secret.project_id == project.id)).scalars())
    chairman = session.get(User, project.chairman_user_id) if project.chairman_user_id else None
    if chairman and chairman.email:
        values.append(("the chairman's email", chairman.email))
        local = chairman.email.split("@", 1)[0]
        if len(local) >= 6:
            values.append(("the chairman's email name", local))
    for domain in project.controlled_domains or []:
        values.append(("a domain the project controls", str(domain)))
    if project.github_repo:
        values.append(("the project's GitHub repository", project.github_repo))
    for entry in (project.integrations or {}).get("mcp_servers") or []:
        host = entry.get("url_host")
        if host:
            values.append((f"the address of the data source '{entry.get('name')}'", str(host)))
    mail = (project.integrations or {}).get("mail") or {}
    for key in ("address", "username", "imap_host", "smtp_host"):
        if mail.get(key):
            values.append(("the project's mailbox", str(mail[key])))
    if project.name and len(project.name) >= MIN_PRIVATE_LEN:
        values.append(("the project's name", project.name))
    return [(what, v) for what, v in values if v and len(v) >= 4]


def install_private(session: Session) -> list[tuple[str, str]]:
    """For a role, which belongs to no project: every key on the install, and
    every person's email."""
    values = _secret_values(session, session.execute(
        select(Secret).where(Secret.kind != "mailbox")).scalars())
    for user in session.execute(select(User)).scalars():
        if user.email:
            values.append(("a person's email on this install", user.email))
    return values


def _variants(what: str, value: str) -> list[tuple[str, str]]:
    out = [(what, value)]
    squeezed = re.sub(r"\s+", "", value)
    if squeezed != value:
        out.append((what, squeezed))
    if what.startswith("the value of"):
        encoded = base64.b64encode(value.encode()).decode()
        out.append((f"{what}, base64-encoded", encoded.rstrip("=")))
    return out


def leaks(payload: dict, private: list[tuple[str, str]]) -> list[dict]:
    """Where anything of the publisher's appears in `payload`, by label. The
    value itself is never repeated back."""
    from yaaf.server.library import BLANK  # noqa: F401  (blanks are fine; values are not)

    found: list[dict] = []
    text_of: list[tuple[str, str]] = []

    def walk(value, path=""):
        if isinstance(value, str):
            text_of.append((path, value))
        elif isinstance(value, dict):
            for k, v in value.items():
                walk(v, f"{path}.{k}" if path else str(k))
        elif isinstance(value, list):
            for i, v in enumerate(value):
                walk(v, f"{path}[{i}]")

    walk(payload)
    seen = set()
    for what, value in private:
        for label, needle in _variants(what, value):
            low = needle.lower()
            for path, text in text_of:
                hay = text.lower()
                if low in hay or (len(low) >= 12 and low in re.sub(r"\s+", "", hay)):
                    key = (label, path)
                    if key not in seen:
                        seen.add(key)
                        found.append({"what": label, "where": path})
    return found


# ── drafts ─────────────────────────────────────────────────────────────────────


def _orders_text(orders) -> list[str]:
    out = []
    for order in orders or []:
        if isinstance(order, str):
            out.append(order)
        elif isinstance(order, dict):
            text = order.get("text") or order.get("order") or order.get("standing_order")
            if text:
                out.append(str(text))
    return out


def _role_definition(version: RoleVersion, role: Role) -> dict:
    return {
        "slug": role.slug, "name": role.name, "is_ceo_role": role.is_ceo_role,
        "category": role.category, "domains": role.domains or [],
        "description": version.description, "system_prompt": version.system_prompt,
        "effort": version.effort, "harness": version.harness,
        "tool_permissions": {k: v for k, v in (version.tool_permissions or {}).items()
                             if k != "mcp_servers"},
        "secret_scopes": list(version.secret_scopes or []),
    }


def _key_needs(secrets: list[dict], hired: set[str]) -> list[dict]:
    keys = []
    for secret in secrets:
        roles = [r for r in secret.get("role_scopes") or [] if r in hired]
        if not roles:
            continue
        keys.append({"name": secret["name"], "label": secret["name"].replace("-", " ").capitalize(),
                     "file": bool(secret.get("file")), "roles": roles, "required": True})
    return keys


def project_draft(session: Session, project: Project, *, public_sources: set[str] | None = None,
                  include_receipt: bool = True) -> dict:
    """A listing for this project, before the Founder generalizes it."""
    from yaaf.server.api import admin, observe

    public_sources = public_sources or set()
    exported = admin.export_project(project.id, include_values=False, session=session)
    spec = dict(exported["project"])
    for private in ("chairman_email", "github_repo", "billing_secret_name", "credential_names",
                    "fallback_credential_name", "deadline", "deadline_source", "ceo_model_override",
                    "ceo_harness_override"):
        spec.pop(private, None)
    inputs: list[dict] = []
    if spec.get("controlled_domains"):
        # Whose evidence counts as their own is the installer's domain, not
        # the publisher's: it becomes a blank the Founder asks about.
        spec["controlled_domains"] = ["{{your_domain}}"]
        inputs.append({"id": "your_domain", "question": "Which domain is yours?", "type": "domain",
                       "example": "your-site.example", "required": True})
    orders = _orders_text(project.standing_orders)
    if orders:
        spec["goal"] = spec["goal"].rstrip() + "\n\nStanding orders:\n" + "\n".join(f"- {o}" for o in orders)
    integrations = dict(spec.get("integrations") or {})
    sources = []
    data_needs = []
    for entry in integrations.get("mcp_servers") or []:
        entry = dict(entry)
        public = entry.get("name") in public_sources
        host, port = entry.pop("url_host", None), entry.pop("url_port", None)
        address = f"https://{host}" + (f":{port}" if port and port != 443 else "") if public and host \
            else "bring-your-own"
        sources.append(entry)
        data_needs.append({"name": entry.get("name"), "serves": entry.get("name"),
                           "address": address, "auth": entry.get("auth") or (
                               "bearer" if entry.get("bearer_secret") else "none"),
                           "access": entry.get("access") or "inline",
                           "tools": list(entry.get("tools") or []),
                           "roles": list(entry.get("roles") or []), "required": True})
    if sources:
        integrations["mcp_servers"] = sources
    spec["integrations"] = {k: v for k, v in integrations.items() if k in ("runtime", "mcp_servers")}
    for team in spec.get("teams") or []:
        for override in ("model_overrides", "effort_overrides", "harness_overrides"):
            team.pop(override, None)
    roles = list(exported.get("roles") or [])
    hired = {r["slug"] for r in roles}
    uses = []
    for definition in roles:
        role = session.execute(select(Role).where(Role.slug == definition["slug"])).scalar_one_or_none()
        if role is None or not role.source:
            continue
        pinned = session.execute(
            select(RoleVersion).where(RoleVersion.role_id == role.id,
                                      RoleVersion.source_version.is_not(None))
            .order_by(RoleVersion.version.desc()).limit(1)).scalar_one_or_none()
        if pinned is not None and library.same_content(pinned, definition):
            uses.append({"id": role.source, "version": pinned.source_version, "slug": role.slug})
    secrets = [s for s in exported.get("secrets") or [] if s.get("kind", "service") == "service"]
    bundle = {"yaaf_bundle": 1, "kind": "project_setup", "source_slug": spec.get("slug"),
              "values_included": False, "project": spec, "roles": roles,
              "secrets": [{"name": s["name"], "role_scopes": s.get("role_scopes") or [],
                           "kind": "service", "file": bool(s.get("file"))} for s in secrets]}
    version = {"kind": "project", "changes": "First version.", "bundle": bundle,
               "needs": {"keys": _key_needs(secrets, hired), "data_sources": data_needs,
                         "mailbox": bool(spec.get("needs_mail")),
                         "github_repo": bool(project.github_repo)},
               "inputs": inputs}
    if uses:
        version["uses"] = uses
    if include_receipt:
        try:
            receipt = observe.receipt(project.id, session=session)
            if (receipt.get("cost") or {}).get("runs"):
                version["receipt"] = {k: receipt[k] for k in ("project", "cost", "verified")
                                      if k in receipt}
                version["receipt"]["project"] = {k: v for k, v in (receipt.get("project") or {}).items()
                                                 if k not in ("name", "slug", "id")}
        except Exception:
            pass
    categories = [r.get("category") for r in roles if r.get("category")]
    listing = {"kind": "project", "name": project.name, "summary": "",
               "category": max(set(categories), key=categories.count) if categories else "build",
               "domains": sorted({d for r in roles for d in (r.get("domains") or [])})[:6] or ["general"],
               "license": "CC-BY-4.0", "status": "active"}
    return {"slug": spec.get("slug") or project.slug, "listing": listing, "version": version}


def role_draft(session: Session, role: Role) -> dict:
    version = session.execute(select(RoleVersion).where(RoleVersion.role_id == role.id)
                              .order_by(RoleVersion.version.desc()).limit(1)).scalar_one()
    definition = _role_definition(version, role)
    keys = [{"name": s, "label": s.replace("-", " ").capitalize(), "file": False,
             "roles": [role.slug], "required": True} for s in definition["secret_scopes"]]
    listing = {"kind": "role", "name": role.name, "summary": "",
               "category": role.category or "build", "domains": role.domains or ["general"],
               "license": "CC-BY-4.0", "status": "active"}
    if role.source:
        listing["forked_from"] = {"id": role.source, "version": version.source_version or 1}
    return {"slug": role.slug, "listing": listing,
            "version": {"kind": "role", "changes": "First version.", "role": definition,
                        "needs": {"keys": keys, "data_sources": [], "mailbox": False,
                                  "github_repo": False}}}


# ── the shape a registry will accept ───────────────────────────────────────────


def shape_problems(draft: dict) -> list[str]:
    """What registry/tools/check.py would refuse, checked here first so a
    submission never fails in CI for something yaaf could have said."""
    problems: list[str] = []
    listing, version = draft.get("listing") or {}, draft.get("version") or {}
    if not SLUG.match(str(draft.get("slug") or "")):
        problems.append("the listing needs a slug like 'ads-audit'")
    summary = str(listing.get("summary") or "")
    if not 10 <= len(summary) <= 160:
        problems.append("the summary is one sentence of 10 to 160 characters")
    if listing.get("kind") not in ("role", "project") or version.get("kind") != listing.get("kind"):
        problems.append("a listing is a role or a project")
    roles = ([version.get("role") or {}] if version.get("kind") == "role"
             else list((version.get("bundle") or {}).get("roles") or []))
    for role in roles:
        if library.blanks_in(role):
            problems.append(f"role {role.get('slug')} has a blank; a role serves many projects, "
                            "so move the specific line to the project's standing orders")
    inputs = {i.get("id"): i for i in version.get("inputs") or [] if isinstance(i, dict)}
    if version.get("kind") == "project":
        used = library.blanks_in((version.get("bundle") or {}).get("project") or {})
        for blank in sorted(used - inputs.keys()):
            problems.append(f"the blank {{{{{blank}}}}} has no question")
        for blank in sorted(inputs.keys() - used):
            problems.append(f"the question '{blank}' is used by no blank")
        for blank, spec in inputs.items():
            if not spec.get("question") or "example" not in spec:
                problems.append(f"the blank {blank} needs a question and an example")
    elif inputs:
        problems.append("a role has no blanks to ask about")
    secrets = ((version.get("bundle") or {}).get("secrets") or [])
    if any("value" in s or "ciphertext" in s for s in secrets if isinstance(s, dict)):
        problems.append("a key's value is in the listing")
    return problems


def finalise(draft: dict, handle: str, version_number: int, today: str) -> tuple[dict, dict, bytes]:
    """The two files a submission writes: listing.json and versions/<n>.json."""
    slug = draft["slug"]
    listing_id = f"@{handle.lower()}/{slug}"
    listing = {"id": listing_id, **{k: v for k, v in draft["listing"].items() if v is not None}}
    listing.setdefault("status", "active")
    body = {"id": listing_id, "version": version_number, "kind": listing["kind"],
            "published_at": today, **draft["version"]}
    body.setdefault("changes", "First version." if version_number == 1 else "Updated.")
    raw = (json.dumps(body, indent=2, ensure_ascii=False) + "\n").encode()
    return listing, body, raw


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def host_of(url: str) -> str:
    return urlparse(url).hostname or ""


# ── telling a listing how it went ──────────────────────────────────────────────


def outcome_report(session: Session, project: Project) -> dict | None:
    """What a project started from a listing can tell that listing: numbers
    from its receipt, and nothing else. No name, no goal, no domain."""
    from yaaf.server.receipt import receipt

    listing = library.listing_of(project)
    if not listing:
        return None
    r = receipt(session, project)
    p, cost, verified = r["project"], r["cost"], r["verified"]
    req, ev = verified["requirements"], verified["evidence"]
    return {
        "listing": listing["id"], "version": listing.get("version"),
        "finished": p["status"] == "completed", "status": p["status"], "mode": p["mode"],
        "days": p["days"], "teams": p["teams"], "seats": p["seats"],
        "usd": round(float(cost["usd"]), 2) if p.get("billing_mode") == "api_key" else None,
        "runs": cost["runs"],
        "requirements_met": req["met"], "requirements": req["met"] + req["open"] + req["cut"],
        "claims_verified": ev["verified"], "claims": ev["external"],
    }
