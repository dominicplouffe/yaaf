/** What a project's numbers mean, in one place.
 *
 * The header, the verdict on Overview and the banner on Progress all answer
 * "is this going to land?" — so they answer it from one function. Three copies
 * of that arithmetic is three chances to tell a chairman something different
 * on three screens about the same project. */

import { ApprovalRow, AskRow, Dashboard, OrgView, PlanView } from "../api";
import { Teammate } from "./kit";

export type Tone = "good" | "warn" | "serious" | "critical";

export interface Verdicts {
  tone: Tone;
  /** Short enough for the vital-signs strip. */
  label: string;
  /** The line under it. */
  sub: string;
  /** A whole sentence, for the verdict at the top of Overview. */
  headline: string;
  /** How many days late, when it is late. */
  daysLate: number | null;
}

const DAY = 86_400_000;

export function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

/** Does the authority dial still hold this decision for the chairman?
 *
 * The five Autonomy rules are on the dashboard's constitution, and until this
 * existed no screen read them. So an autonomous project — one whose whole
 * point is that the CEO decides — put the CEO's decisions on the chairman's
 * desk anyway, under a heading that said they were waiting on him. Asked to
 * describe it, the honest sentence was "I have no idea what these mean and no
 * idea what to do", which is what a desk earns when it lists work its reader
 * does not own.
 *
 * Unknown reads as the chairman's: a rule this cannot see is a rule nobody
 * handed to the CEO, and erring towards showing a decision is the safe
 * direction — the failure it guards is the opposite one, hiding something real.
 */
export function chairmanDecides(
  dash: Dashboard,
  rule: "plan_authority" | "scope_authority" | "commitment_authority"
    | "verdict_policy" | "capability_authority",
): boolean {
  const held = dash.constitution?.[rule];
  if (typeof held !== "string") return true;
  return held === "chairman";
}

/** True when nothing on this project is the chairman's to decide any more —
    every Autonomy rule has been handed to the CEO. The desk says so out loud
    rather than leaving a reader to infer it from an empty list. */
export function fullyAutonomous(dash: Dashboard): boolean {
  return !chairmanDecides(dash, "plan_authority")
    && !chairmanDecides(dash, "scope_authority")
    && !chairmanDecides(dash, "commitment_authority")
    && !chairmanDecides(dash, "verdict_policy")
    && !chairmanDecides(dash, "capability_authority");
}

/** What the platform's own words for a flag mean to the person reading them.
 *
 * `flags.py` writes one summary and two audiences read it. The CEO needs the
 * requirement key, the cycle ordinal and the scoreline, and gets them. The
 * chairman was getting them too — "Cycle 4 closed with scope still open: 8 of
 * 12 met, 2 carried forward (R-136, R-139)" is a sentence only its author
 * enjoys — so the headline here says what happened in words, and the
 * platform's own line keeps its precision underneath.
 */
export const FLAG_PLAIN: Record<string, { title: string; means: string }> = {
  cycle_missed: {
    title: "A cycle ended with work still open",
    means: "The rhythm moved on to the next cycle and carried the unfinished work "
      + "with it. Nothing was lost and nothing stopped.",
  },
  standing_missed: {
    title: "Something owed every cycle did not happen",
    means: "One of the standing obligations failed its check this cycle. It is owed "
      + "again next cycle either way.",
  },
  deadline_at_risk: {
    title: "The arithmetic no longer reaches the date",
    means: "At the measured pace there is not enough time left for the work still "
      + "open. Either the scope or the date has to give.",
  },
  work_starved: {
    title: "There was work ready and no allowance left to run it",
    means: "A spending or pace limit you set stopped work that was otherwise ready "
      + "to go. Raising the limit is the fix, if the work is worth it.",
  },
  spend_window: {
    title: "A spending window reached a checkpoint",
    means: "Money is being measured against a window you funded, and this is the "
      + "report on where it stands.",
  },
  budget_overrun: {
    title: "The month went past its budget",
    means: "Not a failure and not a stop — an obligation on your CEO to say what "
      + "the money bought.",
  },
  self_gated: {
    title: "Work is waiting on a start time nobody is blocking",
    means: "Tasks are scheduled for later while nothing is actually in the way, so "
      + "the project is idling by its own choice.",
  },
  losing_ground: {
    title: "The thing being measured is moving the wrong way",
    means: "This is the one flag about the goal rather than the process: the "
      + "objective is further from its target than it was.",
  },
  run_overrun: {
    title: "A job cost far more than it was estimated to",
    means: "The work may still be right; the estimate it was planned against was "
      + "not, and everything planned from that estimate inherits the error.",
  },
  seat_idle: {
    title: "Somebody you are paying for has had nothing to do",
    means: "A seat on the org chart has gone unused. Either the work has not "
      + "reached it or the role is no longer needed.",
  },
};

export function flagPlain(kind: string): { title: string; means: string } {
  return FLAG_PLAIN[kind] ?? {
    title: kind.replaceAll("_", " "),
    means: "Your CEO is required to bring this to you with a proposal.",
  };
}

function daysBetween(a: string, b: string): number {
  return Math.round((new Date(a).getTime() - new Date(b).getTime()) / DAY);
}

/** The one question a project screen exists to answer. */
export function deliveryVerdict(dash: Dashboard, plan: PlanView | null): Verdicts {
  if (dash.status === "stopped") {
    return { tone: "critical", label: "Stopped", sub: dash.status_reason ?? "stopped for good",
      headline: "This project has been stopped.", daysLate: null };
  }
  if (dash.status === "completed") {
    return { tone: "good", label: "Delivered", sub: "the platform confirmed it",
      headline: "This project delivered what it promised.", daysLate: null };
  }
  if (dash.status === "paused") {
    return { tone: "warn", label: "Paused", sub: dash.status_reason ?? "nothing new is starting",
      headline: "This project is paused. Nothing new is starting.", daysLate: null };
  }

  const c = plan?.commitment;
  if (c && c.projected_done_at && c.deliver_by) {
    const late = daysBetween(c.projected_done_at, c.deliver_by);
    // Already past the date is a different fact from heading for a late
    // landing, and it was being reported as the second one: "this will land
    // about 8 days late" about a promise that expired a week ago, with the
    // date it expired never named. The reader's first question — which date
    // did I sign? — went unanswered by the sentence telling him he had missed
    // it.
    const passed = daysBetween(new Date().toISOString(), c.deliver_by) > 0;
    if (passed) {
      const over = daysBetween(new Date().toISOString(), c.deliver_by);
      return {
        tone: "critical",
        label: "Date passed",
        sub: `${fmtDate(c.deliver_by)} has gone · now looks like ${fmtDate(c.projected_done_at)}`,
        headline: `The date you signed — ${fmtDate(c.deliver_by)} — passed ${
          over === 1 ? "yesterday" : `${over} days ago`}, and the work is not finished.`,
        daysLate: late > 0 ? late : null,
      };
    }
    if (late > 0) {
      return {
        tone: late > 14 ? "critical" : "serious",
        label: "At risk",
        sub: `promised ${fmtDate(c.deliver_by)} · now looks like ${fmtDate(c.projected_done_at)}`,
        headline: `This is heading for ${fmtDate(c.projected_done_at)} — about ${
          late === 1 ? "a day" : `${late} days`} past the ${fmtDate(c.deliver_by)} you signed.`,
        daysLate: late,
      };
    }
    return {
      tone: "good", label: "On track",
      sub: `promised ${fmtDate(c.deliver_by)}`,
      headline: `On track to deliver by ${fmtDate(c.deliver_by)}.`,
      daysLate: null,
    };
  }
  if (c && c.deliver_by && !c.accepted_at) {
    return { tone: "warn", label: "Unsigned", sub: `${fmtDate(c.deliver_by)} is proposed, not agreed`,
      headline: "Your CEO has proposed a delivery date and is waiting for you to sign it.", daysLate: null };
  }
  if (dash.deadline) {
    return { tone: "good", label: fmtDate(dash.deadline), sub: "the window you set for this",
      headline: `This runs until ${fmtDate(dash.deadline)}.`, daysLate: null };
  }
  if (dash.mode === "explore") {
    return { tone: "good", label: "Open-ended", sub: "no fixed end, by design",
      headline: "This one runs until you decide it has earned its keep.", daysLate: null };
  }
  return { tone: "warn", label: "No date yet", sub: "your CEO has not proposed one",
    headline: "There is no delivery date yet — your CEO has not proposed one.", daysLate: null };
}

/** Only the things a decision is actually waiting on from you. A count that
    includes work you cannot do is a count that teaches people to ignore it. */
export function waitingOnYou(
  dash: Dashboard,
  approvals: ApprovalRow[],
  asks: AskRow[],
  plan: PlanView | null,
): { count: number; soonest: string | null } {
  const clocked: string[] = [];
  let n = 0;
  for (const a of approvals) {
    if (a.status === "pending") { n++; clocked.push(a.expires_at); }
  }
  for (const a of asks) {
    // An ask stops waiting on its own at answer_by and takes its default
    // action, so it belongs on the same clock as an approval.
    if (a.status === "open") { n++; clocked.push(a.answer_by); }
  }
  // Only what the dial still holds for the chairman. Proposed scope on a
  // project whose CEO accepts its own scope is not waiting on anybody, and
  // counting it was how an autonomous project came to show a number beside
  // "Waiting on you" that no action of the reader's could ever bring down.
  if (chairmanDecides(dash, "scope_authority")) n += dash.requirements_proposed;
  const c = plan?.commitment;
  if (chairmanDecides(dash, "commitment_authority")
      && c && c.deliver_by && !c.accepted_at && c.status !== "met") n++;
  clocked.sort();
  return { count: n, soonest: clocked[0] ?? null };
}

/** The org chart as a row of faces. The CEO first — it is the one you talk to
    — then each desk's people, in the order the org reports them. */
export function roster(org: OrgView | null): Teammate[] {
  if (!org) return [];
  const out: Teammate[] = [];
  if (org.ceo) {
    out.push({
      name: org.ceo.name,
      role: org.ceo.role_name ?? "CEO",
      team: "leadership",
      working: org.ceo.status === "running",
    });
  }
  for (const t of org.teams) {
    for (const a of t.agents) {
      if (a.stood_down_reason) continue;
      out.push({ name: a.name, role: t.name, team: t.name, working: a.status === "running" });
    }
  }
  return out;
}

export function working(people: Teammate[]): number {
  return people.filter((p) => p.working).length;
}
