import { get, TaskRow } from "../../api";
import { usePoll } from "../../hooks";

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

export default function Board({ projectId }: { projectId: string }) {
  const { data: tasks, error } = usePoll(
    () => get<TaskRow[]>(`/observe/projects/${projectId}/board`),
    6000,
  );
  if (error) return <div className="error">{error}</div>;

  const top = (tasks ?? []).filter((t) => !t.parent_task_id);
  const subtasksOf = (id: string) => (tasks ?? []).filter((t) => t.parent_task_id === id);

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
          {task.blocked_reason && (
            <>
              <dt>blocked because</dt>
              <dd>
                <pre className="block">{task.blocked_reason}</pre>
              </dd>
            </>
          )}
        </dl>
      </details>
    );
  }

  return (
    <>
      <p className="subtitle">
        Read-only by design: only the CEO creates tasks, only leads decompose, only the platform
        marks command-tested work done. Redirect the plan from the boardroom.
      </p>
      <table>
        <thead>
          <tr>
            <th style={{ width: "42%" }}>Task</th>
            <th>Team</th>
            <th>Status</th>
            <th>External</th>
            <th>Holder</th>
          </tr>
        </thead>
        <tbody>
          {top.map((task) => (
            <>
              <tr key={task.id}>
                <td>
                  <strong>{task.title}</strong>
                  <TaskDetails task={task} />
                </td>
                <td>{task.team_name ?? "—"}</td>
                <td>{taskBadge(task.status)}</td>
                <td>{task.external ? "✓ external" : "internal"}</td>
                <td>{task.holder_name ?? "—"}</td>
              </tr>
              {subtasksOf(task.id).map((subtask) => (
                <tr key={subtask.id}>
                  <td style={{ paddingLeft: 32 }}>
                    ↳ {subtask.title}
                    <TaskDetails task={subtask} />
                  </td>
                  <td></td>
                  <td>{taskBadge(subtask.status)}</td>
                  <td></td>
                  <td>{subtask.holder_name ?? "—"}</td>
                </tr>
              ))}
            </>
          ))}
          {top.length === 0 && (
            <tr>
              <td colSpan={5} className="help">
                No tasks yet — the CEO plans on its heartbeat, or immediately after you speak in the
                boardroom.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </>
  );
}
