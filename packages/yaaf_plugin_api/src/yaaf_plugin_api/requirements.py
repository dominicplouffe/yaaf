"""The conformance requirements as data: what 'built in the right shape' means.

The install, `yaaf plugin check`, the report screen and the MCP `plugin_spec`
tool all read this one table, so a requirement cannot be stated one way in the
docs and checked another way at install. docs/plans/factory-plugins.md §6 lists
the same ids, and a test holds the two together.

`checked_by` says which part of yaaf can decide the requirement:

* ``api``     — this package alone, from the checkout and the imported plugin
  (`repo.check_repo`, `hooks.check_plugin`, `hooks.check_return`);
* ``build``   — the sandboxed install build (dependencies, hashes);
* ``host``    — the plugin host, which meters time, memory, network and syscalls;
* ``harness`` — the SDK's harness, which drives a real yaaf with the plugin loaded;
* ``core``    — core's own attacks, replayed against a project of the plugin's kind.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from yaaf_plugin_api.manifest import Manifest

Level = Literal["minimum", "extended"]
CheckedBy = Literal["api", "build", "host", "harness", "core"]


@dataclass(frozen=True)
class Requirement:
    id: str
    level: Level
    #: The capability that makes an extended requirement apply; None for minimum ones.
    capability: str | None
    text: str
    checked_by: CheckedBy


REQUIREMENTS: tuple[Requirement, ...] = (
    Requirement("MIN-01", "minimum", None, "The manifest validates against the schema, and its "
                "api range includes this install's API", "api"),
    Requirement("MIN-02", "minimum", None, "The plugin id and kind key are well-formed and "
                "unique; no reserved name collides with core's or another plugin's", "core"),
    Requirement("MIN-03", "minimum", None, "Every dependency is pinned with a hash, and the "
                "build installs with --require-hashes", "build"),
    Requirement("MIN-04", "minimum", None, "The module imports in the host with no network and "
                "exposes one FactoryPlugin", "host"),
    Requirement("MIN-05", "minimum", None, "Every declared capability has its hook with the "
                "API's signature, and no hook exists that was not declared", "api"),
    Requirement("MIN-06", "minimum", None, "The kind declares at least one mode, at least one "
                "open mode, and each mode is exactly one of ships_once or runs_on_cycles", "api"),
    Requirement("MIN-07", "minimum", None, "Roles, crews and skills validate against core's "
                "schemas; a declared fixed lead exists and leads nothing else", "core"),
    Requirement("MIN-08", "minimum", None, "Every hook returns a schema-valid value on the "
                "plugin's fixtures", "harness"),
    Requirement("MIN-09", "minimum", None, "Every hook survives the fuzz corpus without "
                "raising", "harness"),
    Requirement("MIN-10", "minimum", None, "Hooks stay inside the time and memory limits on the "
                "fixtures and the corpus", "host"),
    Requirement("MIN-11", "minimum", None, "Hooks are deterministic: the same snapshot gives "
                "the same answer twice", "harness"),
    Requirement("MIN-12", "minimum", None, "Hooks make no network call, write only to scratch, "
                "and touch no secret", "host"),
    Requirement("MIN-13", "minimum", None, "Every gate has at least one accept and one refuse "
                "fixture, and gives the expected verdict on each", "harness"),
    Requirement("MIN-14", "minimum", None, "A plugin only tightens: every core refusal still "
                "fires on a project of this kind", "core"),
    Requirement("MIN-15", "minimum", None, "If kind_state is ever written, migrate_state exists "
                "and round-trips the fixtures' states", "harness"),
    Requirement("MIN-16", "minimum", None, "The plugin's own tests pass under the harness, with "
                "at least one attack test per declared gate", "harness"),
    Requirement("MIN-17", "minimum", None, "Every refusal names what to fix: non-empty, at most "
                "500 chars, no stack trace", "harness"),
    Requirement("EXT-ART-1", "extended", "artifacts", "Garbage, a wrong shape, another "
                "project's data and an oversized file are each refused, never accepted or "
                "crashed", "harness"),
    Requirement("EXT-ART-2", "extended", "artifacts", "An accepted artifact's stored bytes, "
                "re-submitted, are accepted again", "harness"),
    Requirement("EXT-PAGE-1", "extended", "platform_pages", "render returns only declared page "
                "names and allowed media types, deterministically; a seat publishing a page "
                "name is refused", "harness"),
    Requirement("EXT-PAGE-2", "extended", "platform_pages", "Draft and reviewed renders differ "
                "only in the draft mark", "harness"),
    Requirement("EXT-REV-1", "extended", "review", "Self-review, review of a stale hash, a "
                "missing load-bearing number and a number outside tolerance are each "
                "refused", "core"),
    Requirement("EXT-REV-2", "extended", "review", "A review that states its own reported "
                "value cannot move the comparison", "core"),
    Requirement("EXT-REV-3", "extended", "review", "Only a passing review makes the project "
                "completable, and a later return takes that back", "core"),
    Requirement("EXT-RCT-1", "extended", "receipts", "fields_of_read returns fields or unknown, "
                "never an empty list; a forged, foreign or field-less receipt backs no "
                "citation", "harness"),
    Requirement("EXT-ACT-1", "extended", "data_actions", "On the fake source the action "
                "completes and stores only its declared outputs", "harness"),
    Requirement("EXT-ACT-2", "extended", "data_actions", "An unreachable, signed-out, failing, "
                "slow or switched-off source never makes the action raise or the endpoint "
                "answer 500", "harness"),
    Requirement("EXT-ACT-3", "extended", "data_actions", "The action calls only the tools its "
                "manifest names", "core"),
    Requirement("EXT-GATE-1", "extended", "gates", "Owed items keep stable keys, and the gate "
                "clears on the fixture state that should clear it", "harness"),
    Requirement("EXT-BRF-1", "extended", "brief", "The brief is within the size cap, "
                "deterministic, and does not imitate core's headings", "harness"),
    Requirement("EXT-STP-1", "extended", "setup_steps", "Each step is done or not on its "
                "fixture states, with a non-empty text", "harness"),
    Requirement("EXT-VIEW-1", "extended", "view", "view.json validates against the view schema, "
                "and view(ctx) fills it on every fixture state", "harness"),
    Requirement("EXT-CRT-1", "extended", "create_fields", "The fields are valid; validate_create "
                "refuses the refuse fixtures, accepts the accept ones, and a closed mode is "
                "refused", "harness"),
    Requirement("EXT-CYC-1", "extended", "runs_on_cycles", "Its standing obligations are "
                "expressible in core's cycle model", "core"),
)

BY_ID = {r.id: r for r in REQUIREMENTS}


def declared_capabilities(manifest: Manifest) -> set[str]:
    """The extended suites this manifest opts into."""
    caps = set()
    for name in ("artifacts", "platform_pages", "review", "data_actions", "setup_steps",
                 "view", "brief", "create_fields"):
        if getattr(manifest, name):
            caps.add(name)
    if manifest.receipts:
        caps.add("receipts")
    if manifest.plan_gate or manifest.completion_gate:
        caps.add("gates")
    if any(m.shape == "runs_on_cycles" and m.open for m in manifest.modes):
        caps.add("runs_on_cycles")
    return caps


def applicable(manifest: Manifest) -> list[Requirement]:
    """Every requirement this plugin is held to: all the minimum ones, and the
    extended ones for each capability it declared. The rest are n/a in its report."""
    caps = declared_capabilities(manifest)
    return [r for r in REQUIREMENTS if r.level == "minimum" or r.capability in caps]
