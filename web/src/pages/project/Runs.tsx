import { get, RunRow } from "../../api";
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
            <th>Kind</th>
            <th>Status</th>
            <th>Woken because</th>
            <th className="num">Tokens out</th>
            <th className="num">Cost</th>
            <th style={{ width: "38%" }}>Summary</th>
          </tr>
        </thead>
        <tbody>
          {(runs ?? []).map((run) => (
            <tr key={run.id}>
              <td className="mono">{fmtWhen(run.started_at)}</td>
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
            </tr>
          ))}
          {runs && runs.length === 0 && (
            <tr>
              <td colSpan={7} className="help">
                No runs yet. An idle project costs exactly nothing — that's the point.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </>
  );
}
