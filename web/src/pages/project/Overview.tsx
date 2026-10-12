import { ReactNode, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  AgendaView,
  Dashboard,
  get,
  HealthCheck,
  post,
  RunRow,
  ShelfView,
} from "../../api";
import Agenda from "../../agenda";
import { ItemActions, itemsFor, needsYou, soonestClock, useDesk } from "../../desk";
import { fmtWhen, usePoll } from "../../hooks";
import Markdown from "../../Markdown";
import { firstName, ProjectCtx } from "../../projectCtx";
import {
  AsOf,
  budgetOf,
  clip,
  earlyRunSentence,
  fmtLeft,
  fmtSoon,
  fmtUnit,
  fmtWhenShort,
  NoteAction,
  paceDate,
  runHealth,
} from "../../ui";
import { Avatar, Badge, Btn, Term, Verdict } from "../../ui/kit";
import { Icon } from "../../ui/Icon";
import { chairmanDecides, deliveryVerdict, flagPlain, fmtDate, fullyAutonomous } from "../../ui/project";
import { heldFor, SeatState } from "../../ui/team";
import { statusBadge } from "../Projects";
import { SpendByTeam } from "./Spend";
import { SeatPill } from "./Team";

/** The first page of a project answers three questions, in the order a
    chairman asks them, in about five seconds:

      is it on track?            four tiles — delivery, progress, money, people
      does anything need me?     the decisions only you can make, with their clocks
      what is the team doing?    every person, and the one thing each is on

    It used to be a twelve-section scroll fed by sixteen pollers, each section
    with its own visual grammar, and the reader assembled the answer. The
    sections that were really other pages went to them — the roster to Team,
    the money to Spend, the records to Activity — and what stayed is what you
    open the project to find out. Everything here links to where its detail
    lives. */

const SEAT_ORDER: SeatState[] = ["working", "waiting", "blocked", "next", "idle", "paused"];

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

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

/** Runs started per hour today, for the sparkline under "Working now". */
function runsByHour(runs: RunRow[]): number[] {
  const now = new Date();
  const hours = new Array(now.getHours() + 1).fill(0);
  for (const r of runs) {
    if (!r.started_at) continue;
    const at = new Date(r.started_at);
    if (at.toDateString() === now.toDateString()) hours[at.getHours()] += 1;
  }
  return hours.length > 1 ? hours : [0, ...hours];
}

function Sparkline({ values }: { values: number[] }) {
  const w = 200;
  const h = 28;
  const max = Math.max(...values, 1);
  const step = w / Math.max(values.length - 1, 1);
  const pts = values.map((v, i) => [i * step, h - 3 - (v / max) * (h - 6)] as const);
  const d = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" ");
  const last = pts[pts.length - 1];
  return (
    <svg className="spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden="true">
      <path d={`${d} L ${w} ${h} L 0 ${h} Z`} fill="var(--accent-fill)" stroke="none" />
      <path d={d} fill="none" stroke="var(--accent)" strokeWidth="1.6" vectorEffect="non-scaling-stroke" />
      <circle cx={last[0]} cy={last[1]} r="2.4" fill="var(--accent)" />
    </svg>
  );
}

/** Today, the promise, and the projection on one line — so "at risk" is a
    picture of a gap and not only a word. */
function Timeline({ start, promised, projected }: { start: string; promised: string; projected: string | null }) {
  const t0 = new Date(start).getTime();
  const tp = new Date(promised).getTime();
  const tj = projected ? new Date(projected).getTime() : tp;
  const end = Math.max(tp, tj) + (Math.max(tp, tj) - t0) * 0.06;
  const pos = (t: number) => `${Math.min(100, Math.max(0, ((t - t0) / (end - t0)) * 100))}%`;
  const late = tj > tp;
  return (
    <div className="tl" aria-hidden="true">
      <i className="tl-done" style={{ width: pos(Date.now()) }} />
      {late && <i className="tl-slip" style={{ left: pos(tp), width: `calc(${pos(tj)} - ${pos(tp)})` }} />}
      <i className="tl-mark today" style={{ left: pos(Date.now()) }} />
      <i className="tl-mark promised" style={{ left: pos(tp) }} />
      {projected && <i className="tl-mark projected" style={{ left: pos(tj) }} />}
      <span className="tl-lab" style={{ left: pos(Date.now()) }}>Today</span>
      <span className={`tl-lab${late ? " end" : ""}`} style={{ left: pos(tp) }}>{fmtDate(promised)}</span>
    </div>
  );
}

function Kpis({ ctx }: { ctx: ProjectCtx }) {
  const { dash, plan, seats, projectId, hypotheses } = ctx;
  const verdict = deliveryVerdict(dash, plan);
  const c = plan?.commitment;
  const { unit, spent, cap, warnRatio, frac } = budgetOf(dash);
  const pace = paceDate(spent, cap);
  const health = runHealth(ctx.runs ?? []);
  const people = seats.filter((s) => s.state !== "stood_down");
  const busy = people.filter((s) => s.state === "working").length;
  const reqs = (plan?.requirements ?? []).filter((r) => r.status !== "cut");
  const explore = dash.mode === "explore";
  const bets = hypotheses.filter((h) => h.status === "active");
  const base = `/projects/${projectId}`;
  const tone = verdict.tone === "good" ? "good" : verdict.tone;

  return (
    <section className="kpis" aria-label="Is it on track?">
      <Link className="kpi" to={explore ? `${base}/tasks` : `${base}/roadmap`}>
        <span className="kpi-l"><Icon name="calendar" size={14} />Delivery</span>
        <span className={`kpi-v ${tone}`}>{verdict.label}</span>
        <span className="kpi-s">{verdict.sub}</span>
        {c?.deliver_by && (
          <Timeline start={c.accepted_at ?? dash.created_at} promised={c.deliver_by} projected={c.projected_done_at} />
        )}
      </Link>
      {explore ? (
        <Link className="kpi" to={`${base}/tasks`}>
          <span className="kpi-l"><Icon name="target" size={14} />Bets in play</span>
          <span className="kpi-v">{bets.length}</span>
          <span className="kpi-s">
            {dash.ceo_judgment.tasks_with_verified_external} of {dash.ceo_judgment.tasks_created} bets
            ever proven outside the project
          </span>
        </Link>
      ) : (
        <Link className="kpi" to={`${base}/roadmap`}>
          <span className="kpi-l"><Icon name="flag" size={14} />Progress</span>
          <span className="kpi-v">
            {plan?.burnup?.settled ?? reqs.filter((r) => r.status === "met").length}
            <span className="kpi-of"> of {plan?.burnup?.scope_total ?? reqs.length}</span>
          </span>
          <span className="kpi-s">
            <Term means="A requirement is a testable outcome; together they define done. Only the platform can mark one met.">
              requirements
            </Term>{" "}met
            {reqs.some((r) => r.status === "in_progress") && ` · ${reqs.filter((r) => r.status === "in_progress").length} in progress`}
            {dash.requirements_proposed > 0 && ` · ${dash.requirements_proposed} proposed`}
          </span>
          {reqs.length > 0 && (
            <span className="segbar">
              {reqs.map((r) => (
                <i key={r.id} className={r.status === "met" ? "met" : r.status === "in_progress" ? "prog" : r.status === "proposed" ? "prop" : ""}
                  title={`${r.key} — ${r.status.replaceAll("_", " ")}`} />
              ))}
            </span>
          )}
        </Link>
      )}
      <Link className="kpi" to={`${base}/spend`}>
        <span className="kpi-l"><Icon name="wallet" size={14} />Spent this month</span>
        <span className="kpi-v">{fmtUnit(spent, unit)}<span className="kpi-of"> of {fmtUnit(cap, unit)}</span></span>
        <span className="kpi-s">
          {frac >= 1 ? "the cap is reached — the project is paused"
            : pace ? `on pace to reach the cap ${fmtWhenShort(pace.toISOString())}`
              : "on pace to finish the month under the cap"}
        </span>
        <span className="meterline">
          <i className={frac >= 1 ? "over" : frac >= warnRatio ? "warn" : ""} style={{ width: `${Math.min(frac, 1) * 100}%` }} />
          <b style={{ left: `${warnRatio * 100}%` }} title={`Warns at ${Math.round(warnRatio * 100)}%`} />
        </span>
      </Link>
      <Link className="kpi" to={`${base}/team`}>
        <span className="kpi-l"><Icon name="users" size={14} />Working now</span>
        <span className="kpi-v">{busy}<span className="kpi-of"> of {people.length}</span></span>
        <span className="kpi-s">
          {dash.activity.daily_runs.used} of {dash.activity.daily_runs.cap} runs today
          {health.early.length > 0 && ` · ${health.early.length} ended early`}
        </span>
        <Sparkline values={runsByHour(ctx.runs ?? [])} />
      </Link>
    </section>
  );
}

/** The mode-shaped fact, one per mode: Explore counts its window down,
    Deliver offers the completion gate the moment everything is true, Operate
    names the turn of the rhythm it is on. */
function ModeStrip({ dash, reload }: { dash: Dashboard; reload: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (dash.mode === "deliver" && dash.delivery_outstanding !== null
      && dash.delivery_outstanding.length === 0) {
    return (
      <div className="strip good">
        <div className="grow">
          <b>Everything is true. This delivery is ready for your confirmation.</b>
          <div className="sb">
            Completing writes the receipt — including which requirements failed before they
            passed — and closes the project down cleanly. Until you do, it keeps running and
            spending.
          </div>
          {error && <div className="error">{error}</div>}
        </div>
        {confirming ? (
          <>
            <button className="small primary" onClick={async () => {
              try {
                await post(`/admin/projects/${dash.id}/complete`);
                reload();
              } catch (e) { setError((e as Error).message); }
            }}>Yes, complete it</button>
            <button className="small" onClick={() => setConfirming(false)}>Not yet</button>
          </>
        ) : (
          <button className="small primary" onClick={() => setConfirming(true)}>Complete the project…</button>
        )}
      </div>
    );
  }
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
          {scope && <Link to={`/projects/${projectId}/roadmap`}><button className="primary">Drop something from the plan</button></Link>}
          {date && <Link to={`/projects/${projectId}/roadmap`}><button>Move the delivery date</button></Link>}
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

/** The CEO's last word, and anything the platform made it bring you — the
    news, beside the work, one step from answering it. */
function CeoCard({ ctx, news }: { ctx: ProjectCtx; news: ReactNode[] }) {
  const { projectId } = ctx;
  const navigate = useNavigate();
  const last = [...ctx.boardroom].reverse().find((m) => !m.from_user);
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
          <div className="ceo-body"><Markdown>{clip(last.body, 420)}</Markdown></div>
        </div>
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

/** The calendar, as a list: what happens next, coloured by who resolves it.
    The full strip — rhythms, every wake, the notes that explain its blanks —
    is one press away rather than half a screen of the first page. */
function ComingUp({ projectId, data }: { projectId: string; data: AgendaView | null }) {
  const [full, setFull] = useState(false);
  const now = Date.now();
  const events = (data?.events ?? []).filter((e) => new Date(e.at).getTime() >= now - 3_600_000).slice(0, 7);
  return (
    <section className="card ov-card">
      <div className="card-h">
        <h3>Coming up</h3>
        {data && <span className="help">{data.timezone}</span>}
        <span className="grow" />
        <button className="card-link" onClick={() => setFull(!full)}>{full ? "Hide the calendar" : "Full calendar"}</button>
      </div>
      {full ? <Agenda projectId={projectId} /> : events.length ? (
        <ul className="agenda-list">
          {events.map((e, i) => (
            <li key={`${e.at}-${i}`} className={`ag-${e.tone}`}>
              <span className="ag-when">
                <b>{new Date(e.at).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })}</b>
                <span>{new Date(e.at).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}</span>
              </span>
              <i className="ag-dot" />
              <span className="ag-what">{e.label}{e.detail && <span className="help"> — {clip(e.detail, 90)}</span>}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="help">{data ? (data.notes[0] ?? "Nothing is scheduled in the next fourteen days.") : "Loading…"}</p>
      )}
      {!full && (
        <div className="ag-key">
          <span className="k-consequence">Decides itself</span>
          <span className="k-deadline">Deadline</span>
          <span className="k-meeting">Meeting</span>
          <span className="k-projection">Projection</span>
        </div>
      )}
    </section>
  );
}

/** Setup & health. A healthy project gets one quiet affirmative line — silence
    is ambiguous with a broken health check, and the ambiguity costs more than
    the pixels. Failing rows keep their plain-language fixes. */
function HealthLine({ ctx }: { ctx: ProjectCtx }) {
  const { projectId, health: data, reload } = ctx;
  const [show, setShow] = useState(false);
  if (!data) return null;
  if (data.all_ok && !show) {
    return (
      <div className="health-line">
        <span className="good"><Icon name="check" size={15} strokeWidth={2.2} /></span>
        <b>Setup is healthy.</b>
        <span className="help">All {data.checks.length} checks pass.</span>
        <span className="grow" />
        <button className="small" onClick={() => setShow(true)}>Details</button>
      </div>
    );
  }
  const badge = (status: HealthCheck["status"]) => (
    <span className={`badge ${status === "fail" ? "critical" : status === "warn" ? "warn" : "good"}`}>
      {status === "fail" ? "broken" : status === "warn" ? "check" : "ok"}
    </span>
  );
  return (
    <section className="card ov-card" style={{ marginTop: "var(--s4)" }}>
      <div className="card-h">
        <h3>Setup &amp; health</h3>
        <span className="grow" />
        {data.all_ok && <button className="small" onClick={() => setShow(false)}>Hide</button>}
      </div>
      {!data.all_ok && (
        <p className="help" style={{ margin: "0 0 8px" }}>
          The project tells you what it needs instead of failing quietly at 9am. Fix the rows below
          and this collapses back to one line.
        </p>
      )}
      {data.checks.map((check) => (
        <div key={check.key} className={`hrow${check.status === "ok" ? " ok" : ""}`}>
          {badge(check.status)}
          <span className="grow">{check.detail}</span>
          {check.action?.kind === "repin" ? (
            <button className="small" onClick={async () => {
              await post(`/admin/teams/${check.action!.team_id}/repin`, {});
              reload();
            }}>adopt the latest role version</button>
          ) : check.status !== "ok" && WHERE_TO[check.where ?? ""] ? (
            <Link to={`/projects/${projectId}/${WHERE_TO[check.where!][0]}`}>{WHERE_TO[check.where!][1]} →</Link>
          ) : null}
        </div>
      ))}
    </section>
  );
}

/** Where a health check says the fix lives — a check that names a place the
    reader cannot click is read once and then skipped. */
const WHERE_TO: Record<string, [string, string]> = {
  settings: ["settings", "fix in Settings"],
  plan: ["roadmap", "open the Roadmap"],
  boardroom: ["chat", "open the chat"],
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
  const { projectId, dash, seats, hypotheses, flags: flagsView } = ctx;
  const navigate = useNavigate();
  const desk = useDesk();
  const [brief, setBrief] = useState(false);
  // Three rows and a count, asked of the same index the Deliverables page
  // reads — not the whole workspace walk, every twenty seconds, to show three.
  // Its own request rather than part of the frame's read: the index is on
  // disk, and its first walk would otherwise hold up the whole screen.
  const { data: shelf } = usePoll(
    () => get<ShelfView>(`/observe/projects/${projectId}/files/browse/rows?state=published&sort=at&desc=true&limit=3`),
    20000);

  // The same rows the Inbox and the sidebar badge count, for this project. A
  // paused project is not counted there (pausing is a state, not a request),
  // but its own page still lists what is parked on it.
  const deskProject = desk.data?.projects.find((p) => p.id === projectId);
  const items = useMemo(
    () => (deskProject ? itemsFor(deskProject, desk.reload) : []),
    [deskProject, desk.reload],
  );
  const soonest = soonestClock(items.filter(needsYou));
  const health = runHealth(ctx.runs ?? []);
  const ceo = firstName(ctx.ceoName);
  const auto = autonomyWord(dash);
  const base = `/projects/${projectId}`;

  // ── needs you ──
  // Only what decides itself against you if ignored, and nothing that merely
  // wants reading. It is the Inbox, filtered to this project, so the count
  // here, the count in the sidebar and the count in the Inbox are one count.
  const rawFlags = flagsView?.flags ?? [];
  const needs: React.ReactNode[] = [];
  for (const it of items.filter(needsYou)) {
    needs.push(
      <div className="need" key={it.key}>
        <span className={`kind-ic k-${it.type}`}><Icon name={KIND_ICON[it.type]} size={15} /></span>
        <div className="grow">
          <Link className="need-t" to={`/inbox?item=${encodeURIComponent(it.key)}`}>{it.what}</Link>
          <div className="need-m">
            <span>{it.kind}</span>
            <span className={it.clocked && it.at - Date.now() < 6 * 3_600_000 ? "warn" : ""}>
              <Icon name="clock" size={12} /> {it.consequence}
            </span>
          </div>
        </div>
        <div className="need-a"><ItemActions item={it} /></div>
      </div>,
    );
  }

  // ── your CEO is telling you ──
  //
  // A raised flag is the CEO bringing you something with a proposal. It does
  // not block, it does not expire, and it proceeds on that proposal whether or
  // not you answer — so it reads beside the CEO's last word, never under the
  // heading that says a decision is waiting on you.
  const news: React.ReactNode[] = [];
  for (const f of rawFlags.filter((f) => f.status === "raised" && !f.acknowledged_at)) {
    const plain = flagPlain(f.kind);
    news.push(
      <div className="flag" key={f.id}>
        <div className="flag-t"><Icon name="flag" size={14} /> <b>{plain.title}</b></div>
        <div className="help">
          {ceo} proposes: {f.proposal ? clip(f.proposal, 200) : "nothing yet"}. That goes ahead unless
          you say otherwise.
        </div>
        <div className="flag-a">
          <NoteAction
            label="Noted"
            className="small"
            placeholder={`Anything to say back? — ${ceo} reads this`}
            confirmLabel="Noted"
            onSubmit={async (note) => {
              await post(`/admin/flags/${f.id}/ack`, { note });
              ctx.reload();
            }}
          />
          <Link to={`${base}/chat`}>talk it through →</Link>
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
  for (const f of rawFlags.filter((f) => f.status === "raised" && f.acknowledged_at)) {
    watch.push(
      <div className="wrow" key={f.id}>
        <span className="badge">noted</span>
        <div className="grow">
          <b>{flagPlain(f.kind).title}</b>{" "}
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
        <Link to={`${base}/activity/memory`}>see what it keeps →</Link>
      </div>,
    );
  }

  const people = [...seats].filter((s) => s.state !== "stood_down")
    .sort((a, b) => SEAT_ORDER.indexOf(a.state) - SEAT_ORDER.indexOf(b.state));
  const reqs = (ctx.plan?.requirements ?? []).filter((r) => r.status !== "cut");
  const rank: Record<string, number> = { in_progress: 0, proposed: 1, accepted: 2, pending: 3, met: 4 };
  const shownReqs = [...reqs].sort((a, b) => (rank[a.status] ?? 3) - (rank[b.status] ?? 3) || a.stage - b.stage).slice(0, 7);
  const bets = hypotheses.filter((h) => h.status === "active" || h.status === "expired")
    .sort((a, b) => a.kill_date.localeCompare(b.kill_date)).slice(0, 5);
  const usdMode = dash.billing_mode === "api_key";

  return (
    <>
      <header className="ov-head">
        <div className="eyebrow">
          <span className="chip">{dash.mode === "deliver" ? "Deliver" : dash.mode === "operate" ? "Operate" : "Explore"}</span>
          {ctx.plan?.commitment?.deliver_by && <span>Due {fmtDate(ctx.plan.commitment.deliver_by)}</span>}
          {!ctx.plan?.commitment?.deliver_by && dash.deadline && <span>Window closes {fmtDate(dash.deadline)}</span>}
          <span className="term-plain"><Term means={auto.means}>{auto.word}</Term></span>
          {dash.my_role && <span>You are {dash.my_role === "chairman" ? "the chairman" : dash.my_role === "co_chair" ? "a co-chair" : "an observer"}</span>}
        </div>
        <div className="ov-title-row">
          <div className="grow">
            <h1 className="ov-title">
              {dash.name} {statusBadge(dash.status)}
              {dash.drought_active && (
                <Badge tone="serious">
                  <Term means="Nothing outside this project has verified any of its claims for longer than your rules allow. Work carries on; new internal-only work is refused until proof lands.">
                    no outside proof lately
                  </Term>
                </Badge>
              )}
              {setupBadge}
            </h1>
            <p className="ov-goal">
              {goalLead}{" "}
              <button className="linkish inline" onClick={() => setBrief(!brief)} aria-expanded={brief}>
                {brief ? "Hide the brief" : "Read the brief"}
              </button>
            </p>
          </div>
          <div className="ov-acts">
            <button className="avstack" onClick={() => navigate(`${base}/team`)} title="The team">
              {people.slice(0, 6).map((s) => (
                <Avatar key={s.id} name={s.name} team={s.ceo ? "leadership" : s.team} size="sm" />
              ))}
            </button>
            {dash.can_steer !== false && dash.status !== "running" && dash.status !== "stopped" && (
              <Btn kind="primary" icon="play" onClick={async () => { await post(`/admin/projects/${projectId}/start`); ctx.reload(); }}>Start</Btn>
            )}
            {dash.can_steer !== false && dash.status === "running" && <Btn icon="pause" onClick={onPause}>Pause</Btn>}
            {dash.can_steer !== false && dash.status !== "stopped" && <Btn kind="danger" icon="stop" onClick={onStop}>Stop</Btn>}
            <Link to={`${base}/chat`} className="btnlink primary"><Icon name="message" size={15} />Message {ceo}</Link>
          </div>
        </div>
        {brief && (
          <div className="briefbox">
            <strong style={{ color: "var(--ink)" }}>The brief every one of them works from</strong>
            <p style={{ whiteSpace: "pre-wrap", margin: "6px 0 0" }}>{dash.goal}</p>
          </div>
        )}
      </header>

      <Kpis ctx={ctx} />
      <ModeStrip dash={dash} reload={ctx.reload} />
      <LateBanner ctx={ctx} />

      <div className="ov-grid">
        <div className="ov-col">
          <section className="card ov-card">
            <div className="card-h">
              <h3>Needs you</h3>
              {needs.length > 0 && <span className="count warn">{needs.length}</span>}
              <span className="grow" />
              {soonest && <span className="help">soonest decides itself {fmtSoon(new Date(soonest.at).toISOString())}</span>}
              <Link className="card-link" to="/inbox">Inbox <Icon name="arrow" size={13} /></Link>
            </div>
            {needs.length > 0 ? <div className="needs">{needs}</div> : (
              <div className="quiet-line">
                <span className="good"><Icon name="check" size={16} strokeWidth={2.2} /></span>
                <span>
                  <b>Nothing needs a decision from you.</b>{" "}
                  <span className="help">
                    {fullyAutonomous(dash)
                      ? `You set this project to run itself, so scope, dates and verdicts are ${ceo}'s to call.`
                      : dash.runs.running > 0
                        ? `${dash.runs.running} ${dash.runs.running === 1 ? "run is" : "runs are"} in flight right now.`
                        : dash.runs.queued > 0
                          ? `${dash.runs.queued} ${dash.runs.queued === 1 ? "run is" : "runs are"} queued to start.`
                          : "Nobody is working at this moment — Coming up says when the next thing wakes."}
                  </span>
                </span>
              </div>
            )}
          </section>

          <section className="card ov-card">
            <div className="card-h">
              <h3>Team right now</h3>
              <span className="grow" />
              <Link className="card-link" to={`${base}/team`}>Org chart <Icon name="arrow" size={13} /></Link>
            </div>
            {people.length === 0 ? <p className="help">Nobody is on this project yet.</p> : (
              <div className="people">
                {people.slice(0, 8).map((s) => (
                  <button key={s.id} className="person-row" onClick={() => ctx.openAgent(s.id)}>
                    <span className="av-wrap">
                      <Avatar name={s.name} team={s.ceo ? "leadership" : s.team} />
                      <span className={`seat-dot ring ${s.state}`} />
                    </span>
                    <span className="grow">
                      <span className="pr-name"><b>{s.name}</b> <span className="help">{s.ceo ? "CEO" : `${s.team}${s.lead ? " lead" : ""}`}</span></span>
                      <span className="pr-doing">{s.doing}</span>
                    </span>
                    <span className="pr-right">
                      <SeatPill state={s.state} />
                      {s.since && <span className="help">{heldFor(s.since)}</span>}
                    </span>
                  </button>
                ))}
                {people.length > 8 && <Link className="more-row" to={`${base}/team`}>and {people.length - 8} more on the org chart</Link>}
              </div>
            )}
          </section>

          {dash.mode === "explore" ? (
            <section className="card ov-card">
              <div className="card-h">
                <h3>Bets</h3>
                <span className="help">hunches with a deadline</span>
                <span className="grow" />
                <Link className="card-link" to={`${base}/tasks?view=list`}>All bets <Icon name="arrow" size={13} /></Link>
              </div>
              {bets.length === 0 ? <p className="help">No live bets.</p> : bets.map((h) => (
                <div className="rm-row" key={h.id}>
                  <Icon name="target" size={14} />
                  <span className="grow">{clip(h.statement, 140)}</span>
                  <span className={`badge${h.status === "expired" ? " serious" : ""}`}>
                    {h.status === "expired" ? "expired" : `ends ${fmtLeft(h.kill_date).replace(" left", "")}`}
                  </span>
                </div>
              ))}
            </section>
          ) : (
            <section className="card ov-card">
              <div className="card-h">
                <h3>Roadmap</h3>
                {ctx.plan?.open_stage !== undefined && <span className="help">stage {ctx.plan.open_stage} is open</span>}
                <span className="grow" />
                <Link className="card-link" to={`${base}/roadmap`}>Open the roadmap <Icon name="arrow" size={13} /></Link>
              </div>
              {shownReqs.length === 0 ? (
                <p className="help">No requirements yet — {ceo} proposes what “done” means once it has planned.</p>
              ) : shownReqs.map((r) => (
                <div className="rm-row" key={r.id}>
                  <span className="mono rm-key">{r.key}</span>
                  <span className="grow rm-st">{r.statement}</span>
                  <span className={`badge${r.status === "met" ? " good" : r.status === "in_progress" ? " accent" : r.status === "proposed" ? " warn" : ""}`}>
                    {r.status === "met" ? "Met" : r.status === "in_progress" ? "In progress" : r.status === "proposed" ? "Proposed" : `Stage ${r.stage}`}
                  </span>
                </div>
              ))}
              <p className="rm-foot help">
                {dash.open_tasks} of {dash.queue_ceiling} tasks open at once ·{" "}
                <Term means="Of the bets the CEO has placed, how many produced proof from outside the project.">
                  how often the CEO is right
                </Term>
                : {dash.ceo_judgment.conversion === null ? "—" : `${Math.round(dash.ceo_judgment.conversion * 100)}%`}
              </p>
            </section>
          )}
        </div>

        <aside className="ov-col">
          <CeoCard ctx={ctx} news={news} />
          <ComingUp projectId={projectId} data={ctx.agenda} />
          <section className="card ov-card">
            <div className="card-h">
              <h3>Where this month's money went</h3>
              <span className="grow" />
              <Link className="card-link" to={`${base}/spend`}>Spend <Icon name="arrow" size={13} /></Link>
            </div>
            <SpendByTeam report={ctx.spendByTeam} usdMode={usdMode} compact />
          </section>
          <section className="card ov-card">
            <div className="card-h">
              <h3>Latest deliverables</h3>
              <span className="grow" />
              <Link className="card-link" to={`${base}/deliverables`}>All {shelf?.matched ?? ""} <Icon name="arrow" size={13} /></Link>
            </div>
            {(shelf?.rows ?? []).length === 0 ? (
              <p className="help">
                Nothing published yet. Deliverables appear here the moment a team publishes one.
              </p>
            ) : (shelf?.rows ?? []).map((a) => (
              <Link className="file-row" key={a.key} to={`${base}/deliverables?open=${encodeURIComponent(a.key)}`}>
                <span className="file-ic"><Icon name="file" size={15} /></span>
                <span className="grow">
                  <span className="file-n">{a.name}</span>
                  <span className="help">{fmtSize(a.size)} · {a.at ? fmtWhen(a.at) : "—"}</span>
                </span>
                {a.receipt ? <span className="badge good">published</span> : <span className="badge warn">no receipt</span>}
              </Link>
            ))}
          </section>
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
          <Link to={`${base}/activity/runs`}>see what happened →</Link>
        </div>
      )}

      {watch.length > 0 && (
        <>
          <h2 className="zone">Watching <span className="cnt">{watch.length}</span></h2>
          <p className="help" style={{ margin: "0 0 var(--s3)" }}>
            Clocks {ceo} is handling. Nothing here waits on you; it is here so nothing happens silently.
          </p>
          <div className="wbox">{watch}</div>
        </>
      )}

      <HealthLine ctx={ctx} />
      <AsOf at={ctx.asOf} error={ctx.error} />
    </>
  );
}

const KIND_ICON = {
  approval: "check", ask: "help", scope: "list", commitment: "calendar", verdict: "eye",
  bet: "target", frozen: "alert", kill: "target", drought: "shield", budget: "wallet", noproof: "shield",
} as const;
