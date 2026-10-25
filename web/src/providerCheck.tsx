// The provider check: can this model do yaaf's work, not only answer a ping.
//
// A check is tens of real calls per model on the credential's own money, so it
// is never one press: the first press asks the server what it would run and
// roughly what that costs (a dry run), and only the second spends anything.
// It runs in the background and writes each probe and task to its row as it
// finishes, so the screen polls and the list fills in as you watch — a minute
// of a button that only says "Checking…" is indistinguishable from a hang.

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, get, post } from "./api";
import { ConfirmModal, fmtAgo } from "./ui";

export interface CheckOutcome {
  name: string;
  kind: "probe" | "task";
  ok: boolean | null;
  detail: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number | null;
  latency_ms: number;
}

export interface CheckRow {
  id: string;
  credential_id?: string;
  provider?: string;
  model: string;
  status: "running" | "passed" | "partial" | "failed" | "error" | "interrupted";
  summary: string;
  tasks?: boolean;
  outcomes: CheckOutcome[];
  passed: number;
  graded: number;
  steps: number;
  cost_usd: number | null;
  started_at: string | null;
  finished_at: string | null;
  /** On the scoreboard only: averages per graded task. */
  task_calls?: number | null;
  task_cost_usd?: number | null;
  task_seconds?: number | null;
}

interface Planned { models: string[]; steps: number; estimate_usd: number | null; checks: CheckRow[] }

const CRED = "/admin/settings/llm-credentials";

export const fmtUsd = (n: number | null | undefined) =>
  n == null ? "—" : n === 0 ? "$0" : n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`;
const fmtMs = (ms: number) => ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
const human = (name: string) => {
  const words = name.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
};

const STATUS: Record<CheckRow["status"], { word: string; tone: string }> = {
  running: { word: "Running", tone: "accent" },
  passed: { word: "Passed", tone: "ok" },
  partial: { word: "Partly passed", tone: "warn" },
  failed: { word: "Failed", tone: "critical" },
  error: { word: "Could not run", tone: "critical" },
  interrupted: { word: "Interrupted", tone: "warn" },
};

export function CheckChip({ status }: { status: string }) {
  const s = STATUS[status as CheckRow["status"]] ?? { word: status, tone: "" };
  return <span className={`chip ${s.tone}`}>{s.word}</span>;
}

/** The newest check of each model on one credential, polled every two seconds
    while any of them is still running and left alone otherwise. */
export function useCredentialChecks(credentialId: string | null) {
  const [rows, setRows] = useState<CheckRow[]>([]);
  const timer = useRef<number | null>(null);
  const alive = useRef(true);
  const load = useCallback(async () => {
    if (!credentialId) return;
    if (timer.current) { clearTimeout(timer.current); timer.current = null; }
    try {
      const found = await get<CheckRow[]>(`${CRED}/${credentialId}/checks`);
      if (!alive.current) return;
      setRows(found);
      if (found.some((r) => r.status === "running")) {
        timer.current = window.setTimeout(load, 2000);
      }
    } catch { /* a missed poll is not news; the next press asks again */ }
  }, [credentialId]);
  useEffect(() => {
    alive.current = true;
    load();
    return () => { alive.current = false; if (timer.current) clearTimeout(timer.current); };
  }, [load]);
  return { rows, reload: load, running: rows.some((r) => r.status === "running") };
}

/** Ask what a check would run and cost, then — on the second press — run it. */
export function CheckConfirm({ credential, models, onStarted, onCancel }: {
  credential: { id: string; name: string };
  /** The models to check; absent, what each class resolves to. */
  models?: string[] | null;
  onStarted: (note?: string) => void;
  onCancel: () => void;
}) {
  const [tasks, setTasks] = useState(true);
  const [plan, setPlan] = useState<Planned | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const body = (dry: boolean) => ({ ...(models?.length ? { models } : {}), tasks, dry_run: dry });

  useEffect(() => {
    let alive = true;
    setPlan(null); setError("");
    post<Planned>(`${CRED}/${credential.id}/check`, body(true))
      .then((p) => { if (alive) setPlan(p); })
      .catch((e) => { if (alive) setError((e as Error).message); });
    return () => { alive = false; };
  }, [credential.id, tasks, (models ?? []).join(",")]);

  async function start() {
    setBusy(true); setError("");
    try {
      await post<Planned>(`${CRED}/${credential.id}/check`, body(false));
      onStarted();
    } catch (e) {
      // One already running: watch that one rather than refuse to show anything.
      if (e instanceof ApiError && e.status === 409) onStarted(e.message);
      else setError((e as Error).message);
    } finally { setBusy(false); }
  }

  const n = plan?.models.length ?? 0;
  return <ConfirmModal title={`Check ${credential.name}?`} confirmLabel={busy ? "Starting…" : "Run the check"}
    busy={busy || !plan} onConfirm={start} onCancel={onCancel}>
    {error && <p className="error" role="alert">{error}</p>}
    {!plan && !error && <p role="status">Working out what it would run…</p>}
    {plan && <>
      <p>It runs {plan.steps} steps on {n === 1 ? <code>{plan.models[0]}</code>
        : <>each of {plan.models.map((m, i) => <span key={m}>{i > 0 && (i === n - 1 ? " and " : ", ")}<code>{m}</code></span>)}</>}
        {" "}— {tasks ? "short probes (does it call a tool, return valid JSON, follow the rules) and five small agent tasks like a seat's"
          : "the short probes only: does it call a tool, return valid JSON, follow the rules"} — and grades each one mechanically.</p>
      <p><b>{plan.estimate_usd != null ? `Roughly ${fmtUsd(plan.estimate_usd)}` : "No estimate"}</b>{" "}
        {plan.estimate_usd != null ? "of this key's own money, " : "— a model here has no price yet, so the cost cannot be worked out in advance. "}
        and a few minutes. Results land on the provider&apos;s card and in Settings → Models → Compare models.</p>
    </>}
    <label className="check-line">
      <input type="checkbox" checked={!tasks} onChange={(e) => setTasks(!e.target.checked)} />
      Probes only — about a quarter of the calls, and no agent tasks
    </label>
  </ConfirmModal>;
}

/** One model's check: a bar while it runs, then every step with its verdict. */
export function CheckResult({ row, onDismiss }: { row: CheckRow; onDismiss?: () => void }) {
  const running = row.status === "running";
  const done = row.outcomes.length;
  const seconds = row.outcomes.reduce((s, o) => s + (o.latency_ms || 0), 0);
  const cost = row.cost_usd ?? (row.outcomes.some((o) => o.cost_usd != null)
    ? row.outcomes.reduce((s, o) => s + (o.cost_usd ?? 0), 0) : null);
  return <div className="check-result" aria-live={running ? "polite" : undefined}>
    <div className="cr-head">
      <code className="cr-model">{row.model}</code>
      <CheckChip status={row.status} />
      <span className="help grow">
        {running ? `${done} of ${row.steps} steps done — ${row.summary}`
          : `${row.summary}${row.finished_at ? ` · ${fmtAgo(row.finished_at)}` : ""}`}
      </span>
      {onDismiss && !running && <button type="button" className="linkish" onClick={onDismiss}>close</button>}
    </div>
    {running && <div className="cr-track" role="progressbar" aria-valuemin={0} aria-valuemax={row.steps}
      aria-valuenow={done} aria-label={`${row.model}: ${done} of ${row.steps} steps`}>
      <i style={{ width: `${row.steps ? (done / row.steps) * 100 : 0}%` }} /></div>}
    {row.outcomes.length > 0 && <div className="table-scroll"><table className="cr-table">
      <thead><tr><th>Step</th><th>Result</th><th>What happened</th><th className="num">Cost</th><th className="num">Time</th></tr></thead>
      <tbody>{row.outcomes.map((o, i) => <tr key={`${o.name}-${i}`}>
        <td data-label="Step"><b>{human(o.name)}</b> <span className="help">{o.kind}</span></td>
        <td data-label="Result">{o.ok === true ? <span className="good">✓ pass</span>
          : o.ok === false ? <span className="critical-ink">✕ fail</span>
          : <span className="muted" title="Not graded on this model — the step does not apply">– n/a</span>}</td>
        <td data-label="What happened" className="cr-detail">{o.detail || "—"}
          {o.calls > 1 && <span className="help"> · {o.calls} calls</span>}</td>
        <td data-label="Cost" className="num">{fmtUsd(o.cost_usd)}</td>
        <td data-label="Time" className="num">{fmtMs(o.latency_ms)}</td>
      </tr>)}</tbody>
    </table></div>}
    {!running && <p className="help cr-foot">
      {row.graded ? `${row.passed} of ${row.graded} graded steps passed` : "Nothing was graded"}
      {cost != null && ` · ${fmtUsd(cost)}`} · {fmtMs(seconds)} of calls</p>}
  </div>;
}
