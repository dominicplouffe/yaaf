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
  pending_approvals: number;
  constitution: Record<string, unknown>;
  config: Record<string, unknown>;
  billing_secret_id: string | null;
  billing_secret_name: string | null;
}

export interface TaskRow {
  id: string;
  title: string;
  status: string;
  team_id: string;
  parent_task_id: string | null;
  assignee: string | null;
  claimed_by: string | null;
  external: boolean;
  success_test: { type: string; command?: string; target?: string };
  evidence: string | null;
  check_output: string | null;
  blocked_reason: string | null;
}

export interface MessageRow {
  id: string;
  from_agent: string | null;
  from_user: string | null;
  thread_id: string | null;
  frozen?: boolean;
  body: string;
  at: string;
}

export interface ChannelRow {
  id: string;
  kind: string;
  team_id: string | null;
}

export interface RunRow {
  id: string;
  agent_id: string | null;
  kind: string;
  status: string;
  trigger: Record<string, unknown>;
  attempt: number;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number | null;
  refusals: number;
  summary: string | null;
  started_at: string | null;
  finished_at: string | null;
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
