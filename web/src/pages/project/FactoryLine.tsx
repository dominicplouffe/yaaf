import { get } from "../../api";
import { usePoll } from "../../hooks";

type RailStage = { stage: string; state: "done" | "now" | "waiting" | "idle"; count: number;
  detail: string };
type FactoryView = {
  phase: string | null;
  rail: RailStage[];
  vitals: {
    requirements_met?: number; requirements?: number; merged_week?: number;
    promises_kept?: number | null; promises?: number;
    merged_total?: number; branch_to_merge_hours?: number | null; returned_pct?: number | null;
    cost_per_merged_change?: number | null; hidden_scenarios?: { pass: number; total: number };
    deploys_month?: number; change_failure_pct?: number | null; recover_hours?: number | null;
  };
};

const STATE_WORD: Record<string, string> = { done: "done", now: "in progress",
  waiting: "waits on someone", idle: "quiet" };

/** The line, on the project home: every stage of a Code Factory at a glance,
    then the numbers that say whether it is a good factory — each counted by
    the platform, none reported by the crew. */
export function FactoryLine({ projectId, base }: { projectId: string; base: string }) {
  const { data } = usePoll(() => get<FactoryView>(`/observe/projects/${projectId}/factory`),
    30000, [projectId]);
  if (!data || !data.rail.length) return null;
  const v = data.vitals;
  const n = (x: number | null | undefined, unit = "") => (x == null ? "—" : `${x}${unit}`);
  // Outcomes first: what is true and what was kept. How much was merged is
  // how the factory got there, not whether it is any good.
  const tiles: [string, string, string?][] = [
    ["Requirements met", `${n(v.requirements_met)} of ${n(v.requirements)}`,
      "proven by the platform's own checks"],
    ["Promises kept", v.promises ? `${n(v.promises_kept)} of ${v.promises}` : "—",
      v.promises ? "last cycle" : "starts with upkeep"],
    ["Broke production", n(v.change_failure_pct, "%"), "rolled back by the guard"],
    ["Time to recover", n(v.recover_hours, " h"), "median, incident to postmortem"],
    ["Merged this week", n(v.merged_week), `of ${n(v.merged_total)} total`],
    ["Branch to merge", n(v.branch_to_merge_hours, " h"), "median, last 30 days"],
    ["Sent back by review", n(v.returned_pct, "%")],
    ["Cost per merged change", v.cost_per_merged_change == null ? "—"
      : `$${v.cost_per_merged_change.toFixed(2)}`],
    ["Hidden scenarios", v.hidden_scenarios?.total
      ? `${v.hidden_scenarios.pass} of ${v.hidden_scenarios.total}` : "—", "on open changes"],
    ["Deploys", n(v.deploys_month), "last 30 days"],
  ];
  return (
    <section className="card factory-line" aria-label="The line">
      <div className="row-between">
        <h3>The line</h3>
        <a href={`${base}/details/changes`} className="small">Changes →</a>
      </div>
      <ol className="fl-rail">
        {data.rail.map((s) => (
          <li key={s.stage} className={`fl-${s.state}`} title={STATE_WORD[s.state]}>
            <b>{s.stage}</b>
            <span>{s.detail}</span>
          </li>
        ))}
      </ol>
      <div className="fl-tiles">
        {tiles.map(([label, value, note]) => (
          <div className="fl-tile" key={label}>
            <span className="fl-label">{label}</span>
            <span className="fl-value mono">{value}</span>
            {note && <span className="help">{note}</span>}
          </div>
        ))}
      </div>
    </section>
  );
}
