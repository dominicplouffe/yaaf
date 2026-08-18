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
    PAUSED = "paused"      # kill switch or budget trip; resumable
    STOPPED = "stopped"    # terminal


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
    chairman_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    # "api_key" | "subscription" — decides the budget's native unit (usd vs tokens)
    billing_mode: Mapped[str] = mapped_column(String(16), default="api_key")
    billing_secret_id: Mapped[str | None] = mapped_column(String(40))
    # Domains/accounts the project controls; evidence from these can never be external.
    controlled_domains: Mapped[list] = mapped_column(JSONVariant, default=list)
    github_repo: Mapped[str | None] = mapped_column(String(255))
    # Mutable configuration (heartbeat cadences, concurrency, slack mapping).
    config: Mapped[dict] = mapped_column(JSONVariant, default=dict)
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
    task_id: Mapped[str | None] = mapped_column(String(40))
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
