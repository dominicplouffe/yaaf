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
        root = str(dest)
        start, hooks = _start_check(H.HostSpec.from_checkout(dest, version=version_label))
        _result(report, "MIN-04", "fail" if start else "pass", start)
        if not start:
            _result(report, "MIN-05", "fail" if hooks else "pass", hooks)
        failed = report["counts"]["fail"] > 0
        if failed:
            shutil.rmtree(dest, ignore_errors=True)
            root = ""
    report["status"] = "failed" if failed else "pending"

    plugin = session.get(Plugin, manifest.id)
    if plugin is None and not identity:
        plugin = Plugin(id=manifest.id, name=manifest.name, kind_key=manifest.kind_key)
        session.add(plugin)
        session.flush()
    elif plugin is None:
        # A version whose names collide is recorded under its own id only when
        # that id is free; otherwise the refusal is the record.
        raise RegistryRefusal("the plugin was not installed: " + "; ".join(identity), 409)
    row = PluginVersion(
        id=ids.new_id(ids.PLUGIN_VERSION), plugin_id=manifest.id, version=version_label,
        source="local", ref=str(source), manifest=_manifest_record(manifest),
        api_version=API_VERSION, status=report["status"], report=report, root=root,
        installed_by=acting.user_id())
    session.add(row)
    session.add(AuditLog(id=ids.new_id(ids.AUDIT), project_id=None, actor=acting.stamp(),
                         action="plugin.install", entity="plugin_version", entity_id=row.id,
                         detail={"plugin": manifest.id, "version": version_label,
                                 "source": "local", "status": row.status}))
    session.flush()
    return Installed(row, True)


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


# ── running it ─────────────────────────────────────────────────────────────────


_POOLS: dict[str, H.HostPool] = {}
_POOLS_LOCK = threading.Lock()


def spec_for(version: PluginVersion) -> H.HostSpec:
    if not version.root:
        raise H.HostFailure("start", f"version {version.version} of {version.plugin_id} has "
                                     "no installed code")
    return H.HostSpec.from_checkout(version.root, version=version.version)


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
