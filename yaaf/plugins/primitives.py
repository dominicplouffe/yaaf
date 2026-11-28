"""The mechanisms every rigorous factory needs, held once by core (§4.4).

The Amazon PPC review found the same three bugs a plugin would write again each
time it implemented these itself. A review could choose the number its
re-derivation was compared with. A read whose fields could not be parsed let
any field be cited. A self-review was possible after another seat republished
the report. So a plugin *declares* these in its manifest, and this module
*holds* them. The plugin's hooks are asked only for what is the plugin's to
say: whether an artifact is good, whether a review's rubric is met, what the
pages look like.

* **Gated artifacts.** Publishing a name in `capabilities.artifacts` asks the
  plugin's `check_artifact`. A refusal stores nothing. An accept may normalise
  the bytes, patch `kind_state` and name the load-bearing numbers, which core
  keeps beside the row (`PluginArtifact`), where no later file can change them.
* **Reserved names.** No seat publishes a platform page or a data-action output.
* **Platform pages.** Rendered by the plugin's `render` from the accepted
  subject (a draft), and again when a review passes (final). If the render
  fails, the publication is refused: a page that could not be drawn is never
  replaced by a seat's.
* **Review.** Bound to one exact subject hash. The reviewer must not have
  published that hash (or any version, under `independent = "strict"`). Each
  load-bearing number is compared with what *the subject* reported, never with
  what the review says was reported. Only then is the plugin's `check_review`
  asked. A pass makes the pages final and attests delivery; a later return
  takes completion back.
* **Receipts.** Every data-source read of a plugin project that declares them is
  recorded, with the fields the plugin's `fields_of_read` names. A read whose
  fields could not be told is recorded as unknown (`fields = None`) and backs
  no field citation.

Every plugin call goes through `host.ask_or_fail_closed`, so a plugin that is
down refuses. It never accepts.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from yaaf_plugin_api import hooks as api_hooks
from yaaf_plugin_api import types as T
from yaaf_plugin_api.manifest import Manifest

from yaaf.core import ids, shape
from yaaf.core.models import (
    Agent,
    Artifact,
    AuditLog,
    DataSourceTool,
    PluginArtifact,
    PluginVersion,
    Project,
    Role,
    RoleVersion,
    Team,
    ToolPermission,
)
from yaaf.plugins import host as H

log = logging.getLogger("yaaf.plugins.primitives")

READ_ACTION = "plugin.read"
#: The fields of a review core reads. The rest is the plugin's rubric.
REVIEW_ENVELOPE = ("subject_sha256", "verdict", "rederived")
MAX_RECEIPT_FIELDS = 400


def _refusal(message: str, status: int = 422):
    from yaaf.server.policy import Refusal

    return Refusal(message, status_code=status)


# ── which plugin a project runs ────────────────────────────────────────────────


def plugin_of(session: Session, project: Project) -> tuple[PluginVersion, Manifest] | None:
    """The version a plugin project is pinned to and its manifest, or None."""
    if not shape.is_plugin_kind(project) or not project.plugin_version_id:
        return None
    from yaaf.plugins import registry

    version = session.get(PluginVersion, project.plugin_version_id)
    if version is None:
        return None
    return version, registry.manifest_for(version)


# ── what a hook is shown ───────────────────────────────────────────────────────


def _role_slugs(session: Session, project_id: str) -> dict[str, str]:
    rows = session.execute(
        select(Agent.id, Agent.role_version_id, Team.role_version_id)
        .outerjoin(Team, Team.id == Agent.team_id).where(Agent.project_id == project_id)).all()
    version_ids = {rv for _, a, t in rows for rv in (a, t) if rv}
    slugs = dict(session.execute(
        select(RoleVersion.id, Role.slug).join(Role, Role.id == RoleVersion.role_id)
        .where(RoleVersion.id.in_(version_ids))).all()) if version_ids else {}
    return {aid: slugs.get(a or t, "") for aid, a, t in rows}


def snapshot(session: Session, project: Project, version: PluginVersion, manifest: Manifest,
             *, lane: str | None = "run", today: date | None = None) -> T.ProjectSnapshot:
    from yaaf.server.policy import scope_requirements

    facts = {f.artifact_id: f for f in session.execute(select(PluginArtifact).where(
        PluginArtifact.project_id == project.id)).scalars()}
    artifacts = tuple(
        T.ArtifactMeta(a.name, a.version, a.sha256, a.size_bytes, a.published_by_agent_id,
                       a.run_id, by_platform=a.id in facts
                       and facts[a.id].role in ("page", "output"))
        for a in session.execute(select(Artifact).where(Artifact.project_id == project.id)
                                 .order_by(Artifact.name, Artifact.version)).scalars())
    requirements = tuple(T.RequirementMeta(r.key, (r.statement or "")[:200], str(r.status))
                         for r in scope_requirements(session, project.id))
    slugs = _role_slugs(session, project.id)
    seats = tuple(T.SeatMeta(aid, slug) for aid, slug in sorted(slugs.items()))
    return T.ProjectSnapshot(
        project_id=project.id, kind=shape.plugin_kind(project) or "",
        mode=project.kind_mode or "", plugin_version=version.version,
        today=today or date.today(), kind_state=dict(project.kind_state or {}),
        coverage=coverage(session, project, version, manifest), artifacts=artifacts,
        requirements=requirements, seats=seats, lane=lane)


def coverage(session: Session, project: Project, version: PluginVersion,
             manifest: Manifest) -> T.Coverage:
    """What the connected data can reach, by the plugin's reading of the tools
    the chairman allows. Nothing is reachable when the plugin cannot be asked."""
    if not manifest.coverage:
        return T.Coverage()
    from yaaf.plugins import registry

    tools = [T.ToolInfo(r.source, r.name, r.description or "", "read")
             for r in session.execute(select(DataSourceTool).where(
                 DataSourceTool.project_id == project.id, DataSourceTool.kind == "read",
                 DataSourceTool.permission == ToolPermission.ALLOW,
                 DataSourceTool.gone_at.is_(None))).scalars()]
    answer, _ = H.ask_or_fail_closed(registry.pool_for(version),
                                     api_hooks.HookKey("classify_tools"), [tools])
    return answer


def callbacks(session: Session, project: Project) -> H.Callbacks:
    """What a hook may read back, for this project only."""
    from yaaf.core.artifacts import store

    def read_artifact(name: str, version: int | None) -> bytes | None:
        q = select(Artifact).where(Artifact.project_id == project.id, Artifact.name == name)
        q = q.where(Artifact.version == version) if isinstance(version, int) else \
            q.order_by(Artifact.version.desc())
        row = session.execute(q.limit(1)).scalars().first()
        return store().get_bytes(row.storage_key) if row is not None else None

    return H.Callbacks(read_artifact=read_artifact, receipt=receipt_lookup(session, project))


# ── receipts ───────────────────────────────────────────────────────────────────


def receipt_lookup(session: Session, project: Project) -> Callable[[str], T.Receipt | None]:
    def find(receipt_id: str) -> T.Receipt | None:
        row = session.get(AuditLog, str(receipt_id))
        if row is None or row.project_id != project.id or row.action != READ_ACTION:
            return None
        d = row.detail or {}
        fields = d.get("fields")
        return T.Receipt(row.id, str(d.get("source", "")), str(d.get("tool", "")),
                         str(d.get("result_sha256", "")),
                         frozenset(fields) if isinstance(fields, list) else None)
    return find


def record_read(session: Session, project: Project, agent, source: str, tool: str,
                arguments: Mapping, result: Mapping, run_id: str | None) -> str | None:
    """Write down one read by a seat of a plugin project that declares receipts,
    and return the receipt id; None for every other project or source."""
    found = plugin_of(session, project)
    if found is None:
        return None
    version, manifest = found
    if not manifest.receipts or (manifest.receipt_sources is not None
                                 and source not in manifest.receipt_sources):
        return None
    if not isinstance(result, Mapping) or result.get("isError"):
        return None
    from yaaf.plugins import registry

    answer, failure = H.ask_or_fail_closed(registry.pool_for(version),
                                           api_hooks.HookKey("fields_of_read"),
                                           [tool, dict(result)])
    fields = list(answer.fields)[:MAX_RECEIPT_FIELDS] if answer.fields is not None else None
    body = json.dumps(result, sort_keys=True, default=str).encode()
    rid = ids.new_id(ids.AUDIT)
    session.add(AuditLog(
        id=rid, project_id=project.id,
        actor=f"agent:{agent.id}" if agent is not None else "system:plugin",
        action=READ_ACTION, entity="data_source", entity_id=source[:40],
        detail={"source": source, "tool": tool, "run_id": run_id,
                "arguments_sha256": hashlib.sha256(json.dumps(
                    dict(arguments or {}), sort_keys=True, default=str).encode()).hexdigest(),
                "result_sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body),
                "fields": fields,
                **({"fields_unknown_because": failure.kind} if failure else {})}))
    session.flush()
    return rid


# ── publishing ─────────────────────────────────────────────────────────────────


@dataclass
class Gate:
    """What artifact_put stores, and what core does once the row exists."""

    data: bytes
    role: str
    after: list[Callable[[Artifact], None]] = field(default_factory=list)


def _next_version(session: Session, project_id: str, name: str) -> int:
    return 1 + (session.execute(select(func.max(Artifact.version)).where(
        Artifact.project_id == project_id, Artifact.name == name)).scalar_one() or 0)


def _latest(session: Session, project_id: str, name: str) -> Artifact | None:
    return session.execute(select(Artifact).where(
        Artifact.project_id == project_id, Artifact.name == name)
        .order_by(Artifact.version.desc()).limit(1)).scalars().first()


def merge_patch(target: Any, patch: Any) -> Any:
    """RFC 7386: how an Accept's state_patch changes kind_state."""
    if not isinstance(patch, Mapping):
        return patch
    out = dict(target) if isinstance(target, Mapping) else {}
    for k, v in patch.items():
        if v is None:
            out.pop(k, None)
        else:
            out[k] = merge_patch(out.get(k), v)
    return out


def render_source(manifest: Manifest) -> str | None:
    """The artifact the platform's pages are drawn from: the reviewed subject,
    else the first gated artifact."""
    if not manifest.platform_pages:
        return None
    if manifest.review:
        return manifest.review.of
    return manifest.artifacts[0] if manifest.artifacts else None


def gate_publication(session: Session, project: Project, *, agent_id: str | None,
                     run_id: str | None, filename: str, data: bytes) -> Gate | None:
    """Before a seat's file is stored: refuse it, or say what to store and what
    to do after. None for a project that is not a plugin's, or a name no
    primitive governs."""
    found = plugin_of(session, project)
    if found is None:
        return None
    version, manifest = found
    if filename in manifest.reserved_names():
        raise _refusal(f"{filename} is written by the platform (a page it renders from the "
                       "accepted artifact, or a data action's output); a seat cannot "
                       "publish it.")
    if manifest.review and filename == manifest.review.artifact:
        return _gate_review(session, project, version, manifest, agent_id=agent_id,
                            run_id=run_id, data=data)
    if filename in manifest.artifacts:
        return _gate_artifact(session, project, version, manifest, agent_id=agent_id,
                              run_id=run_id, filename=filename, data=data)
    return None


def _ask(version: PluginVersion, key: api_hooks.HookKey, args: list,
         cb: H.Callbacks) -> Any:
    from yaaf.plugins import registry

    answer, failure = H.ask_or_fail_closed(registry.pool_for(version), key, args, cb)
    if failure is not None:
        log.warning("plugin %s: %s failed closed: %s", version.plugin_id, key, failure)
    return answer


def _render(session: Session, project: Project, version: PluginVersion, manifest: Manifest,
            meta: T.ArtifactMeta, data: bytes, load_bearing: Mapping, reviewed: bool,
            ctx: T.ProjectSnapshot) -> list[T.Page]:
    pages = _ask(version, api_hooks.HookKey("render"),
                 [ctx, T.ArtifactBody(meta, data, dict(load_bearing)), reviewed],
                 callbacks(session, project))
    if pages is None:
        raise _refusal(H.UNAVAILABLE + " (its pages could not be rendered, so nothing was "
                       "stored)")
    return list(pages)


def _gate_artifact(session, project, version, manifest, *, agent_id, run_id, filename, data):
    ctx = snapshot(session, project, version, manifest)
    verdict = _ask(version, api_hooks.HookKey("check_artifact", filename),
                   [ctx, T.Upload(filename, data, agent_id, run_id)],
                   callbacks(session, project))
    if isinstance(verdict, T.Refuse):
        raise _refusal(f"{filename} was not accepted ({len(verdict.problems)} problem"
                       f"{'s' if len(verdict.problems) != 1 else ''}): "
                       + " · ".join(verdict.problems) + ". Nothing was stored.")
    stored = verdict.data if verdict.data is not None else data
    load_bearing = dict(verdict.load_bearing or {})
    is_subject = filename == render_source(manifest)
    gate = Gate(stored, "subject" if manifest.review and filename == manifest.review.of
                else "gated")
    pages: list[T.Page] = []
    if is_subject:
        meta = T.ArtifactMeta(filename, _next_version(session, project.id, filename),
                              hashlib.sha256(stored).hexdigest(), len(stored), agent_id, run_id)
        pages = _render(session, project, version, manifest, meta, stored, load_bearing,
                        False, ctx)

    def record(row: Artifact) -> None:
        session.add(PluginArtifact(artifact_id=row.id, project_id=project.id, name=filename,
                                   role=gate.role, load_bearing=load_bearing))
        if verdict.state_patch:
            project.kind_state = merge_patch(project.kind_state or {}, verdict.state_patch)
        for page in pages:
            store_platform(session, project, run_id, page.name, page.body, role="page",
                           reviewed=False, like=row)
    gate.after.append(record)
    return gate


def _envelope(data: bytes) -> tuple[dict | None, list[str]]:
    try:
        doc = json.loads(data)
    except (UnicodeDecodeError, ValueError) as exc:
        return None, [f"the review is not JSON ({exc})"]
    if not isinstance(doc, dict):
        return None, ["the review must be one JSON object"]
    out = []
    if not isinstance(doc.get("subject_sha256"), str):
        out.append("subject_sha256 names the exact version reviewed (its sha256)")
    if doc.get("verdict") not in ("pass", "return"):
        out.append('verdict is "pass" or "return"')
    rederived = doc.get("rederived", [])
    if not isinstance(rederived, list) or not all(
            isinstance(r, dict) and isinstance(r.get("claim"), str) for r in rederived):
        out.append('rederived is a list of {"claim", "value"} (or {"claim", "matches": true})')
    return (doc if not out else None), out


def _number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v \
        and v not in (float("inf"), float("-inf"))


def rederivation_problems(load_bearing: Mapping[str, Any], rederived: list[dict],
                          tolerance: float) -> list[str]:
    """Each load-bearing number against what the SUBJECT reported. A review's
    own 'reported' (or any other field it adds) is never read."""
    given = {r["claim"]: r for r in rederived}
    out = []
    for claim, reported in load_bearing.items():
        entry = given.get(claim)
        if entry is None:
            out.append(f"load-bearing claim {claim} was not re-derived")
            continue
        if not _number(reported):
            if entry.get("matches") is not True:
                out.append(f"claim {claim}: say matches: true once your reading agrees")
            continue
        value = entry.get("value")
        if not _number(value):
            out.append(f"claim {claim}: value is the number you re-derived")
            continue
        apart = abs(value) if reported == 0 else abs(value / reported - 1)
        if apart > tolerance:
            out.append(f"claim {claim}: re-derived {value:,.2f} against {reported:,.2f} the "
                       f"subject reported, more than {tolerance:.0%} apart; that is a return")
    return out


def _gate_review(session, project, version, manifest, *, agent_id, run_id, data):
    review_spec = manifest.review
    doc, problems = _envelope(data)
    if doc is None:
        raise _refusal(f"{review_spec.artifact} was not accepted: " + " · ".join(problems))
    subject = _latest(session, project.id, review_spec.of)
    if subject is None:
        raise _refusal(f"there is no {review_spec.of} to review yet", 409)
    if doc["subject_sha256"] != subject.sha256:
        raise _refusal(f"subject_sha256 must be the sha256 of the latest {review_spec.of} "
                       f"(v{subject.version}); a review is of one version, and this names "
                       "another", 409)
    if agent_id is None:
        raise _refusal("a review is a seat's: it is published from a run", 403)
    q = select(Artifact.version).where(Artifact.project_id == project.id,
                                       Artifact.name == review_spec.of,
                                       Artifact.published_by_agent_id == agent_id)
    if review_spec.independent == "hash":
        q = q.where(Artifact.sha256 == subject.sha256)
    own = session.execute(q).scalars().all()
    if own:
        raise _refusal(f"you published {review_spec.of} "
                       f"{'with this exact content' if review_spec.independent == 'hash' else ''}"
                       f" (v{', v'.join(map(str, sorted(own)))}); a different seat reviews it",
                       403)
    facts = session.get(PluginArtifact, subject.id)
    load_bearing = dict(facts.load_bearing or {}) if facts else {}
    if doc["verdict"] == "pass":
        problems = rederivation_problems(load_bearing, doc.get("rederived") or [],
                                         review_spec.rederive_tolerance)
        if problems:
            raise _refusal(f"{review_spec.artifact} cannot pass: " + " · ".join(problems))

    from yaaf.core.artifacts import store

    subject_data = store().get_bytes(subject.storage_key)
    meta = T.ArtifactMeta(subject.name, subject.version, subject.sha256, subject.size_bytes,
                          subject.published_by_agent_id, subject.run_id)
    ctx = snapshot(session, project, version, manifest)
    verdict = _ask(version, api_hooks.HookKey("check_review", review_spec.artifact),
                   [ctx, T.ReviewUpload(review_spec.artifact, data, agent_id, run_id,
                                        subject_sha256=subject.sha256),
                    T.ArtifactBody(meta, subject_data, load_bearing)],
                   callbacks(session, project))
    if isinstance(verdict, T.Refuse):
        raise _refusal(f"{review_spec.artifact} was not accepted: "
                       + " · ".join(verdict.problems))
    passed = doc["verdict"] == "pass"
    pages = _render(session, project, version, manifest, meta, subject_data, load_bearing,
                    True, ctx) if passed and manifest.platform_pages else []
    gate = Gate(data, "review")

    def record(row: Artifact) -> None:
        session.add(PluginArtifact(artifact_id=row.id, project_id=project.id,
                                   name=review_spec.artifact, role="review",
                                   verdict=doc["verdict"], subject_sha256=subject.sha256))
        for page in pages:
            store_platform(session, project, run_id, page.name, page.body, role="page",
                           reviewed=True, like=subject)
        if passed:
            from yaaf.server.policy import attest_shipped

            attest_shipped(session, project,
                           f"{manifest.name}: {review_spec.of} v{subject.version} passed an "
                           f"independent review ({review_spec.artifact} v{row.version})",
                           row.uri)
    gate.after.append(record)
    return gate


def store_platform(session: Session, project: Project, run_id: str | None, name: str,
                   body: bytes, *, role: str, reviewed: bool = False,
                   like: Artifact | None = None) -> Artifact:
    """An artifact the platform wrote: a rendered page or a data action's output.
    It carries the reader and requirements of the artifact it came from."""
    from yaaf.core.artifacts import store

    key = f"projects/{project.id}/{run_id or 'platform'}/{name}"
    row = Artifact(
        id=ids.new_id(ids.ARTIFACT), project_id=project.id, name=name,
        version=_next_version(session, project.id, name),
        sha256=hashlib.sha256(body).hexdigest(), size_bytes=len(body), storage_key=key,
        uri=store().put_bytes(key, body), published_by_agent_id=None, run_id=run_id,
        requirement_ids=list(like.requirement_ids or []) if like else [],
        reader=like.reader if like else None,
        announced_at=like.announced_at if like else None)
    session.add(row)
    session.flush()
    session.add(PluginArtifact(artifact_id=row.id, project_id=project.id, name=name,
                               role=role, reviewed=reviewed))
    return row


# ── completion ─────────────────────────────────────────────────────────────────


def review_passed(session: Session, project: Project, manifest: Manifest) -> bool:
    """The latest review passes the latest subject, exactly that version."""
    spec = manifest.review
    subject = _latest(session, project.id, spec.of)
    review = _latest(session, project.id, spec.artifact)
    if subject is None or review is None:
        return False
    facts = session.get(PluginArtifact, review.id)
    return facts is not None and facts.verdict == "pass" and \
        facts.subject_sha256 == subject.sha256


def outstanding(session: Session, project: Project) -> list[str]:
    """What core itself holds a plugin project's completion on. The plugin's own
    completion gate is added beside this in build step 5; this one it cannot omit."""
    found = plugin_of(session, project)
    if found is None:
        return []
    _, manifest = found
    if manifest.review and not review_passed(session, project, manifest):
        return [f"no passing independent review of the latest {manifest.review.of} "
                f"({manifest.review.artifact})"]
    return []
