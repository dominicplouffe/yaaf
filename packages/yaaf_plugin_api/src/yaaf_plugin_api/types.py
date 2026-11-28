"""What a hook is given and what it may return.

Every type here is a frozen dataclass of plain values, because each one crosses
a process boundary: core builds it, serialises it, and the plugin host rebuilds
it on the other side of a sandbox (docs/plans/factory-plugins.md §3). Nothing a
hook receives can reach back into core — no session, no token, no store handle.
The two ways to read more than the snapshot holds (`read_artifact`, `receipt`)
are callbacks core answers for *this* project only.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, Protocol

# ── what a hook is given ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class ArtifactMeta:
    """One published version of one artifact. The body is not here: a hook that
    needs it asks (`ProjectSnapshot.read_artifact`), so one that does not never
    pays for a 50 MB file."""

    name: str
    version: int
    sha256: str
    size_bytes: int
    published_by_seat: str | None
    run_id: str | None
    #: Written by the platform (a rendered page, a data action's output), not a seat.
    by_platform: bool = False


@dataclass(frozen=True)
class RequirementMeta:
    key: str
    title: str
    status: str


@dataclass(frozen=True)
class SeatMeta:
    """A seat of the project, by role. No token and no transcript: a gate decides
    on what was published, never on what a seat said about it."""

    id: str
    role: str
    team: str | None = None


@dataclass(frozen=True)
class OwnerAnswer:
    """Something the chairman answered — labelled as theirs wherever it is used."""

    question: str
    answer: str


@dataclass(frozen=True)
class ToolInfo:
    """A data-source tool this project may call, as the gateway recorded it."""

    source: str
    name: str
    description: str = ""
    kind: Literal["read", "write", "unknown"] = "read"


@dataclass(frozen=True)
class CoverageArea:
    key: str
    available: bool
    note: str = ""


@dataclass(frozen=True)
class Coverage:
    """What the connected data can support, from `classify_tools`. Core computes
    it from the tools the chairman allows; a seat cannot widen it."""

    capabilities: frozenset[str] = frozenset()
    areas: tuple[CoverageArea, ...] = ()


@dataclass(frozen=True)
class Receipt:
    """One recorded data-source read of this project (a core primitive).

    `fields is None` means the platform could not tell which fields the read
    returned. Such a receipt proves a read happened and backs no field citation —
    the fail-closed answer to "the text was not bare JSON"."""

    id: str
    source: str
    tool: str
    result_sha256: str
    fields: frozenset[str] | None


@dataclass(frozen=True)
class ProjectSnapshot:
    project_id: str
    kind: str
    mode: str
    #: The commit SHA this project is pinned to.
    plugin_version: str
    #: Core's date. Hooks never read the clock, so the same snapshot gives the
    #: same verdict tomorrow, on a re-install, and for a reviewer.
    today: date
    kind_state: Mapping[str, Any] = field(default_factory=dict)
    coverage: Coverage = field(default_factory=Coverage)
    artifacts: tuple[ArtifactMeta, ...] = ()
    requirements: tuple[RequirementMeta, ...] = ()
    seats: tuple[SeatMeta, ...] = ()
    owner_inputs: tuple[OwnerAnswer, ...] = ()
    lane: Literal["run", "chat", "page"] | None = None
    #: Callbacks, filled by the host. Absent in a snapshot built by hand, where
    #: they answer as if nothing were on file.
    _read_artifact: Callable[[str, int | None], bytes | None] | None = field(
        default=None, repr=False, compare=False)
    _receipt: Callable[[str], Receipt | None] | None = field(
        default=None, repr=False, compare=False)

    def latest(self, name: str) -> ArtifactMeta | None:
        versions = [a for a in self.artifacts if a.name == name]
        return max(versions, key=lambda a: a.version) if versions else None

    def read_artifact(self, name: str, version: int | None = None) -> bytes | None:
        """The body of this project's artifact (latest when no version), or None."""
        return self._read_artifact(name, version) if self._read_artifact else None

    def receipt(self, receipt_id: str) -> Receipt | None:
        """This project's receipt by id, or None — also for another project's."""
        return self._receipt(str(receipt_id)) if self._receipt else None


@dataclass(frozen=True)
class Upload:
    """An artifact a seat is publishing, before a byte is stored."""

    name: str
    data: bytes
    seat: str | None
    run_id: str | None


@dataclass(frozen=True)
class ArtifactBody:
    """An accepted artifact, with what core derived from it.

    `load_bearing` maps a claim id to the number the *subject* reported. The
    review primitive compares re-derivations against this and nothing else, so
    a review cannot choose its own yardstick."""

    meta: ArtifactMeta
    data: bytes
    load_bearing: Mapping[str, float | None] = field(default_factory=dict)


@dataclass(frozen=True)
class ReviewUpload(Upload):
    """A review being published, and the exact subject version it names.

    Core reads three fields of every review, and holds them before the plugin
    is asked (yaaf/plugins/primitives.py):

        {"subject_sha256": "<sha256 of the version reviewed>",
         "verdict": "pass" | "return",
         "rederived": [{"claim": "<id>", "value": <number>}      # a number it reported
                       | {"claim": "<id>", "matches": true}]}     # a reported non-number

    Each load-bearing claim of the subject is compared with what the subject
    reported, within the manifest's tolerance. A "reported" the review states
    itself is never read. Everything else in the file is the plugin's rubric,
    for its check_review. A fixture may write "$SUBJECT_SHA256" for the hash;
    the harness substitutes it."""

    subject_sha256: str = ""


@dataclass(frozen=True)
class CreateSpec:
    """A project being created as this kind: its mode and its kind fields."""

    mode: str
    fields: Mapping[str, Any] = field(default_factory=dict)
    title: str = ""
    goal: str = ""


ToolResult = Mapping[str, Any]


class ToolError(Exception):
    """A data-source call did not complete: unreachable, signed out, an error
    result, a timeout, a tool switched off. Core raises it into a data action
    for every one of those, so an action handles one exception and the endpoint
    never answers 500."""

    def __init__(self, message: str, *, state: str = "unreachable"):
        super().__init__(message)
        self.message = message
        self.state = state


class Gateway(Protocol):
    """A data action's only way to the world. Core allows only tools this
    project's chairman allows, records each call as a receipt, and turns every
    failure into ToolError."""

    def call(self, tool: str, arguments: Mapping[str, Any]) -> ToolResult: ...


# ── what a hook returns ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Accept:
    #: The bytes to store (a normalised form), or None to store what was uploaded.
    data: bytes | None = None
    #: A JSON merge patch on kind_state — the only way a plugin keeps state.
    state_patch: Mapping[str, Any] | None = None
    #: Shown to the publishing seat.
    note: str | None = None
    #: For a gated artifact with a declared review: claim id → reported number.
    #: Core stores it with the artifact and hands it back as ArtifactBody.load_bearing.
    load_bearing: Mapping[str, float | None] | None = None


@dataclass(frozen=True)
class Refuse:
    #: Every reason, in words the agent can act on. At least one.
    problems: tuple[str, ...]

    def __init__(self, problems):
        object.__setattr__(self, "problems",
                           tuple(problems) if not isinstance(problems, str) else (problems,))


@dataclass(frozen=True)
class Owed:
    """One thing a plan or completion gate is still waiting for. `key` is stable
    across calls, so a screen can tell 'still owed' from 'owed again'."""

    key: str
    text: str


@dataclass(frozen=True)
class Brief:
    text: str


@dataclass(frozen=True)
class Page:
    name: str
    media_type: str
    body: bytes


@dataclass(frozen=True)
class ReadFields:
    """The fields one data-source read returned. `fields is None` is 'could not
    tell' — say so with `ReadFields.unknown()` rather than an empty list, which
    the API refuses because it reads as 'returned nothing' and once let every
    field through."""

    fields: tuple[str, ...] | None

    @classmethod
    def unknown(cls) -> ReadFields:
        return cls(None)


@dataclass(frozen=True)
class ActionResult:
    #: What the agent is handed back: small, JSON-able.
    summary: Mapping[str, Any]
    #: Reserved output name → bytes. Only names the manifest declares for this action.
    artifacts: Mapping[str, bytes] = field(default_factory=dict)


@dataclass(frozen=True)
class StepState:
    done: bool
    text: str
    #: Where the chairman fixes it: a Settings section key, such as "sources".
    section: str = "sources"


FieldType = Literal["string", "text", "integer", "number", "boolean", "enum"]


@dataclass(frozen=True)
class FieldSpec:
    """One extra field on the New Project form, the REST body, the MCP tool and
    the Founder's create call — all read from the same declaration."""

    key: str
    type: FieldType
    label: str
    help: str = ""
    required: bool = False
    choices: tuple[str, ...] = ()
    default: Any = None


#: Section id → that section's data, in the shape its component takes (yaaf_plugin_api.view).
ViewData = Mapping[str, Any]
