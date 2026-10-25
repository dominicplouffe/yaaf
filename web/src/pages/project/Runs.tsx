import { CSSProperties, Fragment, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Dashboard, get, getText, RunRow, SpendReport } from "../../api";
import { fmtUsd, fmtWhen, usePoll } from "../../hooks";
import { AsOf, downloadCsv, ExportCsv, fmtAgo, fmtUnit, runHealth, spentOf } from "../../ui";

export interface LedgerRow {
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

/** What kind of thing this run is, in the words the rest of the product uses.
    "agent" for everything-but-checks hid the one kind that behaves differently
    from all the others. */
function kindText(kind: string): string {
  if (kind === "success_check") return "check";
  if (kind === "chat_session") return "chat";
  return "agent";
}

/** A resident chat session is exempt from the run timeout on purpose: it is
    meant to stay open for hours, and only a stopped heartbeat means it died
    (yaaf/server/janitor.py, reap_stale_runs). Rendered as plain "running" it
    was indistinguishable from a work run hung past its 1800s reap — the same
    word, the same colour, a start time from this morning. So say which one it
    is, and prove it with the heartbeat rather than asking anyone to trust it. */
function ResidentNote({ run }: { run: RunRow }) {
  if (run.kind !== "chat_session" || run.status !== "running") return null;
  const beat = run.heartbeat_at;
  // One lease TTL (90s) is the janitor's own patience; past that it reaps.
  const stale = beat !== null && Date.now() - new Date(beat).getTime() > 90_000;
  return (
    <div className="help">
      {!beat
        ? "resident · no heartbeat yet"
        : stale
          ? `still open · last sign of life ${fmtAgo(beat)} — the platform closes it if that stops`
          : `resident · alive, heartbeat ${fmtAgo(beat)}`}
    </div>
  );
}

function triggerText(trigger: Record<string, unknown>): string {
  const reasons = trigger["reasons"] as { kind: string }[] | undefined;
  if (reasons?.length) return reasons.map((r) => r.kind).join(", ");
  return String(trigger["type"] ?? "—");
}

/** The context document a run sent to the model: system prompt + context pack,
    rendered as two blocks when it parses, raw text when it does not. */
function ContextView({ text }: { text: string }) {
  const block: CSSProperties = { maxHeight: 320, overflow: "auto", margin: 0 };
  let doc: { system_prompt?: string | null; context_pack?: unknown } | null = null;
  try {
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed === "object") doc = parsed;
  } catch {
    doc = null;
  }
  if (!doc) {
    return <pre className="block mono" style={block}>{text}</pre>;
  }
  return (
    <>
      <div className="label">System prompt</div>
      <pre className="block mono" style={block}>{doc.system_prompt ?? "(none)"}</pre>
      <div className="label" style={{ marginTop: 8 }}>Context pack</div>
      <pre className="block mono" style={block}>
        {doc.context_pack != null ? JSON.stringify(doc.context_pack, null, 2) : "(none)"}
      </pre>
    </>
  );
}

/** Fourteen plain bars, one series, today emphasized — readable in five
    seconds. This and the budget meter are the only charts the product needs. */
export function SpendBars({ ledger, usdMode }: { ledger: LedgerRow[]; usdMode: boolean }) {
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
export function SpendBreakdown({ projectId, usdMode }: { projectId: string; usdMode: boolean }) {
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
      <div className="table-scroll">
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
                {usdMode ? fmtUsd(row.usd) : tok(spentOf(row, usdMode))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </div>
  );
}

export default function Runs({ projectId, dash }: { projectId: string; dash: Dashboard }) {
  // One person's runs (from their page) or one run (from "View run" on a
  // line of the record), narrowed by the server rather than filtered here.
  const [params, setParams] = useSearchParams();
  const onlyAgent = params.get("agent");
  const onlyRun = params.get("run");
  const narrowed = new URLSearchParams({ limit: "50" });
  if (onlyAgent) narrowed.set("agent_id", onlyAgent);
  if (onlyRun) narrowed.set("run_id", onlyRun);
  const { data: runs, error, at } = usePoll(
    () => get<RunRow[]>(`/observe/projects/${projectId}/runs?${narrowed}`),
    5000, [onlyAgent, onlyRun],
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

  // The context document (system prompt + context pack) — same fetch-once
  // cache and toggle as traces.
  const [contexts, setContexts] = useState<Record<string, string>>({});
  const [openContexts, setOpenContexts] = useState<Record<string, boolean>>({});

  async function toggleContext(runId: string) {
    if (openContexts[runId]) {
      setOpenContexts((open) => ({ ...open, [runId]: false }));
      return;
    }
    setOpenContexts((open) => ({ ...open, [runId]: true }));
    if (contexts[runId] === undefined) {
      try {
        const text = await getText(`/observe/runs/${runId}/context`);
        setContexts((cached) => ({ ...cached, [runId]: text }));
      } catch (e) {
        setContexts((cached) => ({ ...cached, [runId]: `⚠ ${(e as Error).message}` }));
      }
    }
  }

  if (error && !runs) return <div className="error">{error}</div>;

  const usdMode = dash.billing_mode === "api_key";
  const health = runHealth(runs ?? []);
  const todayRuns = health.todays;
  const todayUsd = health.usd;
  const todayTokens = health.tokens;
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
        the project itself.
      </p>

      {/* Outcomes before receipts. "12 failures" in the same weight as
          everything else is not a report — a run that ended early was paid for
          and produced nothing, and the reasons are what tell you what to fix. */}
      <div className="tiles" style={{ marginTop: 0 }}>
        <div className="tile">
          <div className="label">Runs today</div>
          <div className="value">{todayRuns.length}</div>
          <div className="sub">
            {usdMode || todayUsd > 0
              ? `$${todayUsd.toLocaleString(undefined, { maximumFractionDigits: 2 })}`
              : `${fmtUnit(todayTokens, "tokens")} tokens`}{" "}
            spent
          </div>
        </div>
        <div className="tile">
          <div className="label">Finished cleanly</div>
          <div className="value">{health.clean}</div>
          <div className="sub">
            {todayRuns.length > 0 ? `${Math.round((health.clean / todayRuns.length) * 100)}%` : "—"}
          </div>
        </div>
        <div className={`tile${health.ratio >= 0.2 ? " alert" : ""}`}>
          <div className="label">Ended early</div>
          <div className="value">{health.early.length}</div>
          <div className="sub">
            {health.byReason.length > 0
              ? health.byReason.map((r) => `${r.count} ${r.phrase}`).join(" · ")
              : "none"}
          </div>
        </div>
        <div className="tile">
          <div className="label">Still running</div>
          <div className="value">{health.running}</div>
          <div className="sub">
            {dash.runs.queued > 0 ? `${dash.runs.queued} queued behind them` : "nothing queued"}
            {dash.chat_live && " · chat session resident"}
          </div>
        </div>
      </div>

      {/* Money over time has a page of its own now, beside the budget it is
          spending against; this one stays about the runs themselves. */}
      <p className="help" style={{ margin: "12px 0 16px" }}>
        Spend per day and by model, team or person is on the{" "}
        <Link to={`/projects/${projectId}/details/spend`}>Spend page →</Link>
      </p>

      {/* Patterns become sentences: the platform knows these share a cause —
          and names the wall, so "give direction" means something to type. */}
      {storms.length >= 3 && (
        <div className="arow" style={{ borderColor: "rgba(var(--serious-rgb), 0.4)", marginBottom: 14 }}>
          <span className="badge serious">pattern</span>
          <div className="grow">
            <div className="tt">
              {storms.length} runs this week ended in refusal storms
            </div>
            <div className="sb">
              The rules held — but at full API price. The walls being hit:
            </div>
            {[...storms.reduce((byAgent, r) => {
              const name = r.agent_name ?? "unknown agent";
              const entry = byAgent.get(name) ?? { count: 0, wall: null as string | null };
              entry.count += 1;
              entry.wall = entry.wall ?? r.last_refusal; // runs are newest-first
              return byAgent.set(name, entry);
            }, new Map<string, { count: number; wall: string | null }>())].map(
              ([name, { count, wall }]) => (
                <div className="sb" key={name}>
                  <b>{name}</b> ×{count}
                  {wall && <> — “{wall.length > 160 ? `${wall.slice(0, 160)}…` : wall}”</>}
                </div>
              ),
            )}
            {/* "Tell the CEO" is the right move for a work rule and the wrong
                one for a full memory: the CEO cannot free a team's slot either.
                Point at the thing that actually ends this wall. */}
            {storms.some((r) => (r.last_refusal ?? "").startsWith("Memory cap")) ? (
              <div className="sb">
                A full memory refuses every write in that scope, and only its curator can fold
                entries to make room — which it has been woken to do. If the job genuinely needs
                to remember more than the cap allows, give it more room; that applies to the
                very next run.
              </div>
            ) : (
              <div className="sb">
                Agents cannot lift these rules themselves. Tell the CEO what should change —
                finish, reassign, or kill the task in question — and it will redirect the agent.
              </div>
            )}
          </div>
          {storms.some((r) => (r.last_refusal ?? "").startsWith("Memory cap")) ? (
            <Link to={`/projects/${projectId}/settings`}>give it more room →</Link>
          ) : (
            <Link to={`/projects/${projectId}/chat`}>give direction →</Link>
          )}
        </div>
      )}

      {(onlyAgent || onlyRun) && (
        <div className="notice-box" style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
          <span className="grow">
            {onlyRun ? "Showing one run." : `Showing ${runs?.[0]?.agent_name ?? "one person"}'s runs only.`}
          </span>
          <button className="small" onClick={() => setParams({}, { replace: true })}>Show every run</button>
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
                <td>{fmtWhen(run.started_at)}</td>
                <td>
                  {run.agent_name ?? <span className="help">—</span>}
                  {run.team_name && <div className="help">{run.team_name}</div>}
                </td>
                <td>{kindText(run.kind)}</td>
                <td>
                  {runBadge(run.status)}
                  <ResidentNote run={run} />
                  {run.refusals > 0 && (
                    <div className="help">{run.refusals} refusal(s)</div>
                  )}
                </td>
                <td>{triggerText(run.trigger)}</td>
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
                  )}{" "}
                  {run.has_context && (
                    <button className="small" onClick={() => toggleContext(run.id)}>
                      {openContexts[run.id] ? "hide context" : "context"}
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
              {openContexts[run.id] && (
                <tr>
                  <td colSpan={9}>
                    {contexts[run.id] === undefined ? (
                      <pre
                        className="block mono"
                        style={{ maxHeight: 320, overflow: "auto", margin: 0 }}
                      >
                        loading…
                      </pre>
                    ) : (
                      <ContextView text={contexts[run.id]} />
                    )}
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
      <AsOf at={at} error={error} />
    </>
  );
}
