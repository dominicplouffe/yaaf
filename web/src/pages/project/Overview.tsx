import { Dashboard, get, OrgView } from "../../api";
import { usePoll } from "../../hooks";

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

export default function Overview({ projectId, dash }: { projectId: string; dash: Dashboard }) {
  const { data: org } = usePoll(() => get<OrgView>(`/observe/projects/${projectId}/org`), 15000);

  const cons = dash.constitution as Record<string, string | number | boolean>;
  return (
    <>
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
      </div>

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
          </div>
        ))}
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
