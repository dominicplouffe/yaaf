import { ReactNode, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  AgendaView,
  ChannelRow,
  Dashboard,
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
  FlagAnswer,
  paceDate,
  runHealth,
} from "../../ui";
import { Avatar, Badge, Term, Verdict } from "../../ui/kit";
import { Icon } from "../../ui/Icon";
import { chairmanDecides, deliveryVerdict, cycleOverdue, flagPlain, flagTold, fmtDate, fmtRoll, fullyAutonomous, oneCardPerCause } from "../../ui/project";
import { MoneyMeter, NODE_WORD, PathNode, requirementNode, Seal, sealOf, verdictBadge } from "../../ui/signal";
import "./overview.css";
import { DecisionCard } from "../Inbox";
import { ProjectSetup } from "../../projectSetup";

/** A project's home answers four questions, in the order they are asked:

      does it need me?          this project's rows of the one Inbox, with their buttons
      where does it stand?      the outcomes against the promise, one dot each
      what changed, what next?  since you looked · the next dated events · the money
      what did it produce?      the newest results, each with how far it can be trusted

    Then the CEO's last word, what it is handling, and Setup & health. The
    team is a page of its own, and Pause, Stop and Settings sit behind ⋯: rare
    and consequential, so never beside the button used every day. */

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
  headline: string | null;
  why: string | null;
  plan: string | null;
  from_you: string | null;
  covered_by_flag_id: string | null;
  /** Raised is news he was given; acknowledged is news he answered. */
  acknowledged_at: string | null;
  acknowledgement: string | null;
}
interface FlagsView { unraised: number; flags: FlagRow[] }


/** Who holds which verb, in one word for the header. */
function autonomyWord(dash: Dashboard): { word: string; means: string } {
  if (fullyAutonomous(dash)) {
    return { word: "Autonomous", means: "Your CEO accepts its own plan, signs dates and judges unverifiable work, and tells you after. Money, legal questions and credentials are always yours." };
  }
  const all = (["plan_authority", "scope_authority", "commitment_authority", "verdict_policy", "capability_authority"] as const)
    .every((r) => chairmanDecides(dash, r));
  return all
    ? { word: "Supervised", means: "You accept scope, sign the delivery date and judge work no script can check. Your CEO proposes." }
    : { word: "Mixed autonomy", means: "Some decisions are yours and some are your CEO's — Settings → How it runs says which." };
}

/** The mode-shaped fact, one per mode: Explore counts its window down,
    Deliver says so once it has completed (its sign-off is an Inbox row), Operate
    names the turn of the rhythm it is on. */
function ModeStrip({ dash, reload }: { dash: Dashboard; reload: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (dash.mode === "deliver" && dash.status === "completed") {
    // Completing is automatic only because it can be taken back.
    return (
      <div className="strip good">
        <div className="grow">
          <b>Delivered.</b>
          <div className="sb">
            The receipt is under Activity → Decisions. If something is missing, reopen it — up
            to 14 days after it closed — and tell the CEO what.
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
          <button className="small" onClick={() => setConfirming(true)}>Reopen…</button>
        ))}
      </div>
    );
  }
  // A finished delivery is not drawn here. It is a sign-off row in the Inbox
  // panel on this same page, with the Complete button, and was once also this
  // callout and a banner above every tab: three copies of one decision, under a
  // queue that said nothing needed you.
  if (dash.mode === "explore" && dash.deadline) {
    const daysLeft = Math.ceil((new Date(dash.deadline).getTime() - Date.now()) / 86_400_000);
    return (
      <div className={`strip${daysLeft <= 0 ? " serious" : ""}`}>
        <div className="grow">
          <b>{daysLeft > 0 ? `${daysLeft} day${daysLeft === 1 ? "" : "s"} left in the window.` : "The window has closed."}</b>
          <div className="sb">
            Open scope, bounded time — {daysLeft > 0
              ? "what exists by then is what this project is judged on."
              : "the judgment is yours: stop it, set a new date in Settings, or convert what worked into a Deliver project."}
          </div>
        </div>
      </div>
    );
  }
  if (dash.mode === "operate" && dash.cycle) {
    const c = dash.cycle;
    // With a date in play the honest question is not "when did this open" but
    // "does the work reach it". That answer is arithmetic — the same the
    // dispatcher gates on — so it is stated rather than left to be asked.
    if (c.due_at && c.capacity) {
      const cap = c.capacity;
      return (
        <div className={`strip${cap.feasible ? "" : " serious"}`}>
          <div className="grow">
            <b>Cycle {c.ordinal} is due {fmtWhen(c.due_at)} — {cap.feasible ? "the work fits." : "the work does not fit."}</b>
            <div className="sb">
              {c.goal ? <>{clip(c.goal)}<br /></> : null}
              {cap.why}. {cap.basis}.
              {cap.retry_reserve > 0 && <> Holding back {cap.retry_reserve} run(s) for retries.</>}
              {c.due_source?.kind === "data" && c.due_source.url && (
                <> Date read from <span className="mono">{c.due_source.url}</span>.</>
              )}
            </div>
          </div>
        </div>
      );
    }
    return (
      <p className="help">
        Cycle {c.ordinal}, open since {fmtWhen(c.opened_at)}
        {c.goal ? <> — {clip(c.goal)}</> : <> — each cycle plans fresh, and the CEO opens it by reading the last three cycles' narratives.</>}
      </p>
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
        <Link to={`/projects/${projectId}/chat`}><button>Ask {firstName(ctx.ceoName)} what it is doing about it</button></Link>
      )}
    >
      <p>
        {v && v.pace_known && v.remaining > 0 && c?.projected_done_at ? (
          <>
            <b>{v.remaining}</b> requirement{v.remaining === 1 ? " is" : "s are"} still open, and at{" "}
            <b>{v.per_week_3w.toFixed(1)} a week</b> {v.remaining === 1 ? "it lands" : "they land"} around{" "}
            <b>{fmtDate(c.projected_done_at)}</b>.{" "}
          </>
        ) : null}
        {scope || date
          ? "Nothing new goes into the plan until you choose, and the work already under way carries on either way."
          : "You left scope and dates to your CEO, so it will cut something or move the date itself and tell you what it did."}
      </p>
    </Verdict>
  );
}

/** Where the outcomes stand, against the promise.

    One axis — started, today, the platform's projection, the promised date —
    and under it the outcomes by stage, one dot each. Outcomes, never tasks or
    runs: a project that closed forty tasks and made nothing true has not moved
    on this strip, which is the point of it. The projection is the server's own
    number with its basis on hover; with no commitment there is no axis at all
    rather than an invented one. */
function PathStrip({ ctx, verdicts }: { ctx: ProjectCtx; verdicts: Set<string> }) {
  const { dash, plan, projectId, hypotheses } = ctx;
  const base = `/projects/${projectId}`;
  if (dash.mode === "explore") {
    const bets = hypotheses.filter((h) => h.status !== "killed");
    if (!bets.length) return null;
    return (
      <section className="card ov-path">
        <div className="card-h"><h3>Where it stands</h3><span className="help">{bets.length} bet{bets.length === 1 ? "" : "s"}</span>
          <span className="grow" /><Link className="card-link" to={`${base}/details/tasks`}>Bets <Icon name="arrow" size={13} /></Link></div>
        <ul className="ov-bets">
          {bets.map((h) => (
            <li key={h.id}>
              <i className={`sg-node md ${h.status === "expired" ? "you" : "work"}`} />
              <span className="grow">{clip(h.statement, 140)}</span>
              <span className="help">{h.status === "expired" ? "past its kill date — yours to extend" : `kill date ${fmtDate(h.kill_date)}`}</span>
            </li>
          ))}
        </ul>
      </section>
    );
  }
  const scopeYours = chairmanDecides(dash, "plan_authority");
  const reqs = (plan?.requirements ?? []).filter((r) => r.status !== "cut");
  if (!reqs.length) return null;
  const nodes = reqs.flatMap((r) => {
    const status = r.status === "proposed" && !scopeYours ? "pending" : r.status;
    const n = requirementNode({ key: r.key, status, name: requirementName(r), stage: r.stage }, verdicts.has(r.key));
    return n ? [n] : [];
  });
  const stages = [...new Set(nodes.map((n) => n.stage ?? 1))].sort((a, b) => a - b);
  const stageWord = (ns: PathNode[]) => ns.every((n) => n.state === "verified") ? ["settled", "good"]
    : ns.some((n) => n.state === "you") ? ["waits on you", "you"]
      : ns.some((n) => n.state === "work" || n.state === "verified") ? ["in work", ""] : ["not started", "muted"];
  const verified = nodes.filter((n) => n.state === "verified").length;

  const c = plan?.commitment;
  const promised = c?.deliver_by ? new Date(c.deliver_by).getTime() : null;
  const projected = c?.projected_done_at && c.status !== "met" ? new Date(c.projected_done_at).getTime() : null;
  const start = new Date(dash.created_at).getTime();
  const now = Date.now();
  const end = Math.max(promised ?? 0, projected ?? 0, now) + 86_400_000;
  const at = (t: number) => `${Math.max(0, Math.min(100, ((t - start) / (end - start)) * 100))}%`;
  const late = projected !== null && promised !== null && projected > promised;

  return (
    <section className="card ov-path">
      <div className="card-h">
        <h3>Where it stands</h3>
        <span className="help">{verified} of {nodes.length} outcome{nodes.length === 1 ? "" : "s"} verified</span>
        <span className="grow" />
        <span className="ov-legend">
          {(["verified", "work", "you", "todo"] as const).map((k) => (
            <span key={k}><i className={`sg-node ${k}`} /> {NODE_WORD[k]}</span>))}
        </span>
        <Link className="card-link" to={`${base}/details/roadmap`}>Roadmap <Icon name="arrow" size={13} /></Link>
      </div>
      {promised !== null && (
        <div className="ov-axis" role="img" aria-label={`Started ${fmtDate(dash.created_at)}; promised ${fmtDate(c!.deliver_by)}${projected ? `; projected ${fmtDate(c!.projected_done_at!)}` : ""}.`}>
          <div className="ov-axis-bar"><i style={{ width: at(Math.min(now, end)) }} /></div>
          <span className="ov-mark now" style={{ left: at(now) }}><b>today</b></span>
          {projected !== null && (
            <span className={`ov-mark proj${late ? " late" : ""}`} style={{ left: at(projected) }}
              title={c?.projection_basis ?? undefined}><b>projected {fmtDate(c!.projected_done_at!)}</b></span>
          )}
          <span className="ov-mark promise" style={{ left: at(promised) }}>
            <b>{c!.status === "proposed" ? "proposed" : c!.status === "met" ? "kept" : "promised"} {fmtDate(c!.deliver_by)}</b>
          </span>
          <span className="ov-axis-start">started {fmtDate(dash.created_at)}</span>
        </div>
      )}
      <div className="ov-stages">
        {stages.map((st) => {
          const ns = nodes.filter((n) => (n.stage ?? 1) === st);
          const [word, tone] = stageWord(ns);
          return (
            <div className="ov-stage" key={st}>
              <h4><span>Stage {st}</span><span className={`ov-st-${tone}`}>{word}</span></h4>
              {ns.map((n) => (
                <div className="ov-node" key={n.key} title={NODE_WORD[n.state]}>
                  <i className={`sg-node md ${n.state}`} />
                  <span className="mono help">{n.key}</span>
                  <span className="ov-node-t">{n.title}</span>
                </div>
              ))}
            </div>
          );
        })}
      </div>
    </section>
  );
}

/** What changed since the reader last looked at this project — the same
    composer as the morning letter, cut to the lines worth a glance. */
function SinceYouLooked({ projectId }: { projectId: string }) {
  const { data } = usePoll(() => get<ProjectBrief>(`/observe/projects/${projectId}/briefing`), 60000, [projectId]);
  const lines = (data?.lines ?? []).filter((l) => l.group === "attention" || l.group === "moved").slice(0, 4);
  return (
    <section className="card ov-card">
      <div className="card-h">
        <h3>Since you looked</h3>
        {data && <span className="help">{fmtSince(data.since)}</span>}
        <span className="grow" />
        <Link className="card-link" to={`/projects/${projectId}/details/activity`}>Activity <Icon name="arrow" size={13} /></Link>
      </div>
      {!data ? <p className="help">Loading…</p> : data.quiet || !lines.length ? (
        <p className="help">Nothing changed that needs a second look.</p>
      ) : (
        <ul className="ov-since">
          {lines.map((l, i) => (
            <li key={`${l.kind}-${i}`} className={`tone-${l.tone}`}><i />{l.text}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Money, drawn once: spent against the plan and the wall, and what the pace
    says about the month. */
function MoneyCard({ ctx }: { ctx: ProjectCtx }) {
  const { dash, projectId } = ctx;
  const { unit, spent, cap, wall, warnRatio } = budgetOf(dash);
  const pace = paceDate(spent, cap);
  const said = wall > 0 && spent >= wall ? "Paused at the hard stop."
    : spent > cap ? `Over its plan. The CEO owes you an account; nothing pauses until ${fmtUnit(wall, unit)}.`
      : pace ? `On pace to reach its plan ${fmtWhenShort(pace.toISOString())}.` : "On pace to stay inside its plan this month.";
  return (
    <section className="card ov-card">
      <div className="card-h">
        <h3>Money · this month</h3>
        <span className="grow" />
        <Link className="card-link" to={`/projects/${projectId}/details/spend`}>Spend <Icon name="arrow" size={13} /></Link>
      </div>
      <div className="ov-money"><b>{fmtUnit(spent, unit)}</b> <span className="help">of {fmtUnit(cap, unit)} plan</span></div>
      <MoneyMeter spent={spent} cap={cap} wall={wall} unit={unit} warnRatio={warnRatio} />
      <p className="help ov-money-s">{said}{spent < cap * warnRatio ? ` Warns you at ${fmtUnit(cap * warnRatio, unit)}.` : ""}</p>
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
        <Link className="card-link" to={`/projects/${projectId}/results`}>All results <Icon name="arrow" size={13} /></Link>
      </div>
      {data.results.length ? (
        <div className="ov-results">
          {data.results.slice(0, 3).map((r) => (
            <Link className="ov-result" key={r.key} to={`/projects/${projectId}/results`}>
              <span className="ov-result-h"><Icon name="file" size={15} /><b>{r.name}</b></span>
              <span className="help">{r.made_by ? `${r.made_by.name} · ` : ""}{fmtDate(r.published_at)}</span>
              <Seal level={sealOf(r.checked)} />
            </Link>
          ))}
        </div>
      ) : (
        <p className="help">
          {met} outcome{met === 1 ? "" : "s"} verified, no file published. The checks ran on the project's workspace —{" "}
          <Link to={`/projects/${projectId}/details/roadmap`}>see what was checked</Link>.
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
        <Icon name="check" size={15} />
        <span><b>Nothing needs you here.</b></span>
        <span className="grow" />
        <Link className="card-link" to={`${inbox}&tab=ack`}>Notes and updates <Icon name="arrow" size={13} /></Link>
      </div>
    );
  }
  return (
    <section className="ov-decisions" aria-label="Needs you">
      <div className="ov-dec-h">
        <h2>Needs you <span className="count warn">{decisions.length}</span></h2>
        <span className="help">soonest consequence first</span>
        <span className="grow" />
        <Link className="card-link" to={inbox}>
          {decisions.length > SHOWN_HERE ? `All ${decisions.length} in the Inbox` : "Inbox"} <Icon name="arrow" size={13} />
        </Link>
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
function MoreMenu({ ctx, onPause, onStop, unpaid }: {
  ctx: ProjectCtx; onPause: () => void; onStop: () => void; unpaid: boolean;
}) {
  const { dash, projectId } = ctx;
  if (dash.can_steer === false) return null;
  const base = `/projects/${projectId}`;
  return (
    <details className="ov-more">
      <summary aria-label="More actions"><Icon name="more" size={16} /></summary>
      <div className="ov-more-menu" role="menu">
        {dash.status === "running" && <button role="menuitem" onClick={onPause}><Icon name="pause" size={14} /> Pause the team…</button>}
        <Link role="menuitem" to={`${base}/settings`}><Icon name="gear" size={14} /> Settings</Link>
        {dash.status !== "stopped" && <button role="menuitem" className="danger" onClick={onStop}><Icon name="stop" size={14} /> Stop for good…</button>}
      </div>
    </details>
  );
}

/** The CEO's last word, and anything the platform made it bring you — the
    news, beside the work, one step from answering it. */
function CeoCard({ ctx, news }: { ctx: ProjectCtx; news: ReactNode[] }) {
  const { projectId } = ctx;
  const navigate = useNavigate();
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
  // under a "From <CEO>" heading it read as the CEO saying it. And not an open
  // question: that is a decision card at the top of this page already.
  const asking = new Set(ctx.asks.filter((a) => a.status === "open").map((a) => a.asked_in_message_id));
  const last = [...(tail ?? [])].reverse().find((m) => !!m.from_agent && !asking.has(m.id));
  const ceo = firstName(ctx.ceoName);
  return (
    <section className="card ov-card">
      <div className="card-h">
        <h3>From {ceo}</h3>
        {last && <span className="help">{fmtWhen(last.at)}</span>}
        <span className="grow" />
        <Link className="card-link" to={`/projects/${projectId}/chat`}>Chat <Icon name="arrow" size={13} /></Link>
      </div>
      {last ? (
        <div className="ceo-last">
          <Avatar name={ctx.ceoName} team="leadership" />
          <div className="ceo-body"><Markdown>{clipMarkdown(last.body, 420)}</Markdown></div>
        </div>
      ) : (tail ?? []).some((m) => !!m.from_agent) ? (
        // Everything it said lately is a question already drawn above, as a
        // decision; "hasn't said anything" would be false under that card.
        <p className="help">{ceo}'s latest is the question above, under Needs you.</p>
      ) : (
        <p className="help">
          {ceo} hasn't said anything yet. Open with the goal, the constraints, and how you want to work.
        </p>
      )}
      {news.length > 0 && <div className="ov-news">{news}</div>}
      <div className="quick">
        <button className="qchip" onClick={() => navigate(`/projects/${projectId}/chat`)}>
          <Icon name="reply" size={13} /> Reply…
        </button>
        <button className="qchip" onClick={() => navigate(`/projects/${projectId}/chat`, { state: { draft: "What changed since we last spoke, and what is next?" } })}>
          What changed?
        </button>
        <button className="qchip" onClick={() => navigate(`/projects/${projectId}/chat`, { state: { draft: "What is the biggest risk to the date right now?" } })}>
          Biggest risk?
        </button>
      </div>
    </section>
  );
}

function validZone(zone: string): boolean {
  try { new Intl.DateTimeFormat(undefined, { timeZone: zone }); return true; } catch { return false; }
}

/** The calendar, as a list: what happens next, coloured by who resolves it.
    The full strip — rhythms, every wake, the notes that explain its blanks —
    is one press away rather than half a screen of the first page. */
function ComingUp({ projectId }: { projectId: string }) {
  const [full, setFull] = useState(false);
  const { data } = usePoll(() => get<AgendaView>(`/observe/projects/${projectId}/agenda?days=14`), 60000);
  const now = Date.now();
  const events = (data?.events ?? []).filter((e) => new Date(e.at).getTime() >= now - 3_600_000).slice(0, 5);
  // Said in the zone named beside the heading, not the browser's: the two
  // disagreed by five hours on the same page as the health line.
  const zone = data?.timezone && validZone(data.timezone) ? data.timezone : undefined;
  return (
    <section className="card ov-card">
      <div className="card-h">
        <h3>Next</h3>
        {data && <span className="help">{data.timezone}</span>}
        <span className="grow" />
        <button className="card-link" onClick={() => setFull(!full)}>{full ? "Hide the calendar" : "Full calendar"}</button>
      </div>
      {full ? <Agenda projectId={projectId} /> : events.length ? (
        <ul className="agenda-list">
          {events.map((e, i) => (
            <li key={`${e.at}-${i}`} className={`ag-${e.tone}`}>
              <span className="ag-when">
                <b>{new Date(e.at).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", timeZone: zone })}</b>
                <span>{new Date(e.at).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", timeZone: zone })}</span>
              </span>
              <i className="ag-dot" />
              <span className="ag-what">{e.label}{e.detail && <span className="help"> — {clip(e.detail, 90)}</span>}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="help">{data ? (data.notes[0] ?? "Nothing is scheduled in the next fourteen days.") : "Loading…"}</p>
      )}
    </section>
  );
}

/** Setup & health. A healthy project gets one quiet affirmative line — silence
    is ambiguous with a broken health check, and the ambiguity costs more than
    the pixels. Failing rows keep their plain-language fixes.

    Two kinds of row, said in the header badge's own words: a problem is
    something broken, a suggestion is advice. A suggestion the reader has read
    can be put away, and then it stops lighting the badge — most of what kept
    the badge lit on every project was advice that is true forever and was
    read on the first day. Put-away rows stay on the card, dimmed, with a way
    back; a problem has no such button. `id` is the badge's target. */
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
        <b>Setup is healthy.</b>
        <span className="help">
          {putAway.length
            ? `${putAway.length} suggestion${putAway.length === 1 ? "" : "s"} put away.`
            : `All ${data.checks.length} checks pass.`}
        </span>
        <span className="grow" />
        <button className="small" onClick={() => setShow(true)}>Details</button>
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
      {c.status === "fail" ? "problem" : c.status === "warn" ? (c.acked_at ? "put away" : "suggestion") : "ok"}
    </span>
  );
  return (
    <section className="card ov-card" id="setup-health" style={{ marginTop: "var(--s4)" }}>
      <div className="card-h">
        <h3>Setup &amp; health</h3>
        <span className="grow" />
        {!open.length && <button className="small" onClick={() => setShow(false)}>Hide</button>}
      </div>
      {open.length > 0 && (
        <p className="help" style={{ margin: "0 0 8px" }}>
          A <b>problem</b> means something is broken: fix it. A <b>suggestion</b> is a choice
          for you: act on it, or put it away once you have decided and it stops counting.
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
            }}>adopt the latest role version</button>
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
              {check.acked_at ? "Show again" : "Got it"}
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
  settings: ["/settings", "fix in Settings"],
  money: ["/settings?s=money", "fix in Settings → Money"],
  data_sources: ["/settings?s=sources", "open Data sources"],
  mail: ["/settings?s=mail", "open Settings → Email"],
  plan: ["/details/roadmap", "open the Roadmap"],
  boardroom: ["/chat", "open the chat"],
  team: ["/team", "see the team"],
  // A price is the install's, not the project's, so it is set where prices
  // live — an app-wide page, not one under the project.
  prices: ["/settings?section=prices", "open Model prices", true],
  // So is a lineup: a model the key stopped listing is moved where the
  // install's models are chosen.
  models: ["/settings?section=models", "open Settings → Models", true],
  providers: ["/settings?section=providers", "open Settings → Providers", true],
};

/** The failover check says "settings" like every other project setting, but
    its control is on one tab of them; landing on the Brief tab instead is a
    link that stops one click short. */
const whereOf = (check: { key: string; where?: string | null }) =>
  check.key === "fallback" ? "money" : check.where ?? "";

/** Where each problem verdict is fixed: page under the project, and the link. */
const FIX_AT: Record<string, [string, string]> = {
  blocked: ["/settings?s=money", "Add a key"],
  failing: ["/details/activity/runs", "See the runs"],
  late: ["/details/roadmap", "Cut scope or move the date"],
  at_risk: ["/details/roadmap", "See the plan"],
  stalled: ["/team", "See who is stuck"],
  capped: [`/settings?s=rules&t=${encodeURIComponent("Pace & brakes")}`, "Raise runs per day"],
  spinning: ["/details/activity/runs", "See what the runs did"],
};

export default function Overview({
  ctx, onPause, onStop, setupBadge, goalLead,
}: {
  ctx: ProjectCtx;
  onPause: () => void;
  onStop: () => void;
  setupBadge: ReactNode;
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
  const auto = autonomyWord(dash);
  const base = `/projects/${projectId}`;
  const unpaid = !dash.billing_secret_id && dash.integrations?.runtime !== "mock";

  const rawFlags = flagsView?.flags ?? [];

  // ── your CEO is telling you ──
  //
  // A raised flag is the CEO bringing you something with a proposal. It does
  // not block, it does not expire, and it proceeds on that proposal whether or
  // not you answer — so it reads beside the CEO's last word, never under the
  // heading that says a decision is waiting on you.
  const news: React.ReactNode[] = [];
  for (const { lead: f, also } of oneCardPerCause(
    rawFlags.filter((f) => f.status === "raised" && !f.acknowledged_at))) {
    const told = flagTold(f);
    news.push(
      <div className="flag" key={f.id}>
        <div className="flag-t"><Icon name="flag" size={14} /> <b>{told.headline}</b></div>
        <dl className="flag-parts">
          {told.parts.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
        </dl>
        {also.length > 0 && (
          <div className="help">
            One cause behind {also.length + 1} alerts — your answer here answers all of them.
          </div>
        )}
        <div className="flag-a">
          <FlagAnswer
            primary={false}
            onSubmit={async (note) => {
              await post(`/admin/flags/${f.id}/ack`, { note });
              reloadFlags();
            }}
          />
          <Link to={`${base}/chat`}>talk it through with {ceo} →</Link>
        </div>
      </div>,
    );
  }

  // ── watching ──
  // Clocks the CEO resolves: shown so nothing happens silently, never counted
  // as needing you. The project-wide ones come from the same rows the Inbox
  // shows under Watching; the two below exist only here.
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
        <span className="badge serious">not yet raised</span>
        <div className="grow">
          <b>{flagPlain(f.kind).title}</b>{" "}
          <span className="help">
            — the platform opened this{f.opened_at ? ` ${fmtWhenShort(f.opened_at)}` : ""}; {ceo} is
            required to bring it to you with a proposal. In its own words: {clip(f.summary)}
          </span>
        </div>
        <Link to={`${base}/chat`}>ask about it →</Link>
      </div>,
    );
  }
  // Acknowledged, and still true: your word ended the conversation, not the
  // condition, so it waits here under your note until the arithmetic turns.
  for (const { lead: f } of oneCardPerCause(
    rawFlags.filter((f) => f.status === "raised" && f.acknowledged_at))) {
    watch.push(
      <div className="wrow" key={f.id}>
        <span className="badge">noted</span>
        <div className="grow">
          <b>{flagTold(f).headline}</b>{" "}
          <span className="help">
            — still true. You noted it{f.acknowledged_at ? ` ${fmtWhenShort(f.acknowledged_at)}` : ""}
            {f.acknowledgement ? `: “${clip(f.acknowledgement, 100)}”` : ""}; it clears when the numbers do.
          </span>
        </div>
        <Link to={`${base}/chat`}>take it up →</Link>
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
        <span className={`badge ${full ? "serious" : "warn"}`}>memory {m.live}/{m.cap}</span>
        <div className="grow">
          {m.name} {full ? "is full" : "is nearly full"} —{" "}
          {full ? "new entries there are being refused, and only " : "at the cap, new entries get refused until "}
          {m.scope === "project" ? "the CEO" : (m.curator ?? "a lead nobody has named")} can fold entries to make room.
        </div>
        <Link to={`${base}/details/activity/memory`}>see what it keeps →</Link>
      </div>,
    );
  }

  const verdicts = new Set((deskProject?.awaiting_you.verdicts ?? []).map((v) => v.key));
  const setupLeft = ctx.setup?.open ? ctx.setup.steps.filter((st) => st.state === "todo").length : 0;
  const quietWait = dash.status === "running" && !dash.activity.brief.banner && dash.activity.brief.clears_at
    ? dash.activity.brief : null;

  return (
    <>
      <header className="ov-head">
        <div className="ov-title-row">
          <div className="grow">
            <h1 className="ov-title">
              {dash.name} {verdictBadge(deskProject ?? { status: dash.status, unpaid },
                deskProject ? needsYouCount(deskProject) : 0)}
              {dash.drought_active && (
                <Badge tone="serious">
                  <Term means="Nothing outside this project has verified any of its claims for longer than your rules allow. Work carries on; new internal-only work is refused until proof lands.">
                    no outside proof lately
                  </Term>
                </Badge>
              )}
              {setupBadge}
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
                Resumes {fmtSoon(quietWait.clears_at!)} — {quietWait.headline}
              </p>
            )}
          </div>
          <div className="ov-acts">
            {dash.can_steer !== false && dash.status !== "running" && dash.status !== "stopped" && (
              unpaid ? (
                <Link to={`${base}/settings?s=money`} className="btnlink primary"
                  title="Agents cannot run without a billing credential">
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
                  <Icon name="play" size={15} />Start
                </button>
              )
            )}
            <Link to={`${base}/chat`} className={`btnlink${dash.status === "running" ? " primary" : ""}`}>
              <Icon name="message" size={15} />Message {ceo}</Link>
            <MoreMenu ctx={ctx} onPause={onPause} onStop={onStop} unpaid={unpaid} />
            {startError && <div className="error ov-start-error" role="alert">{startError}</div>}
          </div>
        </div>
        <div className="eyebrow ov-meta">
          <span className="chip">{dash.mode === "deliver" ? "Deliver" : dash.mode === "operate" ? "Operate" : "Explore"}</span>
          {ctx.plan?.commitment?.deliver_by && <span>{ctx.plan.commitment.status === "proposed" ? "Proposed" : "Promised"} {fmtDate(ctx.plan.commitment.deliver_by)}</span>}
          {dash.mode === "operate" && dash.cycle && (
            cycleOverdue(dash.cycle)
              ? <span style={{ color: "var(--serious)" }}>Cycle {dash.cycle.ordinal} · overdue to close since {fmtDate(dash.cycle.rolls_at)}</span>
              : <span>Cycle {dash.cycle.ordinal} · {dash.cycle.due_at ? "due" : "closes"} {fmtRoll(dash.cycle.due_at ?? dash.cycle.rolls_at)}</span>
          )}
          {!ctx.plan?.commitment?.deliver_by && dash.deadline && <span>Window closes {fmtDate(dash.deadline)}</span>}
          <span className="term-plain"><Term means={auto.means}>{auto.word}</Term></span>
          {dash.my_role && <span>You are {dash.my_role === "chairman" ? "the chairman" : dash.my_role === "co_chair" ? "a co-chair" : "an observer"}</span>}
          {setupLeft > 0 && !unpaid && !dash.mail_start_refusal && (
            <button className="linkish ov-setup-chip" onClick={() => setShowSetup(!showSetup)} aria-expanded={showSetup}>
              Setup · {setupLeft} to go
            </button>
          )}
          <button className="linkish" onClick={() => setBrief(!brief)} aria-expanded={brief}>
            {brief ? "Hide the brief" : "Read the brief"}
          </button>
        </div>
        <p className="ov-goal">{goalLead}</p>
        {brief && (
          <div className="briefbox">
            <strong style={{ color: "var(--ink)" }}>The brief every one of them works from</strong>
            <p style={{ whiteSpace: "pre-wrap", margin: "6px 0 0" }}>{dash.goal}</p>
          </div>
        )}
      </header>

      <ModeStrip dash={dash} reload={ctx.reload} />
      <LateBanner ctx={ctx} />
      <FailoverBanner dash={dash} settingsLink={`${base}/settings?s=money`} />

      {/* Setup leads only when it is the reason nothing can run. Otherwise it
          is a chip in the line above: on a running — or finished — project, a
          six-step checklist above the decisions was configuration outranking
          state. */}
      {(unpaid || !!dash.mail_start_refusal && dash.status !== "running" || showSetup)
        && <ProjectSetup ctx={ctx} />}

      {/* The project's home is what it needs from you first: this project's
          decisions, whole, from the one list the Inbox decides from. */}
      <ProjectDecisions projectId={projectId} desk={desk} />

      <PathStrip ctx={ctx} verdicts={verdicts} />

      <div className="ov-three">
        <SinceYouLooked projectId={projectId} />
        <ComingUp projectId={projectId} />
        <MoneyCard ctx={ctx} />
      </div>

      <LatestResults ctx={ctx} />

      <div className="ov-grid">
        <div className="ov-col">
          <CeoCard ctx={ctx} news={news} />
        </div>
        <aside className="ov-col">
          {watch.length > 0 && (
            <section className="card ov-card">
              <div className="card-h">
                <h3>Watching</h3>
                <span className="count">{watch.length}</span>
                <span className="grow" />
                <span className="help">{ceo} is handling these</span>
              </div>
              <div className="wbox">{watch}</div>
            </section>
          )}
        </aside>
      </div>

      {health.early.length > 0 && (
        <div className={`strip${health.ratio >= 0.2 ? " serious" : ""}`} style={{ marginTop: "var(--s4)" }}>
          <div className="grow">
            <b>{earlyRunSentence(health)}</b>
            <div className="sb">
              {health.faults.length === 0
                ? "Every one of them was stopped on purpose, so nothing points at a fault — though their tokens were still spent."
                : health.ratio >= 0.2
                  ? "Under one in five is normal wear. This much means something is in the way — a dead tool mount, a wrong credential, or work an agent cannot do."
                  : "That is within normal wear for unattended work."}
            </div>
          </div>
          <Link to={`${base}/details/activity/runs`}>see what happened →</Link>
        </div>
      )}

      <HealthLine ctx={ctx} />
      <AsOf at={ctx.asOf} error={ctx.error} />
    </>
  );
}
