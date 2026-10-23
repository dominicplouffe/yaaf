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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from yaaf.runtimes.catalog import DEFAULT_EFFORT, DEFAULT_MODEL
from yaaf.runtimes.providers import DEFAULT_PROVIDER

JSONVariant = JSON().with_variant(JSONB(), "postgresql")

#: Room for the longest attribution any surface writes: `user:<id> via
#: mcp:<client name>`. It was 80 on the audit log and 64 on the event logs,
#: which SQLite never enforces and Postgres does — so an MCP client named past
#: about 37 characters had every write it attempted fail with a 500 on the
#: database people deploy. `test_actor_columns_hold_the_longest_stamp` derives
#: the floor from the columns that feed the stamp rather than restating it.
ACTOR_LEN = 200


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
    """Whether a seat runs, and if not, whose decision that was.

    PAUSED and STOOD_DOWN are identical to the dispatcher and opposite to a
    reader. A paused seat is an intervention: the chairman reached in, and the
    fix is to resume it. A stood-down seat has *finished* — its role was needed
    for the front half of the project and is not needed now, and the right
    response to seeing one is nothing at all. Told only "not active", the idle
    brief advised "resume the agent, or reassign the work" about seats whose
    job was over, which is how a roster that had been managed correctly read as
    a roster somebody had abandoned.
    """

    ACTIVE = "active"
    PAUSED = "paused"
    STOOD_DOWN = "stood_down"


class ChannelKind(enum.StrEnum):
    BOARDROOM = "boardroom"    # chairman <-> CEO only
    LEADERSHIP = "leadership"  # CEO + team leads
    TEAM = "team"              # a team's lead + employees


class TaskStatus(enum.StrEnum):
    READY = "ready"
    CLAIMED = "claimed"
    CHECKING = "checking"   # platform is executing a command-type success test
    # Finished by its claimant and waiting on somebody who did not write it.
    # Only observable-test tasks land here, and only where the constitution
    # asks for an independent read: a command test is already judged by the
    # platform, while an observable used to close on the author's own word.
    REVIEW = "review"
    BLOCKED = "blocked"
    DONE = "done"
    KILLED = "killed"


class SuccessTestKind(enum.StrEnum):
    COMMAND = "command"        # platform-executed in the task's sandbox
    OBSERVABLE = "observable"  # names a checkable artifact/URL/metric; spot-audited
    # A number the platform reads on a schedule, from a source the project does
    # not author. The other two kinds can only ask "does this artifact exist and
    # say the right words", which is why every requirement on every desk became
    # a grep over /workspace and the objective itself — the rank, the revenue,
    # the deficit — had no requirement, no test and no place in the plan.
    #
    # `GOAL_GRAMMAR.md` already stated the rule this implements: "a finish line
    # the project can write into its own status report is not a finish line."
    # COMMAND and OBSERVABLE are both, structurally, exactly that.
    METRIC = "metric"
    # Only ever on R-00, the planning requirement, and only ever written by the
    # platform: the test is a structural check of the plan itself — is there
    # scope, is it staged, is it estimated, does it fit the money — evaluated
    # in the server rather than in a sandbox. An agent proposing this kind is
    # refused, because a project that can author its own planning gate does not
    # have one.
    PLAN = "plan"


class MetricDirection(enum.StrEnum):
    """Which way is winning. Rank falls, revenue rises."""

    INCREASING = "increasing"
    DECREASING = "decreasing"


class MetricSourceKind(enum.StrEnum):
    """Where a reading comes from. Every one of these is outside the project.

    There is deliberately no `workspace` or `agent_reported` member. A source an
    agent can write is a diary entry, and `verifier.py` already refuses evidence
    from a domain the project controls for the same reason.
    """

    HTTP_JSON = "http_json"  # GET a JSON document, read one path out of it
    LEDGER = "ledger"        # sum a LedgerKind over the project's own ledger


class ProjectMode(enum.StrEnum):
    """What shape this project takes, chosen once at creation.

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


class FlagKind(enum.StrEnum):
    """Why the platform decided the chairman needs to hear something.

    Every one of these is arithmetic or a failed test — never a judgement the
    CEO makes about whether the news is worth his time. That distinction is the
    whole point: the CEO that prompted this had written "run ration is the
    binding constraint" in its own notes three hours before telling its
    chairman "solid progress".
    """

    STANDING_MISSED = "standing_missed"    # an obligation failed its check
    DEADLINE_AT_RISK = "deadline_at_risk"  # capacity slack went negative
    CYCLE_MISSED = "cycle_missed"          # a cycle rolled with scope unmet
    WORK_STARVED = "work_starved"          # a cap stopped work with tasks ready
    # A measurement window reached a checkpoint, or is on course to spend its
    # allowance before it ends. Both are arithmetic against the ledger.
    SPEND_WINDOW = "spend_window"
    # The month went past its budget. Not a failure and not a stop — an
    # obligation to say what the money bought, which is the whole difference
    # between a budget and a wall.
    BUDGET_OVERRUN = "budget_overrun"
    # The measured objective is moving AWAY from its target. Every kind above
    # this line is about process or money: a missed obligation, a slipped date,
    # a spent allowance, an overrun. None of them can say the project is
    # losing, so a desk 15 points behind the field average tripped nothing at
    # all while re-explaining a cycle-boundary artifact six times in five days.
    #
    # Arithmetic, like every other member: the latest reading is further from
    # target than the oldest in the window. Not "worse than last time" — one
    # bad reading is noise, and a flag that cried wolf on noise would be muted
    # inside a week, which is worse than not having it.
    LOSING_GROUND = "losing_ground"
    # Nothing is claimable, and the reason is the project's own start times
    # rather than any cap. WORK_STARVED cannot see this: it fires when a WALL
    # stopped work, so a CEO that gates its own board 90 hours out produces
    # claimable 0, gated 8, and an idle desk that trips nothing. That is not a
    # hypothetical — it is 2026-09-04, and the only thing that noticed was a
    # warning on a screen nobody is obliged to read.
    SELF_GATED = "self_gated"
    # The project went past its daily run PLAN and is working in the space
    # between the plan and the wall. Not a failure and not a stop — an
    # obligation to say what the extra runs bought, which is the whole
    # difference between a plan and a cliff. Exactly the shape BUDGET_OVERRUN
    # already has; runs were simply never given it.
    RUN_OVERRUN = "run_overrun"
    # A seat is awake, reading its channels, spending the project's ration, and
    # moving nothing. Deliberately not "has not run recently": a seat waiting
    # behind a start time has not run and is not idle, and a flag that fired on
    # that would be muted inside a week. This is the opposite and worse shape,
    # and it had no reader at all — a desk carried two seats that closed no
    # work for two days and spent 139 million tokens, and the only thing that
    # noticed was the chairman reading a token bill.
    SEAT_IDLE = "seat_idle"


class FlagStatus(enum.StrEnum):
    OPEN = "open"          # tripped, and the chairman has not been told
    RAISED = "raised"      # said out loud in the boardroom, with a proposal
    RESOLVED = "resolved"  # the condition cleared


class Recurrence(enum.StrEnum):
    """Is this requirement done once, or due every cycle?

    Deliver and Explore only ever have the first kind: a thing that must be
    true at the end, met once and closed. Operate runs on a rhythm, and most of
    what it owes is owed again next cycle — the gameweek memo, the morning
    inbox sweep, the hourly routing check. Expressed as TERMINAL, those became
    one row met once, and a project that had shipped 1 of 38 memos looked
    finished. Expressed as prose, they were invisible to every check the
    platform runs.

    A STANDING requirement is met against a CYCLE (see RequirementCycleResult)
    and reopens when the next one starts. Its history — "met in 23 of the last
    24 cycles" — is what an Operate project has instead of a burndown.
    """

    TERMINAL = "terminal"
    STANDING = "standing"


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
    # Retired by the platform because the project is Operate. A rhythm has no
    # finish line for one date to promise, so such a row binds nothing — the
    # cycle's own `due_at` is where an Operate date lives. Kept, not deleted:
    # a promise that existed is part of the record.
    VOID = "void"


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


class CycleStatus(enum.StrEnum):
    """One period of an Operate project's rhythm.

    COMPLETED closed with its scope settled true; MISSED rolled with work
    unfinished (the unsettled scope carries into the next cycle); SKIPPED
    rolled with nothing ever accepted into it at all — and two consecutive
    SKIPPEDs pause the project, because a rhythm nobody dances to is a bill,
    not a project.
    """

    OPEN = "open"
    COMPLETED = "completed"
    MISSED = "missed"
    SKIPPED = "skipped"


class ReviewStatus(enum.StrEnum):
    OPEN = "open"      # the janitor opened it; the meeting is in progress
    CLOSED = "closed"  # the chairman closed it, with his decisions on it


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
    # Reading the inbox. A wake whose ONLY reason is ordinary unread messages —
    # nothing from the chairman, nothing in the boardroom, no task, no notice,
    # no heartbeat. It runs, it is logged, it is billed to the ledger like any
    # other run, and it spends its OWN ceiling rather than the project's work
    # allowance.
    #
    # 57% of every run one audited project ever spent was this: agents waking
    # to read a message and reply "Ack". At the daily reset, three of the first
    # four runs went to agents reading the message that told them not to work —
    # which consumed the hourly ceiling, so the two agents actually holding
    # ready tasks got nothing. Reading is not free, but charging it to the same
    # purse as the work means the reading crowds the work out.
    INBOX_RUN = "inbox_run"


class ApprovalKind(enum.StrEnum):
    SPEND = "spend"
    LEGAL = "legal"
    OWNER_DECISION = "owner_decision"
    TOOL_REQUEST = "tool_request"
    # A case for more of a capacity dial — runs, concurrency, queue depth, the
    # month's money. Its own kind rather than a tool_request because it is the
    # only one where the CEO is arguing for *itself*, and the chairman should
    # be able to see at a glance how often that happens.
    #
    # Nine verbs existed for saying something and none for asking for more of
    # anything. A CEO that had correctly identified its run ration as the
    # binding constraint could describe it in the boardroom and nothing else:
    # "if we ever want genuinely more runs in a day, that's a separate dial and
    # I'd come to you with what it buys before asking." It had no way to come.
    CAPACITY = "capacity"
    # The human-assist protocol (CEO Charter, stage E): a step only a human
    # can do — a captcha, a 2FA code, a KYC form, an account only he can
    # touch. Any agent may file one; the chairman's answer rides the decision
    # note and wakes the asker. The platform never tries to defeat a security
    # challenge at any autonomy level — it hands it over.
    ASSIST = "assist"
    # The chairman changed his mind, and this is the CEO writing it back to him
    # to confirm: the goal in plain words, every standing order with its value,
    # and what no longer applies. Confirming it is what replaces the goal every
    # agent reads first. Direction given in chat used to land in a memory or a
    # file the next run had to think to look up, while the founding goal kept
    # being pushed into every prompt — so each change held for a day or two and
    # the original spirit came back. On one desk a stop the chairman had
    # deleted cut an order five weeks later, "from the original project
    # settings". The approval's `brief` carries the text; approving applies it.
    BRIEF = "brief"


class ApprovalStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"  # timed out; constitution default decision is DENY


class AskStatus(enum.StrEnum):
    OPEN = "open"  # asked, and the chairman has not spoken since
    ANSWERED = "answered"  # he replied in the boardroom
    LAPSED = "lapsed"  # the window passed; the CEO's stated default now stands
    CLOSED = "closed"  # the CEO acted on the answer, or on the default, and said so


class MemoryScope(enum.StrEnum):
    PROJECT = "project"  # curated by the CEO
    TEAM = "team"        # curated by the team lead


class SideEffectStatus(enum.StrEnum):
    RECORDED = "recorded"    # written BEFORE the effect is attempted
    COMPLETED = "completed"
    FAILED = "failed"


class LedgerKind(enum.StrEnum):
    """What a ledger row's dollars (or tokens) mean. The distinction is
    load-bearing arithmetic, not taxonomy: the budget meter sums everything
    that is NOT revenue, the external envelope sums exactly EXTERNAL_SPEND,
    and REVENUE is written only by signed webhooks — no agent verb exists.
    A new kind added here must decide which sums it belongs to."""

    MODEL_USAGE = "model_usage"
    EXTERNAL_SPEND = "external_spend"
    INFRA = "infra"
    REVENUE = "revenue"


class NoticeKind(enum.StrEnum):
    DROUGHT = "drought"
    # The platform decided the chairman needs to hear something. Urgent by
    # construction: a flag delivered on the next scheduled status is a warning
    # that arrives after the deadline it was about.
    FLAG_OPENED = "flag_opened"
    # The chairman answered one. Not urgent — nothing is waiting on it — but a
    # CEO that never learns what came of raising a flag is proposing into a
    # void, and the next one it raises is written the same way.
    FLAG_ACKNOWLEDGED = "flag_acknowledged"
    # He noted a heads-up and wrote something on it. A bare "seen" makes no
    # notice: nothing was waiting, and the CEO reads it off the row.
    HEADS_UP_NOTED = "heads_up_noted"
    HYPOTHESIS_EXPIRED = "hypothesis_expired"
    BUDGET_WARNING = "budget_warning"
    BUDGET_EXHAUSTED = "budget_exhausted"
    # The real-world spend envelope (external_monthly_usd_cap) — warned at the
    # same ratio as the model budget, but never pausing anything: the wall is
    # enforced where money is cleared and booked, not by stopping the project.
    EXTERNAL_SPEND_WARNING = "external_spend_warning"
    # Money came IN — booked only by a signed webhook, never by an agent, so
    # income can be reported without ever being inventable.
    REVENUE = "revenue"
    # The world talked back (stage E): a signed webhook asked that an agent be
    # woken — a customer email, an order, a monitoring alert. Urgent-classed,
    # still under every wake-rate brake: the world can knock, never stampede.
    EXTERNAL_EVENT = "external_event"
    APPROVAL_DECIDED = "approval_decided"
    APPROVAL_EXPIRED = "approval_expired"
    THREAD_FROZEN = "thread_frozen"
    CONSTITUTION_AMENDED = "constitution_amended"
    GOAL_CHANGED = "goal_changed"
    TASK_CHECK_FAILED = "task_check_failed"
    TASK_BLOCKED = "task_blocked"
    AGENT_RUN_REAPED = "agent_run_reaped"
    # The project is running, has work, and has started nothing for hours. Urgent
    # by construction: the whole point is that the silence already went on too
    # long, so batching this notice behind another would extend the fault it
    # exists to report.
    STALLED = "stalled"
    # Scope that quietly stopped moving: every task it ever had is closed and
    # none of them succeeded, so its acceptance test will never run.
    REQUIREMENT_STALLED = "requirement_stalled"
    # A team of two or more with nobody named as lead cannot claim its own
    # queue. The platform names the earliest member and says so.
    LEAD_ELECTED = "lead_elected"
    # A team's declared stand-down condition came true and the platform stood
    # it down. Not a request and not a warning — the decision was made when the
    # CEO wrote the condition; this is the receipt, so the next run plans
    # around a roster it knows has changed under it.
    SEAT_STOOD_DOWN = "seat_stood_down"
    AUDIT_FAILED = "audit_failed"  # chairman spot-audit failed an evidence row or completion
    # Everything in scope is true and only the chairman's confirmation is left.
    # Not urgent: it can wait out a batching window, because the one thing it
    # must not do is buy a run on every tick of a project that has finished.
    DELIVERY_READY = "delivery_ready"

    # Memory pressure warns the scope's curator early enough to curate on a wake
    # it was having anyway; MEMORY_FULL is urgent because the scope has stopped
    # accepting writes and only the curator can restart it.
    MEMORY_PRESSURE = "memory_pressure"
    MEMORY_FULL = "memory_full"
    # The commitment's transitions and re-validations, each disclosed to the
    # CEO exactly once. AT_RISK and BREACHED also freeze new scope — the
    # notice is the explanation, the refusal in policy is the enforcement.
    REVIEW_OPENED = "review_opened"
    EXPLORE_DEADLINE = "explore_deadline"
    CYCLE_ROLLED = "cycle_rolled"
    COMMITMENT_ACCEPTED = "commitment_accepted"
    COMMITMENT_AT_RISK = "commitment_at_risk"
    COMMITMENT_BREACHED = "commitment_breached"
    COMMITMENT_RECOVERED = "commitment_recovered"
    COMMITMENT_MET = "commitment_met"
    COMMITMENT_REVALIDATED = "commitment_revalidated"
    # The chairman's window on a question closed without an answer. Urgent for
    # the same reason a flag is: this notice IS the wake that makes the CEO's
    # stated default happen. Delivered on the next scheduled anything, it is a
    # decision announced after the moment it was supposed to be taken — and on
    # a project whose board emptied while it waited, there is no next anything.
    ASK_LAPSED = "ask_lapsed"


# ── people & projects ───────────────────────────────────────────────────────────


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    #: Stored lower-cased. A sign-in form that cared about case would lock out
    #: the person who typed it differently the second time.
    email: Mapped[str] = mapped_column(String(255), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    #: The instance admin: runs the box, reads every project, decides nothing on
    #: a project it does not chair. See docs/AUTH.md.
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    #: A timestamp rather than a delete, for the reason McpClient.revoked_at
    #: gives: the record names this person forever, so the row has to survive.
    deactivated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class UserSession(Base):
    """A browser signed in as a person.

    A row rather than a signed cookie, so it can be revoked: logging out,
    changing a password and deactivating an account all have to reach a
    browser somebody else is holding, and a self-validating cookie would carry
    on until it expired. Only the hash of the cookie value is stored.
    """

    __tablename__ = "user_sessions"
    __table_args__ = (Index("ix_user_sessions_hash", "token_hash", unique=True),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    token_hash: Mapped[str] = mapped_column(String(64))
    #: What the browser said it was, so a person reading their session list
    #: can tell their laptop from somebody else's.
    user_agent: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class UserToken(Base):
    """A personal token: the person, at a terminal or in a script.

    It reaches exactly what its person reaches, never more, and is stamped
    `via token:<name>` so a script's writes do not read as clicks. Always
    expiring — a credential that outlives the reason it was minted is how a
    laptop sold two years ago still runs a project.
    """

    __tablename__ = "user_tokens"
    __table_args__ = (Index("ix_user_tokens_hash", "token_hash", unique=True),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(80))
    token_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class UserSetupLink(Base):
    """A single-use link that sets a password.

    One mechanism for three moments — accepting an invite, resetting a
    forgotten password, and the first admin signing in on a fresh box — so
    there is one expiry, one single-use rule and one test, not three.
    """

    __tablename__ = "user_setup_links"
    __table_args__ = (Index("ix_user_setup_links_hash", "token_hash", unique=True),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    token_hash: Mapped[str] = mapped_column(String(64))
    #: invite | reset | bootstrap — for the record, not for the rules.
    purpose: Mapped[str] = mapped_column(String(16))
    #: Null when it was issued from a shell on the box rather than by a person.
    created_by_user_id: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class LoginFailure(Base):
    """One wrong password, for the throttle.

    Keyed by a hash of the address rather than by user, so an address with no
    account is throttled exactly like one with an account: a throttle that only
    ever fired for real accounts would answer the question the sign-in page
    is careful not to.
    """

    __tablename__ = "login_failures"
    __table_args__ = (Index("ix_login_failures_email_at", "email_hash", "at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email_hash: Mapped[str] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    goal: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default=ProjectStatus.DRAFT)
    status_reason: Mapped[str | None] = mapped_column(Text)
    # What shape this project takes. Explore is the default and today's
    # behaviour; outside it, a task must serve a requirement.
    mode: Mapped[str] = mapped_column(String(16), default=ProjectMode.EXPLORE,
                                      server_default=ProjectMode.EXPLORE)
    chairman_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    # "api_key" | "subscription" — decides the budget's native unit (usd vs tokens)
    billing_mode: Mapped[str] = mapped_column(String(16), default="api_key")
    billing_secret_id: Mapped[str | None] = mapped_column(ForeignKey("secrets.id"))
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
    # Explore's window: the chairman's own example was "one sale in the next 60
    # days" — open scope, bounded time. When it passes, he is told and the
    # judgment is his; nothing pauses by itself.
    #
    # Deliver may hold one too, as a TARGET rather than a promise — the promise
    # is still the commitment, which only the platform dates. Deliver used to
    # refuse a bare deadline outright, which did not remove the date so much as
    # relocate it: the founder wrote it into the goal prose, where nothing
    # validates it, nothing re-projects it, and nothing compares it to the
    # platform's own arithmetic. It then paced the work regardless, which is
    # how a project that had shipped everything sat idle for thirteen hours
    # because a number in a paragraph said November.
    # What the Founder knew when this project was made. A content fingerprint
    # of the doctrine files, so "founded under older doctrine than we now ship"
    # is a fact the platform holds rather than something a person reconstructs
    # from `git log`. Null on every project founded before the stamp existed,
    # which is exactly the population it exists to find.
    doctrine_stamp: Mapped[str | None] = mapped_column(String(16))
    deadline: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Where that date came from, in the shape `cycles.due_source` established:
    # {"kind": "external"|"chairman", "url", "note", "recorded_at"}. A deadline
    # the world imposes is a fact and cites its source; anything else is a wish
    # and is recorded as one, so the Plan screen and the CEO's pack can say
    # which it is instead of treating both as physics.
    deadline_source: Mapped[dict | None] = mapped_column(JSONVariant)
    # The org shape, computed at creation from the team specs: solo (one brain
    # plans and executes), ceo_team (flat — the CEO talks to individuals), or
    # ceo_leads_workers (the full hierarchy). Stored so the org shape stays a
    # recorded fact, never an inference that could drift.
    topology: Mapped[str] = mapped_column(String(24), default="ceo_leads_workers",
                                          server_default="ceo_leads_workers")
    # The brief's other two halves; `goal` is the first. Standing orders are
    # the chairman's current word, one plain sentence each with its value, and
    # every agent reads them right under the goal on every run. Retired orders
    # are what he has taken back: listed too, because the old rule does not
    # leave the memories, files and check scripts it was written into, and an
    # agent that finds it there needs to be told it is dead.
    standing_orders: Mapped[list | None] = mapped_column(JSONVariant, default=list)
    retired_orders: Mapped[list | None] = mapped_column(JSONVariant, default=list)
    # When he last confirmed the brief. Null on a project still running on the
    # goal it was founded with.
    brief_confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    #: chairman (exactly one, mirrors projects.chairman_user_id) | co_chair |
    #: observer. What each may do is in docs/AUTH.md and yaaf.server.authority.
    role: Mapped[str] = mapped_column(String(16))
    # When this member last read the briefing — the "since you were last here"
    # mark. Per member and per project on purpose: reading one project's brief
    # says nothing about having read another's, and a single box-wide mark
    # would silently swallow whichever project he did not open.
    #
    # NULL means never read, which the briefing reads as "since the project
    # started" rather than "since forever": a first visit should summarise the
    # project, not an empty window.
    last_briefed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # The health-card suggestions this member has put away: {sig: {"key", "at"}}.
    # Per member for the same reason as the mark above — "I have read this
    # advice" is a fact about one reader, and a co-chair tidying their card must
    # not silence the chairman's. Only advice can be put away; a problem stays
    # on every card until it is fixed.
    health_acks: Mapped[dict | None] = mapped_column(JSONVariant)
    # How far into the boardroom this member has read: everything after it,
    # written by somebody else, is their unread count. Per member for the same
    # reason as the two above. NULL means never opened — all of it is unread.
    boardroom_read_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


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
    # How the chairman finds this role when hiring. Identity, like
    # is_ceo_role — deliberately NOT on RoleVersion: editing a prompt must
    # never silently refile a role, and a pinned team must never show a
    # category its role has since moved out of. Null is legal and means
    # "uncategorised", which the pickers show rather than hide.
    category: Mapped[str | None] = mapped_column(String(32))
    # Which worlds it knows: ["markets", "data"]. A filter, never a folder.
    domains: Mapped[list] = mapped_column(JSONVariant, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class RoleVersion(Base):
    __tablename__ = "role_versions"
    __table_args__ = (UniqueConstraint("role_id", "version"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id"))
    version: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(Text)
    system_prompt: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(64), default=DEFAULT_MODEL)
    effort: Mapped[str] = mapped_column(String(8), default=DEFAULT_EFFORT)
    # What drives the model (yaaf.runtimes.harnesses). NULL is the provider's
    # default — which is every role written before there was a choice.
    harness: Mapped[str | None] = mapped_column(String(32))
    # {"bash": true, "web": true, "github": false}
    tool_permissions: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    secret_scopes: Mapped[list] = mapped_column(JSONVariant, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Crew(Base):
    """A named job with a team already on it.

    Categories make thirty roles findable; they do nothing about the harder
    question, which is who you actually need. A crew answers that by starting
    from the work: pick "Read the filings" and the team list fills in, fully
    editable. It is a starting point the chairman edits, never a black box —
    nothing here is referenced again once the project exists.
    """

    __tablename__ = "crews"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    # One sentence, in the chairman's language: what exists at the end.
    outcome: Mapped[str] = mapped_column(Text)
    # Which CEO runs it. A markets desk wants the CIO, not the generic CEO —
    # which is the whole reason the chairman now gets to choose one at all.
    ceo_role_slug: Mapped[str] = mapped_column(String(64), default="ceo")
    domains: Mapped[list] = mapped_column(JSONVariant, default=list)
    # [{"name": "Research", "role_slug": "equity-analyst",
    #   "employees": ["Maya", "Tobias"], "lead": "Maya",
    #   "schedules": {"Maya": "07:00"}}] — the TeamSpec shape the create-project
    # body already takes, so applying a crew is a straight copy.
    teams: Mapped[list] = mapped_column(JSONVariant, default=list)
    # Where it came from: "examples/10-read-the-filings" or "authored". Shown
    # so a chairman can go read the worked example behind the crew.
    source: Mapped[str | None] = mapped_column(String(120))
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
    # When this team's job ends, written while planning and evaluated by the
    # platform — never prose, because the platform has to be able to fire it.
    # `{"kind": "requirement_met", "keys": ["R-03"]}` or
    # `{"kind": "stage_cleared", "stage": 2}`.
    #
    # A project is front-loaded and then it is not: planning, requirements,
    # build, validation, then execution. Every roster was hired for the first
    # half and kept for the second, because the only moment anyone was asked to
    # think about headcount was the moment before any work existed. This is the
    # CEO saying, at the one moment it knows, when it will stop needing a seat.
    stand_down_when: Mapped[dict | None] = mapped_column(JSONVariant)
    stood_down_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
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
    # This seat's model and reasoning effort, when the role library's answer is
    # not the right one for this project. Both null means "whatever the role
    # says", which is the case for almost every seat.
    #
    # `effort_override` arrived late and its absence was load-bearing: a
    # project could buy a better model for the seat whose output the chairman
    # actually reads and could not buy it more thinking, because effort was
    # readable only from the role version every project on the install shares.
    # Raising it in the library would have raised it everywhere.
    model_override: Mapped[str | None] = mapped_column(String(64))
    effort_override: Mapped[str | None] = mapped_column(String(16))
    # The same for what drives the model (yaaf.runtimes.harnesses): null is the
    # role's harness, and the role's null is its provider's default.
    harness_override: Mapped[str | None] = mapped_column(String(32))
    # Per-agent daily wake, "HH:MM" in the constitution's timezone (stage E).
    # The CEO's standup generalized: a support agent that checks the inbox
    # every morning, a metrics agent that reports at close. Chairman-set;
    # fires through the same heartbeat dedup and wake-rate brakes as the CEO's.
    daily_at: Mapped[str | None] = mapped_column(String(8))
    # Why this seat stopped, in the words of whoever stopped it. A stand-down
    # is a staffing decision and the project has to be able to account for one
    # later — "we took the designer off after the template shipped" is a
    # sentence somebody has to be able to read back six weeks on.
    stood_down_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    stood_down_reason: Mapped[str | None] = mapped_column(Text)
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
    # At most one of sender_agent_id / sender_user_id is set. Agent-directed
    # system notices use the notices table; the one sender-less shape is a
    # PLATFORM BOARDROOM LINE (a need or assist announced to the chairman),
    # which mirrors to Slack like any boardroom message and is deliberately
    # never a wake reason — the dispatcher skips sender-less messages.
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
    # Operate only: which turn of the rhythm this scope belongs to. The plan
    # gate, the Plan screen and the feasibility arithmetic read the OPEN
    # cycle's scope; unsettled requirements carry forward when a cycle rolls.
    cycle_id: Mapped[str | None] = mapped_column(String(40))
    # Human-readable and stable: the chairman says "cut R-09", not an opaque id.
    key: Mapped[str] = mapped_column(String(16))
    statement: Mapped[str] = mapped_column(Text)
    # Same shape as a task's: {"type": "command", ...} | {"type": "observable", ...}
    acceptance_test: Mapped[dict] = mapped_column(JSONVariant)
    # What the project's owner would call it: title, a one-line description in
    # their terms, when it lands, where. Never read by the plan gate or the
    # checker — that is `acceptance_test` — so it can say anything.
    meta: Mapped[dict | None] = mapped_column(JSONVariant)
    status: Mapped[str] = mapped_column(String(16), default=RequirementStatus.PROPOSED)
    # Met once and closed, or owed again every cycle. TERMINAL everywhere
    # except Operate, where a standing requirement is the normal shape and
    # `met_at` means "met for the cycle it was last met in".
    recurrence: Mapped[str] = mapped_column(
        String(16), default=Recurrence.TERMINAL, server_default=Recurrence.TERMINAL
    )
    # The waterfall axis. Ordered, chairman-owned, and gated: work may only be
    # created for requirements in the open stage or earlier.
    stage: Mapped[int] = mapped_column(Integer, default=1)
    est_runs: Mapped[int | None] = mapped_column(Integer)
    # The first estimate ever given, never updated. Reestimating moves
    # est_runs so the plan arithmetic stays honest about the work ahead, but
    # the bias multiplier is always scored against the number the CEO first
    # stood behind — discovering a task grew gets a channel, and lowballing
    # still cannot be laundered by revising afterwards.
    est_runs_first: Mapped[int | None] = mapped_column(Integer)
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
    actor: Mapped[str] = mapped_column(String(ACTOR_LEN))  # agent id, user id, "janitor", "checker"
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
    stops holding the project cannot quietly add work: new scope is refused,
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
    actor: Mapped[str] = mapped_column(String(ACTOR_LEN))  # agent id, user id, or "janitor"/"checker"
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
        Index("ix_artifacts_undelivered", "project_id", "slack_ts"),
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
    # Delivered to Slack, and when — the same marker `Message.slack_ts` has
    # carried since the baseline, for the same reason: a delivery that fails
    # leaves this NULL and is retried next tick rather than lost or doubled.
    # The shelf is a screen the chairman has to go to, and the record of what
    # that costs is "where are my deliverables?", "where is the report??" and
    # "can you push to me every morning instead of me going to look for a file".
    slack_ts: Mapped[str | None] = mapped_column(String(40))
    published_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Cycle(Base):
    """One turn of an Operate project — a Deliver that finishes and starts
    again on the constitution's rhythm (`cycle_frequency` + `cycle_day`, read
    in the constitution's one timezone).

    Cycles roll in the JANITOR: a rhythm firing while the project is busy or
    broke must still be recorded, and opening a cycle costs nothing. At most
    one is ever open — a project paused three weeks resumes into one new
    cycle, not twenty-one.

    ``narrative`` is the learning loop, the half the old plan dropped: the
    review writes it (uncapped), and the next cycle's context pack carries
    the last three VERBATIM. Only a lesson still true several cycles out
    spends a memory slot; the narrative is where the rest lives.
    """

    __tablename__ = "cycles"
    __table_args__ = (
        UniqueConstraint("project_id", "ordinal"),
        Index("ix_cycles_project_status", "project_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    ordinal: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default=CycleStatus.OPEN)
    # What actually landed, kept at close. MISSED with 9 of 10 met is not the
    # same event as MISSED with 0 of 10, and the status alone cannot tell them
    # apart — so a CEO whose cycle straddled a deadline spent a run a week
    # hand-explaining the difference, six times in five days, because the flag
    # that made it explain carried the ordinal and the goal and no numbers.
    scorecard: Mapped[dict | None] = mapped_column(JSONVariant)
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    narrative: Mapped[str | None] = mapped_column(Text)  # uncapped, on purpose
    # What this turn is FOR, in the CEO's words. Operate cycles had a number
    # and a narrative but never a goal, so "produce the gameweek memo" lived in
    # prose in a chat message where nothing could check it or count it.
    goal: Mapped[str | None] = mapped_column(Text)
    # When this cycle's work is actually due — which is NOT the same as when it
    # rolls, and for anything driven by the outside world is not on the
    # calendar at all. A gameweek deadline moves every week; a warehouse cutoff
    # moves with the carrier. Null falls back to the constitution's rhythm.
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Where `due_at` came from. The CEO may record a deadline because a
    # deadline is a FACT to be discovered, not a promise to be made — but a
    # fact has provenance: {"kind": "calendar"} or {"kind": "data", "url": ...,
    # "fetched_at": ..., "snapshot_sha256": ...}. The rule that the CEO cannot
    # write a date still governs promises; this is not one. Whether the work
    # will BE done by then remains the platform's arithmetic.
    due_source: Mapped[dict | None] = mapped_column(JSONVariant)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class RequirementCycleResult(Base):
    """One standing requirement, judged for one cycle.

    A terminal requirement has a single `met_at` and that is the whole story. A
    standing one needs a row per turn, because the story IS the pattern: met in
    23 of the last 24 cycles is an operating record, and "met" on its own is
    not. The platform writes these the same way it writes `met_at` — only after
    running the acceptance test itself.
    """

    __tablename__ = "requirement_cycle_results"
    __table_args__ = (
        UniqueConstraint("requirement_id", "cycle_id"),
        Index("ix_reqcycle_project_cycle", "project_id", "cycle_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    requirement_id: Mapped[str] = mapped_column(String(40))
    cycle_id: Mapped[str] = mapped_column(String(40))
    # MET or one of the open statuses, judged at the moment the cycle closed.
    status: Mapped[str] = mapped_column(String(16))
    met_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    met_by_run_id: Mapped[str | None] = mapped_column(String(40))
    check_output: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Measurement(Base):
    """One reading of one metric, at one moment.

    A row per reading rather than a column on the requirement, because the
    *trend* is the information and a latest-value column throws it away. "We
    are losing" is not a fact about a number; it is a fact about a sequence,
    and a platform that keeps only the current value can report a position but
    can never report a direction. `RequirementCycleResult` is the precedent —
    the same argument, made for standing obligations.

    Written only by `yaaf.server.metrics`, from a source outside the project.
    There is no agent verb that writes one, for the same reason nothing but the
    platform marks a requirement MET.
    """

    __tablename__ = "measurements"
    __table_args__ = (
        Index("ix_measurements_requirement_at", "requirement_id", "at"),
        Index("ix_measurements_project_at", "project_id", "at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    requirement_id: Mapped[str] = mapped_column(String(40))
    value: Mapped[float] = mapped_column(Float)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    # What was actually fetched, so a reading that looks wrong can be argued
    # with rather than trusted. Truncated; this is evidence, not a cache.
    source_note: Mapped[str | None] = mapped_column(Text)
    # A reading that could not be taken is still a row: a metric that silently
    # stops measuring is a scoreboard that silently stops saying you are losing.
    error: Mapped[str | None] = mapped_column(Text)


class OverrunOutcome(enum.StrEnum):
    """What the chairman decided about a month that went over."""

    PENDING = "pending"      # the CEO has not accounted for it yet, or he has not ruled
    CONTINUE = "continue"    # fine — carry on at this level, and raise the budget to match
    HOLD = "hold"            # justified once; the budget stays where it is
    REVISE = "revise"        # the work has to change, not the number


class BudgetOverrun(Base):
    """A month that went past its plan, and the conversation that follows.

    Before this the only two moves a project had were stay under the number or
    be paused by it, so the CEO's dominant strategy was to promise little and
    spend less. That is not caution, it is the rational play when crossing the
    plan is fatal — and it made every figure the desk produced a safe one
    rather than an honest one.

    A human team does not work that way. It budgets from an educated guess,
    goes over when the work turns out to need it, and *reports*: here is what I
    spent, here is what it bought, here is what I would do next time. Then the
    two of you decide together whether the number was wrong or the work was.

    So this row is that conversation, with the three parts kept apart. The
    PLATFORM writes the numbers — how far over, over what period, where the
    money actually went — at zero tokens and from the ledger, so nobody
    negotiates the facts. The CEO writes the account: why, and what it bought.
    The CHAIRMAN writes the decision. Same division that makes commitments
    work, applied to money.
    """

    __tablename__ = "budget_overruns"
    __table_args__ = (
        # One per project per month. A budget crossed on the 3rd and still
        # crossed on the 4th is one overrun, not thirty.
        UniqueConstraint("project_id", "period"),
        Index("ix_overruns_project_outcome", "project_id", "outcome"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    # The first day of the month this overran, so the unique constraint holds.
    period: Mapped[datetime] = mapped_column(UTCDateTime)
    unit: Mapped[str] = mapped_column(String(8))
    budget: Mapped[float] = mapped_column(Float)
    # Spend at the moment the platform noticed. `final_spent` is stamped when
    # the month closes, because the interesting number is where it ended up.
    spent_at_crossing: Mapped[float] = mapped_column(Float)
    final_spent: Mapped[float | None] = mapped_column(Float)
    crossed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    # Platform-computed and never from input: where the money went, by run kind
    # and by requirement, plus the daily series. The facts are not negotiable.
    breakdown: Mapped[dict | None] = mapped_column(JSONVariant)
    # The CEO's side: why it went over and what that bought. Written through
    # the flag it cannot close by staying quiet.
    ceo_account: Mapped[str | None] = mapped_column(Text)
    accounted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # The chairman's side.
    outcome: Mapped[str] = mapped_column(String(16), default=OverrunOutcome.PENDING)
    chairman_note: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    decided_by_user_id: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class SpendWindowStatus(enum.StrEnum):
    OPEN = "open"
    CLOSED = "closed"      # ran to its end date; the platform wrote the findings
    STOPPED = "stopped"    # the chairman ended it early


class SpendWindow(Base):
    """A funded period for finding out what something actually costs.

    Every budget conversation on this platform was an argument between two
    guesses. A CEO with no cost model and no lever offered its chairman 3M a
    day, then 5M, then 35M, inside two hours — each one a mean over a window
    that did not fit the question — and the chairman's reply was the design
    for this object:

        "It's okay to say I don't know, so let's add 1 billion tokens for the
        next 30 days and let's evaluate what we are doing right, what we are
        doing wrong, and adjust as time goes on. Then we refine the budget
        together, the CEO and chairman."

    So: an allowance, an end date, a question in writing, and — the part that
    makes it more than a bigger cap — **the platform writes the findings**. The
    measured median, the daily series and the cost of each kind of run are
    computed from the ledger at close, at zero tokens, and no agent verb can
    touch them. It is the commitment doctrine applied to money: the CEO may
    ask the question, and only the arithmetic answers it.

    `checkpoint_hours` is the chairman's "adjust along the way". At each one
    the platform opens a SPEND_WINDOW flag, which the CEO cannot close by
    staying quiet — it closes by being raised in the boardroom with the numbers
    so far and a proposal. A window that is going to overspend its allowance
    before its end date raises the same flag early, for the same reason.
    """

    __tablename__ = "spend_windows"
    __table_args__ = (
        Index("ix_spend_windows_project_status", "project_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    # What we are trying to find out. Required, because a window without a
    # question is just a larger cap with extra steps.
    question: Mapped[str] = mapped_column(Text)
    # In the project's own billing unit — dollars under api_key, tokens under
    # a subscription. Stored as float so one column serves both.
    allowance: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(8))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime)
    # How often the platform makes the CEO report the measured number so far.
    checkpoint_hours: Mapped[float] = mapped_column(Float, default=168.0)
    last_checkpoint_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(16), default=SpendWindowStatus.OPEN)
    opened_by_user_id: Mapped[str] = mapped_column(String(40))
    # Platform-computed at close and never from input: the measured series, the
    # median day, the per-run-kind profile, the total. This is the whole point
    # of the object — a number at the end that nobody argued into existence.
    findings: Mapped[dict | None] = mapped_column(JSONVariant)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    close_reason: Mapped[str | None] = mapped_column(Text)
    # Every adjustment the chairman made while it ran, appended. "Adjust along
    # the way" is only trustworthy if the adjustments are on the record.
    adjustments: Mapped[list] = mapped_column(JSONVariant, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Flag(Base):
    """An obligation to speak, with a state of its own.

    Every mechanism around CEO-to-chairman reporting was a brake. The cadence
    refuses an unprompted status between slots; `may_post_status_now` is a
    permission. Nothing anywhere ever REQUIRED the CEO to say anything — so a
    CEO sitting on a red cycle with a closed cadence slot could not raise it at
    all, and one that was asked directly answered "solid progress".

    A flag is the accelerator. The platform opens it from arithmetic or a
    failed test, it overrides the cadence brake, and it cannot be closed by
    staying quiet: it closes by being RAISED in the boardroom with a proposal.
    Closing one dismissively is a CEO doing its job well — "failed on one
    order, the warehouse API was down four minutes, it self-healed, no action
    needed". Leaving it open while reporting that all is well is the thing that
    is no longer available.

    `raised_at` is null while the chairman has not been told, and the project
    screen shows that count. It is the property that makes the mechanism
    trustworthy without being trusted: if it ever stops working, the silence is
    visible rather than inferred.
    """

    __tablename__ = "flags"
    __table_args__ = (
        Index("ix_flags_project_status", "project_id", "status"),
        # One live flag per condition. A capacity forecast recomputed every
        # tick must not open a new flag every tick.
        #
        # LIVE, and that word is the whole of this index. As a plain unique
        # constraint it also bound the resolved rows, which are history rather
        # than a condition — so the second time a subject was flagged, closed,
        # flagged again and closed again, the close collided with the first
        # one's row and the write blew up. That is a 500 on the chairman's
        # "noted" button, and an IntegrityError in the janitor for any forecast
        # that flickers in and out of feasible. Resolved rows must be free to
        # accumulate: a cycle can be missed, answered, and missed again.
        Index("uq_flags_live_condition", "project_id", "kind", "subject_id", "status",
              unique=True,
              sqlite_where=text("status <> 'resolved'"),
              postgresql_where=text("status <> 'resolved'")),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    kind: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(16), default=FlagStatus.OPEN)
    # What the flag is ABOUT — a requirement id, a cycle id — so recomputing
    # the same condition finds the existing flag instead of opening another.
    subject_id: Mapped[str | None] = mapped_column(String(40))
    summary: Mapped[str] = mapped_column(Text)
    # The numbers that tripped it, kept verbatim. A flag that cannot show its
    # arithmetic is an alarm, and an alarm is just anxiety forwarded to the
    # chairman's desk.
    arithmetic: Mapped[dict | None] = mapped_column(JSONVariant)
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    raised_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    raised_in_message_id: Mapped[str | None] = mapped_column(String(40))
    # What the CEO proposed doing about it. Required to raise: the value a CEO
    # adds over a monitoring alert is the sentence after the bad news.
    proposal: Mapped[str | None] = mapped_column(Text)
    # When the chairman said something back, and what he said. A flag that has
    # been raised is news he has been given, not news he has read: without this
    # the raised ones sat on his desk under "needs you" with nothing to press,
    # and the two kinds nothing auto-resolves sat there permanently.
    acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    acknowledgement: Mapped[str | None] = mapped_column(Text)
    resolution: Mapped[str | None] = mapped_column(Text)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Review(Base):
    """The status meeting, owned by the janitor.

    The chairman described it: "status meetings to discuss the goal and
    hypothesis, alter requirements, and judge whether the deadline will be
    met" — all three clauses. The janitor opens, numbers and emails it with
    ZERO tokens, which is why it happens on exactly the days a busy project
    most needs it. The CEO's variance narrative arrives on a reserved
    governance run (wake reason kind ``review``). The chairman's six levers —
    cut, add, reword, move the date, raise the budget, accept the slip — are
    each recorded here with his reason, disclosed to the CEO, and shown at
    the NEXT review beside what they bought.

    A review with an empty board still fires: that is the one he most needs.
    """

    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("project_id", "number"),
        Index("ix_reviews_project_status", "project_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default=ReviewStatus.OPEN)
    scheduled_for: Mapped[datetime] = mapped_column(UTCDateTime)
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    # The platform's snapshot at open: pace, commitment, budget, scope counts,
    # audit coverage since the last review, and the prior review's decisions
    # beside what they bought. Numbers come from the same producers every
    # screen reads; this is a frozen copy so the meeting's record cannot
    # drift as the week moves on.
    numbers: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    narrative: Mapped[str | None] = mapped_column(Text)
    recommended: Mapped[str | None] = mapped_column(Text)
    narrative_run_id: Mapped[str | None] = mapped_column(String(40))
    narrative_filed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # [{"kind", "reason", "detail", "at"}] — the levers pulled, in order.
    decisions: Mapped[list] = mapped_column(JSONVariant, default=list)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    closed_by_user_id: Mapped[str | None] = mapped_column(String(40))
    nag_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    escalated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


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
    kind: Mapped[str] = mapped_column(String(24))  # LedgerKind
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
    # Which call that wall was hit on, when, and when the same call then went
    # through. A resident chat session lives for days and retries: a brief
    # refused for length and refiled shorter left "last refused" on the
    # boardroom beside a CEO truthfully saying it was done, telling the
    # chairman to doubt the one report that was right.
    last_refusal_route: Mapped[str | None] = mapped_column(String(200))
    last_refusal_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_refusal_cleared_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cache_creation_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    # The same run priced from yaaf's model catalog (yaaf.runtimes.catalog).
    # Equal to cost_usd on a harness yaaf prices itself; on one that reports
    # its own bill, the check on the catalog — the health checklist compares
    # the two (docs/RUNTIMES.md). NULL when unpriced, and where cost_usd is.
    priced_usd: Mapped[float | None] = mapped_column(Float)
    # Pinned at lease time, not resolved at read time: a role repin or model
    # change must never silently re-attribute historical spend.
    model: Mapped[str | None] = mapped_column(String(64))
    effort: Mapped[str | None] = mapped_column(String(16))
    # Who served the model and what drove it (docs/RUNTIMES.md), pinned beside
    # the model for the same reason. NULL on every run from before they were
    # recorded — all of which were the Agent SDK on Anthropic.
    provider: Mapped[str | None] = mapped_column(String(32))
    harness: Mapped[str | None] = mapped_column(String(32))
    # How a failed run failed, as its harness classified it: "auth" |
    # "transient" | "fatal". Kept on the row because the credential-failure
    # pause reads the last three runs, not just this one.
    error_kind: Mapped[str | None] = mapped_column(String(16))
    # Did this run change anything? Incremented by the API layer on every
    # accepted write — a task claimed or closed, a message posted, evidence
    # filed, a memory written, scope moved. Zero means the run woke, thought,
    # and left the project exactly as it found it.
    produced: Mapped[int] = mapped_column(Integer, default=0)
    # Whether this run spends the work allowance. True at birth and set False
    # at finalize when `produced` is zero, because a wake cannot be known to be
    # empty until it has happened — the refund is necessarily retroactive.
    # Still logged, still billed to the ledger, still visible on the Runs page:
    # what it stops doing is crowding out work.
    metered: Mapped[bool] = mapped_column(Boolean, default=True)
    trace_path: Mapped[str | None] = mapped_column(Text)
    # Store uri of {"system_prompt", "context_pack"} — what the run actually
    # sent to the model, stashed beside the trace.
    context_path: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Account(Base):
    """The account registry (CEO Charter, stage E): every identity the project
    creates out in the world, on the record — record-only by the chairman's
    decision. No orphan signups: when a project is stopped for good, this is
    how its owner sees what exists and settles it. `owner` is situational —
    'project' when the account hangs off an identity the project owns (its
    email, its domain), 'chairman' when a responsible human is the name behind
    it — decided per account, in the boardroom, and written down here."""

    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("project_id", "service", "username"),
        Index("ix_accounts_project", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    service: Mapped[str] = mapped_column(String(200))       # "namecheap.com"
    username: Mapped[str] = mapped_column(String(200))      # login / email used
    owner: Mapped[str] = mapped_column(String(16), default="project")  # project|chairman
    # The stored secret holding this account's credential, when one exists —
    # a name, never a value; the registry links, it does not hold.
    credential_name: Mapped[str | None] = mapped_column(String(120))
    url: Mapped[str | None] = mapped_column(String(500))
    # What justified it: the requirement served, and anything worth knowing
    # at wind-down (plan tier, renewal date, "cancel via support email").
    requirement_id: Mapped[str | None] = mapped_column(String(40))
    note: Mapped[str | None] = mapped_column(Text)
    created_by_agent_id: Mapped[str | None] = mapped_column(String(40))
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
    # A TOOL_REQUEST approval that carries a grant IS the capability: approving
    # it is what lets data_fetch make one call the source's allowlist otherwise
    # refuses. {"source", "tool", "arguments_sha256"} — the digest is why an
    # approval cannot be spent on different arguments than the ones decided on.
    # Null on every other kind, and on tool requests filed as prose.
    grant: Mapped[dict | None] = mapped_column(JSONVariant)
    # One call, one approval. Set the moment the granted call is made, so a
    # replay finds it spent rather than approved.
    spent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # What the chairman needs to decide well, structured: the change set
    # (before → after), the rows it rests on, the estimated effect, whether it
    # can be undone, and the case against. Prose for a person, never part of
    # the grant — the digest binds the arguments, not the argument.
    disclosure: Mapped[dict | None] = mapped_column(JSONVariant)
    # A BRIEF approval's proposal: {"goal", "standing_orders", "retired",
    # "what_changed"}. Approving it is what applies it.
    brief: Mapped[dict | None] = mapped_column(JSONVariant)
    # A CAPACITY approval's dial: {"rule", "from", "to", "shared"}. Approving it
    # is what raises the dial. Sixteen were approved before this column existed
    # and none moved anything; one desk filed the same token-cap raise three
    # times, because each yes left the cap exactly where it was.
    capacity: Mapped[dict | None] = mapped_column(JSONVariant)


class HeadsUp(Base):
    """Something the chairman asked to be told, and has to say he saw.

    Three kinds of thing reach him from the CEO. Some he decides, and those are
    approvals: the button changes something. Some are information, and those
    belong in the deliverable or the daily brief, with no button at all. And
    some he asked for himself — "make sure I know tomorrow's weather" — which
    are neither: nothing to decide, but the CEO should know he saw it.

    Before this, the third kind had no verb and went out as an assist. A trading
    desk sent seventy-seven of them, many reading "No trade today. Place
    nothing.", each with an Approve and a Deny button. So a heads-up names the
    standing order it answers, and is refused when the brief holds none: that
    sentence is what separates something he asked to hear from news.
    """

    __tablename__ = "heads_ups"
    __table_args__ = (Index("ix_heads_ups_project", "project_id", "acknowledged_at"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    agent_id: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    # Quoted from the brief at filing, so a later brief cannot orphan the reason.
    standing_order: Mapped[str] = mapped_column(Text)
    message_id: Mapped[str | None] = mapped_column(String(40))
    acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ChairmanNote(Base):
    """Something the chairman wrote on a decision, held until the CEO answers it.

    His note used to ride the wake notice once and go nowhere. Sixty-four
    decisions carried one, and one of them — on a credential request — read
    "yes, please go ahead, every day": a standing order that no prompt ever
    carried again. So a note is a row the CEO sees on every run until it
    answers, and an answer that says the note applies beyond the one item is
    refused unless a brief written after it exists for him to confirm.
    """

    __tablename__ = "chairman_notes"
    __table_args__ = (
        Index("ix_chairman_notes_open", "project_id", "answered_at"),
        Index("ix_chairman_notes_source", "source_kind", "source_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    # approval | flag | heads_up
    source_kind: Mapped[str] = mapped_column(String(16))
    source_id: Mapped[str] = mapped_column(String(40))
    # What it was written on, in his words on the screen: "Approved: Need: GitHub
    # credential". The CEO reads this without fetching the item.
    about: Mapped[str] = mapped_column(String(400))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    answered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    reply: Mapped[str | None] = mapped_column(Text)
    # True: it applies beyond this one item, and the brief now carries it.
    standing: Mapped[bool | None] = mapped_column(Boolean)


class Ask(Base):
    """A question to the chairman that the next move depends on, with a clock.

    An approval is the wrong shape for this and is refused for it: it blocks on
    a human and carries a default-deny, so filing one for a decision he already
    delegated parks his own delegation back on his desk. The verb that refusal
    names is asking him in the boardroom — and until this table existed, that
    was a message and nothing else. No clock, no nag, no desk row, no state. A
    CEO with a finished deliverable and a closed cadence slot deferred the ask
    to a slot that never arrived, because closing the work that produced it
    emptied the board that was the only thing still waking anybody. Six hours
    of silence, on both sides, over a message that was written and ready.

    So an ask is a row. It never blocks — the CEO keeps working, which is the
    one property of asking that was always true and worth keeping. What is new
    is that it cannot be silent:

    * `answer_by` is a real moment, named by the CEO when it asks.
    * `default_action` is what happens if that moment passes, written down
      BEFORE the wait rather than reconstructed after it. A question whose
      asker cannot say what it will do unanswered is not a question, it is a
      deadlock with better manners.
    * The lapse notices the CEO — urgently, because that notice is the wake.
    * While it is OPEN the chairman sees it on his desk, so "he never answered"
      and "he was never asked" stop looking identical from his side.

    It closes the way a flag closes: by being acted on and said out loud, with
    the boardroom message as proof. Going quiet is not a way out of one.
    """

    __tablename__ = "asks"
    __table_args__ = (Index("ix_asks_project_status", "project_id", "status"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    asked_by_agent_id: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default=AskStatus.OPEN)
    # The moment the CEO said it would stop waiting. Capped by the constitution
    # so a CEO cannot grant itself a week of not deciding, nor five minutes of
    # theatre before doing what it wanted anyway.
    answer_by: Mapped[datetime] = mapped_column(UTCDateTime)
    # Exactly what happens at answer_by with no answer. Required, and quoted
    # back verbatim in the lapse notice: the CEO is held to the sentence it
    # wrote when it still did not know which way he would go.
    default_action: Mapped[str] = mapped_column(Text)
    asked_in_message_id: Mapped[str] = mapped_column(String(40))
    # He is nagged before the window closes, for the same reason an approval
    # nags: degrading is fine, degrading silently is not.
    nag_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    answered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    answered_in_message_id: Mapped[str | None] = mapped_column(String(40))
    # When it actually became the CEO's to finish — his reply, or the janitor
    # closing the window. Distinct from `answer_by`, which is only where the
    # window was nominally drawn, and the distinction is load-bearing: the one
    # free boardroom post an owed ask buys is spent by the CEO posting AFTER
    # this moment, so comparing against the nominal time would hand the free
    # post to a message written before the obligation existed.
    lapsed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    closed_in_message_id: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Secret(Base):
    """Envelope-encrypted values scoped to roles. An agent only ever receives
    the secrets its role's secret_scopes name, injected per-run."""

    __tablename__ = "secrets"
    __table_args__ = (
        UniqueConstraint("project_id", "name"),
        Index("uq_llm_credential_name", text("lower(name)"), unique=True,
              postgresql_where=text("llm_mode IS NOT NULL AND hidden_at IS NULL"),
              sqlite_where=text("llm_mode IS NOT NULL AND hidden_at IS NULL")),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(String(40))  # null => platform-level
    name: Mapped[str] = mapped_column(String(120))
    ciphertext: Mapped[str] = mapped_column(Text)
    llm_mode: Mapped[str | None] = mapped_column(String(16))
    # Which provider an LLM credential pays (yaaf.runtimes.providers); `llm_mode`
    # is the kind within it. Set on every row, LLM credential or not, because a
    # default is cheaper than a nullable column every reader has to second-guess.
    provider: Mapped[str] = mapped_column(String(32), default=DEFAULT_PROVIDER,
                                          server_default=DEFAULT_PROVIDER)
    role_scopes: Mapped[list] = mapped_column(JSONVariant, default=list)  # role slugs
    # "service" (role-granted, injected under its own name) vs "billing"
    # (attached as billing_secret_id, injected into EVERY run as the model
    # credential). The split is a refusal, not a hint: billing secrets refuse
    # role grants, billing_secret_id refuses service secrets — a WordPress
    # password can never become the model credential, or vice versa.
    kind: Mapped[str] = mapped_column(String(16), default="service")
    # Set => this credential IS a file, and this is what it is called on disk.
    # The granted role's env var then holds the PATH and never the bytes: a
    # service-account JSON, a PEM key, a kubeconfig is only usable as a file,
    # and the alternative is an agent writing a private key into a git clone
    # by hand. NULL => an ordinary string credential, injected as its value.
    #
    # Orthogonal to `kind` on purpose. `kind` is the grantability axis; being a
    # file says nothing about who may hold it. Folding file-ness into `kind`
    # would turn every existing `kind == "service"` test into a membership
    # check, which is the change spreading instead of staying put.
    filename: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    # An LLM credential retired from every menu without being destroyed.
    # Lifting per-project keys above projects made one managed credential per
    # historical key, so installs woke up choosing from a list mostly made of
    # keys whose projects had already ended. A hidden credential is off the
    # Settings list, off the project picker, off the Founder's menu and
    # unassignable — while the finished projects that spent through it keep
    # naming what paid for them. Refused while a live project still pays with
    # it, so hidden can never mean "a running project cannot be explained".
    # It is also why the name index is partial on this column: a name nothing
    # can show you is not a name worth reserving.
    hidden_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Where an LLM credential's provider is reached, for a provider whose
    # address is the credential's own (yaaf.runtimes.providers: a custom
    # OpenAI-compatible server). Not a secret, so not in the ciphertext.
    base_url: Mapped[str | None] = mapped_column(String(500))


class ProjectLlmCredential(Base):
    """The credential a project pays a *second* provider with.

    `Project.billing_secret_id` stays the project's billing credential — the
    one its meter is read from — and pays whichever provider it is for. A
    project whose seats run on more than one provider holds one row here per
    other provider (docs/RUNTIMES.md, M3). Every credential a project holds
    meters in its billing mode's unit (R4): one cap cannot be kept in two."""

    __tablename__ = "project_llm_credentials"
    __table_args__ = (UniqueConstraint("project_id", "provider"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    provider: Mapped[str] = mapped_column(String(32))
    secret_id: Mapped[str] = mapped_column(ForeignKey("secrets.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ModelPrice(Base):
    """An install's own price for a model, in USD per million tokens. Wins over
    the shipped catalog (yaaf.runtimes.catalog) for that model — which is how a
    provider yaaf ships no price for is priced at all, and how a local server
    is priced at zero."""

    __tablename__ = "model_prices"

    model: Mapped[str] = mapped_column(String(128), primary_key=True)
    input: Mapped[float] = mapped_column(Float)
    output: Mapped[float] = mapped_column(Float)
    cache_read: Mapped[float] = mapped_column(Float, default=0.0)
    cache_write: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PlanReading(Base):
    """What Anthropic last said was left on a subscription, harvested from runs.

    Not fetched. The Claude Agent SDK emits a `RateLimitEvent` carrying the
    same `five_hour` / `seven_day` windows the `/usage` screen draws, and every
    agent run already receives it on the token that is doing the spending —
    23,817 of them sat unread in our own trace files while a poller next door
    was being rate-limited asking Anthropic the same question. So the meter is
    a by-product of work already done: no request, nothing to throttle, and
    fresher than a poll because it arrives when the number actually moves.

    Keyed by a digest of the token rather than by credential, because one
    subscription routinely sits behind several credential rows and they all
    share one meter. Nothing here is a secret: percentages, reset times, and a
    status word.
    """

    __tablename__ = "plan_readings"

    token_fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: [{"key", "label", "utilization", "resets_at"}] — already normalized.
    windows: Mapped[list] = mapped_column(JSONVariant, default=list)
    #: "allowed" | "allowed_warning" | "rejected" — the CLI's own word for it.
    status: Mapped[str | None] = mapped_column(String(24))
    seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class McpClient(Base):
    """A model client that speaks MCP on the chairman's behalf.

    A third principal, and the reason it exists rather than reusing the admin
    token: the admin bearer is unscoped, unattributable and unrevokable
    individually, so handing it to a language model would give that model
    every verb the platform spends its architecture denying to models — and
    would record the chairman as the one who used them.

    This row is the narrowing. `scopes` decides which verbs the client can
    reach at all, and every write made through it is attributed as
    machine-mediated for good. What it can *see* is not stored here: it is
    every project its chairman chairs, read at call time, so founding a
    project needs no second trip through a consent screen. Like a run token,
    only the hash is stored: the plaintext is shown once at creation and
    lives thereafter in the client's own config.
    """

    __tablename__ = "mcp_clients"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    #: What it is, for the audit trail: "dom-laptop", "claude-desktop".
    name: Mapped[str] = mapped_column(String(80))
    #: The chairman this client acts for. Writes are attributed here, beside
    #: the client name — never to the client alone, and never to the user alone.
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    #: Subset of read / steer / create / configure. `read` is implied by all.
    scopes: Mapped[list] = mapped_column(JSONVariant, default=list)
    #: Set when the grant came from a consent screen rather than the CLI, so
    #: the record can tell "the chairman typed a command" from "the chairman
    #: clicked approve in a browser".
    oauth_client_id: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    #: Revocation is a timestamp rather than a delete: the audit trail names
    #: this client forever, so the row it names has to survive.
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class McpToken(Base):
    """One credential under a grant. Tokens rotate; the grant does not.

    OAuth needs refresh, and refresh means the string a client holds changes
    every hour or so. Attribution cannot ride on that: the audit trail names a
    client for good, so the name lives on :class:`McpClient` and only the
    secret lives here. A CLI-minted bearer is the same shape with no expiry —
    one code path validates both, so a rule proven for one holds for the other.
    """

    __tablename__ = "mcp_tokens"
    __table_args__ = (Index("ix_mcp_tokens_hash", "token_hash", unique=True),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("mcp_clients.id"))
    #: static (CLI-minted, no expiry) | access | refresh
    kind: Mapped[str] = mapped_column(String(16), default="static")
    token_hash: Mapped[str] = mapped_column(String(64))
    #: The RFC 8707 audience this token was minted for. A token presented at a
    #: resource it was not minted for is refused even though it is otherwise
    #: valid — the rule that stops one stolen from another service being
    #: replayed here. Empty means a CLI token, which names no audience.
    resource: Mapped[str] = mapped_column(String(500), default="")
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    #: Refresh rotation: the id of the token that replaced this one. Presenting
    #: a rotated refresh token is how a stolen one announces itself.
    rotated_to: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class OAuthClient(Base):
    """A registered MCP client — from RFC 7591 dynamic registration, or typed
    in by a chairman whose provider does not do DCR.

    Registering grants nothing. It says only "this software exists and these
    are the URLs it may be sent back to"; the consent screen is where any
    authority is handed over.
    """

    __tablename__ = "oauth_clients"
    __table_args__ = (Index("ix_oauth_clients_client_id", "client_id", unique=True),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64))
    #: Public clients (PKCE, no secret) are the norm; a secret is stored hashed
    #: for the few that ask for one.
    client_secret_hash: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(120))
    redirect_uris: Mapped[list] = mapped_column(JSONVariant, default=list)
    #: dcr | manual
    source: Mapped[str] = mapped_column(String(16), default="dcr")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class OAuthCode(Base):
    """One authorization code in flight: what consent granted, waiting to be
    exchanged. Single-use and short-lived — a code that outlives the redirect
    it was issued for is a credential nobody is watching."""

    __tablename__ = "oauth_codes"
    __table_args__ = (Index("ix_oauth_codes_hash", "code_hash", unique=True),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    oauth_client_id: Mapped[str] = mapped_column(String(40))
    user_id: Mapped[str] = mapped_column(String(40))
    scopes: Mapped[list] = mapped_column(JSONVariant, default=list)
    #: PKCE, S256 only. A code with no challenge is never issued.
    code_challenge: Mapped[str] = mapped_column(String(128))
    redirect_uri: Mapped[str] = mapped_column(String(500))
    resource: Mapped[str] = mapped_column(String(500), default="")
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class McpConfirmation(Base):
    """A write this client previewed and may now perform, once.

    Rule 4 of docs/MCP_SERVER.md: an irreversible write cannot happen in one
    call. The preview issues this row; the write spends it. Server-side rather
    than left to the host client's permission prompt, because a prompt is a
    suggestion and this platform's whole argument is that rules are not.

    The fingerprint is a hash of the tool and its exact arguments, so the
    second call cannot be a different write wearing the first one's approval.
    """

    __tablename__ = "mcp_confirmations"
    __table_args__ = (Index("ix_mcp_confirmations_token", "token_hash", unique=True),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(40))
    token_hash: Mapped[str] = mapped_column(String(64))
    fingerprint: Mapped[str] = mapped_column(String(64))
    tool: Mapped[str] = mapped_column(String(40))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_project_at", "project_id", "at"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(String(40))
    actor: Mapped[str] = mapped_column(String(ACTOR_LEN))  # user:<id> | agent:<id> | system:<name>
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


class PlatformSetting(Base):
    """Product-wide configuration, above any project.

    Every other setting in this system belongs to a project, because every
    other setting binds a project. These bind the *platform* — the things that
    have to be true before a project exists at all, starting with how the
    Founder pays for the conversation in which a project gets designed.

    Values may be encrypted (`is_secret`), in which case `value` holds
    ciphertext and only the server ever decrypts it. A platform setting is
    never injected into a sandbox and never reaches a project's agents: a
    project still pays its own way with its own attached credential.
    """

    __tablename__ = "platform_settings"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    key: Mapped[str] = mapped_column(String(120), unique=True)
    value: Mapped[str] = mapped_column(Text)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class FoundingThread(Base):
    """One conversation with the Founder, and the project it is drafting.

    This is deliberately not a Project. Nothing here is a project yet: no
    agents exist, no money can be spent, no rule binds anyone. It is a
    transcript and a draft `project_setup` bundle — the same object the
    importer accepts and the form posts — carried together so the chairman can
    argue with a proposal instead of filling in a form.

    The draft is replaced wholesale each time the Founder proposes; the
    transcript is what makes the revision legible, and it is handed to the CEO
    as its day-one briefing when the project is finally created.
    """

    __tablename__ = "founding_threads"
    __table_args__ = (Index("ix_founding_user_at", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(200), default="")
    # Anthropic message dicts, verbatim — thinking blocks included, because a
    # thinking block replayed to the same model must come back unchanged.
    transcript: Mapped[list] = mapped_column(JSONVariant, default=list)
    # The current proposal: {"project": {...}, "roles": [...]}, or null before
    # the Founder has drafted anything.
    draft_bundle: Mapped[dict | None] = mapped_column(JSONVariant)
    # Set once the chairman turns a draft into a real project.
    created_project_id: Mapped[str | None] = mapped_column(String(40))
    # A turn runs in a background thread and takes minutes, so two questions
    # outlive the request that started it and cannot be answered from the
    # in-memory progress store alone.
    #
    # Stamped when a turn starts, cleared when it ends. Still set with nothing
    # running means the process died mid-turn — which is otherwise a thread
    # that silently lost five minutes of work and looks merely idle.
    turn_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # The last failure, kept because people refresh the moment something looks
    # wrong, and an error that lived only in the browser would not survive it.
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
