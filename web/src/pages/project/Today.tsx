import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  ApprovalRow,
  Dashboard,
  get,
  HealthCheck,
  HypothesisRow,
  OrgView,
  post,
  ProjectHealth,
  RoleSummary,
  RunRow,
  TaskRow,
} from "../../api";
import { usePoll } from "../../hooks";
import {
  DecideButtons,
  clockClass,
  fmtLeft,
  fmtUnit,
  fmtWhenShort,
  hoursSince,
  paceDate,
} from "../../ui";

/** Setup & health. A healthy project gets one quiet affirmative line — silence
    is ambiguous with a broken health check, and the ambiguity costs more than
    the pixels. Failing rows keep their plain-language fixes. */
function HealthLine({ projectId }: { projectId: string }) {
  const { data, reload } = usePoll(
    () => get<ProjectHealth>(`/observe/projects/${projectId}/health`),
    10000,
  );
  const [show, setShow] = useState(false);
  if (!data) return null;

  if (data.all_ok && !show) {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 8, margin: "14px 0", fontSize: 13 }}>
        <span className="badge good">healthy</span>
        <span className="help">All {data.checks.length} health checks passing.</span>
        <button className="small" onClick={() => setShow(true)}>show</button>
      </div>
    );
  }
  const badge = (status: HealthCheck["status"]) => (
    <span className={`badge ${status === "fail" ? "critical" : status === "warn" ? "warn" : "good"}`}>
      {status}
    </span>
  );
  return (
    <div className="card" style={{ margin: "14px 0" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
        <strong>Setup &amp; health</strong>
        <div className="spacer" />
        {data.all_ok && <button className="small" onClick={() => setShow(false)}>hide</button>}
      </div>
      {!data.all_ok && (
        <p className="subtitle" style={{ margin: "4px 0 8px" }}>
          The project tells you what it needs, instead of failing quietly at 9am. Fix the rows
          below and this collapses back to one green line.
        </p>
      )}
      {data.checks.map((check) => (
        <div
          key={check.key}
          style={{
            display: "flex",
            gap: 10,
            alignItems: "baseline",
            padding: "4px 0",
            ...(check.status === "ok" ? { opacity: 0.5 } : {}),
          }}
        >
          {badge(check.status)}
          <span style={{ flex: 1, fontSize: 13 }}>{check.detail}</span>
          {check.action?.kind === "repin" ? (
            <button
              className="small"
              onClick={async () => {
                await post(`/admin/teams/${check.action!.team_id}/repin`, {});
                reload();
              }}
            >
              adopt latest role version
            </button>
          ) : check.where === "settings" && check.status !== "ok" ? (
            <Link to={`/projects/${projectId}/settings`}>fix in Settings →</Link>
          ) : null}
        </div>
      ))}
    </div>
  );
}

/** Budget meter with pace: position is a fact, the projected cap date is the
    decision aid — a fuel gauge vs a range estimate. */
function BudgetMeter({ dash }: { dash: Dashboard }) {
  const usdMode = dash.billing_mode === "api_key";
  const unit = usdMode ? ("usd" as const) : ("tokens" as const);
  const spent = usdMode
    ? dash.month_to_date.usd
    : dash.month_to_date.tokens_in + dash.month_to_date.tokens_out;
  const cap = usdMode ? dash.caps.monthly_usd_cap : dash.caps.monthly_token_cap;
  const warnRatio = Number(dash.constitution["budget_warn_ratio"] ?? 0.8);
  const frac = cap > 0 ? Math.min(spent / cap, 1) : 0;
  const cls = frac >= 1 ? "over" : frac >= warnRatio ? "warn" : "";
  const pace = paceDate(spent, cap);
  const state =
    frac >= 1
      ? "✖ exhausted — auto-paused"
      : frac >= warnRatio
        ? "⚠ past the warning threshold"
        : "✓ within budget";
  return (
    <div className="card">
      <div className="tile" style={{ border: "none", padding: 0, background: "none" }}>
        <div className="label">Monthly budget ({usdMode ? "USD" : "tokens"})</div>
        <div className="value">
          {fmtUnit(spent, unit)}{" "}
          <span style={{ fontSize: 14, color: "var(--muted)" }}>/ {fmtUnit(cap, unit)}</span>
        </div>
      </div>
      <div className="meter">
        <div className="track">
          <div className={`fill ${cls}`} style={{ width: `${frac * 100}%` }} />
          <div className="tick" style={{ left: `${warnRatio * 100}%` }} />
        </div>
        <div className="legend">
          <span>
            {state}
            {pace && frac < 1 && (
              <> · on pace to hit the cap {fmtWhenShort(pace.toISOString())} — before the month ends</>
            )}
          </span>
          <span>warn at {Math.round(warnRatio * 100)}%</span>
        </div>
        <div className="legend" style={{ marginTop: 2 }}>
          <span>
            {dash.billing_secret_name
              ? `paying with: ${dash.billing_secret_name} (${usdMode ? "API key" : "Claude subscription"})`
              : "⚠ no payment credential connected — real runs will fail (Settings)"}
          </span>
        </div>
      </div>
    </div>
  );
}

function AddTeam({ projectId, onAdded }: { projectId: string; onAdded: () => void }) {
  const [roles, setRoles] = useState<RoleSummary[]>([]);
  const [roleSlug, setRoleSlug] = useState("");
  const [teamName, setTeamName] = useState("");
  const [employees, setEmployees] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    get<RoleSummary[]>("/admin/roles")
      .then((all) => setRoles(all.filter((r) => !r.is_ceo_role)))
      .catch(() => {});
  }, []);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await post(`/admin/projects/${projectId}/teams`, {
        role_slug: roleSlug,
        name: teamName.trim(),
        employees: employees.split(",").map((s) => s.trim()).filter(Boolean),
      });
      setTeamName("");
      setEmployees("");
      onAdded();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="card">
      <summary>＋ Add a team</summary>
      {error && <div className="error">{error}</div>}
      <label className="field">Role (from the library)</label>
      <select value={roleSlug} onChange={(e) => setRoleSlug(e.target.value)}>
        <option value="">— pick a role —</option>
        {roles.map((role) => (
          <option key={role.slug} value={role.slug}>
            {role.name} ({role.model ?? "default"})
          </option>
        ))}
      </select>
      <label className="field">Team name</label>
      <input value={teamName} onChange={(e) => setTeamName(e.target.value)} placeholder="Content" />
      <label className="field">Employees (comma-separated names; first is lead if several)</label>
      <input value={employees} onChange={(e) => setEmployees(e.target.value)} placeholder="Blogger" />
      <div className="actions">
        <button
          className="primary small"
          disabled={busy || !roleSlug || !teamName.trim() || !employees.trim()}
          onClick={submit}
        >
          Add team
        </button>
        <span className="help">The CEO sees the new team in its next context and can plan for it.</span>
      </div>
    </details>
  );
}

/** The first tab answers the daily question in order: needs you → watch →
    working right now → the numbers → the org. Same facts as before, triaged. */
export default function Today({ projectId, dash }: { projectId: string; dash: Dashboard }) {
  const { data: org, reload: reloadOrg } = usePoll(
    () => get<OrgView>(`/observe/projects/${projectId}/org`),
    15000,
  );
  const { data: approvals, reload: reloadApprovals } = usePoll(
    () => get<ApprovalRow[]>(`/observe/projects/${projectId}/approvals`),
    6000,
  );
  const { data: hypotheses } = usePoll(
    () => get<HypothesisRow[]>(`/observe/projects/${projectId}/hypotheses`),
    15000,
  );
  const { data: tasks } = usePoll(
    () => get<TaskRow[]>(`/observe/projects/${projectId}/board`),
    10000,
  );
  const { data: runs } = usePoll(
    () => get<RunRow[]>(`/observe/projects/${projectId}/runs?limit=20`),
    8000,
  );
  const [repinMsg, setRepinMsg] = useState<string | null>(null);

  const pending = (approvals ?? []).filter((a) => a.status === "pending");
  const expiredBets = (hypotheses ?? []).filter((h) => h.status === "expired" && !h.extended_once);
  const completed = (tasks ?? []).filter((t) => t.status === "done");
  const unaudited = completed.filter((t) => !t.audit_status).length;
  const blocked = (tasks ?? []).filter((t) => t.status === "blocked");
  const killSoon = (hypotheses ?? []).filter(
    (h) => h.status === "active" && hoursSince(h.kill_date) > -7 * 24 && hoursSince(h.kill_date) < 0,
  );

  // ── needs you ──
  const needs: React.ReactNode[] = [];
  for (const a of pending) {
    needs.push(
      <div className="arow" key={a.id}>
        <span className="chip accent">{a.kind.replaceAll("_", " ")}</span>
        <div className="grow">
          <div className="tt">
            {a.title}
            {a.amount_usd !== null && <span className="mono"> — ${a.amount_usd.toLocaleString()}</span>}
          </div>
          <div className="sb">the CEO's full case is one click away</div>
        </div>
        <span className={`clock ${clockClass(a.expires_at)}`}>
          {fmtLeft(a.expires_at).replace(" left", "")} → auto-deny
        </span>
        <DecideButtons approvalId={a.id} onDone={reloadApprovals} />
        <Link to={`/projects/${projectId}/approvals`}>context →</Link>
      </div>,
    );
  }
  for (const h of expiredBets) {
    needs.push(
      <div className="arow" key={h.id}>
        <span className="chip serious">bet expired</span>
        <div className="grow">
          <div className="tt">“{h.statement}” hit its kill date {fmtWhenShort(h.kill_date)}</div>
          <div className="sb">you hold the one extension — or let it stand and the CEO pivots</div>
        </div>
        <Link to={`/projects/${projectId}/evidence`}>decide →</Link>
      </div>,
    );
  }
  if (unaudited >= 3) {
    needs.push(
      <div className="arow" key="audit">
        <span className="chip warn">audit due</span>
        <div className="grow">
          <div className="tt">{unaudited} completions have never been audited</div>
          <div className="sb">the platform picks the sample, you judge it</div>
        </div>
        <Link to={`/projects/${projectId}/evidence`}>deal a sample →</Link>
      </div>,
    );
  }

  // ── watch ──
  const watch: React.ReactNode[] = [];
  {
    const usdMode = dash.billing_mode === "api_key";
    const unit = usdMode ? ("usd" as const) : ("tokens" as const);
    const spent = usdMode
      ? dash.month_to_date.usd
      : dash.month_to_date.tokens_in + dash.month_to_date.tokens_out;
    const cap = usdMode ? dash.caps.monthly_usd_cap : dash.caps.monthly_token_cap;
    const warnRatio = Number(dash.constitution["budget_warn_ratio"] ?? 0.8);
    const frac = cap > 0 ? spent / cap : 0;
    const pace = usdMode ? paceDate(spent, cap) : null;
    if (frac < 1 && (frac >= warnRatio || pace)) {
      watch.push(
        <div className="wrow" key="budget">
          <span className="badge warn">budget {Math.round(frac * 100)}%</span>
          <div className="grow">
            {fmtUnit(spent, unit)} of {fmtUnit(cap, unit)}
            {pace && (
              <>
                {" "}— on pace to hit the cap <strong>{fmtWhenShort(pace.toISOString())}</strong>. The
                platform pauses the project at 100%.
              </>
            )}
          </div>
          <Link to={`/projects/${projectId}/settings`}>amend budget →</Link>
        </div>,
      );
    }
    const windowH = Number(dash.constitution["drought_hours"] ?? 24);
    const applies = hoursSince(dash.created_at) >= windowH;
    if (dash.drought_active) {
      watch.push(
        <div className="wrow" key="drought">
          <span className="badge serious">drought</span>
          <div className="grow">
            No verified external evidence in {windowH}h — the platform is refusing new internal
            work until proof lands.
          </div>
          <Link to={`/projects/${projectId}/evidence`}>see evidence →</Link>
        </div>,
      );
    } else if (applies && dash.last_countable_evidence_at) {
      const elapsed = hoursSince(dash.last_countable_evidence_at);
      if (elapsed >= windowH * 0.5) {
        watch.push(
          <div className="wrow" key="drought">
            <span className="badge warn">drought {Math.floor(elapsed)}h / {windowH}h</span>
            <div className="grow">
              No verified external evidence for {Math.floor(elapsed)} hours — at {windowH}h, new
              internal work gets refused until proof lands.
            </div>
            <Link to={`/projects/${projectId}/evidence`}>see evidence →</Link>
          </div>,
        );
      }
    }
    for (const h of killSoon) {
      watch.push(
        <div className="wrow" key={h.id}>
          <span className="badge warn">kill date {fmtLeft(h.kill_date).replace(" left", "")}</span>
          <div className="grow">“{h.statement}” — {fmtWhenShort(h.kill_date)}</div>
          <Link to={`/projects/${projectId}/evidence`}>see the bet →</Link>
        </div>,
      );
    }
  }

  const runningRuns = (runs ?? []).filter((r) => r.status === "running");
  const soonestApproval = pending.length > 0 ? pending.reduce((a, b) => (a.expires_at < b.expires_at ? a : b)) : null;

  return (
    <>
      {needs.length > 0 ? (
        <>
          <h2 className="zone hot" style={{ marginTop: 6 }}>Needs you <span className="cnt">{needs.length}</span></h2>
          <div className="arows">{needs}</div>
        </>
      ) : (
        <p className="help" style={{ margin: "10px 0 0" }}>Nothing waiting on you here.</p>
      )}

      {watch.length > 0 && (
        <>
          <h2 className="zone">Watch <span className="cnt">{watch.length}</span></h2>
          <div className="wbox">{watch}</div>
        </>
      )}

      <h2 className="zone">Working right now</h2>
      <div className="wbox">
        {runningRuns.map((r) => (
          <div className="wrow" key={r.id}>
            <span className="working-dot" aria-hidden="true" />
            <div className="grow">
              <strong>{r.agent_name ?? "—"}</strong>
              {r.team_name && <span className="help"> ({r.team_name})</span>} — running
              {r.kind === "success_check" ? " a platform success check" : ""}
            </div>
            <span className="mono help">
              {r.started_at ? `${Math.max(1, Math.round(hoursSince(r.started_at) * 60))}m` : ""}
              {r.cost_usd ? ` · $${r.cost_usd.toFixed(2)} so far` : ""}
            </span>
          </div>
        ))}
        {runningRuns.length === 0 && (
          <div className="wrow">
            <span className="badge">idle</span>
            <div className="grow help">
              Nothing running — an idle project costs exactly nothing.
              {dash.runs.queued > 0 && ` ${dash.runs.queued} run(s) queued to start.`}
            </div>
            <Link to={`/projects/${projectId}/boardroom`}>boardroom →</Link>
          </div>
        )}
        {runningRuns.length > 0 && dash.runs.queued > 0 && (
          <div className="wrow help">{dash.runs.queued} more run(s) queued.</div>
        )}
      </div>

      <h2 className="zone">The numbers</h2>
      <div className="tiles" style={{ margin: "8px 0" }}>
        <div className="tile">
          <div className="label">Open tasks</div>
          <div className="value">
            {dash.open_tasks}
            <span style={{ fontSize: 14, color: "var(--muted)" }}> / {dash.queue_ceiling} ceiling</span>
          </div>
          <div className="sub">
            {blocked.length > 0 ? (
              <span className="chip serious">{blocked.length} blocked</span>
            ) : (
              "forces finishing over starting"
            )}
          </div>
        </div>
        <div className="tile">
          <div className="label">External evidence</div>
          <div className="value">{dash.external_evidence_in_window}</div>
          <div className="sub">verified, in the {String(dash.constitution["drought_hours"] ?? 24)}h window</div>
        </div>
        <div className="tile">
          <div className="label">Approvals</div>
          <div className="value">{dash.pending_approvals}</div>
          <div className="sub">
            {soonestApproval ? `expires in ${fmtLeft(soonestApproval.expires_at).replace(" left", "")} → deny` : "expire to DENY"}
          </div>
        </div>
        <div className="tile">
          <div className="label">CEO judgment</div>
          <div className="value">
            {dash.ceo_judgment.conversion === null
              ? "—"
              : `${Math.round(dash.ceo_judgment.conversion * 100)}%`}
          </div>
          <div className="sub">
            {dash.ceo_judgment.tasks_with_verified_external} of {dash.ceo_judgment.tasks_created} bets
            produced real-world proof
          </div>
        </div>
      </div>

      <BudgetMeter dash={dash} />
      <HealthLine projectId={projectId} />

      <h2>Organization</h2>
      {repinMsg && <div className="notice-box">{repinMsg}</div>}
      <div className="list-grid">
        <div className="card">
          <strong>CEO</strong>
          <div style={{ marginTop: 6 }}>
            {org?.ceo ? (
              <span className="badge accent">{org.ceo.name}</span>
            ) : (
              <span className="help">—</span>
            )}
          </div>
          <p className="help" style={{ marginBottom: 0 }}>
            The only agent that speaks to you (boardroom) and the only one that creates tasks.
          </p>
        </div>
        {(org?.teams ?? []).map((team) => (
          <div className="card" key={team.id}>
            <strong>{team.name}</strong>
            <div className="pill-row" style={{ marginTop: 6 }}>
              {team.agents.map((agent) => (
                <span key={agent.id} className={`badge ${agent.status === "active" ? "good" : "warn"}`}>
                  {agent.name}
                  {team.lead_agent_id === agent.id ? " (lead)" : ""}
                </span>
              ))}
            </div>
            {/* Teams pin their role version at creation; edited roles apply
                only when the chairman explicitly adopts them. */}
            <div className="actions" style={{ marginTop: 8 }}>
              <button
                className="small"
                onClick={async () => {
                  const result = await post<{ changed: boolean; version: number }>(
                    `/admin/teams/${team.id}/repin`, {});
                  setRepinMsg(result.changed
                    ? `${team.name} now runs role version ${result.version}; effective on the next run.`
                    : `${team.name} is already on the latest role version (${result.version}).`);
                }}
              >
                adopt latest role version
              </button>
            </div>
          </div>
        ))}
        <AddTeam projectId={projectId} onAdded={reloadOrg} />
      </div>
    </>
  );
}
