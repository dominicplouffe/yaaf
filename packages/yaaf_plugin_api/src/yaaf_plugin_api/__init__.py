"""The contract between yaaf and a factory plugin (docs/plans/factory-plugins.md).

A plugin is a package that exposes one `FactoryPlugin` and a `yaaf-plugin.toml`
declaring what it does. Its hooks are pure: each takes a read-only snapshot and
returns a verdict, text or bytes. Core does every side effect, and core alone
enforces anything, so a plugin can tighten a project's rules but never loosen one.

    from yaaf_plugin_api import Accept, FactoryPlugin, Refuse, hook

    class Grants(FactoryPlugin):
        @hook.check_artifact("application.json")
        def check(self, ctx, upload):
            return Accept() if upload.data.startswith(b"{") else Refuse(["not JSON"])

    plugin = Grants()

This package has no dependencies, and that is part of the contract: it is
imported into every plugin's sandbox before the plugin is.
"""

from yaaf_plugin_api.hooks import SPECS, FactoryPlugin, HookKey, hook
from yaaf_plugin_api.manifest import Manifest, ManifestProblem
from yaaf_plugin_api.types import (
    Accept,
    ActionResult,
    ArtifactBody,
    ArtifactMeta,
    Brief,
    Coverage,
    CoverageArea,
    CreateSpec,
    FieldSpec,
    Gateway,
    Owed,
    OwnerAnswer,
    Page,
    ProjectSnapshot,
    ReadFields,
    Receipt,
    Refuse,
    RequirementMeta,
    ReviewUpload,
    SeatMeta,
    StepState,
    ToolError,
    ToolInfo,
    ToolResult,
    Upload,
    ViewData,
)
from yaaf_plugin_api.version import API_VERSION, compatible

__all__ = [
    "API_VERSION", "SPECS", "Accept", "ActionResult", "ArtifactBody", "ArtifactMeta", "Brief",
    "Coverage", "CoverageArea", "CreateSpec", "FactoryPlugin", "FieldSpec", "Gateway",
    "HookKey", "Manifest", "ManifestProblem", "OwnerAnswer", "Owed", "Page", "ProjectSnapshot",
    "ReadFields", "Receipt", "Refuse", "RequirementMeta", "ReviewUpload", "SeatMeta",
    "StepState", "ToolError", "ToolInfo", "ToolResult", "Upload", "ViewData", "compatible",
    "hook",
]
