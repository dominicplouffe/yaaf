import { ReactNode, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  AgendaView,
  ChannelRow,
  get,
  HealthCheck,
  MessageRow,
  post,
  requirementName,
  ResultsView,
} from "../../api";
import { ProjectBrief } from "../../briefing";
import { FailoverBanner } from "../../fallback";
import Agenda from "../../agenda";
import { itemsFor, needsYou, needsYouCount, useDesk, WARN_VERDICTS } from "../../desk";
import { fmtSince, fmtWhen, usePoll } from "../../hooks";
import Markdown from "../../Markdown";
import { firstName, ProjectCtx } from "../../projectCtx";
import {
  AsOf,
  budgetOf,
  clip,
  clipMarkdown,
  earlyRunSentence,
  fmtSoon,
  fmtUnit,
  fmtWhenShort,
  NotedButton,
  paceDate,
  runHealth,
} from "../../ui";
import { Avatar, Verdict } from "../../ui/kit";
import { Icon } from "../../ui/Icon";
import { chairmanDecides, deliveryVerdict, flagPlain, fmtDate, fmtRoll } from "../../ui/project";
import { MoneyMeter, NodeState, PathNode, requirementNode, Seal, sealOf, verdictBadge } from "../../ui/signal";
import "./overview.css";
import { DecisionCard } from "../Inbox";
import { ProjectSetup } from "../../projectSetup";

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

  if (dash.mode === "deliver" && dash.status === "completed") {
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
  if (dash.mode === "explore" && dash.deadline) {
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
  if (dash.mode === "operate" && c?.due_at && c.capacity) {
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
  if (dash.mode === "deliver" && dash.status === "completed") return <ModeStrip ctx={ctx} />;
  if (dash.mode === "explore" && dash.deadline && new Date(dash.deadline).getTime() <= Date.now()) {
    return <ModeStrip ctx={ctx} />;
  }
  if (dash.mode === "operate" && dash.cycle?.due_at && dash.cycle.capacity && !dash.cycle.capacity.feasible) {
    return <ModeStrip ctx={ctx} />;
  }
  if (dash.drought_active) {
    return (
      <div className="strip serious">
        <div className="grow">
          <b>Your team's work hasn't been checked from outside in a while.</b>
          <div className="sb">
            Your rules ask for outside proof that the work is real. Work keeps going, but new work that
            can only be checked by the team itself waits until something is confirmed.
          </div>
        </div>
        <Link to={`${base}/details/activity/evidence`}>See what's been checked →</Link>
      </div>
    );
  }
  if (early.early.length > 0 && early.ratio >= 0.2) {
    return (
      <div className="strip serious">
        <div className="grow">
          <b>{earlyRunSentence(early)}</b>
          <div className="sb">
            {early.faults.length === 0
              ? "Each one was stopped on purpose, so nothing points at a fault, but they still cost money."
              : "That's more than usual, so something may be in the way: a broken data source, a wrong key, or work your team can't do."}
          </div>
        </div>
        <Link to={`${base}/details/activity/runs`}>See what happened →</Link>
      </div>
    );
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
function ProgressRow({ ctx, verdicts }: { ctx: ProjectCtx; verdicts: Set<string> }) {
  const { dash, plan, projectId, hypotheses } = ctx;
  const base = `/projects/${projectId}`;
  const ceo = firstName(ctx.ceoName);

  // ── goals ──
  let goals: ReactNode;
  if (dash.mode === "explore") {
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
  if (dash.mode === "operate" && dash.cycle) {
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
  } else if (dash.mode === "explore" && dash.deadline) {
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
  const spending = (
    <Link className="ov-tile" to={`${base}/details/spend`}>
      <span className="ov-tile-l">Spending <Icon name="arrow" size={13} /></span>
      <span className="ov-tile-v">{fmtUnit(spent, unit)} <small>of {fmtUnit(cap, unit)} this month</small></span>
      <MoneyMeter spent={spent} cap={cap} wall={wall} unit={unit} warnRatio={warnRatio} compact />
      <span className="ov-tile-s">{said}</span>
    </Link>
  );

  return <section className="ov-tiles" aria-label="Progress">{goals}{delivery}{spending}</section>;
}

/** What changed since the reader last looked: the CEO's latest note first,
    then what the platform made it bring you, then the briefing's lines, then
    what the CEO is handling without you — folded, because you can't act on it. */
function WhatsNew({ ctx, news, watch }: { ctx: ProjectCtx; news: ReactNode[]; watch: ReactNode[] }) {
  const { projectId } = ctx;
  const navigate = useNavigate();
  const ceo = firstName(ctx.ceoName);
  const { data } = usePoll(() => get<ProjectBrief>(`/observe/projects/${projectId}/briefing`), 60000, [projectId]);
  const lines = (data?.lines ?? []).filter((l) => l.group === "attention" || l.group === "moved").slice(0, 4);

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
  const chat = (draft?: string) => navigate(`/projects/${projectId}/chat`, draft ? { state: { draft } } : undefined);

  return (
    <section className="card ov-card">
      <div className="card-h">
        <h3>What's new</h3>
        {data && <span className="help">since {fmtSince(data.since)}</span>}
        <span className="grow" />
        <Link className="card-link" to={`/projects/${projectId}/details/activity`}>All activity <Icon name="arrow" size={13} /></Link>
      </div>
      {last ? (
        <div className="ov-note">
          <div className="ceo-last">
            <Avatar name={ctx.ceoName} team="leadership" />
            <div className="ceo-body">
              <div className="ov-note-h"><b>{ceo}</b> <span className="help">{fmtWhen(last.at)}</span></div>
              <Markdown>{clipMarkdown(last.body, 420)}</Markdown>
            </div>
          </div>
          <div className="quick">
            <button className="qchip" onClick={() => chat()}><Icon name="reply" size={13} /> Reply</button>
            <button className="qchip" onClick={() => chat("What changed since we last talked, and what's next?")}>What changed?</button>
            <button className="qchip" onClick={() => chat("What is the biggest risk to the delivery date right now?")}>Biggest risk?</button>
          </div>
        </div>
      ) : (tail ?? []).some((m) => !!m.from_agent) ? (
        // Everything it said lately is a question already drawn above, as a
        // decision; "hasn't said anything" would be false under that card.
        <p className="help">{ceo}'s latest message is the question above, under Needs you.</p>
      ) : tail ? (
        <p className="help">
          {ceo} hasn't written yet. <Link to={`/projects/${projectId}/chat`}>Tell {ceo} what matters most to you.</Link>
        </p>
      ) : null}
      {news.length > 0 && <div className="ov-news">{news}</div>}
      {!data ? <p className="help">Loading…</p> : lines.length ? (
        <ul className="ov-since">
          {lines.map((l, i) => (
            <li key={`${l.kind}-${i}`} className={`tone-${l.tone}`}>
              {l.tone === "good" ? <Icon name="check" size={14} />
                : l.tone === "bad" ? <Icon name="alert" size={14} /> : <i className="ov-dot" aria-hidden="true" />}
              <span>{l.text}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="help">Nothing new since your last visit.</p>
      )}
      {watch.length > 0 && (
        <details className="ov-handling">
          <summary>{ceo} is handling · {watch.length}</summary>
          <p className="help">No action needed. They're here so nothing happens without you seeing it.</p>
          <div className="wbox">{watch}</div>
        </details>
      )}
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
    || (dash.mode === "deliver" && dash.delivery_outstanding !== null && dash.delivery_outstanding.length === 0);
  if (!data.results.length && !(delivered && met)) return null;
  return (
    <section className="card ov-card">
      <div className="card-h">
        <h3>Latest results</h3>
        <span className="grow" />
        <Link className="card-link" to={`/projects/${projectId}/results`}>See all <Icon name="arrow" size={13} /></Link>
      </div>
      {data.results.length ? (
        <ul className="ov-results">
          {data.results.slice(0, 3).map((r) => {
            const by = r.checks.find((c) => c.kind === r.checked);
            return (
              <li key={r.key}>
                <Link className="ov-result" to={`/projects/${projectId}/results`}>
                  <Icon name="file" size={16} />
                  <span className="ov-result-t">
                    <b>{r.name}</b>
                    <span className="help">{r.made_by ? `${r.made_by.name} · ` : ""}{fmtDate(r.published_at)}</span>
                    {by && by.kind !== "pending"
                      ? <span className="ov-result-chk">{by.label.charAt(0).toUpperCase() + by.label.slice(1)}</span>
                      : <Seal level={sealOf(r.checked)} />}
                  </span>
                </Link>
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

/** What this project needs from you, as whole decision cards — the same
    cards and the same list (`desk.mine`, the Inbox's Decide) as the Inbox.
    It embedded the Inbox's two panes once, which put one decision in a
    760px frame beside an empty column; the decisions are the page's first
    zone now, and the Inbox is one link away for notes and updates. Three at
    most: past that the Inbox, which sorts and filters them, is the place. */
const SHOWN_HERE = 3;

function ProjectDecisions({ projectId, desk }: { projectId: string; desk: ReturnType<typeof useDesk> }) {
  const decisions = desk.mine.filter((i) => i.project.id === projectId);
  const inbox = `/inbox?project=${encodeURIComponent(projectId)}`;
  if (!desk.data) return null;
  if (!decisions.length) {
    return (
      <div className="ov-clear">
        <span className="ov-clear-ic"><Icon name="check" size={18} strokeWidth={2.4} /></span>
        <span className="grow">
          <b>You're all caught up.</b>
          <span className="help">Your team is working. Anything that needs you will show up here.</span>
        </span>
        <Link className="card-link" to={`${inbox}&tab=ack`}>See updates <Icon name="arrow" size={13} /></Link>
      </div>
    );
  }
  return (
    <section className="ov-decisions" aria-labelledby="ov-needs">
      <div className="ov-dec-h">
        <h2 id="ov-needs">Needs you</h2>
        <span className="count warn" aria-label={`${decisions.length} decision${decisions.length === 1 ? "" : "s"}`}>{decisions.length}</span>
        <span className="help">Most urgent first</span>
        <span className="grow" />
        {decisions.length > SHOWN_HERE && (
          <Link className="card-link" to={inbox}>See all {decisions.length} decisions <Icon name="arrow" size={13} /></Link>
        )}
      </div>
      {decisions.slice(0, SHOWN_HERE).map((item) => (
        <div className="card ov-dcard" key={item.key}>
          <DecisionCard item={item} desk={desk} inProject />
        </div>
      ))}
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
function ComingUp({ projectId }: { projectId: string }) {
  const [full, setFull] = useState(false);
  const { data } = usePoll(() => get<AgendaView>(`/observe/projects/${projectId}/agenda?days=14`), 60000);
  const now = Date.now();
  const events = (data?.events ?? []).filter((e) => new Date(e.at).getTime() >= now - 3_600_000).slice(0, 4);
  // Times are said in the project's zone, the one the health line uses too —
  // and the zone is named only when it is not the reader's own, which is the
  // only time the name tells them anything.
  const zone = data?.timezone && validZone(data.timezone) ? data.timezone : undefined;
  const mine = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return (
    <section className="card ov-card">
      <div className="card-h">
        <h3>Coming up</h3>
        {zone && zone !== mine && <span className="help">Times in {zone.replace(/_/g, " ")}</span>}
        <span className="grow" />
        <button className="card-link" onClick={() => setFull(!full)} aria-expanded={full}>
          {full ? "Hide calendar" : "Full calendar"}
        </button>
      </div>
      {full ? <Agenda projectId={projectId} /> : events.length ? (
        <ul className="agenda-list">
          {events.map((e, i) => (
            <li key={`${e.at}-${i}`} className={`ag-${e.tone}`}>
              <span className="ag-when">
                <b>{new Date(e.at).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", timeZone: zone })}</b>
                <span>{new Date(e.at).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit", timeZone: zone })}</span>
              </span>
              <i className="ag-dot" />
              <span className="ag-what">{e.label}{e.detail && <span className="help"> — {clip(e.detail, 90)}</span>}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="help">{data ? (data.notes[0] ?? "Nothing is scheduled in the next two weeks.") : "Loading…"}</p>
      )}
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
  const open = data.checks.filter(isOpen);
  const putAway = data.checks.filter((c) => c.status === "warn" && c.acked_at);
  if (!open.length && !show) {
    return (
      <div className="health-line" id="setup-health">
        <span className="good"><Icon name="check" size={15} strokeWidth={2.2} /></span>
        <b>Everything is set up.</b>
        <span className="help">
          {putAway.length
            ? `${putAway.length} suggestion${putAway.length === 1 ? "" : "s"} dismissed.`
            : `All ${data.checks.length} checks pass.`}
        </span>
        <span className="grow" />
        <button className="small" onClick={() => setShow(true)}>See checks</button>
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
  const rows = [...data.checks].sort((a, b) => rank(a) - rank(b));
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
        {!open.length && <button className="small" onClick={() => setShow(false)}>Hide</button>}
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
  const [showSetup, setShowSetup] = useState(false);
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
  const news: ReactNode[] = [];
  for (const f of rawFlags.filter((f) => f.status === "raised" && !f.acknowledged_at)) {
    const plain = flagPlain(f.kind);
    news.push(
      <div className="flag" key={f.id}>
        <div className="flag-t"><Icon name="flag" size={14} /> <b>{plain.title}</b></div>
        <div className="help">
          {ceo}'s plan: {f.proposal ? clip(f.proposal, 200) : "not written yet"}. This goes ahead unless
          you say otherwise.
        </div>
        <div className="flag-a">
          <NotedButton
            primary={false}
            onSubmit={async (note) => {
              await post(`/admin/flags/${f.id}/ack`, { note });
              reloadFlags();
            }}
          />
          <Link to={`${base}/chat`}>Talk it through →</Link>
        </div>
      </div>,
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
  // A full memory is an outage that looks like nothing: writes are refused,
  // runs die on the refusal cap, and the only symptom is a climbing bill.
  for (const m of dash.memory?.scopes ?? []) {
    if (m.live < m.cap * (dash.memory?.warn_ratio ?? 0.85)) continue;
    const full = m.live >= m.cap;
    watch.push(
      <div className="wrow" key={`memory:${m.scope}:${m.team_id ?? ""}`}>
        <span className={`badge ${full ? "serious" : "warn"}`}>Notes {m.live} of {m.cap}</span>
        <div className="grow">
          {m.name} {full ? "is full, so new notes there are refused" : "is nearly full"} until{" "}
          {m.scope === "project" ? ceo : (m.curator ?? "a team lead")} tidies it up.
        </div>
        <Link to={`${base}/details/activity/memory`}>See what it keeps →</Link>
      </div>,
    );
  }

  const verdicts = new Set((deskProject?.awaiting_you.verdicts ?? []).map((v) => v.key));
  const setupLeft = ctx.setup?.open ? ctx.setup.steps.filter((st) => st.state === "todo").length : 0;
  const quietWait = dash.status === "running" && !dash.activity.brief.banner && dash.activity.brief.clears_at
    ? dash.activity.brief : null;
  const settingUp = unpaid || (!!dash.mail_start_refusal && dash.status !== "running");
  const banner = settingUp ? null : topBanner(ctx, health);

  return (
    <>
      <header className="ov-head">
        <div className="ov-title-row">
          <div className="grow">
            <h1 className="ov-title">
              {dash.name} {verdictBadge(deskProject ?? { status: dash.status, unpaid },
                deskProject ? needsYouCount(deskProject) : 0)}
            </h1>
            {deskProject?.status_line && (
              // The sentence Home, the CLI and Claude's desk all say about this
              // project, with the one page that changes it.
              <p className={`ov-status${WARN_VERDICTS.includes(deskProject.status_line.verdict) ? " warn" : ""}`}>
                {deskProject.status_line.sentence}
                {FIX_AT[deskProject.status_line.verdict] && (
                  <> <Link to={`${base}${FIX_AT[deskProject.status_line.verdict][0]}`}>
                    {FIX_AT[deskProject.status_line.verdict][1]}</Link></>
                )}
              </p>
            )}
            {quietWait && (
              <p className="help ov-wait" title={quietWait.detail}>
                Back to work {fmtSoon(quietWait.clears_at!)}. {quietWait.headline}
              </p>
            )}
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
        {setupLeft > 0 && !settingUp && (
          <button className="linkish ov-setup-chip" onClick={() => setShowSetup(!showSetup)} aria-expanded={showSetup}>
            Finish setting up · {setupLeft} step{setupLeft === 1 ? "" : "s"} left
          </button>
        )}
      </header>

      {/* Setup leads only when it is the reason nothing can run. Otherwise it
          is a link in the header: on a running — or finished — project, a
          six-step checklist above the decisions was configuration outranking
          state. When setup is the reason, it is the one banner. */}
      {(settingUp || showSetup) && <ProjectSetup ctx={ctx} />}
      {banner}

      {/* The project's home is what it needs from you first: this project's
          decisions, whole, from the one list the Inbox decides from. */}
      <ProjectDecisions projectId={projectId} desk={desk} />

      <ProgressRow ctx={ctx} verdicts={verdicts} />

      <div className="ov-grid">
        <div className="ov-col">
          <WhatsNew ctx={ctx} news={news} watch={watch} />
        </div>
        <aside className="ov-col">
          <ComingUp projectId={projectId} />
          <LatestResults ctx={ctx} />
        </aside>
      </div>

      <HealthLine ctx={ctx} />
      <AsOf at={ctx.asOf} error={ctx.error} />
    </>
  );
}
