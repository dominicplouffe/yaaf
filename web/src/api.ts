// Thin API client. The browser holds no credential of its own: the server sets
// an HttpOnly session cookie at sign-in, and every same-origin request carries
// it without this file ever seeing it. That is the point — a script that runs
// in this page cannot read a cookie it cannot see, so it cannot carry one away.
// A 401 means the session is gone, and bounces to the sign-in screen.

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** FastAPI reports validation failures as a list of {loc, msg} objects, which
 *  String()s down to "[object Object]". Flatten it into something a human can
 *  act on: "body.approve: Field required". */
function errorText(data: unknown, status: number): string {
  const detail = (data as { detail?: unknown; error?: unknown })?.detail;
  if (Array.isArray(detail)) {
    const lines = detail
      .map((item) => {
        const { loc, msg } = (item ?? {}) as { loc?: unknown[]; msg?: string };
        if (!msg) return null;
        const where = Array.isArray(loc) ? loc.join(".") : "";
        return where ? `${where}: ${msg}` : msg;
      })
      .filter(Boolean);
    if (lines.length) return lines.join("; ");
  }
  if (typeof detail === "string" && detail) return detail;
  const error = (data as { error?: unknown })?.error;
  if (typeof error === "string" && error) return error;
  return `HTTP ${status}`;
}

/** A 401 from these is the server's answer about a password somebody just
    typed, not news that the session died. Bouncing to the sign-in screen
    there would throw away the form a person mistyped one field of — on the
    account page, that was everything they had filled in. */
const PASSWORD_CHECKS = ["/auth/login", "/auth/password"];

function signedOut(path: string) {
  if (PASSWORD_CHECKS.some((p) => path.startsWith(p))) return;
  // The two screens that exist for people without a session. Redirecting off
  // a setup link would strand the person the link was made for.
  if (location.hash.startsWith("#/login") || location.hash.startsWith("#/setup")) return;
  location.hash = "#/login";
}

export async function api<T = unknown>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const response = await fetch(path, {
    method,
    // Same-origin is the browser's default; said out loud because the cookie
    // riding along is now the whole of how this app authenticates.
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data: unknown = null;
  try {
    data = await response.json();
  } catch {
    /* empty body */
  }
  if (!response.ok) {
    if (response.status === 401) signedOut(path);
    throw new ApiError(response.status, errorText(data, response.status));
  }
  return data as T;
}

export const get = <T = unknown>(path: string) => api<T>("GET", path);
export const post = <T = unknown>(path: string, body?: unknown) => api<T>("POST", path, body);
export const patch = <T = unknown>(path: string, body?: unknown) => api<T>("PATCH", path, body);
export const put = <T = unknown>(path: string, body?: unknown) => api<T>("PUT", path, body);
export const del = <T = unknown>(path: string) => api<T>("DELETE", path);

/** GET an endpoint that returns plain text (run traces) — no JSON parsing. */
export async function getText(path: string): Promise<string> {
  const response = await fetch(path, { credentials: "same-origin" });
  if (!response.ok) {
    if (response.status === 401) signedOut(path);
    let detail = `HTTP ${response.status}`;
    try {
      detail = errorText(JSON.parse(await response.text()), response.status);
    } catch {
      /* not a JSON error body */
    }
    throw new ApiError(response.status, detail);
  }
  return response.text();
}

// ── people: who is signed in, and what they hold ───────────────────────────────

/** GET /auth/me. `via` says which credential made the request — always
    "session" from this app, which is why it is not shown anywhere. */
export interface Me {
  id: string;
  email: string;
  display_name: string;
  is_admin: boolean;
  via: string;
}

/** GET /auth/setup — who a setup link is for, read before it is spent. */
export interface SetupInfo {
  email: string;
  display_name: string;
  purpose: "invite" | "reset" | "bootstrap";
}

/** A personal token as listed. The value itself is never in this shape: it
    exists once, in the answer to the POST that made it. */
export interface PersonalToken {
  id: string;
  name: string;
  created_at: string | null;
  expires_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

export interface MintedToken extends PersonalToken {
  token: string;
}

export interface SignedInSession {
  id: string;
  user_agent: string | null;
  created_at: string;
  last_seen_at: string;
  /** The browser this list is being read in. */
  current: boolean;
}

/** One row of GET /admin/users — instance admins only. */
export interface UserRow {
  id: string;
  email: string;
  display_name: string;
  is_admin: boolean;
  active: boolean;
  /** False until the person has followed their invite link. */
  has_password: boolean;
  created_at: string | null;
  deactivated_at: string | null;
}

/** POST /admin/users and POST /admin/users/{id}/setup-link. The setup fields
    are absent when the invite found an account that already existed. */
export interface IssuedLink extends UserRow {
  existing?: boolean;
  setup_url?: string;
  setup_path?: string;
  setup_expires_in_hours?: number;
  purpose?: "invite" | "reset";
}

/** The link to hand on, built from the origin this page is being read on.
    The server's own `setup_url` can name a host only its containers resolve
    (`http://api:8080` inside compose); the browser always knows where it is. */
export function setupLink(issued: IssuedLink): string | null {
  return issued.setup_path ? `${location.origin}${issued.setup_path}` : null;
}

/** A person's standing on a project. Null on a project payload means the
    reader is the instance admin and not a member — they can read it because
    they run the box, and decide nothing on it. */
export type ProjectRole = "chairman" | "co_chair" | "observer";

export const ROLE_LABEL: Record<ProjectRole, string> = {
  chairman: "Chairman",
  co_chair: "Co-chair",
  observer: "Observer",
};

export interface PersonRef {
  id: string;
  email: string;
  display_name: string;
}

/** One row of GET /admin/projects/{id}/members, chairman first. */
export interface MemberRow {
  user_id: string;
  email: string;
  display_name: string;
  role: ProjectRole;
  active: boolean;
}

/** POST /admin/projects/{id}/chairman. */
export interface ChairHandover {
  chairman: PersonRef;
  members: MemberRow[];
}

// ── shared shapes (mirrors the observe/admin API) ──────────────────────────────

export interface ProjectSummary {
  id: string;
  slug: string;
  name: string;
  status: string;
  goal: string;
  my_role: ProjectRole | null;
  can_steer: boolean;
}

/** The one sentence for why this project is quiet, written server-side beside
    the ladder that decided it.

    It used to be written here instead, in three separate switch statements. The
    backend grew a `pacing` gate; none of the three learned about it, so a brake
    that stops work every hour rendered as "nothing blocking — a run should
    start shortly". Nothing in the browser decides this any more. */
export interface ActivityBrief {
  tone: "ok" | "warn" | "serious" | "critical";
  chip: string;
  /** Tile-sized, lowercase, no trailing period. */
  short: string;
  headline: string;
  detail: string;
  /** Does this deserve the box above the tabs? False when the project is
      simply working — a banner that fires when things are fine teaches the
      reader to stop reading it. */
  banner: boolean;
  /** Set only on briefs that are waiting out a clock, and only those: together
      with `clears_at` it is what makes a banner dismissible. */
  sig: string | null;
  clears_at: string | null;
}

/** How many wakes that only read messages the project may spend, and has. */
export interface InboxReading {
  used_today: number;
  cap_today: number;
  used_this_hour: number;
  cap_this_hour: number;
}

/** GET /observe/projects/:id/inbox — every agent's unread messages, newest
    first. The same rows the dispatcher counts, so this and the banner agree. */
export interface InboxView {
  unread: number;
  reading: InboxReading;
  agents: {
    id: string;
    name: string;
    kind: string;
    team_name: string | null;
    unread: number;
    messages: { id: string; channel_id: string; channel: string; from_agent: string | null;
                from_user: string | null; from_name: string | null; body: string; at: string }[];
  }[];
}

/** Why work is or isn't starting — the dispatcher's own gates, read-only.
    `blocked_by` names the first gate stopping a run, and is null only when
    there is genuinely nothing to do. Prefer `brief` for anything you render;
    `blocked_by` is for highlighting the tile the gate belongs to. */
export interface Activity {
  blocked_by:
    | "daily_runs"
    | "pacing"
    | "governance_reserve"
    | "concurrency"
    | "agents_busy"
    | "wake_ceiling"
    | "coalescing"
    | "scheduled"
    | "no_lead"
    | "paused_agents"
    | "stood_down_agents"
    | "stalled_requirement"
    | "rework_owed"
    | "awaiting_chairman"
    | "awaiting_ceo"
    | "blocked_tasks"
    | "checks_lost"
    | "checking"
    | "review_owed"
    | "delivery_ready"
    | "inbox_runs"
    | null;
  brief: ActivityBrief;
  daily_runs: { used: number; cap: number; resets_at: string };
  /** The trailing-hour brake. Clears run by run rather than resetting. */
  pacing: { used: number; cap: number; frees_at: string | null };
  /** The slice of the day only a review or a re-forecast may spend. */
  governance_reserve: { reserved: number; ordinary_cap: number; active: boolean };
  /** When the hourly ceiling next lets someone through. `frees_at` is null
      when nothing is actually held behind it. */
  wake_ceiling: { cap: number; frees_at: string | null; agents_held: number };
  /** Notices and messages waiting for a third, or for the oldest to age out. */
  coalescing: { ripe_at: string | null; agents_held: number };
  concurrency: { in_flight: number; cap: number };
  /** The reading lane: wakes that only read messages, on their own ceiling. */
  inbox_runs: InboxReading;
  waiting: {
    /** claimable + gated. Kept because "how much is open" is still a question;
        never render it as "ready to run" — that was the original lie. */
    ready_tasks: number;
    claimable_tasks: number;
    /** Ready, but carrying a start time that has not arrived. */
    gated_tasks: number;
    claimed_tasks: number;
    blocked_tasks: number;
    /** Finished work the platform is still verifying. Counted so a board
        holding only checking rows reads as work in hand, not as empty. */
    checking_tasks: number;
    reviewing_tasks: number;
    notices: number;
    /** Messages agents would wake to read — the rows the agent inbox lists. */
    unread: number;
    agents_with_work: number;
  };
  /** Work that exists but the dispatcher cannot reach. */
  board: {
    claimable: number;
    gated: number;
    next_gate_at: string | null;
    blocked: number;
    /** Rows with the platform's own checker (tasks + command requirements). */
    checking: number;
    /** Finished work waiting on a read by somebody who did not write it. */
    reviewing: number;
    /** The wedged subset: checks whose run no longer exists anywhere. The
        janitor requeues these itself; named here so the quiet has a name. */
    checks_lost: string[];
    leaderless_teams: string[];
    /** Seats the chairman paused: an intervention, and the fix is to resume. */
    paused_agents: string[];
    /** Seats whose role finished. Opposite fix — move the work, do not resume
        somebody whose job is over. */
    stood_down_agents: string[];
    stalled_requirements: string[];
    rework_owed: string[];
    proposed_requirements: string[];
    /** Which of the pending decisions the authority dial hands to the CEO —
        "scope", "verdicts". Empty under the Supervised dial. */
    ceo_decides: string[];
    observable_checking: string[];
    plan_open: boolean;
    unreachable: string | null;
  };
  agents: {
    id: string;
    name: string;
    kind: string;
    busy: boolean;
    wakes_last_hour: number;
    wake_cap: number;
    wake_slot_frees_at: string | null;
    pending: number;
    /** What the total is made of. `pending` alone reads like a task count and
        is not one — it is work + notices + unread, and a non-zero value means
        this agent will be woken and will spend a run. */
    pending_detail: {
      work: number;
      notices: number;
      unread: number;
      heartbeat: number;
    };
    /** When this agent's batched wake lands, if one is being held. */
    batching_until: string | null;
  }[];
}

export interface Dashboard {
  id: string;
  slug: string;
  name: string;
  status: string;
  status_reason: string | null;
  goal: string;
  /** The rest of the brief every agent reads under the goal: the chairman's
      current orders, and the ones he took back. */
  standing_orders: string[];
  retired_orders: string[];
  brief_confirmed_at: string | null;
  billing_mode: string;
  /** explore | deliver | operate — decides whether a Plan exists at all. */
  mode: string;
  month_to_date: {
    usd: number;
    tokens_in: number;
    tokens_out: number;
    cache_creation: number;
    cache_read: number;
  };
  caps: { monthly_usd_cap: number; monthly_token_cap: number };
  open_tasks: number;
  queue_ceiling: number;
  external_evidence_in_window: number;
  /** Yesterday, as a pair: what the project spent and what the outside world
      verifiably did about it. They travel together or not at all — either one
      alone is a number that flatters. */
  spend_24h: number;
  evidence_24h: number;
  drought_active: boolean;
  runs: { running: number; queued: number };
  activity: Activity;
  /** True while the resident CEO chat session is live — boardroom replies
      arrive in seconds instead of via queued runs. */
  chat_live: boolean;
  pending_approvals: number;
  /** Requirements the CEO proposed and is parked on — a decision only the
      chairman can make, taken on the Plan tab. */
  requirements_proposed: number;
  /** Boardroom messages the reader has not seen yet — theirs, not the
      project's. Null for a reader with no seat on this project. */
  boardroom_unread: number | null;
  ceo_judgment: {
    tasks_created: number;
    tasks_with_verified_external: number;
    conversion: number | null;
  };
  constitution: Record<string, unknown>;
  /** Explore's bounded window, if the chairman set one. */
  deadline: string | null;
  /** Deliver, while running: what still stands between here and COMPLETED.
      Empty array = ready for the chairman's confirmation. Null elsewhere. */
  delivery_outstanding: string[] | null;
  /** Operate: the open turn of the rhythm. */
  cycle: {
    ordinal: number;
    opened_at: string;
    goal: string | null;
    due_at: string | null;
    due_source: { kind: string; url: string | null; note: string | null } | null;
    /** When this turn closes and is judged: the due-by if the world set one,
        the constitution's rhythm otherwise. */
    rolls_at: string;
    /** The platform's own arithmetic on whether the open work reaches the date.
        Four numbers and never a point estimate — see yaaf/server/capacity.py. */
    capacity: {
      available: number;
      needed: number;
      retry_reserve: number;
      slack: number;
      chain_hours: number;
      hours_left: number;
      feasible: boolean;
      why: string;
      basis: string;
    } | null;
  } | null;
  integrations: Record<string, unknown>;
  github_repo: string | null;
  billing_secret_id: string | null;
  billing_secret_name: string | null;
  /** What pays each provider, the billing credential first. Never a value. */
  credentials?: { provider: string; label: string; credential_id: string; name: string;
                  bills: string; billing: boolean }[];
  /** Drought-clock anchors: the UI counts toward the refusal. */
  created_at: string;
  last_countable_evidence_at: string | null;
  memory: MemoryUsage;
  /** The reader's standing here. Null: the instance admin, reading a project
      they are not a member of. */
  my_role: ProjectRole | null;
  /** May decide and configure — the chairman or a co-chair. */
  can_steer: boolean;
  /** May decide who decides — the chairman alone. */
  can_chair: boolean;
  chairman: PersonRef;
}

/** How full each curated memory scope is, counted the same way the refusal
    counts it. A full scope refuses every member's writes and only its curator
    can clear it, so the curator's name travels with the number. */
export interface MemoryScopeUsage {
  scope: "project" | "team";
  team_id: string | null;
  name: string;
  live: number;
  cap: number;
  curator?: string | null;
}

export interface MemoryUsage {
  warn_ratio: number;
  scopes: MemoryScopeUsage[];
}

/** One rule from GET /admin/constitution/schema — enough to render an editor
    for it. Generated from the pydantic model, so a new rule is editable the
    moment it exists rather than when someone remembers to list it here. */
export interface ConstitutionField {
  key: string;
  label: string;
  help: string;
  group: string;
  /** The tab this rule sits under. Decided server-side beside the model, so the
      editor never keeps a list of its own to go stale. */
  theme: string;
  /** Tab order — declaration order would open on work discipline, not Money. */
  theme_rank: number;
  type: "integer" | "number" | "boolean" | "string";
  minimum: number | null;
  maximum: number | null;
  /** The values a string rule accepts — a menu instead of a guess. */
  choices: string[] | null;
  /** What the rule would be if nobody had touched it. */
  default: unknown;
}

/** One row of GET /observe/projects/{id}/health — a way the configuration can
    be silently wrong, with a plain-language fix. */
export interface HealthCheck {
  key: string;
  status: "ok" | "warn" | "fail";
  detail: string;
  where: string | null;
  action: { kind: string; team_id: string } | null;
  /** This finding's identity: its kind and words with the numbers masked, so
      it survives a count ticking over and changes when the finding does. */
  sig: string;
  /** When the reader put this suggestion away, or null. Only warnings can be. */
  acked_at: string | null;
}

export interface ProjectHealth {
  ok: boolean;
  all_ok: boolean;
  checks: HealthCheck[];
}

/** What the platform made in Slack for a project, room by room. Returned by
    project creation (when an app-wide Slack connection is configured and auto-create
    is on — the boardroom only) and by the "create missing channels" button, which
    makes the rest.

    `deferred` is the rooms this run deliberately left alone: on the creation path
    that is leadership and every team room, so the screen can say they are a click
    away rather than leaving a chairman to wonder where they went. */
export interface SlackProvision {
  ok: boolean;
  visibility?: string;
  detail: string;
  channels: {
    key: string;
    label: string;
    name: string;
    channel_id: string | null;
    created: boolean;
    already_mapped: boolean;
    invited: boolean;
    error?: string;
  }[];
  deferred?: { key: string; label: string; name: string }[];
}

/** A project data source (MCP server) as stored in dash.integrations["mcp_servers"]. */
export interface McpServerEntry {
  name: string;
  url_secret: string;
  bearer_secret?: string;
  roles: string[];
  url_host: string;
  url_port: number;
  /** inline = mounted as mcp__<name>__* tools (results enter the model's
      context); bulk = reachable only via data_fetch, which writes results to a
      workspace file. Absent on sources saved before the field existed. */
  access?: "inline" | "bulk";
  /** Absent on sources saved before OAuth existed: a stored token then means
      bearer, nothing means an open server. */
  auth?: "none" | "bearer" | "oauth";
  /** OAuth connection state, present when auth === "oauth". The tokens
      themselves live in encrypted secrets; this is only what the page shows. */
  oauth?: {
    status: "needs_auth" | "pending" | "connected" | "needs_reauth";
    expires_at?: string | null;
    client_id?: string;
    scope?: string | null;
    error?: string;
  };
  last_check?: { ok: boolean; detail: string; at: string };
}

export interface TaskRow {
  id: string;
  title: string;
  status: string;
  team_id: string;
  team_name: string | null;
  parent_task_id: string | null;
  assignee: string | null;
  claimed_by: string | null;
  /** Agent name of whoever holds the task (claimed or assigned). */
  holder_name: string | null;
  external: boolean;
  success_test: { type: string; command?: string; target?: string };
  evidence: string | null;
  check_output: string | null;
  blocked_reason: string | null;
  /** A start time the CEO set. Until it passes the task is READY but nobody
      may claim it and nobody is woken for it — so it must never render as
      plain "ready". */
  not_before: string | null;
  /** The bet this work serves; tasks inherit meaning — and mortality — from it. */
  hypothesis_id: string | null;
  /** Chairman spot-audit verdict on the completion; null = never audited. */
  audit_status: string | null;
}

export interface MessageRow {
  id: string;
  from_agent: string | null;
  from_user: string | null;
  from_name?: string | null;
  thread_id: string | null;
  frozen?: boolean;
  body: string;
  at: string;
}

export interface ChannelRow {
  id: string;
  kind: string;
  team_id: string | null;
  team_name: string | null;
}

/** One remembered fact — project scope is curated by the CEO, team scope by
    the team's lead. */
export interface MemoryRow {
  id: string;
  scope: "project" | "team";
  team_id: string | null;
  kind: string;
  subject: string;
  body: string;
  at: string;
}

export interface RunRow {
  id: string;
  agent_id: string | null;
  agent_name: string | null;
  team_name: string | null;
  kind: string;
  status: string;
  trigger: Record<string, unknown>;
  attempt: number;
  tokens_in: number;
  tokens_out: number;
  cache_creation: number;
  cache_read: number;
  model: string | null;
  effort: string | null;
  cost_usd: number | null;
  refusals: number;
  /** The last wall this run hit — set whenever the platform refused a call. */
  last_refusal: string | null;
  last_refusal_at: string | null;
  /** When the same call that was refused later went through in this run. A
      retry that worked is not a reason to doubt what the agent then said. */
  last_refusal_cleared_at: string | null;
  summary: string | null;
  has_trace: boolean;
  has_context: boolean;
  /** Last lease heartbeat. The only thing that separates a live resident chat
      session from a hung run, since both read as "running" for hours. */
  heartbeat_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface SpendBucket {
  key: string;
  label: string;
  runs: number;
  tokens_in: number;
  tokens_out: number;
  cache_creation: number;
  cache_read: number;
  usd: number;
}

export interface SpendReport {
  group_by: string;
  days: number;
  window: "days" | "month";
  since: string;
  rows: SpendBucket[];
}

export interface AmendmentRow {
  id: string;
  reason: string;
  changes: Record<string, unknown>;
  status: "pending" | "applied" | "cancelled";
  proposed_at: string;
  effective_at: string;
  disclosed: boolean;
}

/** One decision the CEO made on its own authority. Never pending, never
    expiring — the disclosure half of the autonomy dial, as a record you read
    and can reverse rather than a request that blocks you. */
export interface CeoDecisionRow {
  id: string;
  at: string;
  action: string;
  /** "the plan" | "the date" | "a credential" — what it touched. */
  about: string;
  by: string;
  what: string;
  reason: string | null;
  detail: Record<string, unknown>;
}

export interface ApprovalRow {
  id: string;
  kind: string;
  title: string;
  body: string;
  undo?: string | null;
  amount_usd: number | null;
  status: string;
  requested_by: string;
  expires_at: string;
  decided_at: string | null;
  /** The chairman's note — the agent reads it, so history shows it. */
  decision_note: string | null;
  /** What a data change would do, filed with it: every before → after, how
      much it touches, an estimate, whether it can be undone, the case against.
      Null on every kind but a data write, and on one filed without it. */
  disclosure?: ApprovalDisclosure | null;
  /** A brief only: the direction the CEO wrote back for you to confirm.
      Confirming it replaces the goal and the standing orders. */
  brief?: BriefDraft | null;
  /** Set on a data write: the one call approving it buys. A need for a
      credential is also a tool_request, and carries none. */
  grant?: { source: string; tool: string; arguments_sha256: string } | null;
  capacity?: CapacityAsk | null;
  /** Your note, and the CEO's answer to it once given. */
  held_note?: HeldNote | null;
}

/** The platform files "what happens without it" as the last line of every
    request it composes (approval_request, capacity_request). Split off, it
    goes under "if you say no" in the CEO's own words, instead of sitting at
    the foot of the case beside a generic sentence that guessed at it. */
const WITHOUT = "What happens without it: ";

export function splitWithout(body: string): [string, string | null] {
  const at = body.lastIndexOf(WITHOUT);
  if (at < 0) return [body, null];
  return [body.slice(0, at).trimEnd(), body.slice(at + WITHOUT.length).trim()];
}

export interface BriefDraft {
  goal: string;
  standing_orders: string[];
  retired: string[];
  what_changed?: string;
}

type Cell = string | number | boolean | null;

export interface ApprovalDisclosure {
  category?: string | null;
  change_set?: { entity: string; field: string; before: Cell; after: Cell }[];
  scope_counts?: Record<string, number>;
  evidence?: {
    columns: string[]; rows: Cell[][];
    period_start?: string | null; period_end?: string | null;
    currency?: string | null; as_of?: string | null;
  } | null;
  estimate?: { label: string; value: string; basis?: string } | null;
  reversible?: boolean | null;
  counter_case?: string;
  spend_affected_usd_month?: number | null;
}

// ── the Desk: cross-project attention, one request ─────────────────────────────

export interface DeskApproval {
  id: string;
  title: string;
  kind: string;
  amount_usd: number | null;
  expires_at: string;
  capacity?: CapacityAsk | null;
  /** Whether a yes can be undone, where the kind alone says (yaaf.server.rows). */
  undo?: string | null;
  /** What silence decides on this project. */
  if_silent?: "approved" | "declined";
}

/** A capacity request's dial. Approving it is what raises the dial. */
export interface CapacityAsk {
  rule: string;
  from: number;
  to: number;
  shared: boolean;
}

/** A note you wrote on a decision, held until the CEO answers it. */
export interface HeldNote {
  id: string;
  about: string;
  note: string;
  created_at: string;
  reply: string | null;
  /** True: the CEO said it applies from now on, and put it in the brief. */
  standing: boolean | null;
  answered_at: string | null;
}

/** Something you asked to be told: you note it, there is nothing to decide. */
export interface HeadsUpRow {
  id: string;
  project_id: string;
  title: string;
  body: string;
  /** The standing order in your brief that asked for it. */
  standing_order: string;
  created_at: string;
  acknowledged_at: string | null;
  note: HeldNote | null;
}

/** One of the CEO's questions to you, live or settled. Shaped like an
    approval because from your side it is the same kind of thing: clocked,
    addressed to you, and it resolves whether or not you answer. The
    difference is only what happens at the clock — an approval decides itself
    per the constitution, an ask hands back to the CEO's own stated default. */
export interface AskRow {
  id: string;
  title: string;
  body: string;
  status: "open" | "answered" | "lapsed" | "closed";
  asked_by: string;
  answer_by: string;
  default_action: string;
  asked_in_message_id: string;
  answered_at: string | null;
  lapsed_at: string | null;
  closed_at: string | null;
}

/** A question the CEO is waiting on you for. It never blocks the project:
    `default_action` is what happens at `answer_by` if you say nothing. */
export interface DeskAsk {
  id: string;
  title: string;
  body: string;
  answer_by: string;
  default_action: string;
  /** The choices, when the question has them; answering is one click. */
  options?: string[];
  recommendation?: string | null;
}

export interface DeskBet {
  id: string;
  statement: string;
  kill_date: string;
  blocked_tasks?: number;
}

export interface DeskProject {
  id: string;
  slug: string;
  name: string;
  status: string;
  status_reason: string | null;
  /** Nothing pays for it, so nothing it starts can run. */
  unpaid?: boolean;
  /** One verdict and one sentence, decided on the server so every surface
      says the same thing (yaaf.server.status_line). */
  status_line?: { verdict: string; sentence: string };
  goal: string;
  billing_mode: string;
  budget: {
    unit: "usd" | "tokens"; spent: number; cap: number; warn_ratio: number;
    hard_stop_multiple: number;
  };
  open_tasks: number;
  queue_ceiling: number;
  /** Populated only for switches the chairman holds — empty on an autonomous
      project because the CEO decides these rather than parking them. */
  awaiting_you: {
    plan: number;
    /** `name` is what the owner calls it — the CEO's title for him when it
        wrote one, the statement otherwise. */
    verdicts: { key: string; statement: string; name: string }[];
    commitment: { id: string; deliver_by: string; scope_count: number } | null;
  };
  /** The reader's unread boardroom messages on this project. */
  boardroom_unread: number | null;
  evidence_7d: number;
  /** Yesterday, paired — the "burned money, proved nothing" signal. */
  spend_24h: number;
  evidence_24h: number;
  ceo_judgment: {
    tasks_created: number;
    tasks_with_verified_external: number;
    conversion: number | null;
  };
  pending_approvals: DeskApproval[];
  /** Questions the CEO put to you, each naming when it stops waiting and what
      it does then. Beside the approvals rather than in `awaiting_you`: both
      are clocked things you answer, and neither is gated on the dial. */
  pending_asks: DeskAsk[];
  expired_bets: DeskBet[];
  kill_dates_soon: DeskBet[];
  audit: { completed: number; audited: number; unaudited: number };
  drought: {
    active: boolean;
    window_hours: number;
    applies: boolean;
    last_countable_at: string | null;
  };
  frozen_threads: number;
  runs_running: number;
  last_run_at: string | null;
}

export interface DeskView {
  as_of: string;
  /** Terminal projects (stopped, completed). Always counted, even when
      `?live=true` has kept them out of `projects`. */
  finished: number;
  projects: DeskProject[];
  /** The reader's own snoozes still running (yaaf/server/api/me.py). */
  snoozed?: { item_key: string; project_id: string; until: string }[];
}

// ── the fire drill: the project, run for nothing ───────────────────────────────

export interface RehearsalStep {
  key: string;
  label: string;
  ok: boolean;
  /** True for steps this project's SHAPE makes inapplicable — an Explore
      project has no plan gate and no commitment by design, and drawing those
      as misses would report an absent feature as a broken one. */
  na?: boolean;
  /** Why the rung does not apply: an Explore shape, or a rule left off. */
  na_reason?: string | null;
  detail: string | null;
  tick: number | null;
}

export interface RehearsalReport {
  ok: boolean;
  verdict: string;
  spec: { slug: string | null; name: string | null; mode: string; goal: string; teams: number };
  ticks: number;
  walk: RehearsalStep[];
  /** Grouped: a crew that hits one wall nine times has one problem. */
  refusals: { path: string; error: string; count: number; agents: string[] }[];
  /** Every point the project stopped and waited for a human. The autonomy
      dial in numbers, which nothing else could produce before the money. */
  interventions: { kind: string; count: number; first_tick: number; examples: string[] }[];
  /** Facts about the project's shape that no refusal reports — a CEO with
      nobody to give work to never files a task, so there is no wall to see. */
  findings?: string[];
  plan?: { requirements: number; estimated_runs: number; adjusted_runs: number;
           multiplier: number };
  capacity?: { feasible: boolean; why: string; available: number; needed: number;
               slack: number; chain_hours: number };
  money?: { unit: "usd" | "tokens"; cap: number | null; runs: number;
            usd_per_run: number | null; source: string | null; total: number | null;
            break_even_usd_per_run: number | null; fits: boolean | null; why?: string };
  health?: HealthCheck[];
  relaxed: { rule: string; why: string }[];
  not_exercised: string[];
  /** What the install already has wired that the drill cannot exercise — read
      from a live session by the route that ran the drill, because the drill's
      own database is a throwaway one. Null when nobody could look (the CLI on
      a checkout). */
  install?: {
    slack: { bot_token: boolean; signing_secret: boolean; app_token: boolean;
             chairman_user: boolean; auto_create_channels: boolean };
    notifications: { email: boolean; webhook: boolean };
  } | null;
  caveat: string;
}

// ── the agenda: the future tense ───────────────────────────────────────────────

export interface AgendaEvent {
  at: string;
  /** The local day it lands on, so the strip and the list agree. */
  day: string;
  kind: string;
  label: string;
  detail: string | null;
  /** `consequence` resolves without you, `deadline` is a promise coming due,
      `meeting` wants you in the room, `projection` is arithmetic not a promise. */
  tone: "consequence" | "deadline" | "meeting" | "projection" | "flat";
  where: string | null;
}

export interface AgendaRhythm {
  kind: string;
  label: string;
  detail: string | null;
  every: string;
  next: string | null;
  days: string[];
}

export interface AgendaView {
  as_of: string;
  days: number;
  horizon: string;
  timezone: string;
  first_day: string;
  day_list: string[];
  live: boolean;
  rhythms: AgendaRhythm[];
  events: AgendaEvent[];
  /** Why something a chairman expects to see is not on the calendar. */
  notes: string[];
}

export interface EvidenceRow {
  id: string;
  kind: string;
  provenance: string;
  verification: string;
  description: string;
  source_url: string | null;
  verifier_notes: string | null;
  /** The task this proves — the link that lets a bet report its own proof. */
  task_id: string | null;
  /** What that task had to prove, so a claim can be read without the board. */
  task_title: string | null;
  /** Whether the verifier stored a copy of what it fetched. */
  has_snapshot: boolean;
  at: string;
}

export interface HypothesisRow {
  id: string;
  statement: string;
  kill_condition: string;
  kill_date: string;
  status: string;
  extended_once: boolean;
}

/** What a person runs on and has cost this month (`month_share` is their part
    of the project's own month, 0..1). */
export interface SeatWork {
  model: string;
  model_id: string;
  provider: string;
  month_usd: number;
  month_share: number;
}

export interface OrgView {
  ceo: ({
    id: string; name: string; status: string;
    role_slug: string | null; role_name: string | null;
  } & Dormancy & Partial<SeatWork>) | null;
  teams: {
    id: string;
    name: string;
    role_slug: string | null;
    /** The role as the library names it, and the version this team is pinned
        to — a team runs the version it was hired under until the chairman
        adopts a newer one, so both halves are needed to know what it runs. */
    role_name: string | null;
    role_version: number | null;
    role_latest_version: number | null;
    lead_agent_id: string | null;
    /** Derived, never stored: a team is stood down by standing its people
        down, so "is this team working" is a question about its members. */
    running: boolean;
    stood_down_at: string | null;
    /** When this role's job ends, declared while planning and fired by the
        platform. `{kind: "requirement_met", keys: [...]}` or
        `{kind: "stage_cleared", stage: n}`. */
    stand_down_when: { kind: string; keys?: string[]; stage?: number } | null;
    agents: ({
      id: string; name: string; status: string; stood_down_reason: string | null;
    } & Dormancy & Partial<SeatWork>)[];
  }[];
}

/** An active seat with no run in a week — the same definition the health
    card's "dormant-seats" finding reads. `dormant_days` is null for a seat
    that never ran at all. */
export interface Dormancy {
  dormant: boolean;
  dormant_days: number | null;
}

/** What each seat actually did in a window — the work half of the roster.

    The money half already existed as `SpendReport`, and it is what the
    chairman's Runs tab draws. This is the half a CEO was missing entirely:
    asked who had spent the day's runs it estimated, and was wrong by half. */
export interface ActivityView {
  window: string;
  since: string;
  until: string;
  seats: ActivitySeat[];
  totals: Omit<ActivitySeat, keyof SeatIdentity | "last_run_at">;
}

interface SeatIdentity {
  agent_id: string | null;
  name: string;
  kind: string;
  status: string | null;
  team_id: string | null;
  team: string | null;
  role_slug: string | null;
}

export interface ActivitySeat extends SeatIdentity {
  runs: number;
  /** The subset that spent the daily ration — what the run cap counts. Differs
      from `runs` by the platform's own checks and the chat lane. */
  runs_metered: number;
  runs_by_kind: Record<string, number>;
  runs_by_outcome: Record<string, number>;
  /** Why the platform spent each run, in the trigger's own words. A run woken
      by three notices and a commitment counts under both. */
  woke_for: Record<string, number>;
  tokens_in: number;
  tokens_out: number;
  cache_creation: number;
  cache_read: number;
  usd: number;
  refusals: number;
  tasks_claimed: number;
  tasks_completed: number;
  tasks_killed: number;
  holding: number;
  last_run_at: string | null;
}

/** One team pinned to (any version of) a role — the edit's blast radius. */
export interface RoleUsage {
  project_name: string;
  team_name: string;
  pinned_version: number;
}

export interface RoleSummary {
  id: string;
  slug: string;
  name: string;
  is_ceo_role: boolean;
  /** One of six disciplines, or null for a role written before the taxonomy —
      which the pickers file under "Uncategorised" rather than hiding. */
  category: string | null;
  /** Which worlds it knows. A filter in the hiring desk, never a folder. */
  domains: string[];
  latest_version: number;
  model: string | null;
  effort: string | null;
  description: string;
  tools: string[];
  secret_scopes: string[];
  usage: RoleUsage[];
}

/** One team inside a crew — the same shape TeamSpec takes at creation, so
    applying a crew is a copy rather than a translation. */
export interface CrewTeam {
  name: string;
  role_slug: string;
  /** Resolved against this install's library; null when the role is missing. */
  role_name: string | null;
  employees: string[];
  lead?: string | null;
  schedules?: Record<string, string>;
}

/** A named job with a team already on it. Read once, at hiring time, and
    copied into the project's own teams — nothing points back at it after. */
export interface CrewSummary {
  slug: string;
  name: string;
  outcome: string;
  ceo_role_slug: string;
  domains: string[];
  source: string | null;
  teams: CrewTeam[];
  /** Roles this crew hires that the library does not have. Kept visible by
      name rather than dropping the crew, the same courtesy a setup file gets. */
  missing_role_slugs: string[];
}

/** A role definition as it travels inside a setup file — the POST /admin/roles body. */
export interface BundleRole {
  slug: string;
  name: string;
  is_ceo_role: boolean;
  category?: string | null;
  domains?: string[];
  description: string;
  system_prompt: string;
  model: string;
  effort: string;
  /** What drives the model; absent is the provider's default. */
  harness?: string | null;
  tool_permissions: Record<string, unknown>;
  secret_scopes: string[];
}

/** A project setup file (GET /admin/projects/{id}/export). The `project`
    section is a ready create-project body minus slug collisions. By default
    only credential *names* travel; exported with `include_values=true` — the
    Settings download — every secret's `value` rides along and the file is a
    complete backup. */
export interface ProjectBundle {
  yaaf_bundle: number;
  kind: string;
  source_slug: string;
  exported_at: string;
  /** True when credential values are inside (a full backup). */
  values_included?: boolean;
  project: {
    slug: string;
    name: string;
    goal: string;
    billing_mode: string;
    /** Absent from setup files exported before project shapes existed. */
    mode?: string;
    controlled_domains: string[];
    /** The repo evidence webhooks attest against. */
    github_repo?: string | null;
    /** Explore's window, ISO-dated; may already be in the past. */
    deadline?: string | null;
    constitution: Record<string, unknown>;
    integrations: Record<string, unknown>;
    /** The managed LLM credential the project pays with, by id (a Founder draft
        carries one) and by name (an export carries the name as a hint; the value
        never travels). The form resolves either to a real credential on arrival. */
    billing_secret_id?: string | null;
    billing_secret_name?: string | null;
    /** What pays each other provider, by credential name: {deepseek: "DeepSeek key"}. */
    credential_names?: Record<string, string>;
    ceo_role_slug: string;
    ceo_name: string;
    ceo_daily_at?: string | null;
    ceo_model_override?: string | null;
    ceo_effort_override?: string | null;
    ceo_harness_override?: string | null;
    teams: {
      role_slug: string;
      name: string;
      employees: string[];
      lead: string | null;
      /** "HH:MM" daily wakes, keyed by employee name. */
      schedules?: Record<string, string>;
      /** Per-agent model overrides, keyed by employee name. */
      model_overrides?: Record<string, string>;
      /** Per-agent reasoning-effort overrides, keyed by employee name. */
      effort_overrides?: Record<string, string>;
      /** Per-agent harness overrides, keyed by employee name (docs/RUNTIMES.md). */
      harness_overrides?: Record<string, string>;
    }[];
    solo: boolean;
  };
  roles: BundleRole[];
  secrets: {
    name: string;
    role_scopes: string[];
    kind: string;
    /** True => the credential is a FILE, so the import form must offer a
        picker rather than a box to type in. The file's own name does not
        travel — a key file is usually named after the account it opens. */
    file?: boolean;
    /** Present only in full backups (include_values=true). */
    value?: string;
  }[];
}

/** POST /admin/projects/{id}/clone — a running twin on this install. Unlike a
    setup file it carries credential values, so `copied` reports what came
    across and how the twin's own data sources answered. */
export interface CloneResult {
  id: string;
  slug: string;
  name: string;
  status: string;
  /** The twin's shape. Frozen at creation like any project's, so it is worth
      reading back rather than assuming. */
  mode: string;
  copied: {
    secrets: number;
    data_sources: { name: string; check: { ok: boolean; detail: string } }[];
    slack_rooms_dropped: boolean;
    /** The source's Explore window had already passed, so the twin starts
        without one rather than being closed at birth. */
    deadline_dropped: boolean;
    /** The source paid with a credential that has since been hidden, so the
        twin starts unassigned — the name is here to say which one it was. */
    billing_credential_hidden: string | null;
  };
}

// ── the plan: scope, sequence and the promise ───────────────────────────

export interface PlanTask {
  id: string;
  title: string;
  status: string;
  holder: string | null;
  failed_checks: number;
}

/** The owner's copy of a requirement, written by the CEO "for the person who
    hired you". The statement is the checker's sentence; this is his. */
export interface RequirementMeta {
  title?: string;
  description?: string;
  cadence?: string | null;
  due?: string | null;
  lands?: string;
}

/** What the owner calls a requirement: the title the CEO wrote for him, when
    it wrote one. The statement is the checker's sentence — it stays on screen
    as what has to be true, it just stopped being the only name the work had. */
export function requirementName(r: { statement: string; meta?: RequirementMeta | null }): string {
  return r.meta?.title?.trim() || r.statement;
}

export interface PlanRequirement {
  id: string;
  key: string;
  statement: string;
  meta: RequirementMeta | null;
  status: string;
  stage: number;
  test_kind: string | null;
  acceptance_test: {
    type?: string; command?: string; target?: string;
    direction?: string; target_value?: number;
  };
  /** Present only for a `metric` requirement: what the platform has actually
      read, and which way it is going. Null for every other kind. */
  measurement: {
    value: number | null; target: number; direction: string | null;
    trend: number[]; verdict: string; measured_at: string | null;
    error: string | null;
  } | null;
  est_runs: number | null;
  spent: { runs: number; usd: number };
  failure_load: number;
  depends_on: string[];
  tasks: PlanTask[];
  met_at: string | null;
  cut_reason: string | null;
  check_output: string | null;
  chairman_verdict: string | null;
}

export interface PlanCommitment {
  id: string;
  status: string;
  deliver_by: string;
  projected_done_at: string | null;
  projection_basis: string | null;
  accepted_at: string | null;
  constitution_version: number | null;
  scope_snapshot: string[];
  status_reason: string | null;
  note: string | null;
  met_at: string | null;
}

export interface PlanBurnup {
  scope_total: number;
  /** How many of `scope_total` are true right now. Counted server-side from
      the same set the line is drawn from, because deriving it here as
      `scope_total - velocity.remaining` subtracted two sets that exclude
      different things. */
  settled: number;
  met_points: { at: string; count: number }[];
}

export interface PlanView {
  /** Narrowed from `string` so a mis-typed mode in a gate is a build error
      rather than a chart that silently never draws. */
  mode: "explore" | "deliver" | "operate";
  planless: boolean;
  commitment?: PlanCommitment | null;
  burnup?: PlanBurnup | null;
  plan?: {
    status: string | null;
    holds: boolean;
    outstanding: string[];
    arithmetic: {
      requirements: number;
      estimated_runs: number;
      multiplier: number;
      adjusted_runs: number;
      days_at_the_daily_cap: number | null;
      affordable: boolean;
      budget: { known: boolean; runs?: number; usd_remaining?: number;
        usd_per_run?: number; why?: string };
    };
    planning_spend: { runs: number; usd: number } | null;
  };
  open_stage?: number;
  critical_path?: { keys: string[]; adjusted_runs: number; multiplier?: number };
  velocity?: {
    met_3w: number;
    per_week_3w: number;
    met_12w: number;
    per_week_12w: number;
    remaining: number;
    weeks_left_at_pace: number | null;
    /** False when too little has settled, or too recently, for `per_week_3w`
        to be a measurement. A screen that prints the rate anyway says "0 a
        week" about a project two days old. */
    pace_known: boolean;
    measured_over_days: number;
    runs_3w: { total: number; dead: number; failure_share: number;
      attributed_share: number; usd: number;
      work: number; inbox: number; noop: number;
      inbox_share: number; noop_share: number };
    worst_failure: { key: string; failure_load: number; statement: string } | null;
  };
  /** What an Operate project has instead of a burndown: a burndown of work
      that recurs is a flat line, and the shape that carries the information is
      which obligation slipped, and in which turn. */
  standing?: {
    key: string;
    statement: string;
    met: number;
    judged: number;
    status_now: string;
  }[];
  requirements?: PlanRequirement[];
}

// ── the standing review ──────────────────────────────────────────

export interface ReviewNumbers {
  as_of: string;
  goal: string;
  velocity: {
    per_week_3w: number; per_week_12w: number; remaining: number;
    weeks_left_at_pace: number | null;
    /** Optional: review numbers are a JSON snapshot written at review time, so
        meetings held before this field existed do not carry it. Absent means
        "this snapshot cannot say", not "there was no pace". */
    pace_known?: boolean;
    runs_3w: { total: number; dead: number; failure_share: number };
    worst_failure: { key: string; failure_load: number } | null;
  };
  commitment: {
    status: string; deliver_by: string;
    projected_done_at: string | null; basis: string | null;
  } | null;
  budget: { unit: string; spent_mtd: number; cap: number };
  scope: { open: number; proposed: number; met: number; cut: number;
    met_since_last_review: number };
  hypotheses: { statement: string; kill_date: string; status: string }[];
  audit_coverage: { considered: number; sampled: number; failed: number;
    expired_unreviewed: number };
  prior?: {
    number: number;
    decisions: Decision[];
    bought: { met_since: number; pace_then: number | null; pace_now: number };
  };
}

export interface Decision { kind: string; reason: string; detail: string; at: string }



export interface ReviewRow {
  id: string;
  number: number;
  status: string;
  scheduled_for: string;
  opened_at: string;
  numbers: ReviewNumbers;
  narrative: string | null;
  recommended: string | null;
  narrative_filed_at: string | null;
  decisions: Decision[];
  closed_at: string | null;
}

export interface ReviewsView { open: ReviewRow | null; history: ReviewRow[] }
