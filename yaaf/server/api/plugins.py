"""Factory plugins, as the instance admin installs them and everyone sees them.

Installing is the instance admin's (docs/plans/factory-plugins.md §5.1): a plugin
is code the whole box will run, sandboxed or not, and its text reaches every
seat of every project of its kind. Reading what is installed, and with what
report, is anyone's who is signed in, so a chairman choosing a kind can read
why one is not offered.

A plugin is installed from a public GitHub URL, pinned to a commit
(`POST /admin/plugins`), or, for development behind YAAF_ALLOW_LOCAL_PLUGINS,
from a local path (`POST /admin/plugins/local`). Both land in the same rows
and pass the same checks.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import shape
from yaaf.core.models import Plugin, PluginVersion
from yaaf.plugins import registry
from yaaf.server import authority
from yaaf.server.deps import db
from yaaf.server.identity import signed_in

# Bound per request, as every /admin router does: the audit row of an install
# names the person who made it.
router = APIRouter(dependencies=[Depends(signed_in)])


class GithubInstall(BaseModel):
    url: str = Field(min_length=1, max_length=400)
    ref: str | None = Field(default=None, max_length=200)


class LocalInstall(BaseModel):
    path: str = Field(min_length=1, max_length=1000)


def _version_view(v: PluginVersion, *, full: bool = False) -> dict:
    out = {"id": v.id, "plugin": v.plugin_id, "version": v.version, "source": v.source,
           "status": v.status, "selectable": registry.selectable(v),
           "api_version": v.api_version, "installed_at": v.installed_at.isoformat(),
           "kind": (v.manifest or {}).get("kind"), "name": (v.manifest or {}).get("name"),
           "counts": (v.report or {}).get("counts", {})}
    if full:
        out |= {"manifest": v.manifest, "report": v.report, "ref": v.ref}
    return out


def _version(session: Session, plugin_id: str, version: str) -> PluginVersion:
    row = session.execute(select(PluginVersion).where(
        PluginVersion.plugin_id == plugin_id, PluginVersion.version == version)
    ).scalars().first()
    if row is None:
        raise HTTPException(404, f"no version {version} of plugin {plugin_id} is installed")
    return row


def _refusal(exc: registry.RegistryRefusal) -> HTTPException:
    return HTTPException(exc.status_code, exc.message)


@router.post("/plugins", dependencies=[authority.INSTANCE_ADMIN])
def install_github(body: GithubInstall, session: Session = Depends(db)) -> dict:
    """Install a public GitHub repo, pinned to the commit its ref names now. The
    answer comes once the commit is fetched and its files checked; the version
    reads `installing` while it is built and its conformance suite runs, then
    `ready` or `failed`, with the report (GET …/versions/{sha})."""
    try:
        installed = registry.install_github(session, body.url, body.ref)
    except registry.RegistryRefusal as exc:
        raise _refusal(exc) from None
    return {**_version_view(installed.version, full=True), "created": installed.created}


@router.post("/plugins/local", dependencies=[authority.INSTANCE_ADMIN])
def install_local(body: LocalInstall, session: Session = Depends(db)) -> dict:
    """Install a plugin checkout from this machine (development only). The answer
    carries the version's report: a failed one is kept so it can be read."""
    try:
        installed = registry.install_local(session, body.path)
    except registry.RegistryRefusal as exc:
        raise _refusal(exc) from None
    return {**_version_view(installed.version, full=True), "created": installed.created}


@router.get("/plugins", dependencies=[authority.SIGNED_IN])
def list_plugins(session: Session = Depends(db)) -> dict:
    out = []
    for plugin in session.execute(select(Plugin).order_by(Plugin.id)).scalars():
        versions = session.execute(select(PluginVersion).where(
            PluginVersion.plugin_id == plugin.id).order_by(
            PluginVersion.installed_at.desc())).scalars().all()
        out.append({"id": plugin.id, "name": plugin.name, "kind_key": plugin.kind_key,
                    "enabled": plugin.enabled, "repo_url": plugin.repo_url,
                    "versions": [_version_view(v) for v in versions]})
    return {"plugins": out}


@router.get("/plugins/{plugin_id}/versions/{version}", dependencies=[authority.SIGNED_IN])
def get_version(plugin_id: str, version: str, session: Session = Depends(db)) -> dict:
    return _version_view(_version(session, plugin_id, version), full=True)


@router.post("/plugins/{plugin_id}/versions/{version}/disable",
             dependencies=[authority.INSTANCE_ADMIN])
def disable_version(plugin_id: str, version: str, session: Session = Depends(db)) -> dict:
    row = _version(session, plugin_id, version)
    registry.disable(session, row)
    return _version_view(row)


@router.delete("/plugins/{plugin_id}/versions/{version}", dependencies=[authority.INSTANCE_ADMIN])
def remove_version(plugin_id: str, version: str, session: Session = Depends(db)) -> dict:
    row = _version(session, plugin_id, version)
    try:
        registry.remove(session, row)
    except registry.RegistryRefusal as exc:
        raise _refusal(exc) from None
    return {"removed": f"{plugin_id}@{version}"}


@router.get("/kinds", dependencies=[authority.SIGNED_IN])
def kinds(session: Session = Depends(db)) -> dict:
    """What a project may be created as: the built-in modes, and each plugin
    kind with a version that may be chosen. A kind not listed here is refused
    at creation (build step 5)."""
    built_in = [{"kind": str(m), "label": shape.label(str(m)), "built_in": True}
                for m in shape.MODES]
    plugins = []
    for v in registry.offered(session):
        m = v.manifest or {}
        fields = _create_fields(v)
        plugins.append({"kind": m.get("kind"), "label": m.get("kind_label"), "built_in": False,
                        "plugin": v.plugin_id, "version": v.version, "status": v.status,
                        "description": m.get("description"), "lead": m.get("lead"),
                        "crews": m.get("crews", []),
                        "modes": [x for x in m.get("modes", []) if x.get("open")],
                        "fields": fields})
    return {"kinds": built_in + plugins}


def _create_fields(version) -> list[dict]:
    """The kind's create fields, as its plugin declares them, for the form, the
    Founder and the MCP create tool. None offered when the plugin cannot be
    asked: creation itself then refuses, saying why."""
    from yaaf_plugin_api import hooks as api_hooks

    from yaaf.plugins import host as H

    manifest = registry.manifest_for(version)
    if not manifest.create_fields:
        return []
    specs, _ = H.ask_or_fail_closed(registry.pool_for(version),
                                    api_hooks.HookKey("create_fields"), [])
    return [{"key": f.key, "type": f.type, "label": f.label, "help": f.help,
             "required": f.required, "choices": list(f.choices), "default": f.default}
            for f in specs or []]
