import { Fragment, useState } from "react";
import { get, getText, RunRow } from "../../api";
import { fmtUsd, fmtWhen, usePoll } from "../../hooks";

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
  return <span className={`badge ${cls}`}>{status}</span>;
}

function triggerText(trigger: Record<string, unknown>): string {
  const reasons = trigger["reasons"] as { kind: string }[] | undefined;
  if (reasons?.length) return reasons.map((r) => r.kind).join(", ");
  return String(trigger["type"] ?? "—");
}

export default function Runs({ projectId }: { projectId: string }) {
  const { data: runs, error } = usePoll(
    () => get<RunRow[]>(`/observe/projects/${projectId}/runs?limit=50`),
    5000,
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

  return (
    <>
      <p className="subtitle">
        Every model invocation, why it happened, and what it cost — including the cost of running
        the company itself.
      </p>
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
            <th style={{ width: "38%" }}>Summary</th>
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
    </>
  );
}
