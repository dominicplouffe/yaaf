/** Goal → requirement → task: why a piece of work exists, in one line.

    A task title alone ("pull Saturday's lineups") says what is being done and
    not why; the why lived two pages away, on the Roadmap, under a key the
    reader had to look up. The chain draws it wherever work is shown — on a
    task, a result, a person's "now" — so nothing the team does reaches the
    chairman without its reason attached. */
import { requirementName, TaskRow } from "./api";
import type { ProjectCtx } from "./projectCtx";
import { clip } from "./ui";
import { Icon } from "./ui/Icon";

export function GoalChain({ goal, serves, task }: {
  goal: string; serves: { key: string; statement: string }[]; task: { title: string } | null;
}) {
  const lead = clip(goal.split(/(?<=\.)\s/)[0] ?? goal, 90);
  return (
    <span className="goal-chain">
      <span title={goal}>{lead}</span>
      {serves.map((s) => (
        <span key={s.key}><Icon name="right" size={11} /> <b className="mono">{s.key}</b> {clip(s.statement, 80)}</span>
      ))}
      {task && <span><Icon name="right" size={11} /> {clip(task.title, 80)}</span>}
    </span>
  );
}

/** The chain for one task on the board, from what the project frame holds. */
export function TaskChain({ ctx, task, withTask = true }: {
  ctx: ProjectCtx; task: TaskRow; withTask?: boolean;
}) {
  const req = (ctx.plan?.requirements ?? []).find((r) => r.id === task.requirement_id);
  const bet = !req && task.hypothesis_id ? ctx.hypotheses.find((h) => h.id === task.hypothesis_id) : undefined;
  const serves = req ? [{ key: req.key, statement: requirementName(req) }]
    : bet ? [{ key: "bet", statement: bet.statement }] : [];
  return <GoalChain goal={ctx.dash.goal} serves={serves} task={withTask ? task : null} />;
}
