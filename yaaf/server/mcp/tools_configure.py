"""Setup work: teams, data sources, credentials by reference, rules.

The scope to grant sparingly, and the one where "no rule lives here and nowhere
else" is load-bearing — every call goes through the same admin handler the
Settings screen calls, so a refusal there is a refusal here.

Two things this scope handles that the rest of the surface refuses outright:

**A credential value.** `credential` accepts one, writes it encrypted, and
forgets it. That is the *only* direction values move on this surface: nothing
here reads one back, and `GET /admin/secrets/{id}/value` is unreachable at any
scope. The asymmetry is deliberate — writing a secret is a chairman's setup
step, reading one back into a model's context is an exfiltration.

**An amendment.** Changing a rule the agents run under is the largest thing a
client can do that does not spend money, so it carries a written reason, a
confirmation, and the platform's own schema check on the way in.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from yaaf.core.models import Team
from yaaf.server.api import admin
from yaaf.server.mcp import confirm, envelope
from yaaf.server.mcp.auth import Principal, ToolRefusal, resolve_project
from yaaf.server.mcp.registry import register, required
from yaaf.server.mcp.tools_read import DETAIL_ARG, PROJECT_ARG, _owned
from yaaf.server.mcp.tools_steer import CONFIRM_ARG, REASON_ARG, _call, _record


def _schema(required_args: list[str], **properties) -> dict:
    return {
        "type": "object",
        "properties": {"project": PROJECT_ARG, "confirm": CONFIRM_ARG,
                       "detail": DETAIL_ARG, **properties},
        "required": ["project", *required_args],
        "additionalProperties": False,
    }


@register(
    "project_configure",
    scope="configure",
    description=(
        "The project's outward wiring: name, goal, deadline, controlled domains, which "
        "stored credential it bills against. Rules are not here — those are `amend`, "
        "with a reason. verb `add_team` hires a team; `repin` moves a team to the "
        "library's current version of its role."
    ),
    input_schema=_schema([],
                         verb={"type": "string", "enum": ["settings", "add_team", "repin"]},
                         name={"type": "string"},
                         goal={"type": "string"},
                         deadline={"type": "string", "description": "ISO 8601, or empty to clear"},
                         controlled_domains={"type": "array", "items": {"type": "string"}},
                         billing_secret={"type": "string",
                                         "description": "Name of a stored billing credential"},
                         team={"type": "string", "description": "repin: team id"},
                         role_slug={"type": "string", "description": "add_team: the role to hire"},
                         team_name={"type": "string"},
                         employees={"type": "array", "items": {"type": "string"}},
                         lead={"type": "string"},
                         reason=REASON_ARG),
)
def project_configure(principal: Principal, session: Session, arguments: dict) -> dict:
    from yaaf.server.mcp.tools_create import _billing_secret

    project = resolve_project(session, principal, required(arguments, "project"))
    verb = arguments.get("verb") or "settings"

    if verb == "add_team":
        employees = [e for e in (arguments.get("employees") or []) if isinstance(e, str)]
        if not employees:
            raise ToolRefusal("a team needs at least one employee")
        preview = confirm.gate(session, principal, "project_configure", arguments, {
            "verb": verb, "project": project.slug,
            "hiring": {"role": arguments.get("role_slug"), "name": arguments.get("team_name"),
                       "employees": employees},
            "warning": "New agents wake on their own schedule and bill this project."})
        if preview:
            return preview
        outcome = _call(principal, admin.add_team, project_id=project.id,
                        body=admin.TeamSpec(role_slug=required(arguments, "role_slug"),
                                            name=required(arguments, "team_name"),
                                            employees=employees,
                                            lead=arguments.get("lead")),
                        session=session)
    elif verb == "repin":
        team = _owned(session, project, Team, str(arguments.get("team") or ""), "team")
        preview = confirm.gate(session, principal, "project_configure", arguments, {
            "verb": verb, "project": project.slug, "team": team.name,
            "note": "Repinning changes the prompt this team's agents run under."})
        if preview:
            return preview
        outcome = _call(principal, admin.repin_team, team_id=team.id, session=session)
    else:
        billing = _billing_secret(session, arguments.get("billing_secret"))
        patch = admin.ProjectPatch(
            name=arguments.get("name"),
            goal=arguments.get("goal"),
            controlled_domains=arguments.get("controlled_domains"),
            billing_secret_id=billing.id if billing else None,
            deadline=arguments.get("deadline") or None,
            clear_deadline=arguments.get("deadline") == "",
        )
        changing = {k: v for k, v in patch.model_dump(exclude_none=True).items()
                    if k != "clear_deadline" or v}
        if not changing:
            raise ToolRefusal("nothing to change")
        preview = confirm.gate(session, principal, "project_configure", arguments, {
            "verb": verb, "project": project.slug, "changing": sorted(changing),
            "billing": billing.name if billing else None})
        if preview:
            return preview
        outcome = _call(principal, admin.patch_project, project_id=project.id,
                        body=patch, session=session)
    return _record(session, principal, project.id, "project_configure", arguments, outcome)


@register(
    "data_source",
    scope="configure",
    description=(
        "External MCP servers this project's agents may reach. verb: list, add (name, "
        "url, roles), check (speak MCP to it and report its tools), oauth_start (get the "
        "sign-in URL for a hosted server), remove. Mark anything that serves rows as "
        "access `bulk` — for a bulk source the expensive path stops existing."
    ),
    input_schema=_schema([],
                         verb={"type": "string",
                               "enum": ["list", "add", "check", "oauth_start", "remove"]},
                         name={"type": "string", "description": "The source's short name"},
                         url={"type": "string"},
                         roles={"type": "array", "items": {"type": "string"}},
                         access={"type": "string", "enum": ["inline", "bulk"]},
                         auth={"type": "string", "enum": ["none", "bearer", "oauth"]},
                         bearer={"type": "string",
                                 "description": "A token, written and forgotten. Prefer oauth"}),
)
def data_source(principal: Principal, session: Session, arguments: dict) -> dict:
    project = resolve_project(session, principal, required(arguments, "project"))
    verb = arguments.get("verb") or "list"

    if verb == "list":
        sources = (project.integrations or {}).get("mcp_servers", [])
        return envelope.fit({
            "project": project.slug,
            # Names, URLs, roles and health — never the token secret's value,
            # which is not in this structure to begin with.
            "sources": [{k: v for k, v in s.items() if k != "bearer"} for s in sources],
        }, arguments.get("detail"))

    name = required(arguments, "name")
    if verb == "check":
        outcome = _call(principal, admin.check_data_source, project_id=project.id,
                        name=name, session=session)
        return envelope.fit({"project": project.slug, **outcome}, arguments.get("detail"))
    if verb == "oauth_start":
        raise ToolRefusal(
            "starting an OAuth sign-in needs a browser: the URL it returns has to be "
            "opened by the chairman, and the state it carries is single-use. Do it in "
            "Settings → Data sources → Connect.")

    if verb == "remove":
        preview = confirm.gate(session, principal, "data_source", arguments, {
            "verb": verb, "project": project.slug, "removing": name,
            "warning": "Agents granted this source lose the tools it mounts."})
        if preview:
            return preview
        outcome = _call(principal, admin.delete_data_source, project_id=project.id,
                        name=name, session=session)
    else:
        roles = [r for r in (arguments.get("roles") or []) if isinstance(r, str)]
        if not roles:
            raise ToolRefusal("name at least one role that should get this source")
        preview = confirm.gate(session, principal, "data_source", arguments, {
            "verb": "add", "project": project.slug, "name": name,
            "url": arguments.get("url"), "roles": roles,
            "access": arguments.get("access") or "inline",
            "note": "Saving under an existing name is the edit path."})
        if preview:
            return preview
        outcome = _call(principal, admin.add_data_source, project_id=project.id,
                        body=admin.DataSourceIn(
                            name=name, url=required(arguments, "url"), roles=roles,
                            bearer=arguments.get("bearer"),
                            access=arguments.get("access") or "inline",
                            auth=arguments.get("auth")),
                        session=session)
    # The bearer never reaches the record — `_record` writes the arguments, and
    # a token in an audit row is a token in a log.
    scrubbed = {k: v for k, v in arguments.items() if k != "bearer"}
    return _record(session, principal, project.id, "data_source", scrubbed, outcome)


@register(
    "credential",
    scope="configure",
    description=(
        "Store or re-scope a credential. Values go in and never come back out: no tool "
        "on this surface reads one, at any scope. Omit `value` to change which roles "
        "get an existing credential without re-pasting it."
    ),
    input_schema=_schema([],
                         verb={"type": "string", "enum": ["list", "save"]},
                         name={"type": "string"},
                         value={"type": "string",
                                "description": "Written encrypted and forgotten. Never read back"},
                         roles={"type": "array", "items": {"type": "string"}},
                         kind={"type": "string", "enum": ["service", "billing"]}),
)
def credential(principal: Principal, session: Session, arguments: dict) -> dict:
    project = resolve_project(session, principal, required(arguments, "project"))
    if (arguments.get("verb") or "list") == "list":
        rows = _call(principal, admin.list_secrets, project_id=project.id, session=session)
        # The endpoint returns names and scopes only; asserted here as well
        # because this is the surface where a leak would be worst.
        return envelope.fit({
            "project": project.slug,
            "credentials": [{k: v for k, v in r.items() if k != "value"} for r in rows],
        }, arguments.get("detail"))

    name = required(arguments, "name")
    preview = confirm.gate(session, principal, "credential",
                           {k: v for k, v in arguments.items() if k != "value"}, {
                               "verb": "save", "project": project.slug, "name": name,
                               "roles": arguments.get("roles") or [],
                               "kind": arguments.get("kind") or "service",
                               "value_supplied": bool(arguments.get("value")),
                               "note": ("The value is stored encrypted and cannot be read "
                                        "back through this surface."),
                           })
    if preview:
        return preview

    outcome = _call(principal, admin.put_secret,
                    body=admin.SecretIn(project_id=project.id, name=name,
                                        value=arguments.get("value"),
                                        role_scopes=arguments.get("roles") or [],
                                        kind=arguments.get("kind")),
                    session=session)
    scrubbed = {k: v for k, v in arguments.items() if k != "value"}
    return _record(session, principal, project.id, "credential", scrubbed, outcome)


@register(
    "amend",
    scope="configure",
    description=(
        "The rules the agents run under. verb: schema (every rule, its default and what "
        "it refuses), list, propose (with a written reason — recorded, audited and "
        "disclosed to the CEO), cancel."
    ),
    input_schema=_schema([],
                         verb={"type": "string",
                               "enum": ["schema", "list", "propose", "cancel"]},
                         changes={"type": "object",
                                  "description": "rule name → new value"},
                         amendment={"type": "string", "description": "cancel: amendment id"},
                         reason=REASON_ARG),
)
def amend(principal: Principal, session: Session, arguments: dict) -> dict:
    project = resolve_project(session, principal, required(arguments, "project"))
    verb = arguments.get("verb") or "list"

    if verb == "schema":
        return envelope.fit({"rules": admin.constitution_schema()}, arguments.get("detail"))
    if verb == "list":
        rows = _call(principal, admin.list_amendments, project_id=project.id, session=session)
        return envelope.fit({"project": project.slug, "amendments": rows},
                            arguments.get("detail"))

    if verb == "cancel":
        preview = confirm.gate(session, principal, "amend", arguments, {
            "verb": verb, "project": project.slug,
            "amendment": arguments.get("amendment")})
        if preview:
            return preview
        outcome = _call(principal, admin.cancel_amendment,
                        amendment_id=str(arguments.get("amendment") or ""), session=session)
        return _record(session, principal, project.id, "amend", arguments, outcome)

    changes = arguments.get("changes")
    if not isinstance(changes, dict) or not changes:
        raise ToolRefusal("`changes` must name at least one rule and its new value")
    reason = required(arguments, "reason")
    if len(reason) < 10:
        # The endpoint's own floor, enforced before the preview so the chairman
        # is not shown a change that will be refused a call later.
        raise ToolRefusal("an amendment's reason must be at least ten characters")

    preview = confirm.gate(session, principal, "amend", arguments, {
        "verb": "propose", "project": project.slug, "changes": changes, "reason": reason,
        "warning": ("This changes the rules every agent in this project runs under. "
                    "It is recorded, audited, and disclosed to the CEO.")})
    if preview:
        return preview
    outcome = _call(principal, admin.amend_constitution, project_id=project.id,
                    body=admin.AmendmentIn(reason=reason, changes=changes), session=session)
    return _record(session, principal, project.id, "amend", arguments, outcome)


@register(
    "billing_check",
    scope="configure",
    description=(
        "Verify this project's Anthropic credential actually works. Read-only and free; "
        "the answer is the same one the health page shows."
    ),
    input_schema=_schema([]),
)
def billing_check(principal: Principal, session: Session, arguments: dict) -> dict:
    project = resolve_project(session, principal, required(arguments, "project"))
    outcome = _call(principal, admin.billing_check, project_id=project.id, session=session)
    return envelope.fit({"project": project.slug, **outcome}, arguments.get("detail"))
