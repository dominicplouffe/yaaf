// Thin API client. The admin token lives in localStorage and rides on every
// request; a 401 clears it and bounces to the login screen.

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
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
    const detail =
      (data as { detail?: string; error?: string })?.detail ??
      (data as { error?: string })?.error ??
      `HTTP ${response.status}`;
    throw new ApiError(response.status, String(detail));
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
      detail = String((JSON.parse(await response.text()) as { detail?: string }).detail ?? detail);
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

export interface Dashboard {
  id: string;
  name: string;
  status: string;
  status_reason: string | null;
  goal: string;
  billing_mode: string;
  month_to_date: { usd: number; tokens_in: number; tokens_out: number };
  caps: { monthly_usd_cap: number; monthly_token_cap: number };
  open_tasks: number;
  queue_ceiling: number;
  external_evidence_in_window: number;
  drought_active: boolean;
  runs: { running: number; queued: number };
  /** True while the resident CEO chat session is live — boardroom replies
      arrive in seconds instead of via queued runs. */
  chat_live: boolean;
  pending_approvals: number;
  ceo_judgment: {
    tasks_created: number;
    tasks_with_verified_external: number;
    conversion: number | null;
  };
  constitution: Record<string, unknown>;
  config: Record<string, unknown>;
  github_repo: string | null;
  billing_secret_id: string | null;
  billing_secret_name: string | null;
  /** Drought-clock anchors: the UI counts toward the refusal. */
  created_at: string;
  last_countable_evidence_at: string | null;
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

/** A project data source (MCP server) as stored in dash.config["mcp_servers"]. */
export interface McpServerEntry {
  name: string;
  url_secret: string;
  bearer_secret?: string;
  roles: string[];
  url_host: string;
  url_port: number;
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
  cost_usd: number | null;
  refusals: number;
  summary: string | null;
  has_trace: boolean;
  started_at: string | null;
  finished_at: string | null;
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

export interface RoleSummary {
  id: string;
  slug: string;
  name: string;
  is_ceo_role: boolean;
  latest_version: number;
  model: string | null;
}
