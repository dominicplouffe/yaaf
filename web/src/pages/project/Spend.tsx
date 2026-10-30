import { Link } from "react-router-dom";
import { get, SpendBucket, SpendReport } from "../../api";
import { fmtUsd, usePoll } from "../../hooks";
import { PlanMeter } from "../../llmCredentials";
import { useProviders } from "../../providers";
import type { ProjectCtx } from "../../projectCtx";
import {
  budgetOf,
  downloadCsv,
  fmtUnit,
  fmtWhenShort,
  paceDate,
  runHealth,
  spentOf,
} from "../../ui";
import { Badge, BarChart, ChartHead } from "../../ui/kit";
import { Icon } from "../../ui/Icon";
import { LedgerRow, SpendBars, SpendBreakdown } from "./Runs";

/** The money, on a page of its own.

    It used to be one card two thirds of the way down the Overview and a chart
    on the Runs table — two halves of one question ("is this worth what it
    costs?") on two screens. Here the budget reads as the policy it is: where
    you are, where you will land, and what the platform does at each line. */

/** Position is a fact; the projected landing is the decision aid — a fuel
    gauge versus a range estimate. */
export function BudgetCard({ ctx }: { ctx: ProjectCtx }) {
  const { dash, projectId } = ctx;
  const { usdMode, unit, spent, cap, warnRatio, frac, hardStop, wall } = budgetOf(dash);
  const pace = paceDate(spent, cap);
  const unpaid = !dash.billing_secret_id && dash.integrations?.runtime !== "mock";
  const billing = (dash.credentials ?? []).find((c) => c.billing);
  const seller = useProviders().find((p) => p.name === billing?.provider);
  const state = unpaid ? { tone: "warn" as const, label: "Nothing pays for it" }
    : wall > 0 && spent >= wall ? { tone: "critical" as const, label: "Hard stop — paused" }
    : frac >= 1 ? { tone: "critical" as const, label: "Over budget" }
      : frac >= warnRatio ? { tone: "warn" as const, label: "Past its warning line" }
        : { tone: "good" as const, label: "Healthy" };
  return (
    <div className="card budget-card">
      <div className="bc-top">
        <span className="bc-label">This month's budget</span>
        <Badge tone={state.tone}>{state.label}</Badge>
      </div>
      <div className="bc-num">
        {fmtUnit(spent, unit)} <span>of {fmtUnit(cap, unit)}</span>
      </div>
      <div className="meter">
        <div className="track">
          <div className={`fill${frac >= 1 ? " over" : frac >= warnRatio ? " warn" : ""}`}
            style={{ width: `${Math.min(frac, 1) * 100}%` }} />
          <div className="tick" style={{ left: `${warnRatio * 100}%` }} title={`Warns at ${Math.round(warnRatio * 100)}%`} />
        </div>
        <div className="legend">
          <span>{Math.round(frac * 100)}% used</span>
          <span>
            {pace && frac < 1
              ? <>on pace to reach the cap <b>{fmtWhenShort(pace.toISOString())}</b></>
              : frac < 1 ? "on pace to finish the month under the cap" : "the cap is reached"}
          </span>
        </div>
      </div>
      <ul className="policy">
        <li><Icon name="alert" size={14} /> Warns you at <b>{Math.round(warnRatio * 100)}%</b></li>
        <li>
          <Icon name="flag" size={14} /> Past <b>100%</b> the CEO must account for the overrun;
          nothing stops
        </li>
        <li>
          <Icon name="pause" size={14} /> Pauses the project at <b>{fmtUnit(wall, unit)}</b>
          {" "}({hardStop}× the budget) — the hard stop
        </li>
        <li>
          <Icon name="shield" size={14} />{" "}
          {dash.billing_secret_name
            ? <>Paid with <b>{dash.billing_secret_name}</b> ({billing ? `${billing.label} ` : ""}{usdMode ? "API key" : "subscription"})</>
            : <span className="serious">No payment credential connected — real runs will fail</span>}
        </li>
        {(dash.credentials ?? []).filter((c) => !c.billing && c.credential_id !== dash.fallback?.credential_id).map((c) => (
          <li key={c.credential_id}><Icon name="shield" size={14} /> {c.label}: {c.name}</li>
        ))}
        {/* The fallback pays only while a failover is on, so it is said as the
            fallback — not as one more provider this project pays. */}
        {dash.fallback?.credential_id && (
          <li><Icon name="shield" size={14} /> Fallback: <b>{dash.fallback.name}</b>
            {dash.fallback.active ? <span className="serious"> — class seats are on it now, while the billing provider recovers</span>
              : " — used only if the billing provider keeps failing"}</li>
        )}
      </ul>
      <PlanMeter credentialId={dash.billing_secret_id} mode={dash.billing_mode}
        meters={!!seller?.plan_meter} />
      {dash.can_steer !== false && (
        <Link className="btnlink small" to={`/projects/${projectId}/settings?s=money`}>
          Change the budget…
        </Link>
      )}
    </div>
  );
}

/** Who is spending it. One series, named rows, sorted — the question is
    "which team costs me money", and a time series cannot answer it.

    These rows are the meter above them broken out: same ledger, same
    month-to-date window, same unit, so they add up to it. A reader who catches
    a card contradicting itself is right to stop believing the rest. */
export function SpendByTeam({ projectId, usdMode, compact }: {
  projectId: string; usdMode: boolean; compact?: boolean;
}) {
  const { data, at } = usePoll(
    () => get<SpendReport>(`/observe/projects/${projectId}/spend?group_by=team&window=month`),
    30000,
  );
  const unit = usdMode ? ("usd" as const) : ("tokens" as const);
  const spent = (r: SpendBucket) => spentOf(r, usdMode);
  const rows = [...(data?.rows ?? [])].sort((a, b) => spent(b) - spent(a));
  if (rows.length === 0) {
    return compact ? <p className="help">Nothing spent yet this month.</p> : null;
  }
  const total = rows.reduce((sum, r) => sum + spent(r), 0);
  const top = rows[0];
  const rest = total - spent(top);
  const month = new Date().toLocaleDateString(undefined, { month: "long", year: "numeric" });

  return (
    <>
      {!compact && (
        <ChartHead
          question={`Where did ${month.split(" ")[0]}'s ${fmtUnit(total, unit)} go?`}
          meta={`By team · ${month} · ${unit === "usd" ? "US dollars" : "tokens"}${at ? ` · as of ${at.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}` : ""}`}
          onExport={() =>
            downloadCsv(
              "spend-by-team.csv",
              ["Team", unit === "usd" ? "Spent (USD)" : "Spent (tokens)", "Share", "Runs"],
              rows.map((r) => [r.label, spent(r), `${Math.round((spent(r) / (total || 1)) * 100)}%`, r.runs]),
            )
          }
        />
      )}
      <BarChart
        bars={rows.map((r) => ({ label: r.label, value: spent(r), display: fmtUnit(spent(r), unit), team: r.label }))}
        total={fmtUnit(total, unit)}
        unit={fmtUnit(Math.max(...rows.map(spent)), unit)}
      />
      {rows.length > 1 && spent(top) > rest && (
        <p className="help" style={{ margin: "var(--s3) 0 0" }}>
          {top.label} costs more than the other {rows.length - 1} put together — worth knowing
          before you raise the cap.
        </p>
      )}
    </>
  );
}

/** The dollars a token-metered project's other keys billed this month. A
    project paid by a subscription can run a seat on an API key — a DeepSeek
    CEO — and that seat's runs move real money the token meter does not show.
    Nothing renders until one has. */
function DollarsBilled({ projectId }: { projectId: string }) {
  const { data } = usePoll(
    () => get<SpendReport>(
      `/observe/projects/${projectId}/spend?group_by=provider&window=month&kind=model_usage`),
    30000,
  );
  const billed = (data?.rows ?? []).filter((r) => r.usd > 0).sort((a, b) => b.usd - a.usd);
  if (billed.length === 0) return null;
  const total = billed.reduce((sum, r) => sum + r.usd, 0);
  return (
    <p className="help" style={{ margin: "var(--s3) 0 0" }}>
      Also billed in dollars this month, by keys outside the subscription:{" "}
      <b>{fmtUsd(total)}</b> — {billed.map((r) => `${r.label} ${fmtUsd(r.usd)}`).join(", ")}.
      Those runs count against the cap by their tokens.
    </p>
  );
}

export default function Spend({ ctx }: { ctx: ProjectCtx }) {
  const { dash, projectId } = ctx;
  const usdMode = dash.billing_mode === "api_key";
  const { data: ledger } = usePoll(
    () => get<LedgerRow[]>(`/observe/projects/${projectId}/ledger?limit=500`),
    30000,
  );
  const health = runHealth(ctx.runs ?? []);
  const { cap } = budgetOf(dash);
  const todaySpent = usdMode ? health.usd : health.tokens;
  // The 14-day chart is thirteen empty bars on a project a day old — it reads
  // as "nothing is happening" when the truth is "there is no history yet".
  const daysOfHistory = new Set((ledger ?? []).map((row) => new Date(row.at).toDateString())).size;

  return (
    <>
      <div className="spend-top">
        <BudgetCard ctx={ctx} />
        <div className="card">
          <div className="kpi-row">
            <div><span>Spent today</span><b>{usdMode ? fmtUsd(todaySpent) : fmtUnit(todaySpent, "tokens")}</b>
              <em>{cap > 0 ? `${Math.round((todaySpent / cap) * 100)}% of the monthly cap` : ""}</em></div>
            <div><span>Runs today</span><b>{health.todays.length}</b>
              <em>{dash.activity.daily_runs.used} of {dash.activity.daily_runs.cap} allowed</em></div>
            <div><span>Ended early</span><b>{health.early.length}</b>
              <em>{health.todays.length ? `${Math.round(health.ratio * 100)}% of today's runs` : "none today"}</em></div>
            <div><span>Last 24 hours</span>
              <b>{usdMode ? fmtUsd(dash.spend_24h) : "—"}</b>
              <em>{dash.evidence_24h} thing{dash.evidence_24h === 1 ? "" : "s"} verified outside</em></div>
          </div>
          {!usdMode && <DollarsBilled projectId={projectId} />}
          {daysOfHistory >= 3 ? (
            <SpendBars ledger={ledger ?? []} usdMode={usdMode} />
          ) : (
            <p className="help" style={{ marginTop: "var(--s4)" }}>
              Spend over time appears once this project has three days of history — there is
              nothing for a trend line to say yet.
            </p>
          )}
        </div>
      </div>

      <div className="card" style={{ marginTop: "var(--s4)" }}>
        <SpendByTeam projectId={projectId} usdMode={usdMode} />
      </div>

      <h2 className="zone">The last 30 days, in detail</h2>
      <SpendBreakdown projectId={projectId} usdMode={usdMode} />
      <p className="help">
        Every run with its own cost is on <Link to={`/projects/${projectId}/details/activity/runs`}>Activity → Runs</Link>.
      </p>
    </>
  );
}
