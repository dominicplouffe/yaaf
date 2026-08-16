import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { get, HypothesisRow, TaskRow } from "../../api";
import { usePoll } from "../../hooks";
import { downloadCsv, ExportCsv, killChip } from "../../ui";

function taskBadge(status: string) {
  const cls =
    status === "done"
      ? "good"
      : status === "blocked"
        ? "serious"
        : status === "killed"
          ? "critical"
          : status === "checking"
            ? "warn"
            : "accent";
  return <span className={`badge ${cls}`}>{status}</span>;
}

const FILTERS = [
  { key: "open", label: "Open", match: (s: string) => ["ready", "claimed", "checking"].includes(s) },
  { key: "blocked", label: "Blocked", match: (s: string) => s === "blocked" },
  { key: "done", label: "Done", match: (s: string) => s === "done" },
  { key: "killed", label: "Killed", match: (s: string) => s === "killed" },
  { key: "all", label: "All", match: () => true },
] as const;

/** Tasks, grouped under the bet they serve. Work inherits meaning — and
    mortality — from its hypothesis: when a bet expires, its blocked children
    sit right under the expiry notice. Still read-only, still one dense table. */
export default function Tasks({ projectId }: { projectId: string }) {
  const { data: tasks, error } = usePoll(
    () => get<TaskRow[]>(`/observe/projects/${projectId}/board`),
    6000,
  );
  const { data: hypotheses } = usePoll(
    () => get<HypothesisRow[]>(`/observe/projects/${projectId}/hypotheses`),
    15000,
  );
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["key"]>("all");
  if (error) return <div className="error">{error}</div>;

  const all = tasks ?? [];
  const active = FILTERS.find((f) => f.key === filter)!;
  const top = all.filter((t) => !t.parent_task_id);
  const subtasksOf = (id: string) => all.filter((t) => t.parent_task_id === id);
  // A group survives the filter if the bet's top task or any subtask matches —
  // a blocked subtask should never hide under a filtered-out parent.
  const visible = top.filter(
    (t) => active.match(t.status) || subtasksOf(t.id).some((s) => active.match(s.status)),
  );

  const byHypothesis = new Map<string | null, TaskRow[]>();
  for (const t of visible) {
    const key = t.hypothesis_id;
    byHypothesis.set(key, [...(byHypothesis.get(key) ?? []), t]);
  }
  const hypothesisById = new Map((hypotheses ?? []).map((h) => [h.id, h]));
  // Bets first (active before expired), then work that serves no stated bet.
  const groups = [...byHypothesis.entries()].sort(([a], [b]) => {
    const ha = a ? hypothesisById.get(a) : null;
    const hb = b ? hypothesisById.get(b) : null;
    if (!a) return 1;
    if (!b) return -1;
    return (ha?.status === "active" ? 0 : 1) - (hb?.status === "active" ? 0 : 1);
  });

  function TaskDetails({ task }: { task: TaskRow }) {
    return (
      <details>
        <summary className="help">details</summary>
        <dl className="kv" style={{ margin: "8px 0" }}>
          <dt>success test</dt>
          <dd className="mono">
            [{task.success_test.type}] {task.success_test.command ?? task.success_test.target}
          </dd>
          {task.evidence && (
            <>
              <dt>completion evidence</dt>
              <dd>{task.evidence}</dd>
            </>
          )}
          {task.check_output && (
            <>
              <dt>platform check output</dt>
              <dd>
                <pre className="block">{task.check_output}</pre>
              </dd>
            </>
          )}
          {task.audit_status && (
            <>
              <dt>your audit</dt>
              <dd>{task.audit_status}</dd>
            </>
          )}
        </dl>
      </details>
    );
  }

  function row(task: TaskRow, sub: boolean) {
    return (
      <tr key={task.id}>
        <td style={sub ? { paddingLeft: 32 } : undefined}>
          {sub ? <>↳ {task.title}</> : <strong>{task.title}</strong>}
          <TaskDetails task={task} />
        </td>
        <td>{sub ? "" : task.team_name ?? "—"}</td>
        <td>
          {taskBadge(task.status)}
          {/* Blocked reasons on the row, in plain words — a blocked task is
              precisely the row that must be understood without clicking. */}
          {task.blocked_reason && (
            <div className="help" style={{ marginTop: 2 }}>{task.blocked_reason}</div>
          )}
        </td>
        <td>{sub ? "" : task.external ? "✓ external" : "internal"}</td>
        <td>{task.holder_name ?? "—"}</td>
      </tr>
    );
  }

  return (
    <>
      <div className="pills">
        {FILTERS.map((f) => {
          const count = all.filter((t) => f.match(t.status)).length;
          return (
            <button
              key={f.key}
              className={filter === f.key ? "active" : ""}
              onClick={() => setFilter(f.key)}
            >
              {f.label} {count}
            </button>
          );
        })}
        <ExportCsv
          onClick={() =>
            downloadCsv(
              "tasks.csv",
              ["title", "parent", "team", "status", "external", "holder", "success_test", "blocked_reason", "audit"],
              all.map((t) => [
                t.title,
                t.parent_task_id ?? "",
                t.team_name,
                t.status,
                t.external ? "external" : "internal",
                t.holder_name,
                `[${t.success_test.type}] ${t.success_test.command ?? t.success_test.target ?? ""}`,
                t.blocked_reason,
                t.audit_status,
              ]),
            )
          }
        />
      </div>

      {groups.map(([hypothesisId, groupTasks]) => {
        const h = hypothesisId ? hypothesisById.get(hypothesisId) : null;
        return (
          <Fragment key={hypothesisId ?? "standing"}>
            <div className="bet-head">
              <span className="tag">{h ? "BET" : "STANDING WORK"}</span>
              {h && (
                <>
                  <strong>{h.statement}</strong>
                  {killChip(h.kill_date, h.status)}
                  <div className="spacer" />
                  <Link to={`/projects/${projectId}/evidence`}>the bet →</Link>
                </>
              )}
              {!h && <span className="help">tasks that serve no stated bet</span>}
            </div>
            <div className="bet-body">
              <table>
                <thead>
                  <tr>
                    <th style={{ width: "44%" }}>Task</th>
                    <th>Team</th>
                    <th>Status</th>
                    <th>Proof type</th>
                    <th>Holder</th>
                  </tr>
                </thead>
                <tbody>
                  {groupTasks.map((task) => (
                    <Fragment key={task.id}>
                      {row(task, false)}
                      {subtasksOf(task.id).map((subtask) => row(subtask, true))}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          </Fragment>
        );
      })}
      {visible.length === 0 && (
        <p className="help">
          {all.length === 0
            ? "No tasks yet — the CEO plans on its heartbeat, or immediately after you speak in the boardroom."
            : "Nothing matches this filter."}
        </p>
      )}
      <p className="help" style={{ marginTop: 12 }}>
        Read-only by design: only the CEO creates tasks, only leads decompose, only the platform
        marks command-tested work done. Redirect the plan from the{" "}
        <Link to={`/projects/${projectId}/boardroom`}>Boardroom →</Link>
      </p>
    </>
  );
}
