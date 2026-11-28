"""The plugins an install knows, the versions it holds, and which may be used.

A version is recorded whatever its outcome. A failed install keeps its report,
so the operator can read why. But only a version that passed may be chosen
for a project:

* ``ready``    — every applicable requirement passed (the conformance suite
  sets this; build step 6). The only status a production box offers.
* ``pending``  — nothing failed, but the suite has not run. Offered only when
  the operator set ``allow_unverified_plugins`` on a development box.
* ``failed``   — a requirement failed. Never offered.
* ``disabled`` — the operator withdrew it. Projects on it keep running (their
  rules are pinned); new projects cannot choose it.

What the registry decides itself, at install, is what only core can know:

* MIN-02: the plugin id keeps one kind across its versions, no two plugins
  share a kind, and no kind takes a built-in mode's name;
* MIN-04 and MIN-05, for real: the version's code is started in a sandboxed
  host (the same launcher plugin calls use), and the hooks it reports are
  compared with the hooks its manifest declares.

This step installs from a local path (development). A GitHub URL, pinned to a
commit and built with hashed dependencies, is build step 7 and lands in the
same rows.
"""

from __future__ import annotations

import atexit
import hashlib
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from yaaf_plugin_api import hooks as api_hooks
from yaaf_plugin_api import report as api_report
from yaaf_plugin_api.manifest import CORE_KINDS
from yaaf_plugin_api.version import API_VERSION

from yaaf.core import ids
from yaaf.core.config import settings
from yaaf.core.models import AuditLog, Plugin, PluginVersion, Project
from yaaf.plugins import host as H
from yaaf.server import acting

SELECTABLE = ("ready",)
MAX_TREE_BYTES = 50 * 1024 * 1024
_SKIP = {".git", "__pycache__", ".pytest_cache", ".venv", "node_modules"}


class RegistryRefusal(Exception):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class Installed:
    version: PluginVersion
    created: bool  # False when this exact content was already installed


def plugins_root() -> Path:
    root = Path(settings().plugins_root)
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _files(root: Path) -> list[Path]:
    out = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in _SKIP for part in rel.parts):
            continue
        if path.is_file() or path.is_symlink():
            out.append(path)
    return out


def tree_hash(root: Path) -> str:
    """The content of a checkout, as one hash: what a local version is named by,
    so installing the same files twice is one version, and a changed file is another."""
    digest = hashlib.sha256()
    total = 0
    for path in _files(root):
        rel = path.relative_to(root).as_posix()
        data = path.read_bytes() if path.is_file() and not path.is_symlink() else b"<link>"
        total += len(data)
        if total > MAX_TREE_BYTES:
            raise RegistryRefusal(f"the plugin is larger than {MAX_TREE_BYTES // 2**20} MB")
        digest.update(rel.encode() + b"\0" + hashlib.sha256(data).digest())
    return digest.hexdigest()


def _result(report: dict, rid: str, status: str, why: list[str]) -> None:
    for r in report["results"]:
        if r["id"] == rid:
            r["status"] = status
            r["why"] = list(why)
    report["counts"] = {s: sum(x["status"] == s for x in report["results"])
                        for s in ("pass", "fail", "pending", "n/a")}


def _identity(session: Session, manifest) -> list[str]:
    """MIN-02: what only the registry can know about a plugin's names."""
    out = []
    if manifest.kind_key in CORE_KINDS:
        out.append(f"kind {manifest.kind_key!r} is a built-in mode")
    known = session.get(Plugin, manifest.id)
    if known is not None and known.kind_key != manifest.kind_key:
        out.append(f"plugin {manifest.id} creates kind {known.kind_key!r}; a version may not "
                   f"change it to {manifest.kind_key!r}, because projects on the old kind "
                   "would no longer have one")
    other = session.execute(select(Plugin).where(Plugin.kind_key == manifest.kind_key,
                                                 Plugin.id != manifest.id)).scalars().first()
    if other is not None:
        out.append(f"kind {manifest.kind_key!r} already belongs to plugin {other.id}")
    return out


def _start_check(spec: H.HostSpec) -> tuple[list[str], list[str]]:
    """(MIN-04 problems, MIN-05 problems) from starting the version for real."""
    try:
        host = H.Host(spec, H.launcher_for_mode())
    except H.HostFailure as exc:
        return [f"the plugin did not start in a sandboxed host: {exc.message}"], []
    try:
        reported = set(host.hooks)
    finally:
        host.kill()
    declared = {str(k) for k in api_hooks.expected(spec.manifest)}
    out = [f"the host reports @hook.{k}, which the manifest does not declare"
           for k in sorted(reported - declared)]
    out += [f"the manifest declares {k}, which the host does not report"
            for k in sorted(declared - reported)]
    return [], out


def install_local(session: Session, path: str | Path) -> Installed:
    """Install a plugin from a directory on this machine (development only)."""
    if not settings().allow_local_plugins:
        raise RegistryRefusal(
            "installing a plugin from a local path is off on this box: it is for "
            "development, where YAAF_ALLOW_LOCAL_PLUGINS=1 turns it on. In production a "
            "plugin is installed from its GitHub repo, pinned to a commit.", 403)
    source = Path(path).expanduser()
    if not source.is_absolute() or not source.is_dir():
        raise RegistryRefusal(f"{path} is not an absolute path to a directory")
    source = source.resolve()
    if source == plugins_root() or plugins_root() in source.parents:
        raise RegistryRefusal("install from a checkout, not from inside the plugins root")

    static = api_report.check(source, load_module=False)
    report = static.to_dict()
    manifest = None
    try:
        from yaaf_plugin_api.manifest import load_path

        manifest = load_path(source)
    except Exception:  # its problems are already in the static report
        manifest = None
    if manifest is None:
        raise RegistryRefusal("the plugin was not installed: "
                              + "; ".join(w for r in static.results for w in r.why)[:2000])
    version_label = f"local-{tree_hash(source)}"
    existing = session.execute(select(PluginVersion).where(
        PluginVersion.plugin_id == manifest.id,
        PluginVersion.version == version_label)).scalars().first()
    if existing is not None:
        return Installed(existing, False)

    identity = _identity(session, manifest)
    _result(report, "MIN-02", "fail" if identity else "pass", identity)
    failed = report["counts"]["fail"] > 0
    root = ""
    if not failed:
        dest = plugins_root() / manifest.id / version_label
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source, dest, ignore=shutil.ignore_patterns(*_SKIP))
        failed, root, _ = verify(session, dest, manifest, version_label, report, local=True)
    report["status"] = _status(report, failed)
    row = _record(session, manifest, identity, version_label, report, root, source="local",
                  ref=str(source), status=report["status"])
    return Installed(row, True)


def _status(report: dict, failed: bool) -> str:
    pending = any(r["status"] == "pending" for r in report["results"])
    return "failed" if failed else ("pending" if pending else "ready")


def _record(session: Session, manifest, identity: list[str], version_label: str, report: dict,
            root: str, *, source: str, ref: str, status: str, repo_url: str | None = None,
            build_log: str = "") -> PluginVersion:
    plugin = session.get(Plugin, manifest.id)
    if plugin is None and not identity:
        plugin = Plugin(id=manifest.id, name=manifest.name, kind_key=manifest.kind_key,
                        repo_url=repo_url)
        session.add(plugin)
        session.flush()
    elif plugin is None:
        # A version whose names collide is recorded under its own id only when
        # that id is free; otherwise the refusal is the record.
        raise RegistryRefusal("the plugin was not installed: " + "; ".join(identity), 409)
    row = PluginVersion(
        id=ids.new_id(ids.PLUGIN_VERSION), plugin_id=manifest.id, version=version_label,
        source=source, ref=ref, manifest=_manifest_record(manifest), api_version=API_VERSION,
        status=status, report=report, root=root, installed_by=acting.user_id(),
        build_log=build_log)
    session.add(row)
    _audit_install(session, row, manifest.id)
    session.flush()
    return row


def _audit_install(session: Session, row: PluginVersion, plugin_id: str) -> None:
    session.add(AuditLog(id=ids.new_id(ids.AUDIT), project_id=None, actor=acting.stamp(),
                         action="plugin.install", entity="plugin_version", entity_id=row.id,
                         detail={"plugin": plugin_id, "version": row.version,
                                 "source": row.source, "status": row.status}))


def verify(session: Session, dest: Path, manifest, version_label: str, report: dict, *,
           local: bool) -> tuple[bool, str, str]:
    """Every check an install runs on code already in place under the plugins
    root: build (GitHub) or the local no-dependency rule, the start check, the
    conformance suite with the plugin's own tests, and its roles. Returns
    (failed, root, build log). A failed version's code is removed."""
    from yaaf.plugins import build

    log = ""
    site = None
    if local:
        deps = _local_dependencies(dest)
        _result(report, "MIN-03", "fail" if deps else "pass", deps or [
            "a local install builds nothing: the plugin declares no dependencies"])
    else:
        problems = build.min_03(dest)
        if not problems:
            ok, log, site = build.build(dest)
            if not ok:
                problems = ["the dependency build failed (pip, --require-hashes):",
                            *log.strip().splitlines()[-8:]]
        _result(report, "MIN-03", "fail" if problems else "pass", problems or [
            "built from requirements.lock with --require-hashes" if site
            else "the plugin declares no dependencies"])
    if report["counts"]["fail"] == 0:
        spec = H.HostSpec.from_checkout(dest, version=version_label,
                                        site_packages=(site,) if site else ())
        start, hooks = _start_check(spec)
        _result(report, "MIN-04", "fail" if start else "pass", start)
        if not start:
            _result(report, "MIN-05", "fail" if hooks else "pass", hooks)
        if report["counts"]["fail"] == 0:
            for rid, (status, why) in conformance_results(
                    spec, run_tests=lambda: build.run_tests(dest, site)).items():
                _result(report, rid, status, why)
    roles = None
    if report["counts"]["fail"] == 0:
        roles = session.begin_nested()
        problems = _roles_and_crews(session, dest, manifest)
        _result(report, "MIN-07", "fail" if problems else "pass", problems)
    failed = report["counts"]["fail"] > 0
    if roles is not None:
        # The plugin's roles join the library only with a version that passed;
        # a failed one leaves the library as it found it.
        (roles.rollback if failed else roles.commit)()
    if failed:
        shutil.rmtree(dest, ignore_errors=True)
        return True, "", log
    return False, str(dest), log


# ── from GitHub ────────────────────────────────────────────────────────────────


def install_github(session: Session, url: str, ref: str | None = None, *,
                   wait: bool = False) -> Installed:
    """Install a public GitHub repo at one commit (§5.2). Resolving, fetching and
    the static checks happen now, so a bad URL, a missing repo or a broken
    manifest is refused at once. The build, the suite and the plugin's tests
    run in the background; the version reads `installing` until they finish."""
    from yaaf_plugin_api.manifest import load_path

    from yaaf.plugins import github

    try:
        source = github.parse(url, ref)
        sha = github.resolve(source)
    except github.FetchRefusal as exc:
        raise RegistryRefusal(exc.message, exc.status_code) from None
    staging = plugins_root() / ".staging" / ids.new_id("stg")
    try:
        github.fetch(source, sha, staging)
    except github.FetchRefusal as exc:
        raise RegistryRefusal(exc.message, exc.status_code) from None
    try:
        static = api_report.check(staging, load_module=False)
        try:
            manifest = load_path(staging)
        except Exception:
            raise RegistryRefusal("the plugin was not installed: " + "; ".join(
                w for r in static.results for w in r.why)[:2000]) from None
        existing = session.execute(select(PluginVersion).where(
            PluginVersion.plugin_id == manifest.id, PluginVersion.version == sha)
        ).scalars().first()
        if existing is not None:
            return Installed(existing, False)
        report = static.to_dict()
        identity = _identity(session, manifest)
        _result(report, "MIN-02", "fail" if identity else "pass", identity)
        failed = report["counts"]["fail"] > 0
        dest = plugins_root() / manifest.id / sha
        if not failed:
            if dest.exists():
                shutil.rmtree(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            staging.rename(dest)
        report["status"] = "failed" if failed else "installing"
        row = _record(session, manifest, identity, sha, report, "" if failed else str(dest),
                      source="github", ref=source.ref or "HEAD", status=report["status"],
                      repo_url=source.url)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    if row.status == "installing":
        session.commit()
        actor = acting.current()
        job = threading.Thread(target=_finish_github, args=(row.id, actor),
                               name=f"plugin-install-{manifest.id}", daemon=True)
        job.start()
        if wait:
            job.join()
            session.expire_all()
    return Installed(row, True)


def _finish_github(version_id: str, actor) -> None:
    """The slow half of a GitHub install, in its own session."""
    from yaaf.core.db import db_session

    with acting.acting(actor or acting.Actor("system", "plugin-install")), \
            db_session() as session:
        row = session.get(PluginVersion, version_id)
        if row is None or row.status != "installing":
            return
        report = dict(row.report)
        try:
            manifest = manifest_for(row)
            failed, root, log = verify(session, Path(row.root), manifest, row.version, report,
                                       local=False)
        except Exception as exc:  # the install's own failure, recorded on the version
            failed, root, log = True, "", f"the install stopped: {type(exc).__name__}: {exc}"
            shutil.rmtree(row.root, ignore_errors=True)
        report["status"] = _status(report, failed)
        row.report, row.status, row.root, row.build_log = report, report["status"], root, log
        _MANIFESTS.pop(row.id, None)
        _audit_install(session, row, row.plugin_id)


def recover_interrupted(session: Session) -> int:
    """At start: an install the last process was running cannot finish. It is
    marked failed with the reason, and its code removed."""
    rows = session.execute(select(PluginVersion).where(
        PluginVersion.status == "installing")).scalars().all()
    for row in rows:
        row.status = "failed"
        row.report = {**(row.report or {}), "status": "failed",
                      "interrupted": "the server stopped before this install finished; "
                                     "remove the version and install it again"}
        if row.root:
            shutil.rmtree(row.root, ignore_errors=True)
            row.root = ""
    return len(rows)


def conformance_results(spec: H.HostSpec, run_tests=None) -> dict:
    """The conformance suite (yaaf_plugin_api.conformance), asked through this
    box's sandboxed plugin host, the same suite an author runs locally."""
    from yaaf_plugin_api import conformance

    pool = H.HostPool(spec, H.launcher_for_mode(), size=1)

    def call(key, args, gateway=None):
        try:
            return pool.call(key, args, H.Callbacks(
                gateway_call=gateway.call if gateway is not None else None))
        except H.HostFailure as exc:
            raise conformance.Failure(exc.kind, exc.message, exc.violations) from None

    if run_tests is None:
        from yaaf.plugins import build

        def run_tests():
            return build.run_tests(spec.root, None)
    try:
        return conformance.run(spec.root, spec.manifest, call, run_tests=run_tests)
    finally:
        pool.close()


def _local_dependencies(root: Path) -> list[str]:
    """MIN-03 for a local install: it builds nothing, so it may need nothing."""
    import tomllib

    path = root / "pyproject.toml"
    if not path.is_file():
        return []
    try:
        deps = tomllib.loads(path.read_text("utf-8")).get("project", {}).get("dependencies")
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        return [f"pyproject.toml could not be read: {exc}"]
    others = [d for d in deps or [] if not str(d).startswith("yaaf-plugin-api")]
    return [f"a local install builds no dependencies, and this plugin declares "
            f"{', '.join(others)}: install it from GitHub, where they are built with hashes"
            ] if others else []


def _roles_and_crews(session: Session, root: Path, manifest) -> list[str]:
    """MIN-07: each role passes the library's own rules (the route a person adds
    a role through), and joins the library when absent. A role already in the
    library is left as it is. Its lead is a lead, and its crews are crews."""
    import json

    from fastapi import HTTPException
    from pydantic import ValidationError

    from yaaf.core.models import Role
    from yaaf.server.api.admin import CrewIn, RoleIn, create_role

    out: list[str] = []
    for path in sorted((root / "roles").glob("*.json")) if (root / "roles").is_dir() else []:
        try:
            body = RoleIn.model_validate(json.loads(path.read_text("utf-8")))
        except (ValueError, ValidationError) as exc:
            out.append(f"roles/{path.name} is not a role: {str(exc)[:300]}")
            continue
        if body.slug != path.stem:
            out.append(f"roles/{path.name} declares slug {body.slug!r}; name the file after it")
            continue
        if body.slug == manifest.lead and not body.is_ceo_role:
            out.append(f"kind.lead {body.slug} must be a lead (is_ceo_role: true)")
        if session.execute(select(Role).where(Role.slug == body.slug)).scalars().first():
            continue
        try:
            create_role(body, session)
        except HTTPException as exc:
            out.append(f"roles/{path.name}: {exc.detail}")
    for crew in manifest.crews:
        try:
            body = CrewIn.model_validate(json.loads((root / "crews" / f"{crew}.json")
                                                    .read_text("utf-8")))
        except (OSError, ValueError, ValidationError) as exc:
            out.append(f"crews/{crew}.json is not a crew: {str(exc)[:300]}")
            continue
        if manifest.lead and body.ceo_role_slug != manifest.lead:
            out.append(f"crew {crew} is led by {body.ceo_role_slug}, not the kind's lead "
                       f"{manifest.lead}")
    if manifest.lead and not (root / "roles" / f"{manifest.lead}.json").is_file() and \
            not session.execute(select(Role).where(Role.slug == manifest.lead)).scalars().first():
        out.append(f"kind.lead {manifest.lead} is neither in roles/ nor in the library")
    return out


def _manifest_record(m) -> dict:
    """The manifest as data for the screens and the kind list."""
    return {"id": m.id, "name": m.name, "version": m.version, "api": m.api, "module": m.module,
            "description": m.description, "kind": m.kind, "kind_key": m.kind_key,
            "kind_label": m.kind_label, "lead": m.lead, "crews": list(m.crews),
            "modes": [{"key": x.key, "shape": x.shape, "open": x.open, "label": x.label}
                      for x in m.modes]}


# ── what may be used ───────────────────────────────────────────────────────────


def selectable(version: PluginVersion) -> bool:
    plugin_on = version.status in SELECTABLE or (
        version.status == "pending" and settings().allow_unverified_plugins)
    return plugin_on and bool(version.root)


def offered(session: Session) -> list[PluginVersion]:
    """Per enabled plugin, the newest version a new project may be created on."""
    out = []
    for plugin in session.execute(select(Plugin).where(Plugin.enabled.is_(True))
                                  .order_by(Plugin.id)).scalars():
        versions = session.execute(select(PluginVersion).where(
            PluginVersion.plugin_id == plugin.id).order_by(
            PluginVersion.installed_at.desc())).scalars().all()
        chosen = next((v for v in versions if selectable(v)), None)
        if chosen is not None:
            out.append(chosen)
    return out


def projects_on(session: Session, version: PluginVersion) -> list[str]:
    return list(session.execute(select(Project.id).where(
        Project.plugin_version_id == version.id)).scalars())


def disable(session: Session, version: PluginVersion) -> None:
    version.status = "disabled"
    session.add(AuditLog(id=ids.new_id(ids.AUDIT), project_id=None, actor=acting.stamp(),
                         action="plugin.disable", entity="plugin_version",
                         entity_id=version.id, detail={"plugin": version.plugin_id,
                                                       "version": version.version}))
    close_pool(version)


def remove(session: Session, version: PluginVersion) -> None:
    on = projects_on(session, version)
    if on:
        raise RegistryRefusal(
            f"{len(on)} project(s) run on this version ({', '.join(on[:5])}): their rules are "
            "pinned to it, so it cannot be removed. Disable it to stop new projects choosing it.",
            409)
    close_pool(version)
    if version.root:
        shutil.rmtree(version.root, ignore_errors=True)
    session.add(AuditLog(id=ids.new_id(ids.AUDIT), project_id=None, actor=acting.stamp(),
                         action="plugin.remove", entity="plugin_version", entity_id=version.id,
                         detail={"plugin": version.plugin_id, "version": version.version}))
    plugin_id = version.plugin_id
    session.delete(version)
    session.flush()
    left = session.execute(select(func.count()).select_from(PluginVersion).where(
        PluginVersion.plugin_id == plugin_id)).scalar_one()
    if not left:
        plugin = session.get(Plugin, plugin_id)
        if plugin is not None:
            session.delete(plugin)


def move(session: Session, project: Project, target: PluginVersion) -> dict:
    """Move a plugin project onto another installed version of its plugin: how a
    fix reaches a project that is already running (§5.5). Only a version a new
    project could be created on, of the same plugin, keeping the project's mode
    and lead. Its state goes through the new version's migrate_state; a state
    the new version cannot take refuses the move. What was already accepted
    keeps its verdict; only new publications meet the new rules."""
    current = session.get(PluginVersion, project.plugin_version_id) \
        if project.plugin_version_id else None
    if current is None:
        raise RegistryRefusal("this project is not a plugin project, so it has no plugin "
                              "version to move", 409)
    if target.plugin_id != current.plugin_id:
        raise RegistryRefusal(f"{target.plugin_id} is a different plugin; a project moves "
                              f"only between versions of {current.plugin_id}", 422)
    if target.id == current.id:
        raise RegistryRefusal("the project already runs on this version", 409)
    if not selectable(target):
        raise RegistryRefusal(f"version {target.version[:12]} is {target.status}; a project "
                              "moves only to a version that passed every check", 409)
    old_m, new_m = manifest_for(current), manifest_for(target)
    mode = next((m for m in new_m.modes if m.key == project.kind_mode), None)
    if mode is None or not mode.open:
        raise RegistryRefusal(f"the new version has no open mode {project.kind_mode!r}, which "
                              "this project runs in", 422)
    if new_m.lead != old_m.lead:
        raise RegistryRefusal(f"the new version is led by {new_m.lead}, not {old_m.lead}; a "
                              "new lead means a new project", 422)
    state = dict(project.kind_state or {})
    own = {k: v for k, v in state.items() if k != "create"}
    if own and not new_m.state:
        raise RegistryRefusal("this project holds plugin state, and the new version keeps "
                              "none (no migrate_state), so the state would be lost", 422)
    if new_m.state and state:
        migrated, failure = H.ask_or_fail_closed(
            pool_for(target), api_hooks.HookKey("migrate_state"), [state, old_m.version])
        if failure is not None or not isinstance(migrated, dict):
            why = failure.kind if failure is not None else "it did not return a JSON object"
            raise RegistryRefusal(f"the new version's migrate_state could not carry this "
                                  f"project's state ({why}); the project stays where it is",
                                  409)
        # The New Project answers are core's record, not the plugin's state: a
        # migration that drops them does not lose them.
        if "create" in state:
            migrated = {**migrated, "create": migrated.get("create", state["create"])}
        state = dict(migrated)
    project.plugin_version_id = target.id
    project.kind_state = state or None
    detail = {"plugin": target.plugin_id, "from": current.version,
              "from_release": old_m.version, "to": target.version,
              "to_release": new_m.version}
    session.add(AuditLog(id=ids.new_id(ids.AUDIT), project_id=project.id,
                         actor=acting.stamp(), action="plugin.move", entity="project",
                         entity_id=project.id, detail=detail))
    session.flush()
    return detail


# ── running it ─────────────────────────────────────────────────────────────────


_POOLS: dict[str, H.HostPool] = {}
_POOLS_LOCK = threading.Lock()


_MANIFESTS: dict[str, object] = {}


def manifest_for(version: PluginVersion):
    """The full manifest of an installed version, read once from its code. The
    row's `manifest` is a summary for the screens; the capabilities a primitive
    enforces come from the file the code was installed with."""
    from yaaf_plugin_api.manifest import load_path

    cached = _MANIFESTS.get(version.id)
    if cached is None:
        cached = _MANIFESTS[version.id] = load_path(version.root)
    return cached


def spec_for(version: PluginVersion) -> H.HostSpec:
    if not version.root:
        raise H.HostFailure("start", f"version {version.version} of {version.plugin_id} has "
                                     "no installed code")
    from yaaf.plugins.build import SITE

    site = Path(version.root) / SITE
    return H.HostSpec.from_checkout(version.root, version=version.version,
                                    site_packages=(site,) if site.is_dir() else ())


def pool_for(version: PluginVersion) -> H.HostPool:
    """The warm hosts for one version, made on first use and kept."""
    with _POOLS_LOCK:
        pool = _POOLS.get(version.id)
        if pool is None:
            pool = _POOLS[version.id] = H.HostPool(spec_for(version))
        return pool


def close_pool(version: PluginVersion) -> None:
    with _POOLS_LOCK:
        pool = _POOLS.pop(version.id, None)
    if pool is not None:
        pool.close()


def close_all() -> None:
    with _POOLS_LOCK:
        pools = list(_POOLS.values())
        _POOLS.clear()
    for pool in pools:
        pool.close()


# Hosts exit on their own when their stdin closes, so this is tidiness, not
# safety: a server that stops kills its warm hosts instead of letting them
# notice.
atexit.register(close_all)
