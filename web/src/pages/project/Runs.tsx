import { Fragment, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Dashboard, get, getText, RunRow, SpendReport } from "../../api";
import { fmtUsd, fmtWhen, usePoll } from "../../hooks";
import { downloadCsv, ExportCsv, fmtUnit } from "../../ui";

interface LedgerRow {
  id: string;
  kind: string;
  tokens_in: number;
  tokens_out: number;
  cache_creation: number;
  cache_read: number;
  usd: number | null;
  note: string | null;
  at: string;
}

function runBadge(status: string) {
  const cls =
    status === "done"
      ? "good"
      : status === "running"
        ? "accent"
        : status === "failed" || status === "reaped"
          ? "serious"
          : status === "terminated_refusals" || status === "killed"
            ? "critical"
            : "warn";
  const label = status === "terminated_refusals" ? "terminated · refusals" : status;
  return <span className={`badge ${cls}`}>{label}</span>;
}

function triggerText(trigger: Record<string, unknown>): string {
  const reasons = trigger["reasons"] as { kind: string }[] | undefined;
  if (reasons?.length) return reasons.map((r) => r.kind).join(", ");
  return String(trigger["type"] ?? "—");
}

/** Fourteen plain bars, one series, today emphasized — readable in five
    seconds. This and the budget meter are the only charts the product needs. */
function SpendBars({ ledger, usdMode }: { ledger: LedgerRow[]; usdMode: boolean }) {
  const days: { key: string; label: string; value: number }[] = [];
  for (let i = 13; i >= 0; i--) {
    const d = new Date();
    d.setDate(d.getDate() - i);
    days.push({
      key: d.toDateString(),
      label: d.toLocaleDateString(undefined, { month: "short", day: "numeric" }),
      value: 0,
    });
  }
  const byKey = new Map(days.map((d) => [d.key, d]));
  for (const row of ledger) {
    const day = byKey.get(new Date(row.at).toDateString());
    if (day)
      day.value += usdMode
        ? (row.usd ?? 0)
        : row.tokens_in + row.tokens_out + row.cache_creation + row.cache_read;
  }
  const max = Math.max(...days.map((d) => d.value), 1e-9);
  const fmt = (v: number) =>
    usdMode ? `$${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}` : v.toLocaleString();
  return (
    <div className="bars-wrap">
      <div className="bars-label">Spend per day — last 14 days ({usdMode ? "USD" : "tokens"})</div>
      <div className="bars">
        {days.map((d, i) => (
          <i
            key={d.key}
            className={i === days.length - 1 ? "hi" : ""}
            style={{ height: `${Math.max((d.value / max) * 100, 1.5)}%` }}
            title={`${d.label}: ${fmt(d.value)}`}
          />
        ))}
      </div>
      <div className="bars-base" />
      <div className="bars-axis">
        <span>{days[0].label}</span>
        <span>{days[days.length - 1].label}</span>
      </div>
    </div>
  );
}

/** Where the money goes: a 30-day rollup by model, team, or agent. `Input` is
    only the uncached remainder — the cache columns are the rest of the real
    input bill. */
function SpendBreakdown({ projectId, usdMode }: { projectId: string; usdMode: boolean }) {
  const [groupBy, setGroupBy] = useState<"model" | "team" | "agent">("model");
  const { data, reload } = usePoll(
    () => get<SpendReport>(`/observe/projects/${projectId}/spend?group_by=${groupBy}&days=30`),
    15000,
  );
  useEffect(() => reload(), [groupBy, reload]);
  const rows = data?.rows ?? [];
  if (rows.length === 0) return null;
  const tok = (n: number) => fmtUnit(n, "tokens");
  return (
    <div style={{ margin: "0 0 14px" }}>
      <div className="pills">
        <span className="label" style={{ marginRight: 6 }}>Spend by</span>
        {(["model", "team", "agent"] as const).map((g) => (
          <button key={g} className={groupBy === g ? "active" : ""} onClick={() => setGroupBy(g)}>
            {g}
          </button>
        ))}
        <span className="help">last 30 days</span>
      </div>
      <table>
        <thead>
          <tr>
            <th>{groupBy === "model" ? "Model" : groupBy === "team" ? "Team" : "Agent"}</th>
            <th className="num">Runs</th>
            <th className="num">Input</th>
            <th className="num">Cache write</th>
            <th className="num">Cache read</th>
            <th className="num">Output</th>
            <th className="num">{usdMode ? "Cost" : "Tokens"}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key}>
              <td>{row.label}</td>
              <td className="num">{row.runs}</td>
              <td className="num">{tok(row.tokens_in)}</td>
              <td className="num">{tok(row.cache_creation)}</td>
              <td className="num">{tok(row.cache_read)}</td>
              <td className="num">{tok(row.tokens_out)}</td>
              <td className="num">
                {usdMode
                  ? fmtUsd(row.usd)
                  : tok(row.tokens_in + row.tokens_out + row.cache_creation + row.cache_read)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function Runs({ projectId, dash }: { projectId: string; dash: Dashboard }) {
  const { data: runs, error } = usePoll(
    () => get<RunRow[]>(`/observe/projects/${projectId}/runs?limit=50`),
    5000,
  );
  const { data: ledger } = usePoll(
    () => get<LedgerRow[]>(`/observe/projects/${projectId}/ledger?limit=500`),
    15000,
  );
  // Traces are fetched once per run (plain text, can be large) and cached here;
  // the toggle only shows/hides the cached copy after that.
  const [traces, setTraces] = useState<Record<string, string>>({});
  const [openTraces, setOpenTraces] = useState<Record<string, boolean>>({});

  async function toggleTrace(runId: string) {
    if (openTraces[runId]) {
      setOpenTraces((open) => ({ ...open, [runId]: false }));
      return;
    }
    setOpenTraces((open) => ({ ...open, [runId]: true }));
    if (traces[runId] === undefined) {
      try {
        const text = await getText(`/observe/runs/${runId}/trace`);
        setTraces((cached) => ({ ...cached, [runId]: text }));
      } catch (e) {
        setTraces((cached) => ({ ...cached, [runId]: `⚠ ${(e as Error).message}` }));
      }
    }
  }

  if (error) return <div className="error">{error}</div>;

  const usdMode = dash.billing_mode === "api_key";
  const today = new Date().toDateString();
  const todayRuns = (runs ?? []).filter(
    (r) => r.started_at && new Date(r.started_at).toDateString() === today,
  );
  const todayUsd = todayRuns.reduce((sum, r) => sum + (r.cost_usd ?? 0), 0);
  const todayTokens = todayRuns.reduce(
    (sum, r) => sum + r.tokens_in + r.tokens_out + r.cache_creation + r.cache_read,
    0,
  );
  const todayFailures = todayRuns.filter((r) =>
    ["failed", "reaped", "killed", "terminated_refusals"].includes(r.status),
  ).length;
  const weekAgo = Date.now() - 7 * 86_400_000;
  const storms = (runs ?? []).filter(
    (r) =>
      r.status === "terminated_refusals" &&
      r.started_at &&
      new Date(r.started_at).getTime() >= weekAgo,
  );

  return (
    <>
      <p className="subtitle">
        Every model invocation, why it happened, and what it cost — including the cost of running
        the company itself.
      </p>

      {/* Totals before receipts: the first number answers the question that
          brought the chairman here. */}
      <div style={{ display: "flex", gap: 24, alignItems: "flex-end", flexWrap: "wrap", margin: "0 0 14px" }}>
        <div className="tile" style={{ border: "none", padding: 0, background: "none" }}>
          <div className="label">Today</div>
          <div className="value">
            {usdMode || todayUsd > 0
              ? `$${todayUsd.toLocaleString(undefined, { maximumFractionDigits: 2 })}`
              : `${fmtUnit(todayTokens, "tokens")} tokens`}{" "}
            <span style={{ fontSize: 14, color: "var(--muted)" }}>
              · {todayRuns.length} run{todayRuns.length === 1 ? "" : "s"} · {todayFailures} failure
              {todayFailures === 1 ? "" : "s"}
            </span>
          </div>
        </div>
        {(ledger ?? []).length > 0 && <SpendBars ledger={ledger ?? []} usdMode={usdMode} />}
      </div>

      <SpendBreakdown projectId={projectId} usdMode={usdMode} />

      {/* Patterns become sentences: the platform knows these share a cause. */}
      {storms.length >= 3 && (
        <div className="arow" style={{ borderColor: "rgba(236,131,90,.4)", marginBottom: 14 }}>
          <span className="badge serious">pattern</span>
          <div className="grow">
            <div className="tt">
              {storms.length} runs this week ended in refusal storms
            </div>
            <div className="sb">
              The rules held — but an agent keeps arguing with a wall, at full API price. Consider
              redirecting it in the Boardroom.
            </div>
          </div>
          <Link to={`/projects/${projectId}/boardroom`}>give direction →</Link>
        </div>
      )}

      <table>
        <thead>
          <tr>
            <th>Started</th>
            <th>Agent</th>
            <th>Kind</th>
            <th>Status</th>
            <th>Woken because</th>
            <th className="num">Tokens out</th>
            <th className="num">Cost</th>
            <th style={{ width: "34%" }}>Summary</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {(runs ?? []).map((run) => (
            <Fragment key={run.id}>
              <tr>
                <td className="mono">{fmtWhen(run.started_at)}</td>
                <td>
                  {run.agent_name ?? <span className="help">—</span>}
                  {run.team_name && <div className="help">{run.team_name}</div>}
                </td>
                <td className="mono">{run.kind === "success_check" ? "check" : "agent"}</td>
                <td>
                  {runBadge(run.status)}
                  {run.refusals > 0 && (
                    <div className="help">{run.refusals} refusal(s)</div>
                  )}
                </td>
                <td className="mono">{triggerText(run.trigger)}</td>
                <td className="num">{run.tokens_out.toLocaleString()}</td>
                <td className="num">{fmtUsd(run.cost_usd)}</td>
                <td>
                  {run.summary ? (
                    <details>
                      <summary>{run.summary.slice(0, 90)}{run.summary.length > 90 ? "…" : ""}</summary>
                      <pre className="block">{run.summary}</pre>
                    </details>
                  ) : (
                    <span className="help">—</span>
                  )}
                </td>
                <td>
                  {run.has_trace && (
                    <button className="small" onClick={() => toggleTrace(run.id)}>
                      {openTraces[run.id] ? "hide trace" : "trace"}
                    </button>
                  )}
                </td>
              </tr>
              {openTraces[run.id] && (
                <tr>
                  <td colSpan={9}>
                    <pre
                      className="block mono"
                      style={{ maxHeight: 320, overflow: "auto", margin: 0 }}
                    >
                      {traces[run.id] ?? "loading…"}
                    </pre>
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
          {runs && runs.length === 0 && (
            <tr>
              <td colSpan={9} className="help">
                No runs yet. An idle project costs exactly nothing — that's the point.
              </td>
            </tr>
          )}
        </tbody>
      </table>
      {(runs ?? []).length > 0 && (
        <div style={{ display: "flex", marginTop: 8 }}>
          <ExportCsv
            onClick={() =>
              downloadCsv(
                "runs.csv",
                ["started", "agent", "team", "model", "kind", "status", "trigger", "tokens_in", "tokens_out", "cache_creation", "cache_read", "cost_usd", "refusals", "summary"],
                (runs ?? []).map((r) => [
                  r.started_at,
                  r.agent_name,
                  r.team_name,
                  r.model,
                  r.kind,
                  r.status,
                  triggerText(r.trigger),
                  r.tokens_in,
                  r.tokens_out,
                  r.cache_creation,
                  r.cache_read,
                  r.cost_usd,
                  r.refusals,
                  r.summary,
                ]),
              )
            }
          />
        </div>
      )}
    </>
  );
}
