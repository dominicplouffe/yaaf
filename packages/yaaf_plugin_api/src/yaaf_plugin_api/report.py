"""The static half of a conformance report: what the checkout and the imported
plugin already prove, before a host, a harness or core is involved.

Each requirement comes back as one of:

* ``pass``    — decided here, and met;
* ``fail``    — a problem was found here; the reason says what to change;
* ``pending`` — only the build, the host, the harness or core can decide it.
  Nothing found here is wrong with it yet;
* ``n/a``     — an extended requirement for a capability the plugin did not declare.

A plugin is ``ready`` only when every applicable requirement passes. This
report can never say ``ready`` on its own, because no plugin is ready before
its sandboxed build and harness have run. So `status` here is ``failed`` or
``pending`` (§5.2), and an author who sees ``pending`` with no failures has done
everything a checkout can show.

Importing the plugin runs its code in this process, unsandboxed. That is the
author's own code on the author's own machine. The install imports it in the
plugin host instead (MIN-04).
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from yaaf_plugin_api import hooks
from yaaf_plugin_api.repo import check_repo
from yaaf_plugin_api.requirements import REQUIREMENTS, applicable
from yaaf_plugin_api.version import API_VERSION

Status = Literal["pass", "fail", "pending", "n/a"]


@dataclass
class Result:
    id: str
    status: Status
    text: str
    checked_by: str
    why: list[str] = field(default_factory=list)


@dataclass
class Report:
    plugin: str | None
    version: str | None
    api: str
    status: Literal["failed", "pending"]
    results: list[Result]

    def failures(self) -> list[Result]:
        return [r for r in self.results if r.status == "fail"]

    def to_dict(self) -> dict:
        counts = {s: sum(r.status == s for r in self.results)
                  for s in ("pass", "fail", "pending", "n/a")}
        return {"plugin": self.plugin, "version": self.version, "api": self.api,
                "status": self.status, "counts": counts,
                "results": [asdict(r) for r in self.results]}


def import_plugin(root: Path, module_ref: str):
    """The object `module` names, imported from the checkout (src/ layout or flat)."""
    module_name, _, attr = module_ref.partition(":")
    paths = [str(root / "src"), str(root)]
    saved = list(sys.path)
    sys.path[:0] = paths
    try:
        module = importlib.import_module(module_name)
        return getattr(module, attr)
    finally:
        sys.path[:] = saved


def check(root: str | Path, *, load_module: bool = True) -> Report:
    root = Path(root)
    manifest, problems = check_repo(root)
    found: dict[str, list[str]] = {}
    for rid, text in problems:
        found.setdefault(rid, []).append(text)
    decided = {"MIN-01", "MIN-06"}
    if manifest is not None and load_module:
        try:
            plugin = import_plugin(root, manifest.module)
        except Exception as exc:  # the author's import error, reported as theirs
            found.setdefault("MIN-04", []).append(
                f"{manifest.module} could not be imported: {type(exc).__name__}: {exc}")
        else:
            found.setdefault("MIN-05", []).extend(hooks.check_plugin(plugin, manifest))
            decided.add("MIN-05")
    applies = {r.id for r in applicable(manifest)} if manifest else \
        {r.id for r in REQUIREMENTS if r.level == "minimum"}
    results = []
    for req in REQUIREMENTS:
        why = found.get(req.id, [])
        if req.id not in applies:
            status: Status = "n/a"
        elif why:
            status = "fail"
        elif req.id in decided and manifest is not None:
            status = "pass"
        else:
            status = "pending"
        results.append(Result(req.id, status, req.text, req.checked_by, why))
    if manifest is None:
        # No manifest: nothing past MIN-01 can be judged, so nothing claims to pass.
        for r in results:
            if r.status == "pass":
                r.status = "pending"
    return Report(manifest.id if manifest else None, manifest.version if manifest else None,
                  API_VERSION, "failed" if any(r.status == "fail" for r in results)
                  else "pending", results)
