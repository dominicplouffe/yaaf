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
    if (late > 0) {
      return {
        tone: late > 14 ? "critical" : "serious",
        label: "At risk",
        sub: `promised ${fmtDate(c.deliver_by)} · now looks like ${fmtDate(c.projected_done_at)}`,
        headline: `This will land about ${late === 1 ? "a day" : `${late} days`} late.`,
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
  n += dash.requirements_proposed;
  const c = plan?.commitment;
  if (c && c.deliver_by && !c.accepted_at && c.status !== "met") n++;
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
