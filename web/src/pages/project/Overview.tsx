import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Dashboard, get, HealthCheck, OrgView, post, ProjectHealth, RoleSummary } from "../../api";
import { usePoll } from "../../hooks";

/** Setup & health checklist: every way the configuration can be silently
    wrong, checked server-side. A fully green project renders NOTHING — the
    card's absence is the "all clear"; its presence is the to-do list. */
function HealthCard({ projectId }: { projectId: string }) {
  const { data, reload } = usePoll(
    () => get<ProjectHealth>(`/observe/projects/${projectId}/health`),
    10000,
  );
  if (!data || data.all_ok) return null;

  const badge = (status: HealthCheck["status"]) => (
    <span className={`badge ${status === "fail" ? "critical" : status === "warn" ? "warn" : "good"}`}>
      {status}
    </span>
  );
  return (
    <div className="card">
      <strong>Setup &amp; health</strong>
      <p className="subtitle" style={{ margin: "4px 0 8px" }}>
        The project tells you what it needs, instead of failing quietly at 9am — each row is a
        server-side check of this project's configuration. Green rows vanish; fix the rest and
        this whole card disappears.
      </p>
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

/** Single-value budget meter: track + fill + threshold tick at the warn ratio.
    The fill wears warning past the tick and critical at/over the cap; the icon
    + text pairing below carries the state, never color alone. */
function BudgetMeter({ dash }: { dash: Dashboard }) {
  const usdMode = dash.billing_mode === "api_key";
  const spent = usdMode
    ? dash.month_to_date.usd
    : dash.month_to_date.tokens_in + dash.month_to_date.tokens_out;
  const cap = usdMode ? dash.caps.monthly_usd_cap : dash.caps.monthly_token_cap;
  const warnRatio = Number(dash.constitution["budget_warn_ratio"] ?? 0.8);
  const frac = cap > 0 ? Math.min(spent / cap, 1) : 0;
  const cls = frac >= 1 ? "over" : frac >= warnRatio ? "warn" : "";
  const fmt = (n: number) =>
    usdMode ? `$${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}` : n.toLocaleString();
  const state =
    frac >= 1 ? "✖ exhausted — auto-paused" : frac >= warnRatio ? "⚠ past warning threshold" : "✓ within budget";
  return (
    <div className="card">
      <div className="tile" style={{ border: "none", padding: 0, background: "none" }}>
        <div className="label">Monthly budget ({usdMode ? "USD" : "tokens"})</div>
        <div className="value">
          {fmt(spent)} <span style={{ fontSize: 14, color: "var(--muted)" }}>/ {fmt(cap)}</span>
        </div>
      </div>
      <div className="meter">
        <div className="track">
          <div className={`fill ${cls}`} style={{ width: `${frac * 100}%` }} />
          <div className="tick" style={{ left: `${warnRatio * 100}%` }} />
        </div>
        <div className="legend">
          <span>{state}</span>
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

export default function Overview({ projectId, dash }: { projectId: string; dash: Dashboard }) {
  const { data: org, reload: reloadOrg } = usePoll(
    () => get<OrgView>(`/observe/projects/${projectId}/org`),
    15000,
  );

  const cons = dash.constitution as Record<string, string | number | boolean>;
  return (
    <>
      <HealthCard projectId={projectId} />
      <div className="tiles">
        <div className="tile">
          <div className="label">Open tasks</div>
          <div className="value">
            {dash.open_tasks}
            <span style={{ fontSize: 14, color: "var(--muted)" }}> / {dash.queue_ceiling}</span>
          </div>
          <div className="sub">queue ceiling</div>
        </div>
        <div className="tile">
          <div className="label">Runs</div>
          <div className="value">{dash.runs.running}</div>
          <div className="sub">{dash.runs.queued} queued</div>
        </div>
        <div className="tile">
          <div className="label">External evidence</div>
          <div className="value">{dash.external_evidence_in_window}</div>
          <div className="sub">verified, in drought window</div>
        </div>
        <div className="tile">
          <div className="label">Approvals pending</div>
          <div className="value">{dash.pending_approvals}</div>
          <div className="sub">expire to DENY</div>
        </div>
        <div className="tile">
          <div className="label">CEO judgment</div>
          <div className="value">
            {dash.ceo_judgment.conversion === null
              ? "—"
              : `${Math.round(dash.ceo_judgment.conversion * 100)}%`}
          </div>
          <div className="sub">
            {dash.ceo_judgment.tasks_with_verified_external} / {dash.ceo_judgment.tasks_created}{" "}
            bets converted
          </div>
        </div>
      </div>
      <p className="subtitle">
        CEO judgment is the share of the CEO's bets (top-level tasks) that ever produced verified
        external evidence — the CEO-quality signal, and an M2 rubric input.
      </p>

      <BudgetMeter dash={dash} />

      <h2>Organization</h2>
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
                  alert(result.changed
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

      <h2>Constitution in force</h2>
      <div className="card">
        <dl className="kv">
          {Object.entries(cons).map(([key, value]) => (
            <span key={key} style={{ display: "contents" }}>
              <dt>{key.replaceAll("_", " ")}</dt>
              <dd className="mono">{String(value)}</dd>
            </span>
          ))}
        </dl>
        <p className="help">
          Frozen at project creation. Changes go through Settings → Amendment, take effect after a
          72-hour cooling-off, and are disclosed to the CEO.
        </p>
      </div>
    </>
  );
}
