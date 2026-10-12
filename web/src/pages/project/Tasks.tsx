import { Link, useSearchParams } from "react-router-dom";
import { TaskRow } from "../../api";
import { fmtWhen } from "../../hooks";
import { firstName, ProjectCtx } from "../../projectCtx";
import { clip, Skeleton } from "../../ui";
import { Avatar, Seg, teamHue } from "../../ui/kit";
import { Icon } from "../../ui/Icon";
import Work, { AuditSamples } from "./Work";

/** The work, as a board.

    The same tasks the table lists, grouped by where they are — because a
    board is the one view of work every reader already knows how to read, and
    "what is stuck?" is answered by looking at a column rather than filtering
    a list. The table (bets, subtasks, proof per bet, the audit) is the List
    view, one press away, unchanged.

    Read-only, and said in one line with the way forward beside it: only the
    CEO creates tasks, only leads decompose, only the platform marks tested
    work done. The button a reader would look for — "New task" — is "Ask the
    CEO", because that is the real verb. */

type Column = { key: string; label: string; tone: string; match: (t: TaskRow) => boolean };

const scheduled = (t: TaskRow) =>
  t.status === "ready" && !!t.not_before && new Date(t.not_before) > new Date();

const COLUMNS: Column[] = [
  { key: "ready", label: "Ready", tone: "", match: (t) => t.status === "ready" },
  { key: "doing", label: "In progress", tone: "accent", match: (t) => t.status === "claimed" },
  { key: "checking", label: "Being checked", tone: "warn",
    match: (t) => t.status === "checking" || t.status === "review" },
  { key: "blocked", label: "Stuck", tone: "serious", match: (t) => t.status === "blocked" },
  { key: "done", label: "Done", tone: "good", match: (t) => t.status === "done" },
];

/** Done grows for ever; the newest are the ones a reader came to see. */
const DONE_SHOWN = 12;

function TaskCard({ task, parent, bet, onPerson }: {
  task: TaskRow;
  parent: TaskRow | undefined;
  bet: string | undefined;
  onPerson: (agentId: string) => void;
}) {
  const holder = task.claimed_by ?? task.assignee;
  return (
    <div className="tcard">
      {parent && <div className="tc-parent">↳ part of {clip(parent.title, 48)}</div>}
      <div className="tc-t">{task.title}</div>
      {bet && <div className="tc-bet"><Icon name="target" size={12} /> {clip(bet, 60)}</div>}
      {task.status === "blocked" && task.blocked_reason && (
        <div className="tc-note serious">{clip(task.blocked_reason, 140)}</div>
      )}
      {scheduled(task) && <div className="tc-note warn">Starts {fmtWhen(task.not_before)}</div>}
      {(task.status === "checking" || task.status === "review") && (
        <div className="tc-note">
          {task.status === "checking" ? "The platform is running its test." : "Waiting for a second read."}
        </div>
      )}
      <div className="tc-f">
        <span className={`team-dot${teamHue(task.team_name)}`} />
        <span className="grow tc-team">{task.team_name ?? "—"}</span>
        {task.external && (
          <span className="tc-proof" title="Its success needs proof from outside the project">
            <Icon name="shield" size={12} /> outside proof
          </span>
        )}
        {task.audit_status && <span className="tc-proof">audited</span>}
        {holder && task.holder_name ? (
          <button className="tc-who" onClick={() => onPerson(holder)} title={`${task.holder_name} — see what they are doing`}>
            <Avatar name={task.holder_name} team={task.team_name} size="sm" />
          </button>
        ) : (
          <span className="tc-nobody">unassigned</span>
        )}
      </div>
    </div>
  );
}

function Board({ ctx }: { ctx: ProjectCtx }) {
  const { tasks, hypotheses, projectId } = ctx;
  if (!tasks) return <Skeleton rows={4} />;
  const byId = new Map(tasks.map((t) => [t.id, t]));
  const bets = new Map(hypotheses.map((h) => [h.id, h.statement]));
  const killed = tasks.filter((t) => t.status === "killed").length;
  if (tasks.length === 0) {
    return (
      <p className="help">
        No work yet. Your CEO plans on its own schedule, or right after you speak to it.{" "}
        <Link to={`/projects/${projectId}/chat`}>Open the chat →</Link>
      </p>
    );
  }
  return (
    <>
      <AuditSamples projectId={projectId} />
      <div className="board">
        {COLUMNS.map((col) => {
          const all = tasks.filter(col.match);
          const shown = col.key === "done" ? all.slice(0, DONE_SHOWN) : all;
          return (
            <section className="bcol" key={col.key} aria-label={col.label}>
              <div className="bcol-h">
                <span className={`badge${col.tone ? ` ${col.tone}` : ""}`}>{col.label}</span>
                <span className="bcol-n">{all.length}</span>
              </div>
              {shown.map((t) => (
                <TaskCard key={t.id} task={t}
                  parent={t.parent_task_id ? byId.get(t.parent_task_id) : undefined}
                  bet={t.hypothesis_id ? bets.get(t.hypothesis_id) : undefined}
                  onPerson={ctx.openAgent} />
              ))}
              {all.length === 0 && <div className="bcol-empty">Nothing here</div>}
              {all.length > shown.length && (
                <Link className="bcol-more" to="?view=list">and {all.length - shown.length} more in the list</Link>
              )}
            </section>
          );
        })}
      </div>
      {killed > 0 && (
        <p className="help" style={{ marginTop: 10 }}>
          {killed} task{killed === 1 ? " was" : "s were"} dropped; they are in the{" "}
          <Link to="?view=list">list</Link>.
        </p>
      )}
    </>
  );
}

export default function Tasks({ ctx }: { ctx: ProjectCtx }) {
  const [params, setParams] = useSearchParams();
  const view = params.get("view") === "list" ? "list" : "board";
  const ceo = firstName(ctx.ceoName);
  return (
    <>
      <div className="relay">
        <Icon name="message" size={16} />
        <span>
          {ceo} creates and assigns the work; team leads split it up. This board is read-only —
          to change the plan, tell {ceo}.
        </span>
        <Link to={`/projects/${ctx.projectId}/chat`} className="btnlink small">Ask {ceo} for a change</Link>
      </div>
      <div className="viewbar">
        <Seg<"board" | "list">
          value={view}
          onPick={(v) => setParams(v === "list" ? { view: "list" } : {}, { replace: true })}
          options={[
            { key: "board", label: "Board", count: ctx.dash.open_tasks },
            { key: "list", label: "List & bets" },
          ]}
        />
      </div>
      {view === "board" ? <Board ctx={ctx} /> : <Work ctx={ctx} />}
    </>
  );
}
