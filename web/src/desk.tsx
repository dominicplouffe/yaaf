import { createContext, ReactNode, useContext, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { del, DeskApproval, DeskProject, DeskView, get, post } from "./api";
import { usePoll } from "./hooks";
import { useHeldKeys } from "./undo";
import { clip, DecideButtons, fmtSoon, fmtUnit, fmtWhenShort, hoursSince, paceDate } from "./ui";

/** What needs the chairman, decided once for every screen that says so.

    **Only rows the chairman holds a lever on are counted as needing him.**
    That is the whole rule, and the Desk spent three revisions learning it.

    A row is `yours` when the chairman holds the only lever — an approval that
    auto-denies, a bet only he extends, a thread only he unfreezes, scope only
    he accepts, a date only he signs, a verdict nobody on the project may pass.
    Everything else — a drought, a kill date, a budget on pace — is the CEO's
    to resolve. Those rows are still built: the Inbox shows them under
    *Watching*, which has no badge and asks for nothing, so they exist without
    pretending to want a click.

    They were once drawn beside the decisions under a calm heading, on the
    argument that nothing should happen silently. The argument is right and
    that remedy was wrong: a notice shaped exactly like an approval *is* a
    request, whatever the heading over it says. It cost this exchange, twice in
    one morning, over an evidence-drought counter at 8h of 12:

      CHAIRMAN: have I been asked to approve or acknowledge something?
                I'm wondering what?
      CEO:      No — everything I've filed has already been approved.
      CHAIRMAN: why do I see this then?

    So `yours` decides which list a row is in, it is required on every row,
    and the compiler is what enforces that a new row type answers the question
    at all. Tier orders rows by time to consequence (approvals auto-deny, bets
    auto-block their tasks, budgets auto-pause):

      1  stopped      it has stopped, and only the chairman restarts it
      2  on a clock   it resolves at a time we can name
      3  drifting     nothing breaks; the claim just gets weaker

    One poll feeds every reader. The sidebar badge, Home, the Inbox and each
    project's Overview used to count on their own clocks from their own
    sources — the audit found three definitions of "needs you" across fourteen
    places — and a badge that disagrees with the list under it is worse than no
    badge. They all read `useDesk()` now, so the number is one number.

    Only a project that is *running* counts. The server drops the terminal ones
    (`?live=true`); draft and paused ones are dropped here, because neither is
    working and neither can break while the tab is closed. A paused project's
    own Overview still shows what is parked on it — see `itemsFor`.

    The autonomy dial decides what is even eligible for a row: where the
    constitution says `ceo`, the server leaves `awaiting_you` empty. What the
    dial never moves: spend, legal, tool_request and assist approvals are
    undelegable at every setting. Authority is configuration; integrity is
    structure, and this list draws the line where the server does. */

type Tier = 1 | 2 | 3;

/** Which verb a row asks for — what the Inbox's icon, filter and detail pane
    key on. Separate from `kind`, which is the human label. */
export type ItemType =
  | "approval" | "ask" | "scope" | "commitment" | "verdict" | "bet" | "frozen"
  | "kill" | "drought" | "budget" | "noproof";

export interface Item {
  key: string;
  type: ItemType;
  tier: Tier;
  /** Epoch ms of the consequence — the sort key inside a tier. Tier 1 has
      already happened (0); tier 3 never does (+∞). */
  at: number;
  /** Whether `at` is a real future deadline, as opposed to a sort position.
      Only a clocked row may be quoted as "the soonest decides itself in X" —
      the headline names a deadline, so it has to come from something that has
      one. */
  clocked?: boolean;
  /** Whether the chairman holds the only lever on this row. Required, not
      optional: a new row type must answer this before it compiles, because
      the one failure mode here is a notice slipping into *Needs you* wearing
      an ask's clothes. */
  yours: boolean;
  project: DeskProject;
  kind: string;
  /** One line, plain text — the row in a list. */
  title: string;
  what: ReactNode;
  consequence: ReactNode;
  /** Where the lever lives when it is not on the row itself. */
  to: string;
  toLabel: string;
  /** Present on the two kinds a row can settle in place. */
  approvalId?: string;
  approval?: DeskApproval;
  ask?: { id: string; body: string; defaultAction: string };
  amountUsd?: number | null;
  actions?: ReactNode;
}

const NEVER = Number.MAX_SAFE_INTEGER;
// A floor under the "spent, proved nothing" row, so it can't fire on a few
// cents of overnight cache traffic. Arbitrary, and deliberately so: there is
// no principled number here, only a threshold below which the row is noise.
const SPEND_FLOOR_USD = 1;

/** What counts toward the badge: a project doing work right now. */
export const isLive = (p: DeskProject) => p.status === "running";

/** The one definition of "a decision is waiting on the chairman". */
export const needsYou = (i: Item) => i.yours === true;

/** How many decisions one project is holding the chairman up on — the same
    rows the Inbox draws for it, counted rather than rendered. Cached on the
    project object: the counts are pure functions of the row, the list is
    rebuilt on every poll, and sorts call this O(n log n) times per render. */
const NEEDS = new WeakMap<DeskProject, number>();
/** Status-line verdicts that are a problem, in the warning tone wherever the
    sentence appears (yaaf.server.status_line decides the verdict). */
export const WARN_VERDICTS = ["blocked", "failing", "late", "at_risk", "spinning", "stalled", "needs_you"];

export function needsYouCount(p: DeskProject): number {
  const hit = NEEDS.get(p);
  if (hit !== undefined) return hit;
  const n = buildItems([p], () => {}).filter(needsYou).length;
  NEEDS.set(p, n);
  return n;
}

/** Every row for the projects that count — the running ones. */
export function buildItems(projects: DeskProject[], reload: () => void): Item[] {
  return projects.filter(isLive).flatMap((p) => itemsFor(p, reload))
    .sort((x, y) => x.tier - y.tier || x.at - y.at);
}

/** Every row for one project, whatever its state. A paused project is never
    counted (see `isLive`): pausing is a state, not a request, and a badge
    pinned at four by four parked projects is a badge nobody reads — which
    costs the chairman the approval that auto-denies tomorrow. But a paused
    project's own Overview still lists what is parked on it, because that is
    the one screen where the reader came to look at exactly that project. */
export function itemsFor(p: DeskProject, reload: () => void): Item[] {
  const items: Item[] = [];
  const now = Date.now();
  const to = `/projects/${p.id}`;

  // ── tier 1: stopped, and only the chairman restarts it ──────────────────
  for (const bet of p.expired_bets) {
    items.push({
      key: bet.id, type: "bet", tier: 1, at: 0, yours: true, project: p, kind: "Bet expired",
      title: `A bet hit its kill date: “${clip(bet.statement, 80)}”`,
      what: (
        <>
          “{clip(bet.statement, 160)}” hit its kill date {fmtWhenShort(bet.kill_date)}
          {(bet.blocked_tasks ?? 0) > 0 && (
            <>, and <b>{bet.blocked_tasks} task(s) are blocked</b> behind it</>
          )}.
        </>
      ),
      consequence: "The CEO pivots. You hold the one extension.",
      to: `${to}/tasks`, toLabel: "Decide on the Tasks page",
    });
  }
  if (p.frozen_threads > 0) {
    items.push({
      key: `${p.id}-frozen`, type: "frozen", tier: 1, at: 0, yours: true, project: p,
      kind: "Thread frozen",
      title: `${p.frozen_threads} conversation${p.frozen_threads === 1 ? " was" : "s were"} frozen — two agents were looping`,
      what: (
        <>
          The pair breaker froze <b>{p.frozen_threads} thread(s)</b>: two agents were talking in
          a loop.
        </>
      ),
      consequence: "Those agents stay stuck until you unfreeze the thread.",
      to: `${to}/activity/messages`, toLabel: "Review the thread",
    });
  }

  // The dial's own three. Each list is server-side empty where the CEO holds
  // that switch, so an autonomous project reaches none of this.
  const w = p.awaiting_you;
  for (const v of w.verdicts) {
    items.push({
      key: `${p.id}-verdict-${v.key}`, type: "verdict", tier: 1, at: 0, yours: true, project: p,
      kind: "Your verdict",
      title: `Is this done? “${clip(v.name, 80)}”`,
      what: (
        <>
          <b>{clip(v.name, 80)}</b> has no work left open, and whether it is done is something
          no script can check{v.name !== v.statement
            ? <>. What has to be true: “{clip(v.statement, 200)}”</>
            : "."}
        </>
      ),
      consequence: "It stays open. Nobody on the project can mark it met.",
      to: `${to}/roadmap`, toLabel: "Judge it on the Roadmap",
    });
  }
  if (w.plan > 0) {
    items.push({
      key: `${p.id}-plan`, type: "scope", tier: 1, at: 0, yours: true, project: p,
      kind: "Proposed scope",
      title: `The CEO proposed ${w.plan} requirement${w.plan === 1 ? "" : "s"} for you to accept`,
      what: (
        <>
          The CEO proposed <b>{w.plan} requirement{w.plan === 1 ? "" : "s"}</b> — what “done”
          should mean — and is waiting on you.
        </>
      ),
      consequence: "Proposed scope is not scope: no work can be built against it.",
      to: `${to}/roadmap`, toLabel: "Review on the Roadmap",
    });
  }
  if (w.commitment) {
    items.push({
      key: `${p.id}-commitment`, type: "commitment", tier: 2, at: NEVER - 1, yours: true,
      project: p, kind: "Sign the date",
      title: `Sign a delivery date: ${w.commitment.scope_count} requirement${w.commitment.scope_count === 1 ? "" : "s"} by ${fmtWhenShort(w.commitment.deliver_by)}`,
      what: (
        <>
          The CEO proposes delivering <b>{w.commitment.scope_count} requirement
          {w.commitment.scope_count === 1 ? "" : "s"}</b> by{" "}
          <b>{fmtWhenShort(w.commitment.deliver_by)}</b>.
        </>
      ),
      consequence: "The project keeps working, but it is promising nothing until you sign.",
      to: `${to}/roadmap`, toLabel: "Sign or move it on the Roadmap",
    });
  }

  // ── tier 2: it resolves without you, at a time we can name ────────────
  for (const a of p.pending_approvals) {
    items.push({
      key: a.id, type: "approval", tier: 2, at: new Date(a.expires_at).getTime(), clocked: true,
      yours: true, project: p,
      kind: a.kind === "assist" ? "For you to do" : `Decision · ${a.kind.replaceAll("_", " ")}`,
      title: a.title, approvalId: a.id, amountUsd: a.amount_usd, approval: a,
      what: (
        <>
          {a.title}
          {a.amount_usd !== null && (
            <> — <b>${a.amount_usd.toLocaleString()}</b></>
          )}
        </>
      ),
      consequence: `It is ${a.if_silent ?? "declined"} ${fmtSoon(a.expires_at)}. Silence is a decision here.`,
      to: `${to}/activity/decisions`, toLabel: "Read the full request",
      actions: <DecideButtons approval={a} onDone={reload} />,
    });
  }
  for (const ask of p.pending_asks) {
    items.push({
      key: ask.id, type: "ask", tier: 2, at: new Date(ask.answer_by).getTime(), clocked: true,
      yours: true, project: p, kind: "Question",
      title: ask.title,
      ask: { id: ask.id, body: ask.body, defaultAction: ask.default_action },
      what: <>{ask.title}</>,
      consequence: `It stops waiting ${fmtSoon(ask.answer_by)}, then: ${clip(ask.default_action, 160)}`,
      to: `${to}/chat`, toLabel: "Answer in the chat",
    });
  }
  for (const bet of p.kill_dates_soon) {
    items.push({
      key: bet.id, type: "kill", tier: 2, at: new Date(bet.kill_date).getTime(), clocked: true,
      yours: false, project: p, kind: "Bet deadline",
      title: `A bet expires ${fmtSoon(bet.kill_date)}: “${clip(bet.statement, 70)}”`,
      what: <>“{clip(bet.statement, 160)}”</>,
      consequence: "Then its tasks block and the CEO re-plans.",
      to: `${to}/tasks`, toLabel: "See the bet",
    });
  }

  // A drought is the platform refusing new internal work until proof lands.
  // Active is happening now, so it sorts to the head of the clock tier;
  // approaching is dated from the last countable evidence.
  const d = p.drought;
  const droughtShown = d.active || (d.applies && d.last_countable_at !== null
    && hoursSince(d.last_countable_at) >= d.window_hours * 0.5);
  if (d.active) {
    items.push({
      key: `${p.id}-drought`, type: "drought", tier: 2, at: now, yours: false, project: p,
      kind: "No outside proof",
      title: `Nothing outside the project has verified anything in ${d.window_hours}h`,
      what: <>No verified external evidence in <b>{d.window_hours}h</b>.</>,
      consequence: "Happening now: new internal-only work is refused until proof lands.",
      to: `${to}/activity/evidence`, toLabel: "See the proof",
    });
  } else if (droughtShown && d.last_countable_at) {
    const startsAt = new Date(d.last_countable_at).getTime() + d.window_hours * 3_600_000;
    items.push({
      key: `${p.id}-drought`, type: "drought", tier: 2, at: startsAt, clocked: true, yours: false,
      project: p, kind: "No outside proof",
      title: `No outside proof for ${Math.floor(hoursSince(d.last_countable_at))}h of ${d.window_hours}h`,
      what: (
        <>
          <b>{Math.floor(hoursSince(d.last_countable_at))}h</b> without verified external
          evidence, of {d.window_hours}h.
        </>
      ),
      consequence: `The platform starts refusing internal work ${fmtSoon(new Date(startsAt).toISOString())}.`,
      to: `${to}/activity/evidence`, toLabel: "See the proof",
    });
  }

  // Budget. A cap already reached pauses the project; a projected landing is a
  // real clock; a high-but-static fraction is worth knowing and has no clock.
  // paceDate is arithmetic, not currency, so token-billed projects get the
  // dated warning too.
  const b = p.budget;
  const frac = b.cap > 0 ? b.spent / b.cap : 0;
  const pace = paceDate(b.spent, b.cap);
  if (frac < 1 && pace) {
    items.push({
      key: `${p.id}-budget`, type: "budget", tier: 2, at: pace.getTime(), clocked: true,
      yours: false, project: p, kind: "Budget pace",
      title: `Budget on pace to run out ${fmtWhenShort(pace.toISOString())}`,
      what: (
        <>
          {fmtUnit(b.spent, b.unit)} of {fmtUnit(b.cap, b.unit)} spent — at this pace the cap
          lands <b>{fmtWhenShort(pace.toISOString())}</b>.
        </>
      ),
      consequence: `Over the budget the CEO must account for it; the platform pauses the `
        + `project at ${fmtUnit(b.cap * b.hard_stop_multiple, b.unit)}, the hard stop.`,
      to: `${to}/spend`, toLabel: "See the spend",
    });
  } else if (frac < 1 && frac >= b.warn_ratio) {
    items.push({
      key: `${p.id}-budget`, type: "budget", tier: 3, at: NEVER, yours: false, project: p,
      kind: "Budget",
      title: `${Math.round(frac * 100)}% of the monthly budget used`,
      what: (
        <>
          {Math.round(frac * 100)}% of the monthly cap used — {fmtUnit(b.spent, b.unit)} of{" "}
          {fmtUnit(b.cap, b.unit)}.
        </>
      ),
      consequence: "Nothing yet: at this rate the cap holds to the end of the month.",
      to: `${to}/spend`, toLabel: "See the spend",
    });
  }

  // ── tier 3: nothing breaks; the claim gets weaker ─────────────────────
  // Money moved and nothing outside the project confirmed anything. Distinct
  // from the drought, which counts hours rather than dollars, so it is
  // suppressed while a drought row already speaks for this project. USD only:
  // a zero in token billing means "not priced in dollars", not "spent nothing".
  if (b.unit === "usd" && !droughtShown && p.spend_24h >= SPEND_FLOOR_USD
      && p.evidence_24h === 0 && p.status === "running") {
    items.push({
      key: `${p.id}-spend-noproof`, type: "noproof", tier: 3, at: NEVER, yours: false, project: p,
      kind: "No proof",
      title: `$${p.spend_24h.toLocaleString(undefined, { maximumFractionDigits: 2 })} spent in 24h with nothing verified`,
      what: (
        <>
          <b>${p.spend_24h.toLocaleString(undefined, { maximumFractionDigits: 2 })}</b> spent in
          the last 24h, and nothing verified by anyone outside the project.
        </>
      ),
      consequence: "Nothing breaks. The work may be real, but only the agents say so.",
      to: `${to}/tasks`, toLabel: "See what it bought",
    });
  }

  return items.sort((x, y) => x.tier - y.tier || x.at - y.at);
}

/** The soonest real deadline among `items` — the "decides itself in X" clause. */
export function soonestClock(items: Item[]): Item | undefined {
  return items.filter((i) => i.tier === 2 && i.clocked).sort((a, b) => a.at - b.at)[0];
}

interface DeskState {
  data: DeskView | null;
  error: string | null;
  at: Date | null;
  reload: () => void;
  /** Every row, running projects only, ranked by time to consequence. */
  items: Item[];
  /** The subset the badge counts: yours, not snoozed, not just decided. */
  mine: Item[];
  /** Yours, snoozed by you, with when each comes back. */
  snoozed: { item: Item; until: string }[];
  /** Keys every list leaves out: snoozed, or decided and still in its undo window. */
  hidden: Set<string>;
}

const DeskContext = createContext<DeskState | null>(null);

/** One request, every fifteen seconds, for every screen that asks "what needs
    me?". Each screen used to poll for itself — the sidebar every 30s, the Desk
    every 10s — which is two answers to one question on two clocks. */
export function DeskProvider({ children }: { children: ReactNode }) {
  const { data, error, at, reload } = usePoll(
    () => get<DeskView>("/observe/desk?live=true"), 15000);
  const items = useMemo(
    () => (data ? buildItems(data.projects, reload) : []),
    [data, reload],
  );
  const held = useHeldKeys();
  const value = useMemo(() => {
    // A snooze that has ended stops hiding at once, not on the next poll.
    const now = Date.now();
    const until = new Map((data?.snoozed ?? [])
      .filter((s) => new Date(s.until).getTime() > now)
      .map((s) => [s.item_key, s.until] as const));
    const hidden = new Set<string>([...until.keys(), ...held]);
    return {
      data, error, at, reload, items, hidden,
      mine: items.filter((i) => needsYou(i) && !hidden.has(i.key)),
      snoozed: items.filter((i) => needsYou(i) && until.has(i.key) && !held.has(i.key))
        .map((item) => ({ item, until: until.get(item.key)! })),
    };
  }, [data, error, at, reload, items, held]);
  return <DeskContext.Provider value={value}>{children}</DeskContext.Provider>;
}

export function useDesk(): DeskState {
  const state = useContext(DeskContext);
  if (!state) throw new Error("useDesk() outside <DeskProvider>");
  return state;
}

/** How close to its own deadline a snooze may run (yaaf/server/api/me.py). */
const SNOOZE_MARGIN_MS = 2 * 3_600_000;

function tomorrowMorning(): Date {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  d.setHours(8, 0, 0, 0);
  return d;
}

/** "Not now" on one row of your own Inbox — never past the moment it decides
    itself: a row with a clock comes back at least two hours before it. */
export function SnoozeMenu({ item, onDone }: { item: Item; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!item.yours) return null;
  const now = Date.now();
  const latest = item.clocked ? item.at - SNOOZE_MARGIN_MS : now + 7 * 86_400_000 - 60_000;
  if (latest <= now + 5 * 60_000) {
    return <span className="help">Too close to its deadline to snooze</span>;
  }
  const options: { label: string; at: number }[] = [
    { label: "For 1 hour", at: now + 3_600_000 },
    { label: "Until tomorrow morning", at: tomorrowMorning().getTime() },
    ...(item.clocked
      ? [{ label: "Until 2 hours before it decides", at: latest - 60_000 }]
      : [{ label: "For a week", at: latest }]),
  ].filter((o) => o.at <= latest);
  const snooze = async (at: number) => {
    setError(null);
    try {
      await post("/me/snoozes", { project_id: item.project.id, item_key: item.key,
        until: new Date(at).toISOString() });
      setOpen(false);
      onDone();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <span className="snooze">
      <button className="small" aria-expanded={open} onClick={() => setOpen(!open)}>Snooze ▾</button>
      {open && (
        <span className="snooze-menu" role="menu">
          {options.map((o) => (
            <button key={o.label} role="menuitem" onClick={() => snooze(o.at)}>
              {o.label}<span className="help"> · back {fmtSoon(new Date(o.at).toISOString())}</span>
            </button>
          ))}
        </span>
      )}
      {error && <span className="help" style={{ color: "var(--critical)" }}>{error}</span>}
    </span>
  );
}

/** Bring a snoozed row back now. */
export async function wake(itemKey: string) {
  await del(`/me/snoozes/${encodeURIComponent(itemKey)}`);
}

/** The row's own verb, or the way to it. Shared by the Inbox, Home and the
    Overview so an approval reads the same wherever it appears. */
export function ItemActions({ item }: { item: Item }) {
  if (item.actions) return <>{item.actions}</>;
  return (
    <Link className="btnlink small" to={item.to}>
      {item.toLabel}
    </Link>
  );
}
