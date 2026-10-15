/** What each person on a project is doing right now, in one place.

    The roster used to say who was on a team and how much they had spent, and
    nothing at all about what any of them was doing — the question everyone
    reading an org chart is actually asking. Answering it meant joining the
    board's holder column against the runs table by hand. The join is here,
    once, so the sidebar's "Live now", the Overview's team card, the org chart
    and the agent drawer cannot each work it out differently. */

import { ActivitySeat, ApprovalRow, Dashboard, OrgView, RunRow, TaskRow } from "../api";
import { readingBlocked, readingOff } from "../inbox";

/** The vocabulary, and the only one. A seat is in exactly one of these. */
export type SeatState = "working" | "next" | "waiting" | "blocked" | "idle" | "paused" | "stood_down";

export const SEAT_LABEL: Record<SeatState, string> = {
  working: "Working",
  next: "Up next",
  waiting: "Waiting on you",
  blocked: "Blocked",
  idle: "Idle",
  paused: "Paused",
  stood_down: "Stood down",
};

/** Which pill a state wears. Status colour always travels with the word. */
export const SEAT_TONE: Record<SeatState, string> = {
  working: "accent",
  next: "accent",
  waiting: "warn",
  blocked: "serious",
  idle: "",
  paused: "",
  stood_down: "",
};

export interface Seat {
  id: string;
  name: string;
  /** "CEO" for the CEO, the team's name for everyone else. */
  team: string;
  teamId: string | null;
  /** The job, as the role library names it. */
  title: string;
  ceo: boolean;
  lead: boolean;
  state: SeatState;
  /** One line: the thing they are on, or why they are not on anything. */
  doing: string;
  /** The task behind `doing`, when there is one. */
  task: TaskRow | null;
  /** The run in flight, when there is one. */
  run: RunRow | null;
  /** When the current state began, for "12m" beside it. */
  since: string | null;
  today: ActivitySeat | undefined;
  /** "No run in 9 days" / "Never run" for an active seat that has gone a week
      without running, else null. */
  dormant: string | null;
}

/** What woke a run, in words. The trigger is the dispatcher's own record. */
export function wokeFor(trigger: Record<string, unknown>): string {
  const reasons = trigger["reasons"] as { kind: string }[] | undefined;
  const kinds = reasons?.length ? reasons.map((r) => r.kind) : [String(trigger["type"] ?? "")];
  const words = kinds.filter(Boolean).map((k) => k.replaceAll("_", " "));
  return words.length ? words.join(", ") : "scheduled work";
}

function heldBy(tasks: TaskRow[], agentId: string): TaskRow[] {
  return tasks.filter((t) => t.claimed_by === agentId || (!t.claimed_by && t.assignee === agentId));
}

function seatOf(
  base: { id: string; name: string; status: string; stood_down_reason?: string | null;
          dormant?: boolean; dormant_days?: number | null },
  where: { team: string; teamId: string | null; title: string; ceo: boolean; lead: boolean },
  ctx: {
    tasks: TaskRow[];
    runs: RunRow[];
    approvals: ApprovalRow[];
    busy: Map<string, { busy: boolean; pending: number; mailOnly: number }>;
    /** Why mail alone would not wake anyone right now, or null if it would. */
    reading: "off" | "used" | null;
    today: Map<string | null, ActivitySeat>;
  },
): Seat {
  const held = heldBy(ctx.tasks, base.id);
  const claimed = held.find((t) => t.status === "claimed" || t.status === "checking" || t.status === "review");
  const blocked = held.find((t) => t.status === "blocked");
  const ready = held.find((t) => t.status === "ready");
  // The newest live run of theirs. A resident chat session is a run too, and
  // it is "running" for days — it is presence, not work, so it never counts.
  const run = ctx.runs.find(
    (r) => r.agent_id === base.id && r.status === "running" && r.kind !== "chat_session",
  ) ?? null;
  const asked = ctx.approvals.find((a) => a.status === "pending" && a.requested_by === base.id);
  const live = ctx.busy.get(base.id);
  const seat = (state: SeatState, doing: string, task: TaskRow | null, since: string | null): Seat => ({
    id: base.id, name: base.name, ...where, state, doing, task, run, since,
    today: ctx.today.get(base.id),
    dormant: !base.dormant ? null
      : base.dormant_days == null ? "Never run" : `No run in ${base.dormant_days} days`,
  });

  if (base.stood_down_reason || base.status === "stood_down") {
    return seat("stood_down", base.stood_down_reason ?? "Their part of the job is finished.", null, null);
  }
  if (base.status === "paused") return seat("paused", "Paused by a chair. Resume them to wake them.", null, null);
  if (run || base.status === "running" || live?.busy) {
    const task = claimed ?? null;
    return seat("working", task ? task.title : `Working: ${wokeFor(run?.trigger ?? {})}`, task,
      run?.started_at ?? null);
  }
  if (asked) return seat("waiting", `Waiting for your decision: ${asked.title}`, claimed ?? blocked ?? null, null);
  if (blocked) {
    return seat("blocked", `${blocked.title}${blocked.blocked_reason ? ` — ${blocked.blocked_reason}` : ""}`,
      blocked, null);
  }
  if ((live?.pending ?? 0) > 0) {
    const task = claimed ?? ready ?? null;
    // "Up next" over mail the project will not wake anyone to read was a
    // promise nobody kept: on a zero-read project it sat in Live now for days.
    // Mail alone is a quiet seat with messages waiting, read on its next wake.
    if (!task && live?.mailOnly && ctx.reading) {
      const n = live.mailOnly;
      const unread = `${n} unread message${n === 1 ? "" : "s"}`;
      return seat("idle", ctx.reading === "off"
        ? `${unread}, read on its next work wake`
        : `${unread}, read when inbox reads free up`, null, null);
    }
    return seat("next", task ? task.title : "Has new messages to read", task, null);
  }
  if (claimed) return seat("idle", `Holding: ${claimed.title}`, claimed, null);
  if (ready) return seat("idle", `Next: ${ready.title}`, ready, null);
  return seat("idle", "Nothing assigned right now", null, null);
}

/** Every seat on the project, the CEO first, then each team in the order the
    org reports them. Stood-down seats are kept: the org chart shows them as
    such, and the lists that only want working people filter them out. */
export function seatsOf(input: {
  org: OrgView | null;
  dash: Dashboard;
  tasks: TaskRow[] | null;
  runs: RunRow[] | null;
  approvals: ApprovalRow[];
  seats: ActivitySeat[] | undefined;
}): Seat[] {
  const { org } = input;
  if (!org) return [];
  const busy = new Map(input.dash.activity.agents.map((a) => {
    const d = a.pending_detail;
    const mailOnly = d && !d.work && !d.notices && !d.heartbeat ? d.unread : 0;
    return [a.id, { busy: a.busy, pending: a.pending, mailOnly }];
  }));
  const reading = input.dash.activity.inbox_runs;
  const ctx = {
    reading: !reading || !readingBlocked(reading) ? null
      : readingOff(reading) ? "off" as const : "used" as const,
    tasks: input.tasks ?? [],
    runs: input.runs ?? [],
    approvals: input.approvals,
    busy,
    today: new Map((input.seats ?? []).map((s) => [s.agent_id, s])),
  };
  const out: Seat[] = [];
  if (org.ceo) {
    out.push(seatOf(org.ceo, {
      team: "CEO", teamId: null, title: org.ceo.role_name ?? "Chief Executive", ceo: true, lead: true,
    }, ctx));
  }
  for (const t of org.teams) {
    for (const a of t.agents) {
      out.push(seatOf(a, {
        team: t.name, teamId: t.id, title: t.role_name ?? t.name, ceo: false,
        lead: t.lead_agent_id === a.id,
      }, ctx));
    }
  }
  return out;
}

/** "12m", "3h", "2d" — how long a state has held. */
export function heldFor(iso: string | null): string {
  if (!iso) return "";
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60_000));
  if (mins < 60) return `${mins}m`;
  const hours = Math.round(mins / 60);
  return hours < 48 ? `${hours}h` : `${Math.round(hours / 24)}d`;
}
