import { Link } from "react-router-dom";
import { fmtUnit } from "./ui";

/** "What happened while I was away", on the screen as well as in the email.

    The briefing was composed for the morning mail, the CLI and MCP, and kept
    off the dashboard on the argument that the dashboard is the present tense.
    The argument was tidy and the result was not: the one question a chairman
    opening the app after a weekend asks first — *what did I miss?* — had no
    answer on the screen he was looking at, only in a different application.
    It is shown here now, read from the same composer, so the panel and the
    email cannot disagree about what happened. It still asks for nothing: no
    badge counts it, and nothing on it expires. */

export interface BriefLine {
  kind: string;
  text: string;
  tone: "good" | "bad" | "flat" | string;
  count: number | null;
  where: string | null;
  note: string | null;
  group: "attention" | "moved" | "delegated" | null;
  yours: boolean | null;
}

export interface ProjectBrief {
  project_id: string;
  slug: string;
  name: string;
  status: string;
  since: string;
  until: string;
  window_source: string;
  window_hours: number;
  quiet: boolean;
  headline: string;
  lines: BriefLine[];
  groups: Record<string, number>;
  needs_you: number;
  money: { unit: "usd" | "tokens"; window: number; month_to_date: number; cap: number };
  pulse: { last_run_at: string | null; running: number; open_tasks: number };
}

export interface DeskBrief {
  as_of: string;
  forced_window: string | null;
  quiet: boolean;
  projects: ProjectBrief[];
}

/** The three questions a briefing answers, in the order a chairman asks them.
    Named exactly as the email names them, so the two never differ. */
export const GROUPS: [NonNullable<BriefLine["group"]>, string][] = [
  ["attention", "Worth your eye"],
  ["moved", "What moved"],
  ["delegated", "The CEO handled this itself"],
];

/** The composer still names pages by their old addresses; each one lands on
    the page that replaced it. */
const WHERE: Record<string, string> = {
  overview: "",
  plan: "details/roadmap",
  work: "details/tasks",
  review: "details/reviews",
  deliverables: "results",
  decisions: "details/activity/decisions",
  "records/decisions": "details/activity/decisions",
  "records/messages": "details/activity/messages",
  "records/runs": "details/activity/runs",
};

export function whereTo(projectId: string, where: string | null): string | null {
  if (where === null) return null;
  const page = WHERE[where] ?? where;
  return page ? `/projects/${projectId}/${page}` : `/projects/${projectId}`;
}

/** One project's window, grouped. */
export function BriefBody({ brief }: { brief: ProjectBrief }) {
  const money = brief.money;
  return (
    <div className="bf">
      <p className="bf-head">{brief.headline}</p>
      <p className="bf-meta">
        {brief.window_source} · spent {fmtUnit(money.window, money.unit)} in this window ·{" "}
        {fmtUnit(money.month_to_date, money.unit)} of {fmtUnit(money.cap, money.unit)} this month
      </p>
      {brief.quiet && (
        <p className="help">
          Nothing happened in this window that changes anything.{" "}
          {brief.pulse.running > 0
            ? `${brief.pulse.running} run${brief.pulse.running === 1 ? " is" : "s are"} in flight right now.`
            : brief.pulse.open_tasks > 0
              ? `${brief.pulse.open_tasks} task${brief.pulse.open_tasks === 1 ? " is" : "s are"} open and waiting to run.`
              : "There is no open work."}
        </p>
      )}
      {GROUPS.map(([group, heading]) => {
        const lines = brief.lines.filter((l) => l.group === group);
        if (!lines.length) return null;
        return (
          <section key={group} className={`bf-group ${group}`}>
            <h4>{heading}</h4>
            <ul>
              {lines.map((l, i) => {
                const to = whereTo(brief.project_id, l.where);
                return (
                  <li key={`${l.kind}-${i}`} className={`tone-${l.tone}`}>
                    <i />
                    <span>
                      {l.text}
                      {l.note && <span className="bf-note"> {l.note}</span>}
                      {to && <> <Link to={to}>open</Link></>}
                    </span>
                  </li>
                );
              })}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
