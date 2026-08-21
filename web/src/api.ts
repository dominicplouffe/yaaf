// Thin API client. The admin token lives in localStorage and rides on every
// request; a 401 clears it and bounces to the login screen.

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

const TOKEN_KEY = "yaaf_admin_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

export async function api<T = unknown>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data: unknown = null;
  try {
    data = await response.json();
  } catch {
    /* empty body */
  }
  if (!response.ok) {
    if (response.status === 401) {
      clearToken();
      if (!location.hash.startsWith("#/login")) location.hash = "#/login";
    }
    throw new ApiError(response.status, errorText(data, response.status));
  }
  return data as T;
}

export const get = <T = unknown>(path: string) => api<T>("GET", path);
export const post = <T = unknown>(path: string, body?: unknown) => api<T>("POST", path, body);
export const patch = <T = unknown>(path: string, body?: unknown) => api<T>("PATCH", path, body);
export const del = <T = unknown>(path: string) => api<T>("DELETE", path);

/** GET an endpoint that returns plain text (run traces) — no JSON parsing. */
export async function getText(path: string): Promise<string> {
  const response = await fetch(path, {
    headers: getToken() ? { Authorization: `Bearer ${getToken()}` } : {},
  });
  if (!response.ok) {
    if (response.status === 401) {
      clearToken();
      if (!location.hash.startsWith("#/login")) location.hash = "#/login";
    }
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

// ── shared shapes (mirrors the observe/admin API) ──────────────────────────────

export interface ProjectSummary {
  id: string;
  slug: string;
  name: string;
  status: string;
  goal: string;
}

/** The one sentence for why this company is quiet, written server-side beside
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
  /** Does this deserve the box above the tabs? False when the company is
      simply working — a banner that fires when things are fine teaches the
      reader to stop reading it. */
  banner: boolean;
  /** Set only on briefs that are waiting out a clock, and only those: together
      with `clears_at` it is what makes a banner dismissible. */
  sig: string | null;
  clears_at: string | null;
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
    | "stalled_requirement"
    | "awaiting_chairman"
    | "blocked_tasks"
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
  waiting: {
    /** claimable + gated. Kept because "how much is open" is still a question;
        never render it as "ready to run" — that was the original lie. */
    ready_tasks: number;
    claimable_tasks: number;
    /** Ready, but carrying a start time that has not arrived. */
    gated_tasks: number;
    claimed_tasks: number;
    blocked_tasks: number;
    notices: number;
    agents_with_work: number;
  };
  /** Work that exists but the dispatcher cannot reach. */
  board: {
    claimable: number;
    gated: number;
    next_gate_at: string | null;
    blocked: number;
    leaderless_teams: string[];
    paused_agents: string[];
    stalled_requirements: string[];
    proposed_requirements: string[];
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
  cycle: { ordinal: number; opened_at: string } | null;
  integrations: Record<string, unknown>;
  github_repo: string | null;
  billing_secret_id: string | null;
  billing_secret_name: string | null;
  /** Drought-clock anchors: the UI counts toward the refusal. */
  created_at: string;
  last_countable_evidence_at: string | null;
  memory: MemoryUsage;
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
}

export interface ProjectHealth {
  ok: boolean;
  all_ok: boolean;
  checks: HealthCheck[];
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

export interface ApprovalRow {
  id: string;
  kind: string;
  title: string;
  body: string;
  amount_usd: number | null;
  status: string;
  requested_by: string;
  expires_at: string;
  decided_at: string | null;
  /** The chairman's note — the agent reads it, so history shows it. */
  decision_note: string | null;
}

// ── the Desk: cross-project attention, one request ─────────────────────────────

export interface DeskApproval {
  id: string;
  title: string;
  kind: string;
  amount_usd: number | null;
  expires_at: string;
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
  goal: string;
  billing_mode: string;
  budget: { unit: "usd" | "tokens"; spent: number; cap: number; warn_ratio: number };
  open_tasks: number;
  queue_ceiling: number;
  evidence_7d: number;
  ceo_judgment: {
    tasks_created: number;
    tasks_with_verified_external: number;
    conversion: number | null;
  };
  pending_approvals: DeskApproval[];
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
  away: {
    since: string;
    tasks_completed: number;
    evidence_verified: number;
    usd_spent: number;
    runs: number;
    refusal_storms: number;
  };
  projects: DeskProject[];
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

export interface OrgView {
  ceo: { id: string; name: string; status: string } | null;
  teams: {
    id: string;
    name: string;
    role_slug: string | null;
    lead_agent_id: string | null;
    agents: { id: string; name: string; status: string }[];
  }[];
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
  latest_version: number;
  model: string | null;
  effort: string | null;
  description: string;
  tools: string[];
  usage: RoleUsage[];
}

/** A role definition as it travels inside a setup file — the POST /admin/roles body. */
export interface BundleRole {
  slug: string;
  name: string;
  is_ceo_role: boolean;
  description: string;
  system_prompt: string;
  model: string;
  effort: string;
  tool_permissions: Record<string, unknown>;
  secret_scopes: string[];
}

/** A portable project setup file (GET /admin/projects/{id}/export). The
    `project` section is a ready create-project body minus slug collisions and
    the chairman; credential values never travel — only their names. */
export interface ProjectBundle {
  yaaf_bundle: number;
  kind: string;
  source_slug: string;
  exported_at: string;
  project: {
    slug: string;
    name: string;
    goal: string;
    billing_mode: string;
    /** Absent from setup files exported before project shapes existed. */
    mode?: string;
    controlled_domains: string[];
    constitution: Record<string, unknown>;
    integrations: Record<string, unknown>;
    ceo_role_slug: string;
    ceo_name: string;
    teams: { role_slug: string; name: string; employees: string[]; lead: string | null }[];
    solo: boolean;
  };
  roles: BundleRole[];
  secrets: { name: string; role_scopes: string[]; kind: string }[];
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
  };
}
