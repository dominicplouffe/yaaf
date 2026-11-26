import { ReactNode, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  AgendaView,
  ChannelRow,
  get,
  HealthCheck,
  MessageRow,
  post,
  requirementName,
  ResultsView,
  VitalsView,
} from "../../api";
import { ProjectBrief } from "../../briefing";
import { FailoverBanner } from "../../fallback";
import Agenda from "../../agenda";
import { Item, itemsFor, needsYou, needsYouCount, useDesk } from "../../desk";
import { fmtSince, fmtWhen, usePoll } from "../../hooks";
import { firstName, ProjectCtx } from "../../projectCtx";
import {
  AsOf,
  budgetOf,
  clip,
  earlyRunSentence,
  fmtUnit,
  fmtWhenShort,
  NotedButton,
  paceDate,
  runHealth,
} from "../../ui";
import { Verdict } from "../../ui/kit";
import { Icon } from "../../ui/Icon";
import { chairmanDecides, deliveryVerdict, flagPlain, fmtDate, fmtRoll } from "../../ui/project";
import { MoneyMeter, NodeState, PathNode, requirementNode, verdictBadge } from "../../ui/signal";
import "./overview.css";
import "./vitals.css";
import {
  LimitGrid, MemoryCard, ModePanel, ModePill, resumesAt, RunsTile, RunsWeek, SharedPlan, SpendTile, StatusLine, TimeVisual,
  WeekTimeline, YouPanel,
} from "./vitals";
import { DecisionCard } from "../Inbox";
import { ProjectSetup, SetupStrip } from "../../projectSetup";
import { DeliverComingUp, DeliverHero, DeliverNumbers, GoalChecklist, goalsOf, GoalsOverTime } from "./deliverHome";

/** A project's Inbox answers three questions, in the order they are asked:

      does it need me?        this project's rows of the one Inbox, with their buttons
      is it on track?         goals, delivery and spending — one number each
      what's new?             what changed, what's next, what the team made

    Above them sits at most one banner: the single worst reason the team can't
    work, with its fix. Six banners used to take turns above the decisions —
    mode, lateness, failover, drought, early runs, setup — and a reader who
    had to scan all of them before learning whether anything needed them had
    been given a control room instead of an inbox. Everything that left the
    page (mode, autonomy, the stage axis, tokens) still lives in Details or
    Settings, and comes back here only as the banner, when it stops the work.

    Pause, Stop and Settings sit behind ⋯: rare and consequential, so never
    beside the button used every day. */

/** An obligation to speak, opened by the platform from arithmetic or a failed
    check — never by the CEO deciding the news was worth your time. */
interface FlagRow {
  id: string;
  kind: string;
  status: string;
  summary: string;
  arithmetic: Record<string, unknown> | null;
  opened_at: string | null;
  raised_at: string | null;
  proposal: string | null;
  /** Raised is news he was given; acknowledged is news he answered. */
  acknowledged_at: string | null;
  acknowledgement: string | null;
}
interface FlagsView { unraised: number; flags: FlagRow[] }

/** "Fri, Dec 4" — the day a person plans around, not a timestamp. */
function fmtDay(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

/** The banners that are about the mode, drawn only when they are a problem or
    the project's last word: a finished delivery (with Reopen), an Explore
    window that has closed, an Operate cycle whose work does not fit. The
    ordinary facts — days left, cycle due — are the Delivery tile's now. */
function ModeStrip({ ctx }: { ctx: ProjectCtx }) {
  const { dash, reload } = ctx;
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ceo = firstName(ctx.ceoName);

  if (dash.shape === "deliver" && dash.status === "completed") {
    // Completing is automatic only because it can be taken back.
    return (
      <div className="strip good double-ruled">
        <div className="grow">
          <b>Delivered. Your results are final.</b>
          <div className="sb">
            If something is missing, you can reopen the project up to 14 days after it closed and
            tell {ceo} what to fix. What you decided is under Details › Activity › Decisions.
          </div>
          {error && <div className="error">{error}</div>}
        </div>
        {dash.can_steer !== false && (confirming ? (
          <>
            <button className="small primary" onClick={async () => {
              try {
                await post(`/admin/projects/${dash.id}/reopen`);
                reload();
              } catch (e) { setError((e as Error).message); }
            }}>Yes, reopen it</button>
            <button className="small" onClick={() => setConfirming(false)}>Cancel</button>
          </>
        ) : (
          <button className="small" onClick={() => setConfirming(true)}>Reopen project</button>
        ))}
      </div>
    );
  }
  if (dash.shape === "explore" && dash.deadline) {
    return (
      <div className="strip serious">
        <div className="grow">
          <b>The review date has passed.</b>
          <div className="sb">
            Decide what happens next: stop the project, set a new date in Settings, or turn what
            worked into a project with a delivery date.
          </div>
        </div>
        <Link to={`/projects/${dash.id}/settings`}>Open Settings →</Link>
      </div>
    );
  }
  const c = dash.cycle;
  if (dash.shape === "operate" && c?.due_at && c.capacity) {
    return (
      <div className="strip serious">
        <div className="grow">
          <b>This cycle won't finish in time.</b>
          <div className="sb">
            Cycle {c.ordinal} is due {fmtWhen(c.due_at)} and the open work doesn't fit. {c.capacity.why}.
            {c.capacity.retry_reserve > 0 && <> {c.capacity.retry_reserve} pieces of work are held back for retries.</>}
          </div>
        </div>
        <Link to={`/projects/${dash.id}/details/roadmap`}>See the plan →</Link>
      </div>
    );
  }
  return null;
}

/** The arithmetic behind "at risk", with the levers beside it — only when the
    date is actually in trouble, and only the levers the dial left with you. */
function LateBanner({ ctx }: { ctx: ProjectCtx }) {
  const { dash, plan, projectId } = ctx;
  const verdict = deliveryVerdict(dash, plan);
  if (verdict.daysLate === null) return null;
  const v = plan?.velocity;
  const c = plan?.commitment;
  const scope = chairmanDecides(dash, "scope_authority");
  const date = chairmanDecides(dash, "commitment_authority");
  const ceo = firstName(ctx.ceoName);
  return (
    <Verdict
      tone={verdict.tone === "good" ? undefined : verdict.tone}
      headline={verdict.headline}
      actions={scope || date ? (
        <>
          {scope && <Link to={`/projects/${projectId}/details/roadmap`}><button className="primary">Drop something from the plan</button></Link>}
          {date && <Link to={`/projects/${projectId}/details/roadmap`}><button>Move the delivery date</button></Link>}
        </>
      ) : (
        <Link to={`/projects/${projectId}/chat`}><button>Ask {ceo} about it</button></Link>
      )}
    >
      <p>
        {v && v.pace_known && v.remaining > 0 && c?.projected_done_at ? (
          <>
            <b>{v.remaining}</b> goal{v.remaining === 1 ? " is" : "s are"} still open. At{" "}
            <b>{v.per_week_3w.toFixed(1)} a week</b>, {v.remaining === 1 ? "it will be" : "they will be"} done
            around <b>{fmtDay(c.projected_done_at)}</b>.{" "}
          </>
        ) : null}
        {scope || date
          ? "Nothing new goes into the plan until you choose. Work already started keeps going."
          : `You left the plan and dates to ${ceo}, so ${ceo} will cut something or move the date and tell you.`}
      </p>
    </Verdict>
  );
}

/** The single worst reason the team can't work, or null. Ordered by what
    stops the most: money and setup first (ProjectSetup draws those, above the
    decisions), then a failing key, a date already slipping, a finished or
    stuck mode, refused work, and runs dying early. One banner, always the
    worst; the rest are still true and still on their own pages. */
function topBanner(ctx: ProjectCtx, early: ReturnType<typeof runHealth>): ReactNode {
  const { dash, plan, projectId } = ctx;
  const base = `/projects/${projectId}`;
  if (dash.fallback?.active) return <FailoverBanner dash={dash} settingsLink={`${base}/settings?s=money`} />;
  if (deliveryVerdict(dash, plan).daysLate !== null) return <LateBanner ctx={ctx} />;
  if (dash.shape === "deliver" && dash.status === "completed") return <ModeStrip ctx={ctx} />;
  if (dash.shape === "explore" && dash.deadline && new Date(dash.deadline).getTime() <= Date.now()) {
    return <ModeStrip ctx={ctx} />;
  }
  if (dash.shape === "operate" && dash.cycle?.due_at && dash.cycle.capacity && !dash.cycle.capacity.feasible) {
    return <ModeStrip ctx={ctx} />;
  }
  return null;
}

/** The words for a goal's state, beside the pattern each draws in — never
    colour alone. */
const GOAL_WORD: Record<NodeState, string> = {
  verified: "done", work: "in progress", you: "needs you", todo: "not started", failed: "failed",
};

/** Goals, delivery and spending: one number each, and each opens the page
    with the whole story. The stage axis, requirement keys and legend this
    replaced are Details › Roadmap's to draw. */
function ProgressRow({ ctx, verdicts, vitals }: { ctx: ProjectCtx; verdicts: Set<string>; vitals: VitalsView | null }) {
  const { dash, plan, projectId, hypotheses } = ctx;
  const base = `/projects/${projectId}`;
  const ceo = firstName(ctx.ceoName);

  // ── goals ──
  let goals: ReactNode;
  if (dash.shape === "explore") {
    const bets = hypotheses.filter((h) => h.status !== "killed");
    const expired = bets.filter((h) => h.status === "expired").length;
    const next = bets.filter((h) => h.status === "active").map((h) => h.kill_date).sort()[0];
    goals = (
      <Link className="ov-tile" to={`${base}/details/tasks`}>
        <span className="ov-tile-l">Ideas being tested <Icon name="arrow" size={13} /></span>
        <span className="ov-tile-v">{bets.length}</span>
        <span className="ov-tile-s">
          {expired > 0 ? `${expired} past the review date. Only you can extend ${expired === 1 ? "it" : "them"}.`
            : next ? `Next review ${fmtDay(next)}` : `${ceo} hasn't proposed any yet.`}
        </span>
      </Link>
    );
  } else {
    const scopeYours = chairmanDecides(dash, "plan_authority");
    const reqs = (plan?.requirements ?? []).filter((r) => r.status !== "cut");
    const nodes: PathNode[] = reqs.flatMap((r) => {
      const status = r.status === "proposed" && !scopeYours ? "pending" : r.status;
      const n = requirementNode({ key: r.key, status, name: requirementName(r), stage: r.stage }, verdicts.has(r.key));
      return n ? [n] : [];
    });
    const count = (s: NodeState) => nodes.filter((n) => n.state === s).length;
    const done = count("verified");
    const rest = (["you", "failed", "work"] as const).filter((s) => count(s) > 0)
      .map((s) => `${count(s)} ${GOAL_WORD[s]}`);
    // Done first, then what needs you, then work, then what has not started,
    // so the bar reads left to right the way the project moves.
    const order: NodeState[] = ["verified", "you", "failed", "work", "todo"];
    const sorted = [...nodes].sort((a, b) => order.indexOf(a.state) - order.indexOf(b.state));
    goals = (
      <Link className="ov-tile" to={`${base}/details/roadmap`}>
        <span className="ov-tile-l">Goals <Icon name="arrow" size={13} /></span>
        {nodes.length ? (
          <>
            <span className="ov-tile-v">{done} of {nodes.length} done</span>
            <span className="ov-segs" role="img"
              aria-label={order.filter((s) => count(s) > 0).map((s) => `${count(s)} ${GOAL_WORD[s]}`).join(", ")}>
              {sorted.map((n) => <i key={n.key} className={`seg-${n.state}`} title={`${n.title}: ${GOAL_WORD[n.state]}`} />)}
            </span>
            <span className="ov-tile-s">{rest.length ? rest.join(" · ") : done === nodes.length ? "Every goal is done." : `${count("todo")} not started`}</span>
          </>
        ) : (
          <>
            <span className="ov-tile-v">No plan yet</span>
            <span className="ov-tile-s">{ceo} is writing it. You'll approve it here.</span>
          </>
        )}
      </Link>
    );
  }

  // ── delivery ──
  const v = deliveryVerdict(dash, plan);
  const c = plan?.commitment;
  const toneOf = v.tone === "good" ? "good" : v.tone;
  let label = "Delivery";
  let value = v.label;
  let pill: string | null = null;
  let sub = v.sub;
  if (dash.shape === "operate" && dash.cycle) {
    label = "This cycle";
    value = `Cycle ${dash.cycle.ordinal}`;
    pill = v.label === value ? null : v.label;
    sub = `${dash.cycle.due_at ? "Due" : "Closes"} ${fmtRoll(dash.cycle.due_at ?? dash.cycle.rolls_at)}`;
  } else if (c?.deliver_by && dash.status !== "completed" && dash.status !== "stopped") {
    value = fmtDay(c.deliver_by);
    pill = v.label;
    sub = c.projected_done_at && c.status !== "met" && v.daysLate === null
      ? `Expected to finish ${fmtDay(c.projected_done_at)}`
      : c.status === "proposed" ? `${ceo} proposed this date. Confirm it in your decisions.` : v.sub;
  } else if (dash.shape === "explore" && dash.deadline) {
    label = "Review date";
    const days = Math.ceil((new Date(dash.deadline).getTime() - Date.now()) / 86_400_000);
    value = fmtDay(dash.deadline);
    pill = days > 0 ? `${days} day${days === 1 ? "" : "s"} left` : "Passed";
    sub = "What exists by then is what this project is judged on.";
  }
  const delivery = (
    <Link className="ov-tile" to={`${base}/details/roadmap`}>
      <span className="ov-tile-l">{label} <Icon name="arrow" size={13} /></span>
      <span className="ov-tile-v">{value} {pill && <span className={`badge ${toneOf}`}>{pill}</span>}</span>
      <TimeVisual dash={dash} plan={plan} />
      <span className="ov-tile-s">{sub.charAt(0).toUpperCase() + sub.slice(1)}</span>
    </Link>
  );

  // ── spending ──
  const { unit, spent, cap, wall, warnRatio } = budgetOf(dash);
  const pace = paceDate(spent, cap);
  const said = wall > 0 && spent >= wall ? "Your team is paused. It reached your spending limit."
    : spent > cap ? `Over budget, so ${ceo} owes you an explanation. Your team pauses at ${fmtUnit(wall, unit)}.`
      : pace ? `At this pace you'll reach your budget ${fmtWhenShort(pace.toISOString())}.`
        : `On pace to stay within budget. Your team pauses at ${fmtUnit(wall, unit)}.`;
  const spending = vitals ? <SpendTile vitals={vitals} base={base} /> : (
    <Link className="ov-tile" to={`${base}/details/spend`}>
      <span className="ov-tile-l">Spending <Icon name="arrow" size={13} /></span>
      <span className="ov-tile-v">{fmtUnit(spent, unit)} <small>of {fmtUnit(cap, unit)} this month</small></span>
      <MoneyMeter spent={spent} cap={cap} wall={wall} unit={unit} warnRatio={warnRatio} compact />
      <span className="ov-tile-s">{said}</span>
    </Link>
  );

  return (
    <section className={`ov-tiles${vitals ? " vt-four" : ""}`} aria-label="Progress">
      {delivery}{vitals && <RunsTile vitals={vitals} base={base} />}{spending}{goals}
    </section>
  );
}

/** What changed since the reader last looked: the CEO's latest note first,
    then what the platform made it bring you, then the briefing's lines, then
    what the CEO is handling without you — folded, because you can't act on it. */
function WhatsNew({ ctx, watch }: { ctx: ProjectCtx; watch: ReactNode[] }) {
  const { projectId } = ctx;
  const ceo = firstName(ctx.ceoName);
  const { data } = usePoll(() => get<ProjectBrief>(`/observe/projects/${projectId}/briefing`), 60000, [projectId]);
  const lines = (data?.lines ?? []).filter((l) => l.group === "attention" || l.group === "moved").slice(0, 3);

  const [channelId, setChannelId] = useState<string | null>(null);
  useEffect(() => {
    get<ChannelRow[]>(`/observe/projects/${projectId}/channels`)
      .then((cs) => setChannelId(cs.find((c) => c.kind === "boardroom")?.id ?? null))
      .catch(() => setChannelId(null));
  }, [projectId]);
  const { data: tail } = usePoll(
    () => channelId ? get<MessageRow[]>(`/observe/channels/${channelId}/messages?limit=6`) : Promise.resolve([] as MessageRow[]),
    15000, [channelId],
  );
  // The CEO's own words only: a line the platform posted has no sender, and
  // under the CEO's name it read as the CEO saying it. And not an open
  // question: that is a decision card at the top of this page already.
  const asking = new Set(ctx.asks.filter((a) => a.status === "open").map((a) => a.asked_in_message_id));
  const last = [...(tail ?? [])].reverse().find((m) => !!m.from_agent && !asking.has(m.id));

  return (
    <section className="card ov-card">
      <div className="card-h">
        <h3>What's new</h3>
        {data && <span className="help">since {fmtSince(data.since)}</span>}
        <span className="grow" />
        <Link className="card-link" to={`/projects/${projectId}/details/activity`}>All <Icon name="arrow" size={13} /></Link>
      </div>
      <ul className="vt-list">
        {last && (
          <li>
            <span className="vt-who">{ceo}</span>
            <Link to={`/projects/${projectId}/chat`}>{clip(last.body.replace(/[#*_`>]/g, "").replace(/\s+/g, " "), 160)}</Link>
            <span className="vt-when">{fmtWhenShort(last.at)}</span>
          </li>
        )}
        {lines.map((l, i) => (
          <li key={`${l.kind}-${i}`}>
            <span className="vt-who">{l.tone === "bad" ? <Icon name="alert" size={13} /> : l.tone === "good" ? <Icon name="check" size={13} /> : "·"}</span>
            <span>{l.text.charAt(0).toUpperCase() + l.text.slice(1)}</span>
            <span />
          </li>
        ))}
        {watch.length > 0 && (
          <li>
            <span className="vt-who">{ceo}</span>
            <details className="ov-handling" style={{ margin: 0, border: 0, padding: 0 }}>
              <summary style={{ padding: 0 }}>Handling {watch.length} on its own</summary>
              <div className="wbox">{watch}</div>
            </details>
            <span />
          </li>
        )}
        {!last && !lines.length && !watch.length && (
          <li><span className="vt-who" /><span className="help">{data ? `Nothing new. Tell ${ceo} what matters most to you.` : "Loading…"}</span><span /></li>
        )}
      </ul>
    </section>
  );
}

/** The newest results and how far each can be trusted. */
function LatestResults({ ctx }: { ctx: ProjectCtx }) {
  const { projectId, dash, plan } = ctx;
  const { data } = usePoll(() => get<ResultsView>(`/observe/projects/${projectId}/results`), 30000, [projectId]);
  if (!data) return null;
  const met = (plan?.requirements ?? []).filter((r) => r.status === "met").length;
  const delivered = dash.status === "completed"
    || (dash.shape === "deliver" && dash.delivery_outstanding !== null && dash.delivery_outstanding.length === 0);
  if (!data.results.length && !(delivered && met)) return null;
  return (
    <section className="card ov-card">
      <div className="card-h">
        <h3>Latest results</h3>
        <span className="grow" />
        <Link className="card-link" to={`/projects/${projectId}/results`}>See all <Icon name="arrow" size={13} /></Link>
      </div>
      {data.results.length ? (
        <ul className="vt-list">
          {data.results.slice(0, 3).map((r) => {
            const by = r.checks.find((c) => c.kind === r.checked);
            const checked = !!by && by.kind !== "pending";
            return (
              <li key={r.key}>
                <span className="vt-who">{r.made_by ? firstName(r.made_by.name) : ""}</span>
                <Link to={`/projects/${projectId}/results`}>{r.name}
                  <span className="help"> · {fmtDate(r.published_at)}</span></Link>
                <span className={`vt-chip${checked ? " good" : ""}`} title={by?.label}>{checked ? "Checked" : "Not checked"}</span>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="help">
          {met} goal{met === 1 ? "" : "s"} done, but no files have been published yet.{" "}
          <Link to={`/projects/${projectId}/details/roadmap`}>See what was checked</Link>.
        </p>
      )}
    </section>
  );
}

/** One row of Needs your OK, as the approved mockup draws it: what it is,
    the plan in a line, how long until it goes ahead on its own, and the two
    buttons. A decision's full case opens under its row. */
function OkRow({ title, plan, fuse, actions, children }: {
  title: ReactNode; plan?: ReactNode; fuse?: { share: number | null; text: string }; actions: ReactNode; children?: ReactNode;
}) {
  return (
    <li className="vt-row">
      <div className="vt-row-main">
        <span className="vt-row-t">{title}</span>
        <span className="vt-row-a">{actions}</span>
        {plan && <span className="vt-row-p">{plan}</span>}
        {fuse && (
          <span className="vt-fuse">
            {fuse.share !== null && <span className="vt-mini"><span className="warn" style={{ width: `${Math.round(fuse.share * 100)}%` }} /></span>}
            {fuse.text}
          </span>
        )}
      </div>
      {children}
    </li>
  );
}

const WEEK_MS = 7 * 86_400_000;

function DecisionRow({ item, base, card }: { item: Item; base: string; card: ReactNode }) {
  const [open, setOpen] = useState(false);
  const lead = item.approval?.recommendation ?? item.ask?.recommendation ?? null;
  const left = item.at - Date.now();
  const fuse = item.clocked && left > 0
    ? { share: Math.min(1, Math.max(0.04, 1 - left / WEEK_MS)), text: `decides itself ${fmtWhenShort(new Date(item.at).toISOString())}` }
    : { share: null, text: "waits for you" };
  return (
    <OkRow
      title={item.title}
      plan={lead ? <>CEO recommends: {clip(lead, 150)}</> : item.consequence}
      fuse={fuse}
      actions={<>
        <button className="small primary" onClick={() => setOpen(!open)} aria-expanded={open}>{open ? "Close" : "Decide"}</button>
        <Link className="btnlink small" to={`${base}/chat`}>Talk</Link>
      </>}
    >
      {open && <div className="card ov-dcard">{card}</div>}
    </OkRow>
  );
}

/** What this project needs from you, as whole decision cards — the same
    cards and the same list (`desk.mine`, the Inbox's Decide) as the Inbox.
    It embedded the Inbox's two panes once, which put one decision in a
    760px frame beside an empty column; the decisions are the page's first
    zone now, and the Inbox is one link away for notes and updates. Three at
    most: past that the Inbox, which sorts and filters them, is the place. */
const SHOWN_HERE = 3;

function ProjectDecisions({ projectId, desk, rows, vitals, compact }: {
  projectId: string; desk: ReturnType<typeof useDesk>; rows: ReactNode[]; vitals: VitalsView | null;
  /** A Deliver home: one line when there is nothing, and "You, last 30
      days" lives on the Decisions page instead of beside the rows. */
  compact?: boolean;
}) {
  const base = `/projects/${projectId}`;
  const decisions = desk.mine.filter((i) => i.project.id === projectId);
  const inbox = `/inbox?project=${encodeURIComponent(projectId)}`;
  if (!desk.data) return null;
  if (compact && !decisions.length && !rows.length) {
    return (
      <div className="dh-caught">
        <span className="dh-caught-ic" aria-hidden="true">✓</span>
        <span><b>Nothing needs your OK.</b> <span className="help">Decisions show up here.</span></span>
      </div>
    );
  }
  if (compact) vitals = null;
  // Two groups under one panel. Only a decision is counted and given a fuse:
  // a flag or a meeting goes ahead without you, and a count that included
  // them would say something was waiting on you when nothing was.
  return (
    <section className="card ov-card vt-ok-panel" aria-labelledby="ov-needs" style={{ marginTop: "var(--s4)" }}>
      <div className="card-h">
        <h3 id="ov-needs">Needs your OK</h3>
        {decisions.length > 0 && <span className="count warn">{decisions.length}</span>}
        <span className="help">{decisions.length ? "Most urgent first" : "Nothing to decide"}</span>
        <span className="grow" />
        {decisions.length > SHOWN_HERE && (
          <Link className="card-link" to={inbox}>See all {decisions.length} <Icon name="arrow" size={13} /></Link>
        )}
      </div>
      <div className={vitals ? "vt-needs" : undefined}>
        <div className="vt-main">
          {decisions.length ? (
            <ul className="vt-rows" aria-label="To decide">
              {decisions.slice(0, SHOWN_HERE).map((item) => (
                <DecisionRow key={item.key} item={item} base={base}
                  card={<DecisionCard item={item} desk={desk} inProject />} />
              ))}
            </ul>
          ) : (
            <div className="ov-clear" style={{ marginTop: 0 }}>
              <span className="ov-clear-ic"><Icon name="check" size={18} strokeWidth={2.4} /></span>
              <span className="grow"><b>You're all caught up.</b>
                <span className="help">Anything you need to decide will show up here.</span></span>
            </div>
          )}
          {rows.length > 0 && (
            <>
              <span className="vt-k vt-group">To note · goes ahead unless you say otherwise</span>
              <ul className="vt-rows" aria-label="To note">{rows}</ul>
            </>
          )}
        </div>
        {vitals && <YouPanel vitals={vitals} />}
      </div>
    </section>
  );
}

/** Pause, Settings and Stop: rare and consequential, so one press away rather
    than beside the button you use every day. Start is not here: on a project
    that is not running it is the one thing to do, so it stays in view. */
function MoreMenu({ ctx, onPause, onStop }: { ctx: ProjectCtx; onPause: () => void; onStop: () => void }) {
  const { dash, projectId } = ctx;
  if (dash.can_steer === false) return null;
  const base = `/projects/${projectId}`;
  return (
    <details className="ov-more">
      <summary aria-label="More actions"><Icon name="more" size={16} /></summary>
      <div className="ov-more-menu" role="menu">
        {dash.status === "running" && <button role="menuitem" onClick={onPause}><Icon name="pause" size={14} /> Pause team</button>}
        <Link role="menuitem" to={`${base}/settings`}><Icon name="gear" size={14} /> Settings</Link>
        {dash.status !== "stopped" && <button role="menuitem" className="danger" onClick={onStop}><Icon name="stop" size={14} /> Stop project</button>}
      </div>
    </details>
  );
}

function validZone(zone: string): boolean {
  try { new Intl.DateTimeFormat(undefined, { timeZone: zone }); return true; } catch { return false; }
}

/** The calendar, as a list: what happens next. The full strip is one press
    away rather than half a screen of the first page. */
function ComingUp({ projectId, vitals, pausedUntil }: { projectId: string; vitals: VitalsView | null; pausedUntil: string | null }) {
  const [full, setFull] = useState(false);
  const { data } = usePoll(() => get<AgendaView>(`/observe/projects/${projectId}/agenda?days=14`), 60000);
  // Times are said in the project's zone, the one the health line uses too —
  // and the zone is named only when it is not the reader's own, which is the
  // only time the name tells them anything.
  const zone = data?.timezone && validZone(data.timezone) ? data.timezone : undefined;
  const mine = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return (
    <section className="card ov-card">
      <div className="card-h">
        <h3>This week</h3>
        {zone && zone !== mine && <span className="help">Times in {zone.replace(/_/g, " ")}</span>}
        <span className="grow" />
        <button className="card-link" onClick={() => setFull(!full)} aria-expanded={full}>
          {full ? "Hide calendar" : "Full calendar"}
        </button>
      </div>
      {full ? <Agenda projectId={projectId} /> : data ? <WeekTimeline agenda={data} vitals={vitals} pausedUntil={pausedUntil} />
        : <p className="help">Loading…</p>}
    </section>
  );
}

/** Setup checks. A healthy project gets one quiet line at the foot of the
    page — silence is ambiguous with a broken check, and the ambiguity costs
    more than the pixels. Failing rows keep their plain-language fixes.

    Two kinds of row: something that needs fixing, and a suggestion. A
    suggestion the reader has read can be dismissed, and then it stops
    counting — most of what kept this lit on every project was advice that is
    true forever and was read on the first day. Dismissed rows stay on the
    card, dimmed, with a way back; a problem has no such button. `id` is the
    target the header's setup link scrolls to. */
function HealthLine({ ctx }: { ctx: ProjectCtx }) {
  const { projectId, health: data, reloadHealth, reloadOrg } = ctx;
  const [show, setShow] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!data) return null;
  const isOpen = (c: HealthCheck) => c.status === "fail" || (c.status === "warn" && !c.acked_at);
  // "Out of runs" and its kin are the status line's to say, with the gate
  // drawn and the time it opens; as a setup check it was the third telling.
  const checks = data.checks.filter((c) => c.key !== "activity" || c.status === "fail");
  const open = checks.filter(isOpen);
  const putAway = checks.filter((c) => c.status === "warn" && c.acked_at);
  if (!show) {
    const ok = checks.filter((c) => c.status === "ok").length;
    return (
      <div className="vt-setup" id="setup-health">
        <span className="vt-k" style={{ margin: 0 }}>Setup</span>
        {ok > 0 && <span className="vt-tag good">{ok} OK</span>}
        {open.slice(0, 3).map((c) => (
          <span key={c.key}>
            <span className={`vt-tag ${c.status === "fail" ? "bad" : "warn"}`}>{c.status === "fail" ? "Fix" : "Suggestion"}</span>
            {clip(c.detail, 90)}
            {WHERE_TO[whereOf(c)] && <> · <Link to={WHERE_TO[whereOf(c)][2] ? WHERE_TO[whereOf(c)][0]
              : `/projects/${projectId}${WHERE_TO[whereOf(c)][0]}`}>{WHERE_TO[whereOf(c)][1]}</Link></>}
          </span>
        ))}
        {open.length > 3 && <span className="help">+{open.length - 3} more</span>}
        {!open.length && putAway.length > 0 && <span className="help">{putAway.length} dismissed</span>}
        <span className="grow" />
        <button className="small" onClick={() => setShow(true)}>See all checks</button>
      </div>
    );
  }
  const ack = async (check: HealthCheck, put: boolean) => {
    setError(null);
    try {
      await post(`/admin/projects/${projectId}/health/ack`, { sig: check.sig, ack: put });
      reloadHealth();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  // The pace row answers itself: switching is an amendment like any made in
  // Settings, with its reason written for him, so it is on the record and
  // disclosed to the CEO the same way.
  const switchTempo = async (check: HealthCheck) => {
    if (check.action?.kind !== "tempo") return;
    setError(null);
    try {
      await post(`/admin/projects/${projectId}/amendments`, {
        reason: `Pace set to ${check.action.to_label} from Setup & health, to match the project's cycle.`,
        changes: check.action.changes,
      });
      reloadHealth();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  // The missing seat, hired the way the Team page hires one: on the role's
  // own model, with its own channel, disclosed in the audit trail.
  const hire = async (check: HealthCheck) => {
    if (check.action?.kind !== "hire") return;
    setError(null);
    try {
      await post(`/admin/projects/${projectId}/teams`, {
        role_slug: check.action.role_slug,
        name: check.action.team_name,
        employees: [check.action.employee],
      });
      reloadHealth();
      reloadOrg();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  // Keeping is the project's answer, not one reader's tidying, so it is a
  // decision on the record and the row leaves every chair's card.
  const keepTempo = async () => {
    setError(null);
    try {
      await post(`/admin/projects/${projectId}/tempo/keep`, {});
      reloadHealth();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  // Problems, then suggestions, then what passes, then what was put away.
  const rank = (c: HealthCheck) =>
    c.status === "fail" ? 0 : c.status === "warn" ? (c.acked_at ? 3 : 1) : 2;
  const rows = [...checks].sort((a, b) => rank(a) - rank(b));
  const badge = (c: HealthCheck) => (
    <span className={`badge ${c.status === "fail" ? "critical" : c.status === "ok" ? "good" : c.acked_at ? "" : "warn"}`}>
      {c.status === "fail" ? "Needs fixing" : c.status === "warn" ? (c.acked_at ? "Dismissed" : "Suggestion") : "OK"}
    </span>
  );
  return (
    <section className="card ov-card" id="setup-health" style={{ marginTop: "var(--s4)" }}>
      <div className="card-h">
        <h3>Setup checks</h3>
        <span className="grow" />
        <button className="small" onClick={() => setShow(false)}>Hide</button>
      </div>
      {open.length > 0 && (
        <p className="help" style={{ margin: "0 0 8px" }}>
          <b>Needs fixing</b> means something is broken and your team may not be able to work.
          A <b>suggestion</b> is your choice: act on it, or dismiss it once you've decided.
        </p>
      )}
      {error && <div className="error">{error}</div>}
      {rows.map((check) => (
        <div key={check.key}
          className={`hrow${check.status === "ok" ? " ok" : ""}${check.acked_at ? " put-away" : ""}`}>
          {badge(check)}
          <span className="grow">{check.detail}</span>
          {check.action?.kind === "repin" && !check.acked_at ? (
            <button className="small" onClick={async () => {
              if (check.action?.kind !== "repin") return;
              await post(`/admin/teams/${check.action.team_id}/repin`, {});
              reloadHealth();
              reloadOrg();
            }}>Update to the latest version</button>
          ) : check.action?.kind === "hire" && !check.acked_at ? (
            <button className="small" onClick={() => hire(check)}>
              {check.action.label}
            </button>
          ) : check.action?.kind === "tempo" && !check.acked_at ? (
            <button className="small" onClick={() => switchTempo(check)}>
              Switch to “{check.action.to_label}”
            </button>
          ) : isOpen(check) && WHERE_TO[whereOf(check)] ? (
            <Link to={WHERE_TO[whereOf(check)][2]
              ? WHERE_TO[whereOf(check)][0]
              : `/projects/${projectId}${WHERE_TO[whereOf(check)][0]}`}>{WHERE_TO[whereOf(check)][1]} →</Link>
          ) : null}
          {check.action?.kind === "tempo" && !check.acked_at ? (
            <button className="small" onClick={() => keepTempo()}>
              Keep “{check.action.keep_label}”
            </button>
          ) : check.status === "warn" && (
            <button className="small" onClick={() => ack(check, !check.acked_at)}>
              {check.acked_at ? "Show again" : "Dismiss"}
            </button>
          )}
        </div>
      ))}
    </section>
  );
}

/** Where a health check says the fix lives — a check that names a place the
    reader cannot click is read once and then skipped. */
const WHERE_TO: Record<string, [string, string, boolean?]> = {
  settings: ["/settings", "Fix in Settings"],
  money: ["/settings?s=money", "Fix in Settings › Money"],
  data_sources: ["/settings?s=sources", "Open Data sources"],
  mail: ["/settings?s=mail", "Open Settings › Email"],
  plan: ["/details/roadmap", "Open the Roadmap"],
  boardroom: ["/chat", "Open the chat"],
  team: ["/team", "See the team"],
  // A price is the install's, not the project's, so it is set where prices
  // live — an app-wide page, not one under the project.
  prices: ["/settings?section=prices", "Open Model prices", true],
  // So is a lineup: a model the key stopped listing is moved where the
  // install's models are chosen.
  models: ["/settings?section=models", "Open Settings › Models", true],
  providers: ["/settings?section=providers", "Open Settings › Providers", true],
};

/** The failover check says "settings" like every other project setting, but
    its control is on one tab of them; landing on the Brief tab instead is a
    link that stops one click short. */
const whereOf = (check: { key: string; where?: string | null }) =>
  check.key === "fallback" ? "money" : check.where ?? "";

/** Where each problem verdict is fixed: page under the project, and the link. */
const FIX_AT: Record<string, [string, string]> = {
  blocked: ["/settings?s=money", "Add a key"],
  failing: ["/details/activity/runs", "See what went wrong"],
  late: ["/details/roadmap", "Cut scope or move the date"],
  at_risk: ["/details/roadmap", "See the plan"],
  stalled: ["/team", "See who is stuck"],
  capped: [`/settings?s=rules&t=${encodeURIComponent("Pace & brakes")}`, "Allow more work per day"],
  spinning: ["/details/activity/runs", "See what the team tried"],
};

export default function Overview({
  ctx, onPause, onStop, goalLead,
}: {
  ctx: ProjectCtx;
  onPause: () => void;
  onStop: () => void;
  goalLead: string;
}) {
  const { projectId, dash } = ctx;
  const desk = useDesk();
  const [brief, setBrief] = useState(false);
  // Start can be refused (a check the server runs that the page cannot); the
  // reason is said beside the button rather than leaving it to look unpressed.
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const start = async () => {
    setStarting(true);
    setStartError(null);
    try {
      await post(`/admin/projects/${projectId}/start`);
      ctx.reload();
    } catch (e) { setStartError((e as Error).message); } finally { setStarting(false); }
  };
  const { data: flagsView, reload: reloadFlags } = usePoll(
    () => get<FlagsView>(`/observe/projects/${projectId}/flags`), 15000);
  const { data: vitals } = usePoll(
    () => get<VitalsView>(`/observe/projects/${projectId}/vitals`), 30000, [projectId]);

  // The same rows the Inbox and the sidebar badge count, for this project. A
  // paused project is not counted there (pausing is a state, not a request),
  // but its own page still lists what is parked on it.
  const deskProject = desk.data?.projects.find((p) => p.id === projectId);
  const items = useMemo(
    () => (deskProject ? itemsFor(deskProject, desk.reload) : []),
    [deskProject, desk.reload],
  );
  const health = runHealth(ctx.runs ?? []);
  const ceo = firstName(ctx.ceoName);
  const base = `/projects/${projectId}`;
  const unpaid = !dash.billing_secret_id && dash.integrations?.runtime !== "mock";

  const rawFlags = flagsView?.flags ?? [];

  // ── your CEO is telling you ──
  //
  // A raised flag is the CEO bringing you something with a proposal. It does
  // not block, it does not expire, and it proceeds on that proposal whether or
  // not you answer — so it reads beside the CEO's last word, never under the
  // heading that says a decision is waiting on you.
  const notes: ReactNode[] = [];
  for (const f of rawFlags.filter((f) => f.status === "raised" && !f.acknowledged_at)) {
    notes.push(
      <OkRow key={f.id} title={flagPlain(f.kind).title}
        plan={<>{ceo}'s plan: {f.proposal ? clip(f.proposal, 160) : "not written yet"}</>}
        fuse={{ share: null, text: "no deadline" }}
        actions={<>
          <NotedButton primary={false} onSubmit={async (note) => {
            await post(`/admin/flags/${f.id}/ack`, { note });
            reloadFlags();
          }} />
          <Link className="btnlink small" to={`${base}/chat`}>Talk</Link>
        </>} />,
    );
  }

  // ── handling ──
  // Clocks the CEO resolves: shown so nothing happens silently, never counted
  // as needing you. The project-wide ones come from the same rows the Inbox
  // shows under Watching; the ones below exist only here.
  const watch: ReactNode[] = items.filter((i) => !needsYou(i)).map((it) => (
    <div className="wrow" key={it.key}>
      <span className="badge">{it.kind}</span>
      <div className="grow">{it.what} <span className="help">{it.consequence}</span></div>
      <Link to={it.to}>{it.toLabel} →</Link>
    </div>
  ));
  // An UNRAISED flag: your CEO has news it has not yet brought you. Drawn
  // here, cheerfulness in the chat is self-refuting rather than something
  // you have to catch.
  for (const f of rawFlags.filter((f) => f.status === "open")) {
    watch.push(
      <div className="wrow" key={f.id}>
        <span className="badge serious">Plan coming</span>
        <div className="grow">
          <b>{ceo} will bring you a plan for: {flagPlain(f.kind).title.toLowerCase()}</b>{" "}
          <span className="help">
            yaaf noticed this{f.opened_at ? ` ${fmtWhenShort(f.opened_at)}` : ""}, and {ceo} has to bring it
            to you with a proposal. So far: {clip(f.summary)}
          </span>
        </div>
        <Link to={`${base}/chat`}>Ask about it →</Link>
      </div>,
    );
  }
  // Acknowledged, and still true: your word ended the conversation, not the
  // condition, so it waits here under your note until the arithmetic turns.
  for (const f of rawFlags.filter((f) => f.status === "raised" && f.acknowledged_at)) {
    watch.push(
      <div className="wrow" key={f.id}>
        <span className="badge">You noted this</span>
        <div className="grow">
          <b>{flagPlain(f.kind).title}</b>{" "}
          <span className="help">
            Still true. You noted it{f.acknowledged_at ? ` ${fmtWhenShort(f.acknowledged_at)}` : ""}
            {f.acknowledgement ? `: “${clip(f.acknowledgement, 100)}”` : ""}. It clears when the numbers do.
          </span>
        </div>
        <Link to={`${base}/chat`}>Bring it up →</Link>
      </div>,
    );
  }
  // A full memory is an outage that looks like nothing: writes are refused and
  // the only symptom is a climbing bill. It has its own card with a bar per
  // scope now (MemoryCard), so it is not a line under "handling" as well.

  const verdicts = new Set((deskProject?.awaiting_you.verdicts ?? []).map((v) => v.key));
  const settingUp = unpaid || (!!dash.mail_start_refusal && dash.status !== "running");
  const verdict = deskProject?.status_line?.verdict;
  const fix = verdict && verdict !== "capped" && FIX_AT[verdict]
    ? <Link to={`${base}${FIX_AT[verdict][0]}`}>{FIX_AT[verdict][1]} →</Link> : undefined;
  const banner = settingUp ? null : topBanner(ctx, health);
  // The things that used to be banners and are really asks of the chairman:
  // a meeting to hold, work nobody outside has checked, runs dying early.
  const rows: ReactNode[] = [];
  if (ctx.reviews?.open) {
    rows.push(<OkRow key="meeting" title={`Your status meeting #${ctx.reviews.open.number} is open`}
      plan={`What got done, where it stands, what's next, and what ${ceo} needs from you.`}
      actions={<Link className="btnlink small primary" to={`${base}/details/reviews`}>Open meeting</Link>} />);
  }
  // A promised credential that stopped working: something he cares about has
  // stopped, so it is said here with the one thing to do, not only as a red
  // chip in the setup strip.
  for (const st of ctx.setup?.steps ?? []) {
    if (st.credential !== "failed") continue;
    rows.push(<OkRow key={st.key} title={`${st.label} stopped working`} plan={st.detail}
      actions={<Link className="btnlink small primary" to={`${base}/settings?s=${st.section}`}>Fix it</Link>} />);
  }
  rows.push(...notes);
  if (dash.drought_active) {
    rows.push(<OkRow key="drought" title="Nothing has been checked from outside in a while"
      plan="Work goes on, but new work only the team can check waits until something outside confirms it."
      actions={<Link className="btnlink small" to={`${base}/details/activity/evidence`}>See checks</Link>} />);
  }
  if (health.early.length > 0 && health.ratio >= 0.2) {
    rows.push(<OkRow key="early" title={earlyRunSentence(health)}
      plan={health.faults.length === 0 ? "Each was stopped on purpose, but they still cost runs."
        : "More than usual: a data source, a key, or work the team can't do may be in the way."}
      actions={<Link className="btnlink small" to={`${base}/details/activity/runs`}>See runs</Link>} />);
  }
  // A Deliver project's goals, worked out once for the hero, the checklist
  // and the chart, so the three cannot disagree.
  const goals = dash.shape === "deliver" ? goalsOf(ctx, verdicts) : null;
  const unstarted = goals?.list.filter((g) => g.state === "todo" && !g.after.length && !g.r.tasks.length) ?? [];
  const idleWhy = unstarted.length
    ? `${unstarted.map((g) => g.key).join(", ")} ${unstarted.length === 1 ? "has" : "have"} no tasks yet` : null;
  const pausedUntil = dash.status === "running" && ["daily_runs", "governance_reserve", "pacing", "inbox_runs"]
    .includes(dash.activity.blocked_by ?? "") ? resumesAt(dash, vitals) : null;

  return (
    <>
      <header className="ov-head">
        <div className="ov-title-row">
          <div className="grow">
            <h1 className="ov-title">
              {dash.name} {verdictBadge(deskProject ?? { status: dash.status, unpaid },
                deskProject ? needsYouCount(deskProject) : 0)} <ModePill dash={dash} plan={ctx.plan} />
            </h1>
          </div>
          <div className="ov-acts">
            {dash.can_steer !== false && dash.status !== "running" && dash.status !== "stopped" && (
              unpaid ? (
                <Link to={`${base}/settings?s=money`} className="btnlink primary"
                  title="Your team can't work until it has an AI service key">
                  <Icon name="play" size={15} />Add a key to start
                </Link>
              ) : dash.mail_start_refusal ? (
                // The server would refuse the start in these words; say them on
                // the button that leads to the fix, not after a press.
                <Link to={`${base}/settings?s=mail`} className="btnlink primary"
                  title={dash.mail_start_refusal}>
                  <Icon name="mail" size={15} />Set up email to start
                </Link>
              ) : (
                <button className="primary" disabled={starting} onClick={start}>
                  <Icon name="play" size={15} />{dash.status === "paused" ? "Resume" : "Start"}
                </button>
              )
            )}
            {/* Never primary: on this page the decisions own the one strong
                button, and two of them means neither stands out. */}
            <Link to={`${base}/chat`} className="btnlink">
              <Icon name="message" size={15} />Message {ceo}</Link>
            <MoreMenu ctx={ctx} onPause={onPause} onStop={onStop} />
            {startError && <div className="error ov-start-error" role="alert">{startError}</div>}
          </div>
        </div>
        <p className="ov-goal">
          <span className="ov-goal-k">Goal:</span> {goalLead}{" "}
          <button className="linkish" onClick={() => setBrief(!brief)} aria-expanded={brief}>
            {brief ? "Hide the full goal" : "See the full goal"}
          </button>
        </p>
        {brief && (
          <div className="briefbox">
            <strong style={{ color: "var(--ink)" }}>The full goal your team works from</strong>
            <p style={{ whiteSpace: "pre-wrap", margin: "6px 0 0" }}>{dash.goal}</p>
          </div>
        )}
      </header>

      {/* Setup leads only when it is the reason nothing can run. Otherwise it
          is a link in the header: on a running — or finished — project, a
          six-step checklist above the decisions was configuration outranking
          state. When setup is the reason, it is the one banner. */}
      {/* Setup reads first: the whole checklist when it is why nothing can
          run, the strip of steps (each one click from its section) while the
          project is only unfinished. */}
      {settingUp ? <ProjectSetup ctx={ctx} /> : <SetupStrip ctx={ctx} />}
      {goals ? (
        // A Deliver project leads with how close it is to done; the rest of
        // its home is the approved Deliver mockup, below.
        <>
          <DeliverHero ctx={ctx} goals={goals} />
          {!settingUp && <StatusLine dash={dash} vitals={vitals} fix={fix} idleWhy={idleWhy} />}
          {banner}
          <ProjectDecisions projectId={projectId} desk={desk} rows={rows} vitals={vitals} compact />
          <div className="ov-grid">
            <div className="ov-col">
              <GoalChecklist ctx={ctx} goals={goals} />
              <GoalsOverTime ctx={ctx} goals={goals} />
            </div>
            <aside className="ov-col">
              <DeliverNumbers ctx={ctx} goals={goals} vitals={vitals} />
              <DeliverComingUp projectId={projectId} />
              {vitals && <SharedPlan vitals={vitals} />}
              <MemoryCard dash={dash} base={base} />
              <WhatsNew ctx={ctx} watch={watch} />
              <LatestResults ctx={ctx} />
            </aside>
          </div>
          <HealthLine ctx={ctx} />
          <AsOf at={ctx.asOf} error={ctx.error} />
        </>
      ) : <>
      {!settingUp && <StatusLine dash={dash} vitals={vitals} fix={fix} />}
      {banner}

      {/* The project's home is what it needs from you first: this project's
          decisions, whole, from the one list the Inbox decides from. */}
      <ProjectDecisions projectId={projectId} desk={desk} rows={rows} vitals={vitals} />

      <ProgressRow ctx={ctx} verdicts={verdicts} vitals={vitals} />

      <div className="ov-grid">
        <div className="ov-col">
          <ModePanel dash={dash} plan={ctx.plan} hypotheses={ctx.hypotheses} base={base} vitals={vitals} />
          {vitals && <LimitGrid vitals={vitals} base={base} />}
          {vitals && <RunsWeek vitals={vitals} base={base} />}
          <ComingUp projectId={projectId} vitals={vitals} pausedUntil={pausedUntil} />
        </div>
        <aside className="ov-col">
          {vitals && <SharedPlan vitals={vitals} />}
          <MemoryCard dash={dash} base={base} />
          <WhatsNew ctx={ctx} watch={watch} />
          <LatestResults ctx={ctx} />
        </aside>
      </div>

      <HealthLine ctx={ctx} />
      <AsOf at={ctx.asOf} error={ctx.error} />
      </>}
    </>
  );
}
