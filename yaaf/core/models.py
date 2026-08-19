"""The yaaf domain model.

Every rule that matters is enforced against these tables by the policy layer —
never by prompt text. Conventions:

- ids are prefixed strings from :mod:`yaaf.core.ids`
- enums are plain strings validated by :class:`enum.StrEnum` values at the API
  layer (no native PG enums; they make migrations miserable)
- timestamps are timezone-aware UTC
- JSON columns hold structured-but-flexible payloads (success tests, triggers,
  constitutions); anything the policy layer must query efficiently gets a column
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JSONVariant = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetimes on every dialect. SQLite (unit tests)
    round-trips naive values; this pins them back to UTC so aware/naive
    comparisons can never blow up in policy code."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value


class Base(DeclarativeBase):
    pass


# ── enums (string-valued) ───────────────────────────────────────────────────────


class ProjectStatus(enum.StrEnum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"        # kill switch or budget trip; resumable
    STOPPED = "stopped"      # terminal: abandoned
    COMPLETED = "completed"  # terminal: the work is finished and the platform proved it

    @property
    def is_terminal(self) -> bool:
        return self in (ProjectStatus.STOPPED, ProjectStatus.COMPLETED)


class AgentKind(enum.StrEnum):
    CEO = "ceo"
    EMPLOYEE = "employee"  # a team lead is a *derived* designation, never stored here


class AgentStatus(enum.StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"


class ChannelKind(enum.StrEnum):
    BOARDROOM = "boardroom"    # chairman <-> CEO only
    LEADERSHIP = "leadership"  # CEO + team leads
    TEAM = "team"              # a team's lead + employees


class TaskStatus(enum.StrEnum):
    READY = "ready"
    CLAIMED = "claimed"
    CHECKING = "checking"   # platform is executing a command-type success test
    BLOCKED = "blocked"
    DONE = "done"
    KILLED = "killed"


class SuccessTestKind(enum.StrEnum):
    COMMAND = "command"        # platform-executed in the task's sandbox
    OBSERVABLE = "observable"  # names a checkable artifact/URL/metric; spot-audited
    # Only ever on R-00, the planning requirement, and only ever written by the
    # platform: the test is a structural check of the plan itself — is there
    # scope, is it staged, is it estimated, does it fit the money — evaluated
    # in the server rather than in a sandbox. An agent proposing this kind is
    # refused, because a company that can author its own planning gate does not
    # have one.
    PLAN = "plan"


class ProjectMode(enum.StrEnum):
    """What shape this company takes, chosen once at creation.

    EXPLORE is the open-ended arm — a goal, bets, evidence, and the chairman's
    judgement on when it has earned its keep. DELIVER ships one thing and then
    finishes. OPERATE is a DELIVER that starts again on a rhythm.

    The field exists from the first stage of the rebuild because the rules
    differ by mode: outside EXPLORE a task must serve a requirement. What later
    stages add is the machinery — cycles, the picker, the completion receipt —
    not the distinction.
    """

    EXPLORE = "explore"
    DELIVER = "deliver"
    OPERATE = "operate"


class RequirementStatus(enum.StrEnum):
    """A requirement is a thing that must be true at the end.

    PROPOSED is the CEO's suggestion and counts as nothing: it is not scope
    until the chairman accepts it, which is what keeps the denominator of the
    forecast out of the hands of the party the forecast grades. MET is written
    only by the platform, after it has run the acceptance test itself.
    """

    PROPOSED = "proposed"        # the CEO's; not scope yet
    PENDING = "pending"          # accepted, no work started
    IN_PROGRESS = "in_progress"  # has live tasks
    CHECKING = "checking"        # platform is running its acceptance test
    MET = "met"                  # the platform ran the test and it passed
    CUT = "cut"                  # the chairman removed it from scope


class CommitmentStatus(enum.StrEnum):
    """A delivery date, and what the promise is currently worth.

    PROPOSED is the CEO asking to commit; it binds nothing. ACCEPTED is the
    chairman's act, and from that moment the platform recomputes the projection
    on every janitor tick and moves the status itself. There is no agent verb
    behind any of these transitions — the strongest form of "the CEO never
    states a date" is that the date is computed and the walls move by
    themselves.
    """

    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    AT_RISK = "at_risk"    # the projection lands past deliver_by
    BREACHED = "breached"  # deliver_by passed with scope still open
    MET = "met"            # every requirement in scope settled true


class EvidenceProvenance(enum.StrEnum):
    AGENT_CLAIMED = "agent_claimed"        # unverified until the platform checks it
    PLATFORM_ATTESTED = "platform_attested"  # arrived via a connector agents can't forge


class EvidenceKind(enum.StrEnum):
    EXTERNAL = "external"
    INTERNAL = "internal"


class VerificationStatus(enum.StrEnum):
    PENDING = "pending"
    VERIFIED = "verified"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"  # internal evidence; attested evidence


class AuditSampleStatus(enum.StrEnum):
    """One observable close, drawn (or passed over) by the janitor.

    PENDING waits for the chairman; PASSED/FAILED are his verdict; EXPIRED is
    "not reviewed in time", recorded and counted in review coverage but never
    blocking anything — sampling is a thermometer, not a gate. SKIPPED closes
    are recorded too, so the draw is never re-rolled and the coverage line has
    a denominator.
    """

    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    EXPIRED = "expired"
    SKIPPED = "skipped"


class HypothesisStatus(enum.StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"    # kill date arrived; auto-set by the janitor
    KILLED = "killed"


class RunStatus(enum.StrEnum):
    QUEUED = "queued"          # in run_queue only
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    REAPED = "reaped"          # lease expired; janitor recovered it
    TERMINATED_REFUSALS = "terminated_refusals"  # hit the per-run refusal cap
    KILLED = "killed"          # stop/pause honored mid-run


class RunKind(enum.StrEnum):
    AGENT_RUN = "agent_run"
    SUCCESS_CHECK = "success_check"  # platform executes a command-type success test
    # The same thing one level up: the acceptance test on a requirement, run by
    # the platform when the requirement's last task closes. Separate from
    # SUCCESS_CHECK because it resolves a different row and finalizes
    # differently — a failed requirement check reopens the requirement rather
    # than handing a task back to a claimant.
    REQUIREMENT_CHECK = "requirement_check"
    # The talk lane: a resident CEO conversation session, supervised by the
    # runner outside the run queue. Exempt from wake/concurrency/daily caps —
    # chat is not work — but it authenticates, meters, heartbeats, and revokes
    # exactly like a run.
    CHAT_SESSION = "chat_session"


class ApprovalKind(enum.StrEnum):
    SPEND = "spend"
    LEGAL = "legal"
    OWNER_DECISION = "owner_decision"
    TOOL_REQUEST = "tool_request"


class ApprovalStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"  # timed out; constitution default decision is DENY


class MemoryScope(enum.StrEnum):
    PROJECT = "project"  # curated by the CEO
    TEAM = "team"        # curated by the team lead


class SideEffectStatus(enum.StrEnum):
    RECORDED = "recorded"    # written BEFORE the effect is attempted
    COMPLETED = "completed"
    FAILED = "failed"


class NoticeKind(enum.StrEnum):
    DROUGHT = "drought"
    HYPOTHESIS_EXPIRED = "hypothesis_expired"
    BUDGET_WARNING = "budget_warning"
    BUDGET_EXHAUSTED = "budget_exhausted"
    APPROVAL_DECIDED = "approval_decided"
    APPROVAL_EXPIRED = "approval_expired"
    THREAD_FROZEN = "thread_frozen"
    CONSTITUTION_AMENDED = "constitution_amended"
    GOAL_CHANGED = "goal_changed"
    TASK_CHECK_FAILED = "task_check_failed"
    TASK_BLOCKED = "task_blocked"
    AGENT_RUN_REAPED = "agent_run_reaped"
    AUDIT_FAILED = "audit_failed"  # chairman spot-audit failed an evidence row or completion
    # Memory pressure warns the scope's curator early enough to curate on a wake
    # it was having anyway; MEMORY_FULL is urgent because the scope has stopped
    # accepting writes and only the curator can restart it.
    MEMORY_PRESSURE = "memory_pressure"
    MEMORY_FULL = "memory_full"
    # The commitment's transitions and re-validations, each disclosed to the
    # CEO exactly once. AT_RISK and BREACHED also freeze new scope — the
    # notice is the explanation, the refusal in policy is the enforcement.
    COMMITMENT_ACCEPTED = "commitment_accepted"
    COMMITMENT_AT_RISK = "commitment_at_risk"
    COMMITMENT_BREACHED = "commitment_breached"
    COMMITMENT_RECOVERED = "commitment_recovered"
    COMMITMENT_MET = "commitment_met"
    COMMITMENT_REVALIDATED = "commitment_revalidated"


# ── people & projects ───────────────────────────────────────────────────────────


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    goal: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default=ProjectStatus.DRAFT)
    status_reason: Mapped[str | None] = mapped_column(Text)
    # What shape this company takes. Explore is the default and today's
    # behaviour; outside it, a task must serve a requirement.
    mode: Mapped[str] = mapped_column(String(16), default=ProjectMode.EXPLORE,
                                      server_default=ProjectMode.EXPLORE)
    chairman_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    # "api_key" | "subscription" — decides the budget's native unit (usd vs tokens)
    billing_mode: Mapped[str] = mapped_column(String(16), default="api_key")
    billing_secret_id: Mapped[str | None] = mapped_column(String(40))
    # Domains/accounts the project controls; evidence from these can never be external.
    controlled_domains: Mapped[list] = mapped_column(JSONVariant, default=list)
    github_repo: Mapped[str | None] = mapped_column(String(255))
    # Wiring, not rules. Runtime backend, MCP servers, Slack mapping, webhook
    # secrets — things that say *how to reach the outside*, never how much an
    # agent may spend or how often it may wake. Every limit that used to live
    # here (heartbeat cadence, concurrency, the daily wake time) moved into
    # ConstitutionDoc in the rebuild: a limit kept here generated no settings
    # UI, recorded no amendment, and was never disclosed to the CEO it bound.
    integrations: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String(16))  # "chairman" | "observer"


# ── constitution (frozen policy) ────────────────────────────────────────────────


class Constitution(Base):
    """The policy document, versioned. Version 1 is written at project start;
    later versions only ever appear via a recorded chairman amendment. Append-
    only, so the rulebook has a history rather than a current value. The
    *effective* constitution is the highest version with effective_at <= now."""

    __tablename__ = "constitutions"
    __table_args__ = (UniqueConstraint("project_id", "version"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    version: Mapped[int] = mapped_column(Integer)
    body: Mapped[dict] = mapped_column(JSONVariant)
    effective_at: Mapped[datetime] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Amendment(Base):
    __tablename__ = "amendments"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    proposed_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(Text)
    changes: Mapped[dict] = mapped_column(JSONVariant)  # partial constitution overlay
    effective_at: Mapped[datetime] = mapped_column(UTCDateTime)  # always now
    applied_constitution_id: Mapped[str | None] = mapped_column(String(40))
    # Amendments now take effect when saved, so nothing is ever pending. Kept
    # for rows written while the cooling-off existed, which can still be
    # cancelled before their old effective_at passes.
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Set once the CEO has seen it in a context pack — disclosure is mandatory.
    disclosed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ── role library ────────────────────────────────────────────────────────────────


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    is_ceo_role: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class RoleVersion(Base):
    __tablename__ = "role_versions"
    __table_args__ = (UniqueConstraint("role_id", "version"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id"))
    version: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(Text)
    system_prompt: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(64), default="claude-sonnet-5")
    effort: Mapped[str] = mapped_column(String(8), default="high")
    # {"bash": true, "web": true, "github": false, "egress_allow": ["api.example.com"]}
    tool_permissions: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    secret_scopes: Mapped[list] = mapped_column(JSONVariant, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ── org: teams & agents ─────────────────────────────────────────────────────────


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    role_version_id: Mapped[str] = mapped_column(ForeignKey("role_versions.id"))
    name: Mapped[str] = mapped_column(String(120))
    # Explicit lead for multi-employee teams; a sole employee is the de-facto
    # lead — resolve with policy.effective_lead(), never read this raw.
    lead_agent_id: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"))  # null => CEO
    kind: Mapped[str] = mapped_column(String(16))  # AgentKind
    name: Mapped[str] = mapped_column(String(120))
    # Pinned role for the CEO; employees inherit their team's pinned role version.
    role_version_id: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(16), default=AgentStatus.ACTIVE)
    model_override: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ── comms: channels & messages ──────────────────────────────────────────────────


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    kind: Mapped[str] = mapped_column(String(16))  # ChannelKind
    team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"))
    slack_channel_id: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_channel_created", "channel_id", "created_at"),
        Index("ix_messages_thread", "thread_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id"))
    # Exactly one of sender_agent_id / sender_user_id is set. System notices use
    # the notices table, not messages.
    sender_agent_id: Mapped[str | None] = mapped_column(String(40))
    sender_user_id: Mapped[str | None] = mapped_column(String(40))
    thread_id: Mapped[str | None] = mapped_column(String(40))  # root message id
    body: Mapped[str] = mapped_column(Text)
    # Set on a thread ROOT when the pair circuit-breaker trips; posts refused.
    frozen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    slack_ts: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ChannelRead(Base):
    """Per-agent read cursor; the dispatcher's cheap-exit check is a SQL
    comparison against this, not a model invocation."""

    __tablename__ = "channel_reads"
    __table_args__ = (UniqueConstraint("agent_id", "channel_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id"))
    last_read_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ── scope: requirements ─────────────────────────────────────────────────────────


class Requirement(Base):
    """A thing that must be true at the end.

    The root object of the rebuild. Before it, the board was the spine and the
    CEO's standing instruction was "keep the queue fed but small" — a pipeline
    with no terminus, so there was nothing to divide by velocity and nothing to
    cut when a date slipped. A requirement gives the work a denominator, an
    owner who is not the party being graded, and a test the platform runs.

    Three properties are load-bearing and each is enforced elsewhere as a
    refusal or an absent verb:

    * The CEO **proposes**; only the chairman **accepts**. `accepted_by_user_id`
      is set on a chairman-only route, so scope cannot be self-served.
    * Nothing marks a requirement `met` except the platform. There is no agent
      tool that writes it — the strongest form of "done is never self-graded"
      is that the verb does not exist.
    * `est_runs` is the CEO's estimate and is *scored*, not trusted: stage 2
      compares it against `actual_runs` and multiplies future estimates by the
      ratio, so writing smaller numbers buys nothing.
    """

    __tablename__ = "requirements"
    __table_args__ = (
        UniqueConstraint("project_id", "key"),
        Index("ix_requirements_project_status", "project_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    # Human-readable and stable: the chairman says "cut R-09", not an opaque id.
    key: Mapped[str] = mapped_column(String(16))
    statement: Mapped[str] = mapped_column(Text)
    # Same shape as a task's: {"type": "command", ...} | {"type": "observable", ...}
    acceptance_test: Mapped[dict] = mapped_column(JSONVariant)
    status: Mapped[str] = mapped_column(String(16), default=RequirementStatus.PROPOSED)
    # The waterfall axis. Ordered, chairman-owned, and gated: work may only be
    # created for requirements in the open stage or earlier.
    stage: Mapped[int] = mapped_column(Integer, default=1)
    est_runs: Mapped[int | None] = mapped_column(Integer)
    actual_runs: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Runs that died and checks that failed against this requirement. The
    # chairman asked the CEO to be adaptable to "plan changes, parameter
    # changes, errors"; this is the errors half, and it is what lets a review
    # say the rate fell because one requirement ate fifty runs.
    failure_load: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    proposed_by_agent_id: Mapped[str | None] = mapped_column(String(40))
    accepted_by_user_id: Mapped[str | None] = mapped_column(String(40))
    accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    met_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Which platform-run check passed. Null met_at with a set met_by is
    # impossible by construction; the pair is the receipt.
    met_by_run_id: Mapped[str | None] = mapped_column(String(40))
    check_output: Mapped[str | None] = mapped_column(Text)
    cut_reason: Mapped[str | None] = mapped_column(Text)
    cut_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class RequirementEvent(Base):
    """Every requirement transition. The mirror of TaskEvent, and deliberately
    so: velocity is measured on requirements because the forecast divides by
    requirements, and a metric needs its own log rather than an inference from
    somebody else's."""

    __tablename__ = "requirement_events"
    __table_args__ = (Index("ix_requirement_events_project_at", "project_id", "at"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    requirement_id: Mapped[str] = mapped_column(ForeignKey("requirements.id"))
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str] = mapped_column(String(16))
    actor: Mapped[str] = mapped_column(String(64))  # agent id, user id, "janitor", "checker"
    note: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class RequirementEdge(Base):
    """`from_requirement_id` must be met before `to_requirement_id` may be
    worked. The graph the chairman asked to look at, and the one a previous
    design declined to draw on the correct grounds that nothing had a
    `depends_on`. Now something does.

    Kept acyclic by a check on insert rather than by hope: a cycle would make
    the critical path infinite and the stage gate unsatisfiable, and the
    refusal names the loop it found.
    """

    __tablename__ = "requirement_edges"
    __table_args__ = (
        UniqueConstraint("from_requirement_id", "to_requirement_id"),
        Index("ix_requirement_edges_to", "to_requirement_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    from_requirement_id: Mapped[str] = mapped_column(ForeignKey("requirements.id"))
    to_requirement_id: Mapped[str] = mapped_column(ForeignKey("requirements.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Commitment(Base):
    """The delivery date, as a row the platform owns.

    The chairman's ask: "it's impossible to get a delivery date or anything of
    substance that can offer me some sort of guarantee." The guarantee is not
    that the date holds — nobody can promise that. It is that when the date
    stops holding the company cannot quietly add work: new scope is refused,
    the chairman is emailed, and what happens next is his decision, made with
    the projection in front of him.

    The CEO proposes a commitment but never writes the date — `deliver_by` is
    stamped by the platform from its own projection at proposal time, and the
    chairman may replace it with his own date when he accepts. Two freezes at
    acceptance make later drift *detectable*: `constitution_version` records
    the rules the promise was made under, `scope_snapshot` the requirement
    keys it covered. The janitor re-validates when either moves, or when
    failure load crosses its constitution threshold — state-based diffs
    against `projection`, never event hooks, because amendments apply inline
    from three different callers and a hook misses every one it was not wired
    to.
    """

    __tablename__ = "commitments"
    __table_args__ = (Index("ix_commitments_project_status", "project_id", "status"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    status: Mapped[str] = mapped_column(String(16), default=CommitmentStatus.PROPOSED)
    deliver_by: Mapped[datetime] = mapped_column(UTCDateTime)
    proposed_by_agent_id: Mapped[str | None] = mapped_column(String(40))
    # The CEO's words with the proposal — why it believes the date.
    note: Mapped[str | None] = mapped_column(Text)
    accepted_by_user_id: Mapped[str | None] = mapped_column(String(40))
    accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Frozen at acceptance and never updated: the rulebook the promise was
    # made under. The current version lives in `projection`, where the
    # janitor's trigger diff can see it move.
    constitution_version: Mapped[int | None] = mapped_column(Integer)
    # The requirement keys in force (accepted, not cut) at acceptance.
    scope_snapshot: Mapped[list] = mapped_column(JSONVariant, default=list)
    # Platform-computed, janitor-refreshed. No route writes this from input.
    projected_done_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # What the janitor last read to project: constitution version, scope keys,
    # failure load, method and basis sentence. The three re-validation
    # triggers are diffs against this.
    projection: Mapped[dict | None] = mapped_column(JSONVariant)
    status_reason: Mapped[str | None] = mapped_column(Text)
    met_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


# ── work: tasks ─────────────────────────────────────────────────────────────────


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_project_status", "project_id", "status"),
        Index("ix_tasks_team_status", "team_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))
    # Top-level tasks are created by the CEO with assignee null; a lead
    # decomposes into subtasks (parent_task_id set) assigned to employees.
    parent_task_id: Mapped[str | None] = mapped_column(String(40))
    assignee_agent_id: Mapped[str | None] = mapped_column(String(40))
    created_by_agent_id: Mapped[str] = mapped_column(String(40))
    hypothesis_id: Mapped[str | None] = mapped_column(String(40))
    # What this work is FOR. Nullable in the column because an Explore
    # project has no requirements; required by policy everywhere else, where
    # a task serving nothing is a refusal rather than a smell.
    requirement_id: Mapped[str | None] = mapped_column(ForeignKey("requirements.id"))
    title: Mapped[str] = mapped_column(String(300))
    goal: Mapped[str] = mapped_column(Text)
    # {"type": "command", "command": "..."} | {"type": "observable", "target": "..."}
    success_test: Mapped[dict] = mapped_column(JSONVariant)
    produces_external_evidence: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default=TaskStatus.READY)
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    not_before: Mapped[datetime | None] = mapped_column(UTCDateTime)
    claimed_by_agent_id: Mapped[str | None] = mapped_column(String(40))
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    claim_run_id: Mapped[str | None] = mapped_column(String(40))
    completion_evidence: Mapped[str | None] = mapped_column(Text)
    check_output: Mapped[str | None] = mapped_column(Text)  # platform-run success test output
    # Consecutive failed success tests. Deliberately NOT reset by a claim, a
    # janitor release or a re-claim: the janitor launders a stuck task back
    # through READY, so a counter that lived on the claim would be zeroed by
    # the very loop it exists to catch. Only a passing check clears it.
    check_failures: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Last few failed attempts: [{"at", "evidence", "output"}]. Without this an
    # agent sees only the newest failure and cannot tell it is repeating itself
    # — one claimant oscillated between two rejected fixes for 22 hours.
    check_history: Mapped[list | None] = mapped_column(JSONVariant)
    # Chairman spot-audit verdict on the completion ("passed"/"failed"); null = unaudited.
    audit_status: Mapped[str | None] = mapped_column(String(16))
    audit_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow
    )


class TaskEvent(Base):
    """Every status transition, for audit and for the wake-loop circuit breaker
    (a thread with N ping-pong messages and no task transition gets frozen)."""

    __tablename__ = "task_events"
    __table_args__ = (Index("ix_task_events_project_at", "project_id", "at"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str] = mapped_column(String(16))
    actor: Mapped[str] = mapped_column(String(64))  # agent id, user id, or "janitor"/"checker"
    note: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Suggestion(Base):
    """The whole of a non-CEO agent's authority over the backlog: a note the
    CEO reads next planning cycle. Creates no work."""

    __tablename__ = "suggestions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    by_agent_id: Mapped[str] = mapped_column(String(40))
    text: Mapped[str] = mapped_column(Text)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Which run ate it — so a run that dies after building its pack gives it
    # back, exactly as notices are given back. Without this a failed CEO run
    # destroyed every suggestion it was handed, permanently: an employee's
    # only channel to the backlog, lost to a crash it had no part in.
    consumed_by_run_id: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ── goals: hypotheses, evidence, decisions ──────────────────────────────────────


class Hypothesis(Base):
    __tablename__ = "hypotheses"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    statement: Mapped[str] = mapped_column(Text)
    kill_condition: Mapped[str] = mapped_column(Text)  # falsifiable, written before work starts
    kill_date: Mapped[datetime] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(16), default=HypothesisStatus.ACTIVE)
    # Only the chairman may extend, exactly once, with a written reason.
    extended_once: Mapped[bool] = mapped_column(Boolean, default=False)
    extension_reason: Mapped[str | None] = mapped_column(Text)
    created_by_agent_id: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Evidence(Base):
    __tablename__ = "evidence"
    __table_args__ = (Index("ix_evidence_project_created", "project_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    # A real foreign key since stage 6: it used to be an un-FK'd string that
    # ceo_judgment joined on — paste any id and you inflate your own score.
    # Still nullable, because the two legitimate taskless authors remain: a
    # connector (webhooks carry no task) and an Explore-mode agent (bets and
    # evidence, no board). Agent-written evidence in Deliver/Operate must name
    # the task it came from — enforced as a Refusal in policy.
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    created_by_agent_id: Mapped[str | None] = mapped_column(String(40))  # null => connector
    kind: Mapped[str] = mapped_column(String(16))  # EvidenceKind
    provenance: Mapped[str] = mapped_column(String(24))  # EvidenceProvenance
    description: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    verification: Mapped[str] = mapped_column(String(16), default=VerificationStatus.PENDING)
    verify_attempts: Mapped[int] = mapped_column(Integer, default=0)
    verifier_notes: Mapped[str | None] = mapped_column(Text)
    snapshot_sha256: Mapped[str | None] = mapped_column(String(64))
    snapshot_path: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Decision(Base):
    """Append-only record of bets chosen, extended, killed — and why."""

    __tablename__ = "decisions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    by_agent_id: Mapped[str | None] = mapped_column(String(40))
    by_user_id: Mapped[str | None] = mapped_column(String(40))
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Artifact(Base):
    """A deliverable as a row: named, versioned, hashed, and tied to the
    requirements it claims to satisfy.

    Before this, "published" meant a storage key started with ``projects/``
    and the date was a filesystem mtime a restore would rewrite. The row is
    the receipt: the platform computes the sha256 and the version at write
    time, so what shipped, when, and what it was FOR are facts with an author
    — not properties of a directory listing.
    """

    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("project_id", "name", "version"),
        Index("ix_artifacts_project_published", "project_id", "published_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    name: Mapped[str] = mapped_column(String(255))
    # Per (project, name), starting at 1. Publishing the same name again is a
    # new version, never an overwrite — the store key is run-scoped anyway.
    version: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(Text)
    uri: Mapped[str] = mapped_column(Text)
    published_by_agent_id: Mapped[str | None] = mapped_column(String(40))
    run_id: Mapped[str | None] = mapped_column(String(40))
    # Requirement ids this deliverable claims to satisfy. Validated against
    # the project at publish; a claim, not a verdict — met is still only ever
    # written by the platform.
    requirement_ids: Mapped[list] = mapped_column(JSONVariant, default=list)
    published_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AuditSample(Base):
    """One observable task close, drawn for the chairman's sampled audit.

    The janitor rolls the dice AFTER the close, server-side, at the rate the
    constitution sets — so no run token can know which claim will be checked,
    which is the entire mechanism. Every considered close gets a row (drawn or
    skipped), so nothing is re-rolled and the review's coverage line has a
    denominator. An expired sample records "not reviewed" and blocks nothing.
    """

    __tablename__ = "audit_samples"
    __table_args__ = (
        UniqueConstraint("task_id"),
        Index("ix_audit_samples_project_status", "project_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    status: Mapped[str] = mapped_column(String(16), default=AuditSampleStatus.PENDING)
    drawn_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    note: Mapped[str | None] = mapped_column(Text)


# ── memory ──────────────────────────────────────────────────────────────────────


class Memory(Base):
    __tablename__ = "memories"
    __table_args__ = (Index("ix_memories_scope", "project_id", "scope", "team_id"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    scope: Mapped[str] = mapped_column(String(16))  # MemoryScope
    team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"))
    kind: Mapped[str] = mapped_column(String(24))  # fact|lesson|preference|contact
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    created_by_agent_id: Mapped[str] = mapped_column(String(40))
    superseded_by_id: Mapped[str | None] = mapped_column(String(40))
    superseded_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ── money & runs ────────────────────────────────────────────────────────────────


class LedgerEntry(Base):
    """Tokens always; dollars only when dollars move. A subscription-mode run
    books tokens with usd NULL — never a fictional list price."""

    __tablename__ = "ledger"
    __table_args__ = (Index("ix_ledger_project_at", "project_id", "at"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    kind: Mapped[str] = mapped_column(String(24))  # model_usage|external_spend|infra
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    # The API's input_tokens is only the uncached remainder; the real prompt is
    # tokens_in + cache_creation_tokens + cache_read_tokens.
    cache_creation_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    usd: Mapped[float | None] = mapped_column(Float)
    run_id: Mapped[str | None] = mapped_column(String(40))
    note: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class RunQueue(Base):
    """Hot table: work waiting for a runner. A runner claims a row with
    FOR UPDATE SKIP LOCKED, inserts the runs row, deletes this one — one
    transaction, so a job is never both queued and running."""

    __tablename__ = "run_queue"
    __table_args__ = (Index("ix_run_queue_ready", "not_before", "created_at"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    agent_id: Mapped[str | None] = mapped_column(String(40))  # null for success checks
    kind: Mapped[str] = mapped_column(String(24), default=RunKind.AGENT_RUN)
    trigger: Mapped[dict] = mapped_column(JSONVariant)
    task_id: Mapped[str | None] = mapped_column(String(40))
    requirement_id: Mapped[str | None] = mapped_column(String(40))
    not_before: Mapped[datetime | None] = mapped_column(UTCDateTime)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    prior_run_id: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Run(Base):
    """History + live-lease table. Live rows have status=running and a fresh
    heartbeat; the janitor reaps stale ones without touching side effects."""

    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_project_created", "project_id", "created_at"),
        Index("ix_runs_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    agent_id: Mapped[str | None] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(24), default=RunKind.AGENT_RUN)
    trigger: Mapped[dict] = mapped_column(JSONVariant)
    task_id: Mapped[str | None] = mapped_column(String(40))
    # What this run was FOR. 62% of runs in the audit carried no task id and
    # consumed half the spend, because a planning run is unattributable by
    # construction — so cost per deliverable was not computable, and the
    # forecast needs it. A run stamps the requirement it served even when it
    # served no single task.
    requirement_id: Mapped[str | None] = mapped_column(String(40))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    prior_run_id: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(24), default=RunStatus.RUNNING)
    worker: Mapped[str | None] = mapped_column(String(120))
    token_hash: Mapped[str | None] = mapped_column(String(64))
    token_revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    refusal_count: Mapped[int] = mapped_column(Integer, default=0)
    # The most recent Refusal message — a storm banner that can't say which
    # wall was hit isn't actionable.
    last_refusal: Mapped[str | None] = mapped_column(Text)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cache_creation_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    # Pinned at lease time, not resolved at read time: a role repin or model
    # change must never silently re-attribute historical spend.
    model: Mapped[str | None] = mapped_column(String(64))
    effort: Mapped[str | None] = mapped_column(String(16))
    trace_path: Mapped[str | None] = mapped_column(Text)
    # Store uri of {"system_prompt", "context_pack"} — what the run actually
    # sent to the model, stashed beside the trace.
    context_path: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class SideEffect(Base):
    """Written BEFORE any external side effect fires, so a reaped run's retry
    can see what its predecessor already did instead of double-firing."""

    __tablename__ = "side_effects"
    __table_args__ = (
        UniqueConstraint("project_id", "idempotency_key"),
        Index("ix_side_effects_task", "task_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    run_id: Mapped[str] = mapped_column(String(40))
    task_id: Mapped[str | None] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(64))  # e.g. "slack_post", "github_pr", "email"
    idempotency_key: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default=SideEffectStatus.RECORDED)
    payload: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    result: Mapped[dict | None] = mapped_column(JSONVariant)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


# ── approvals, secrets, audit, notices ──────────────────────────────────────────


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    kind: Mapped[str] = mapped_column(String(24))  # ApprovalKind
    requested_by_agent_id: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    amount_usd: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default=ApprovalStatus.PENDING)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    # The chairman is nagged before expiry — a vacationing chairman degrades
    # the project, never deadlocks it, but not silently.
    nag_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    decided_by_user_id: Mapped[str | None] = mapped_column(String(40))
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    decision_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Secret(Base):
    """Envelope-encrypted values scoped to roles. An agent only ever receives
    the secrets its role's secret_scopes name, injected per-run."""

    __tablename__ = "secrets"
    __table_args__ = (UniqueConstraint("project_id", "name"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(String(40))  # null => platform-level
    name: Mapped[str] = mapped_column(String(120))
    ciphertext: Mapped[str] = mapped_column(Text)
    role_scopes: Mapped[list] = mapped_column(JSONVariant, default=list)  # role slugs
    # "service" (role-granted, injected under its own name) vs "billing"
    # (attached as billing_secret_id, injected into EVERY run as the model
    # credential). The split is a refusal, not a hint: billing secrets refuse
    # role grants, billing_secret_id refuses service secrets — a WordPress
    # password can never become the model credential, or vice versa.
    kind: Mapped[str] = mapped_column(String(16), default="service")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_project_at", "project_id", "at"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(String(40))
    actor: Mapped[str] = mapped_column(String(80))  # user:<id> | agent:<id> | system:<name>
    action: Mapped[str] = mapped_column(String(80))
    entity: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(40))
    detail: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Notice(Base):
    """Governance events addressed to an agent (usually the CEO). Delivered in
    the next context pack; the *enforcement* of the rule that produced them is
    always elsewhere — a refusal or an automatic state change. A notice is
    information, never the mechanism."""

    __tablename__ = "notices"
    __table_args__ = (Index("ix_notices_agent_unconsumed", "agent_id", "consumed_at"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    agent_id: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(32))  # NoticeKind
    body: Mapped[str] = mapped_column(Text)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    consumed_by_run_id: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Heartbeat(Base):
    """Last firing time per (agent, cadence-name); the dispatcher uses this to
    decide when a scheduled wake (CEO planning, standup) is due."""

    __tablename__ = "heartbeats"
    __table_args__ = (UniqueConstraint("agent_id", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    name: Mapped[str] = mapped_column(String(64))
    last_fired_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
