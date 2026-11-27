import { ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AgendaView, Dashboard, HypothesisRow, PlanView, VitalsView } from "../../api";
import { fmtSoon, fmtUnit } from "../../ui";
import { Icon } from "../../ui/Icon";
import { fmtRoll, kindLabel } from "../../ui/project";

/** The project home's pictures. Each of these replaced a sentence that held
    a number with a limit: "out of runs for today (40 of 40)", "537,922,038 of
    750,000,000 tokens this month", "work was waiting and no run started for
    hours (294 times)". A number with a limit is a bar; a limit hit on some
    days and not others is a grid. The words that remain say what to do.

    Colour is state only — good, warning, critical — and every state also
    carries a word or a shape, never colour alone. Charts draw from the
    theme's tokens through classes, so one SVG reads in both themes. */

const DAY_MS = 86_400_000;

const time = (iso: string) =>
  new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
const day = (iso: string) =>
  new Date(`${iso}T12:00:00Z`).toLocaleDateString(undefined, { day: "numeric", timeZone: "UTC" });
const minutes = (s: number | null) =>
  s === null ? "—" : s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`;

// ── mode ───────────────────────────────────────────────────────────────────────

/** Deliver, Explore or Operate, said first and in the words of its rhythm:
    the three run on different clocks, and a page that hides which one it is
    leaves the reader to guess what "on track" is measured against. */
export function ModePill({ dash, plan }: { dash: Dashboard; plan: PlanView | null }) {
  let rhythm: string;
  if (dash.shape === "operate") {
    const c = dash.cycle;
    // A factory in upkeep says so: its rhythm is the scorecard, not the work.
    const days = c ? Math.max(1, Math.round((new Date(c.rolls_at).getTime() - new Date(c.opened_at).getTime()) / DAY_MS)) : null;
    rhythm = days ? `Repeats every ${days === 7 ? "week" : `${days} days`}` : "Repeats on a rhythm";
  } else if (dash.shape === "deliver") {
    const by = plan?.commitment?.deliver_by;
    rhythm = by ? `Ships once · due ${new Date(by).toLocaleDateString(undefined, { month: "short", day: "numeric" })}` : "Ships once, then finishes";
  } else {
    rhythm = dash.deadline
      ? `Open-ended · review ${new Date(dash.deadline).toLocaleDateString(undefined, { month: "short", day: "numeric" })}`
      : "Open-ended · ideas tested against evidence";
  }
  return (
    <span className={`vt-mode vt-mode-${dash.shape}`}>
      <b>{kindLabel(dash).toUpperCase()}</b>
      <span>{rhythm}</span>
    </span>
  );
}

// ── right now ──────────────────────────────────────────────────────────────────

type Tone = "ok" | "warn" | "serious" | "critical" | "idle";

/** The reasons the platform writes for itself on a pause or a stop: nobody
    typed one, so they are not said as if somebody had. */
const UNSPOKEN = new Set(["paused by chairman", "stopped by chairman"]);

/** The state, in the words a person would use, for each gate the dispatcher
    can name. The server's own sentence stays one hover away. */
const STATE: Record<string, [string, Tone]> = {
  daily_runs: ["Paused: no runs left today", "warn"],
  governance_reserve: ["Paused: today's runs are used; the rest are kept for reviews", "warn"],
  budget_reserve: ["Handing in: the job budget is nearly spent", "warn"],
  pacing: ["Paused for the hour: hourly limit reached", "warn"],
  wake_ceiling: ["Waiting: each agent has used its runs for this hour", "warn"],
  coalescing: ["Waiting a moment to batch messages", "ok"],
  concurrency: ["Working: every run slot is busy", "ok"],
  agents_busy: ["Working", "ok"],
  inbox_runs: ["Waiting: agents read messages on their next work wake", "idle"],
  scheduled: ["Waiting for work scheduled later", "idle"],
  awaiting_chairman: ["Waiting on you", "warn"],
  awaiting_ceo: ["Waiting on the CEO", "idle"],
  delivery_ready: ["Delivered: waiting for your sign-off", "ok"],
  checking: ["Checking finished work", "ok"],
  review_owed: ["Waiting for a review", "idle"],
};

interface Gate { key: string; label: string; state: "ok" | "stop" | "busy"; hint?: string }

/** What the two run counters count. "This hour 16/15" read as sixteen of
    something, with nothing to say what — and the reading runs, checks and scans
    that also happened that hour made it look miscounted. */
const COUNTS = "Counts runs where an agent works. Reading messages, checks and scans "
  + "are not counted. A run counts when it starts, so the count can pass the limit by one.";

function gates(dash: Dashboard, vitals: VitalsView | null): Gate[] {
  const a = dash.activity;
  const b = a.blocked_by;
  const waitingOnWork = b !== null && !(b in STATE && ["daily_runs", "governance_reserve", "budget_reserve", "pacing", "wake_ceiling",
    "coalescing", "concurrency", "agents_busy", "inbox_runs"].includes(b));
  // Ready means a task an agent can claim, or a run already going. Unread mail
  // between agents is not work: counting it put "✓ Work ready" beside "Idle".
  const idle = b === null && dash.runs.running === 0 && a.waiting.claimable_tasks === 0;
  const planShut = vitals?.plan?.status === "rejected";
  const list: Gate[] = [
    { key: "work", label: waitingOnWork ? "Work not ready" : "Work ready",
      state: idle || waitingOnWork ? "stop" : "ok" },
    { key: "day", label: `Work runs today ${a.daily_runs.used}/${vitals?.today.wall ?? a.daily_runs.cap}`,
      state: b === "daily_runs" || b === "governance_reserve" ? "stop" : "ok",
      hint: `Since midnight. ${COUNTS}` },
    { key: "hour", label: b === "pacing" ? `Work runs this hour ${a.pacing.used}/${a.pacing.cap}`
      : "Hourly limit",
      state: b === "pacing" ? "stop" : "ok",
      hint: `${a.pacing.used} of ${a.pacing.cap} in the last 60 minutes`
        + (a.pacing.frees_at ? `; the next one may start at ${time(a.pacing.frees_at)}` : "")
        + `. ${COUNTS}` },
    { key: "agent", label: b === "agents_busy" ? "Agents busy" : "Agent free",
      state: b === "wake_ceiling" || b === "coalescing" ? "stop" : b === "agents_busy" ? "busy" : "ok" },
    { key: "slot", label: `Run slots ${a.concurrency.in_flight}/${a.concurrency.cap}`,
      state: b === "concurrency" ? "busy" : "ok" },
  ];
  if (b === "inbox_runs") list.splice(2, 0, { key: "inbox", label: "Reading off", state: "stop" });
  if (vitals?.plan) list.push({ key: "plan", label: "Claude plan", state: planShut ? "stop" : "ok" });
  return list;
}

/** When the wall that is up comes down, if a clock says so. */
export function resumesAt(dash: Dashboard, vitals: VitalsView | null): string | null {
  const a = dash.activity;
  switch (a.blocked_by) {
    case "daily_runs":
    case "governance_reserve": return a.daily_runs.resets_at;
    case "pacing": return a.pacing.frees_at;
    case "wake_ceiling": return a.wake_ceiling.frees_at;
    case "coalescing": return a.coalescing.ripe_at;
    case "scheduled": return a.board.next_gate_at;
  }
  if (vitals?.plan?.status === "rejected") {
    const full = vitals.plan.windows.filter((w) => (w.utilization ?? 0) >= 1 && w.resets_at && !w.expired)
      .map((w) => w.resets_at!).sort();
    if (full.length) return full[full.length - 1];
  }
  return a.brief.clears_at;
}

/** One line for "what is it doing right now?": the state, the chain of gates
    a run has to pass with the shut one marked, and when it opens. It replaces
    the capped banner, the yellow sentence under the title, and the setup
    check that said the same thing a third time. */
export function StatusLine({ dash, vitals, fix, idleWhy }: {
  dash: Dashboard; vitals: VitalsView | null; fix?: ReactNode;
  /** Why nothing is ready, when the page knows better than "Nothing waiting". */
  idleWhy?: string | null;
}) {
  const navigate = useNavigate();
  const a = dash.activity;
  if (dash.status !== "running") {
    const said: Record<string, [string, Tone, string]> = {
      paused: ["Paused", "warn", "Nothing new starts until you resume. The board and the record are intact."],
      stopped: ["Stopped", "idle", "This is final. Duplicate it from Settings to carry the work forward."],
      completed: ["Delivered", "ok", ""],
      draft: ["Not started yet", "idle", ""],
    };
    const [title, tone, means] = said[dash.status] ?? [dash.status, "idle" as Tone, ""];
    // "paused by chairman" is the platform's default, not a reason anybody gave.
    const reason = dash.status_reason && !UNSPOKEN.has(dash.status_reason) ? dash.status_reason : null;
    return (
      <section className={`vt-status tone-${tone}`} aria-label="Right now">
        <div className="vt-state"><i className="vt-dot" aria-hidden="true" />
          <div><b>{title}{reason ? `: ${reason}` : ""}</b>{means && <span className="help">{means}</span>}</div>
        </div>
      </section>
    );
  }
  const b = a.blocked_by;
  const planShut = vitals?.plan?.status === "rejected";
  let [title, tone] = b && STATE[b] ? STATE[b]
    : b ? [`Stuck: ${a.brief.short}`, "serious" as Tone]
      : dash.runs.running > 0 ? ["Working", "ok" as Tone] : ["Idle: no work is ready", "idle" as Tone];
  if (planShut && (b === null || tone === "ok")) [title, tone] = ["Paused: the Claude plan is full", "warn"];
  const w = a.waiting;
  // Agents' mail to each other is not said here: it is held a few minutes and
  // read together, nothing is lost, and "15 unread messages" read as his. It
  // is said only when it is stuck, which is the reading allowance below.
  const reading = b === "inbox_runs";
  const behind = [
    w.claimable_tasks ? `${w.claimable_tasks} task${w.claimable_tasks === 1 ? "" : "s"} ready` : null,
    w.gated_tasks ? `${w.gated_tasks} scheduled` : null,
    reading && w.unread ? `${w.unread} message${w.unread === 1 ? "" : "s"} between agents waiting` : null,
  ].filter(Boolean).join(" · ");
  const at = tone === "ok" ? null : resumesAt(dash, vitals);
  const cost = a.brief.chip === "work waiting" ? a.brief.headline : null;
  const idleLine = idleWhy && title.startsWith("Idle") ? idleWhy : null;
  return (
    <section className={`vt-status tone-${tone}`} aria-label="Right now">
      <div className="vt-state" title={`${a.brief.headline} ${a.brief.detail}`}>
        <i className="vt-dot" aria-hidden="true" />
        <div>
          <b>{title}</b>
          <span className="help">{behind || idleLine || (dash.runs.running ? `${dash.runs.running} running` : "Nothing waiting")}
            {/* Only worth saying while a limit has stopped the team. */}
            {dash.chat_live && tone === "warn" ? " · your messages still get answered" : ""}</span>
        </div>
      </div>
      <ol className="vt-gates" aria-label="What a run needs">
        {gates(dash, vitals).map((g) => (
          <li key={g.key} className={`vt-gate ${g.state}`} title={g.hint}>
            <span aria-hidden="true">{g.state === "stop" ? "✕" : g.state === "busy" ? "●" : "✓"}</span>{g.label}
          </li>
        ))}
      </ol>
      {at ? (
        <div className="vt-resume">
          <span className="vt-k">Resumes</span>
          <b>{time(at)}</b>
          <span className="help">{fmtSoon(at)}</span>
        </div>
      ) : fix ? <div className="vt-resume">{fix}</div> : null}
      {reading && (
        // Reading has no ceiling any more; past its allowance it is read on
        // the daily runs. Mail waits only where reading is switched off,
        // which is a choice, so the line says how to switch it back on.
        <div className="vt-cost">
          <span><b>Why:</b> this project doesn't wake agents just to read messages. They read them the next time they wake up for work.</span>
          <Link to={`/projects/${dash.id}/settings?s=rules&t=${encodeURIComponent("Pace & brakes")}`}>Let agents wake up to read</Link>
        </div>
      )}
      {cost && (
        // Ready work held up by the day's limit is the one wait with a price,
        // so it is said inside the line, with its two fixes, rather than in a
        // box of its own above every page.
        <div className="vt-cost">
          <span><b>Waiting:</b> {cost}</span>
          <Link to={`/projects/${dash.id}/settings?s=rules&t=${encodeURIComponent("Pace & brakes")}`}>Allow more runs per day</Link>
          <button className="linkish" onClick={() => navigate(`/projects/${dash.id}/chat`,
            { state: { draft: `${cost} What would you drop so it can run today?` } })}>Ask the CEO what to drop</button>
        </div>
      )}
    </section>
  );
}

// ── gauges ─────────────────────────────────────────────────────────────────────

/** Runs per hour since the daily refill, the hourly limit as a dashed line,
    and the hours that reached it in the warning colour. */
export function RunsTile({ vitals, base }: { vitals: VitalsView; base: string }) {
  const t = vitals.today;
  const out = t.used >= t.wall;
  const W = 240, H = 60, top = 6, floor = 46, bw = W / 24;
  const peak = Math.max(...t.hours, 1);
  const showCap = t.hourly_cap <= peak * 2;
  const max = showCap ? Math.max(t.hourly_cap, peak) * 1.1 : peak * 1.2;
  const y = (v: number) => floor - (v / max) * (floor - top);
  return (
    <Link className="ov-tile" to={`${base}/details/activity/runs`}>
      <span className="ov-tile-l">Runs today <span className={`vt-tag ${out ? "warn" : "good"}`}>{out ? "Used up" : `${t.wall - t.used} left`}</span></span>
      <span className="ov-tile-v">{t.used} <small>of {t.wall}</small></span>
      <svg className="vt-chart" viewBox={`0 0 ${W} ${H}`} role="img"
        aria-label={`Runs per hour today; the hourly limit is ${t.hourly_cap}`}>
        {t.hours.map((v, i) => i > t.current_hour
          ? <rect key={i} className="vt-future" x={i * bw + 1} y={floor - 1} width={bw - 2} height={1} />
          : <rect key={i} className={v >= t.hourly_cap ? "vt-hot" : "vt-bar"} x={i * bw + 1} y={y(Math.max(v, 0.08))}
              width={bw - 2} height={floor - y(Math.max(v, 0.08))} rx={1.5} />)}
        {showCap && <>
          <line className="vt-limit" x1={0} x2={W} y1={y(t.hourly_cap)} y2={y(t.hourly_cap)} />
          <text x={W} y={y(t.hourly_cap) - 3} textAnchor="end">hourly limit {t.hourly_cap}</text>
        </>}
        <text x={0} y={H - 2}>{time(t.starts_at)}</text>
        <text x={(t.current_hour + 0.5) * bw} y={H - 2} textAnchor="middle" className="vt-now">now</text>
      </svg>
      <span className="ov-tile-s">Resets {time(t.resets_at)} · {fmtSoon(t.resets_at)}{showCap ? "" : ` · up to ${t.hourly_cap} an hour`}</span>
    </Link>
  );
}

/** Spent so far this month as a line, the run-rate projection dashed to the
    month's end, and the warning and budget lines it is measured against. */
export function SpendTile({ vitals, base }: { vitals: VitalsView; base: string }) {
  const s = vitals.spend;
  const length = new Date(Date.UTC(Number(s.month.slice(0, 4)), Number(s.month.slice(5, 7)), 0)).getUTCDate();
  const W = 240, H = 60, top = 12, floor = 46;
  const max = Math.max(s.cap * 1.05, s.projected * 1.02, s.spent, 1);
  const x = (d: number) => 2 + (d / length) * (W - 4);
  const y = (v: number) => floor - (v / max) * (floor - top);
  let run = 0;
  const pts = s.days.map((v, i) => { run += v; return [x(i + 1), y(run)] as const; });
  const line = [[x(0), y(0)], ...pts].map(([px, py]) => `${px},${py}`).join(" ");
  const last = pts[pts.length - 1] ?? [x(0), y(0)];
  const over = s.spent > s.cap;
  const tag = over ? ["bad", "Over budget"] : s.projected > s.cap ? ["warn", "Over at this pace"]
    : s.spent >= s.warn_at ? ["warn", "Past the warning"] : ["good", "On pace"];
  return (
    <Link className="ov-tile" to={`${base}/details/spend`}>
      <span className="ov-tile-l">{s.unit === "usd" ? "Spending" : "Tokens"} this month <span className={`vt-tag ${tag[0]}`}>{tag[1]}</span></span>
      <span className="ov-tile-v">{fmtUnit(s.spent, s.unit)} <small>of {fmtUnit(s.cap, s.unit)}</small></span>
      <svg className="vt-chart" viewBox={`0 0 ${W} ${H}`} role="img"
        aria-label={`Used ${fmtUnit(s.spent, s.unit)}; projected ${fmtUnit(s.projected, s.unit)} by month end`}>
        <polygon className="vt-area" points={`${line} ${last[0]},${floor} ${x(0)},${floor}`} />
        <polyline className="vt-line" points={line} />
        <line className="vt-proj" x1={last[0]} y1={last[1]} x2={x(length)} y2={y(s.projected)} />
        <circle className="vt-pt" cx={last[0]} cy={last[1]} r={3} />
        <line className="vt-budget" x1={0} x2={W} y1={y(s.cap)} y2={y(s.cap)} />
        <text x={2} y={y(s.cap) - 3}>budget</text>
        {s.warn_at < s.cap && <>
          <line className="vt-warnline" x1={0} x2={W} y1={y(s.warn_at)} y2={y(s.warn_at)} />
          <text x={W} y={y(s.warn_at) + 10} textAnchor="end">warning</text>
        </>}
        <text x={0} y={H - 2}>1st</text>
        <text x={W} y={H - 2} textAnchor="end">{length}th</text>
      </svg>
      <span className="ov-tile-s">
        Ends near <b>{fmtUnit(s.projected, s.unit)}</b> at this pace · pauses at {fmtUnit(s.pauses_at, s.unit)}
      </span>
    </Link>
  );
}

// ── the mode's own clock ───────────────────────────────────────────────────────

/** The picture under the first tile: an Operate cycle's days with today
    marked, a Deliver date's time used so far, an Explore window's days. */
export function TimeVisual({ dash, plan }: { dash: Dashboard; plan: PlanView | null }) {
  const now = Date.now();
  if (dash.shape === "operate" && dash.cycle) {
    const c = dash.cycle;
    const opened = new Date(c.opened_at).getTime();
    const days = Math.max(1, Math.round((new Date(c.due_at ?? c.rolls_at).getTime() - opened) / DAY_MS));
    const today = Math.min(days - 1, Math.max(0, Math.floor((now - opened) / DAY_MS)));
    return <Segments total={days} done={today} now={today} />;
  }
  const end = dash.shape === "deliver" ? plan?.commitment?.deliver_by : dash.shape === "explore" ? dash.deadline : null;
  if (!end) return null;
  const start = new Date(dash.created_at).getTime();
  const used = Math.min(1, Math.max(0, (now - start) / Math.max(new Date(end).getTime() - start, 1)));
  return (
    <span className="vt-mini big" role="img" aria-label={`${Math.round(used * 100)}% of the time used`}>
      <span className={used >= 1 ? "bad" : ""} style={{ width: `${used * 100}%` }} />
    </span>
  );
}

/** Needed against available, drawn to one scale: the capacity forecast's four
    numbers (capacity.py) as two bars rather than a sentence. */
function FitBars({ needed, reserve, available, label }: { needed: number; reserve: number; available: number; label: string }) {
  const W = 300, max = Math.max(needed + reserve, available, 1) * 1.08;
  const sx = (v: number) => (v / max) * (W - 60);
  const short = needed + reserve > available;
  return (
    <svg className="vt-chart vt-fit" viewBox={`0 0 ${W} 86`} role="img"
      aria-label={`Needs ${needed} runs plus ${reserve} for retries; ${available} available`}>
      <text x={0} y={14}>Needed</text>
      <rect className="vt-bar" x={0} y={18} width={Math.max(sx(needed), 1)} height={14} rx={2} />
      <rect className="vt-reserve" x={sx(needed) + 1} y={18} width={sx(reserve)} height={14} rx={2} />
      <text x={sx(needed + reserve) + 6} y={29} className="vt-strong">{needed}{reserve ? ` + ${reserve} retries` : ""}</text>
      <text x={0} y={56}>{label}</text>
      <rect className={short ? "vt-bad" : "vt-ok"} x={0} y={60} width={Math.max(sx(available), 1)} height={14} rx={2} />
      <text x={sx(available) + 6} y={71} className="vt-strong">{available}</text>
    </svg>
  );
}

function Segments({ total, done, now }: { total: number; done: number; now?: number }) {
  return (
    <span className="ov-segs vt-segs" role="img" aria-label={`${done} of ${total}`}>
      {Array.from({ length: total }, (_, i) => (
        <i key={i} className={i < done ? "seg-verified" : i === now ? "seg-work" : ""} />
      ))}
    </span>
  );
}

/** What differs by mode, in one panel: an Operate cycle and whether its work
    fits, a Deliver date against where the pace lands, or an Explore project's
    ideas and the time each has left. */
export function ModePanel({ dash, plan, hypotheses, base, vitals }: {
  dash: Dashboard; plan: PlanView | null; hypotheses: HypothesisRow[]; base: string; vitals: VitalsView | null;
}) {
  const now = Date.now();
  if (dash.shape === "operate" && dash.cycle) {
    const c = dash.cycle;
    const cap = c.capacity;
    const pub = vitals?.published;
    const shipped = pub ? pub.counts.filter((n) => n > 0).length : 0;
    return (
      <section className="card ov-card">
        <div className="card-h"><h3>This cycle</h3>
          <span className="help">Cycle {c.ordinal} · {new Date(c.opened_at).toLocaleDateString(undefined, { month: "short", day: "numeric" })} → {fmtRoll(c.due_at ?? c.rolls_at)}</span>
          <span className="grow" /><Link className="card-link" to={`${base}/details/roadmap`}>The plan <Icon name="arrow" size={13} /></Link></div>
        <div className="vt-twin">
          <div>
            <span className="vt-k">Results published, last 14 days</span>
            {pub ? (
              <>
                <span className="vt-days" role="img" aria-label={`Something was published on ${shipped} of the last 14 days`}>
                  {pub.counts.map((n, i) => (
                    <i key={pub.days[i]} className={`${n > 0 ? "on" : ""}${i === pub.counts.length - 1 ? " today" : ""}`}
                      title={`${pub.days[i]}: ${n} published`} />
                  ))}
                </span>
                <div className="vt-legend"><span><i className="vt-ok" />Published {shipped}</span>
                  <span><i className="h0" />Nothing {14 - shipped}</span></div>
              </>
            ) : <p className="help">Loading…</p>}
          </div>
          <div>
            <span className="vt-k">Can the cycle finish?</span>
            {cap && cap.needed > 0 ? (
              <>
                <FitBars needed={cap.needed} reserve={cap.retry_reserve} available={cap.available} label="Available before it closes" />
                <p className="help">{cap.feasible ? `Needs about ${cap.needed} runs; ${cap.available} available. Fits.` : `Does not fit. ${cap.why}.`}</p>
              </>
            ) : <p className="help">Nothing open in this cycle needs runs.</p>}
          </div>
        </div>
      </section>
    );
  }
  if (dash.shape === "deliver") {
    const c = plan?.commitment;
    if (!c) return null;
    const start = new Date(dash.created_at).getTime();
    const due = new Date(c.deliver_by).getTime();
    const projected = c.projected_done_at ? new Date(c.projected_done_at).getTime() : null;
    const end = Math.max(due, projected ?? 0, now) + DAY_MS;
    const px = (t: number) => `${Math.min(100, Math.max(0, ((t - start) / (end - start)) * 100))}%`;
    const late = projected !== null && projected > due;
    const left = Math.ceil((due - now) / DAY_MS);
    return (
      <section className="card ov-card">
        <div className="card-h"><h3>Delivery</h3>
          <span className="help">due {fmtRoll(c.deliver_by)} · {left > 0 ? `${left} day${left === 1 ? "" : "s"} left` : "date passed"}</span>
          <span className="grow" /><Link className="card-link" to={`${base}/details/roadmap`}>The plan <Icon name="arrow" size={13} /></Link></div>
        <div className="vt-track" role="img" aria-label="Time from start to the delivery date">
          <span className="vt-track-used" style={{ width: px(now) }} />
          <span className="vt-mark vt-mark-due" style={{ left: px(due) }}><b>Due</b></span>
          {projected !== null && <span className={`vt-mark ${late ? "vt-mark-late" : "vt-mark-ok"}`} style={{ left: px(projected) }}><b>At this pace</b></span>}
          <span className="vt-mark vt-mark-now" style={{ left: px(now) }}><b>Today</b></span>
        </div>
        <p className="help">
          {projected === null ? "Not enough finished work yet to project a date."
            : late ? `At the current pace it finishes ${Math.ceil((projected - due) / DAY_MS)} day(s) after the date.`
              : "At the current pace it finishes before the date."}
        </p>
      </section>
    );
  }
  if (dash.shape === "explore") {
    const live = hypotheses.filter((h) => h.status === "active" || h.status === "expired");
    return (
      <section className="card ov-card">
        <div className="card-h"><h3>Ideas being tested</h3>
          {dash.deadline && <span className="help">review {fmtRoll(dash.deadline)}</span>}
          <span className="grow" /><Link className="card-link" to={`${base}/details/tasks`}>All <Icon name="arrow" size={13} /></Link></div>
        {live.length ? (
          <ul className="vt-bets">
            {live.map((h) => {
              const leftDays = Math.ceil((new Date(h.kill_date).getTime() - now) / DAY_MS);
              const soon = leftDays <= 2;
              return (
                <li key={h.id}>
                  <span className="vt-bet-t">{h.statement}</span>
                  <span className={`vt-bet-l${soon ? " soon" : ""}`}>
                    {h.status === "expired" ? "Ended: yours to extend" : leftDays > 0 ? `Ends in ${leftDays} day${leftDays === 1 ? "" : "s"}` : "Ends today"}
                  </span>
                  <span className="vt-mini"><span style={{ width: `${Math.max(4, Math.min(100, 100 - (leftDays / 14) * 100))}%` }}
                    className={soon ? "warn" : ""} /></span>
                </li>
              );
            })}
          </ul>
        ) : <p className="help">No ideas are being tested right now.</p>}
      </section>
    );
  }
  return null;
}

// ── what held it back ──────────────────────────────────────────────────────────

const heat = (h: number) => h <= 0 ? "h0" : h < 2 ? "h1" : h < 6 ? "h2" : "h3";

/** Fourteen days by five limits: the hours each one held the project. The
    question this answers — which wall does this project actually hit? — had
    no answer anywhere before; the status line only ever showed the one that
    was binding at the moment the page was opened. */
export function LimitGrid({ vitals, base }: { vitals: VitalsView; base: string }) {
  const { days, rows } = vitals.limits;
  const any = rows.some((r) => r.hours.some((h) => h > 0));
  return (
    <section className="card ov-card">
      <div className="card-h"><h3>What held work back</h3><span className="help">last 14 days, hours at each limit</span>
        <span className="grow" />
        <Link className="card-link" to={`${base}/settings?s=rules&t=${encodeURIComponent("Pace & brakes")}`}>Limits <Icon name="arrow" size={13} /></Link></div>
      {!any && <p className="help" style={{ margin: "0 0 6px" }}>Nothing held this project back in the last two weeks.</p>}
      <div className="vt-heat" role="table" aria-label="Hours at each limit, last 14 days">
        <span role="row" className="vt-heat-row vt-heat-head">
          <span />
          <span className="vt-cells">{days.map((d, i) => <span key={d}>{i % 3 === 0 || i === days.length - 1 ? day(d) : ""}</span>)}</span>
          <span className="vt-total">hours</span>
        </span>
        {rows.map((r) => {
          const total = r.hours.reduce((sum, h) => sum + h, 0);
          return (
            <span role="row" key={r.key} className="vt-heat-row">
              <span role="rowheader"><span>{r.label}</span><span className="help">{r.means}</span></span>
              <span className="vt-cells">{r.hours.map((h, i) => <i key={days[i]} className={heat(h)} title={`${days[i]}: ${h} h`} />)}</span>
              <span className="vt-total">{total ? Math.round(total) : "—"}</span>
            </span>
          );
        })}
      </div>
      <div className="vt-legend"><span>Hours:</span><span><i className="h0" />0</span><span><i className="h1" />under 2</span>
        <span><i className="h2" />2–6</span><span><i className="h3" />6+</span></div>
    </section>
  );
}

/** The last seven days by outcome against the daily limit. A failed run
    still spends the allowance, which is why the count drains faster than the
    work moves. Only counted runs are solid; a given-back run or a message
    read is an outline above, so no bar can pass the limit it is drawn under. */
export function RunsWeek({ vitals, base }: { vitals: VitalsView; base: string }) {
  const r = vitals.runs;
  const cap = vitals.today.wall;
  const W = 600, H = 132, floor = 108, top = 10, left = 28;
  const totals = r.days.map((d) => d.finished + d.failed + d.given_back + d.reads);
  // Scaled to the week's own runs, so a quiet week still has bars; the daily
  // limit is drawn only when it is within reach of them, and named otherwise.
  const peak = Math.max(...totals, 1);
  const showCap = cap <= peak * 2;
  const max = showCap ? Math.max(cap, peak) * 1.1 : peak * 1.2;
  const y = (v: number) => (v / max) * (floor - top);
  const slot = (W - left) / r.days.length;
  const bw = Math.min(56, slot - 14);
  const high = (r.failure_share ?? 0) >= 0.15;
  return (
    <section className="card ov-card">
      <div className="card-h"><h3>Runs this week</h3>
        {r.failure_share !== null && <span className={`help${high ? " vt-warn-t" : ""}`}>
          <b>{Math.round(r.failure_share * 100)}%</b> failed{high ? ": each one still used a run" : ""}</span>}
        <span className="grow" /><Link className="card-link" to={`${base}/details/activity/runs`}>All runs <Icon name="arrow" size={13} /></Link></div>
      <svg className="vt-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Runs per day by outcome">
        {[0.5, 1].map((f) => {
          const v = Math.round(max * f / (showCap ? 1.1 : 1.2));
          return <g key={f}><line className="vt-grid" x1={left} x2={W} y1={floor - y(v)} y2={floor - y(v)} />
            <text x={0} y={floor - y(v) + 4}>{v}</text></g>;
        })}
        {showCap && <>
          <line className="vt-limit" x1={left} x2={W} y1={floor - y(cap)} y2={floor - y(cap)} />
          <text x={W} y={floor - y(cap) - 4} textAnchor="end">daily limit {cap}</text>
        </>}
        {r.days.map((d, i) => {
          const x = left + i * slot + (slot - bw) / 2;
          let cur = floor;
          const parts: [number, string][] = [[d.finished, "vt-ok"], [d.failed, "vt-bad"]];
          const extra = d.given_back + d.reads;
          return (
            <g key={d.day}>
              {parts.map(([v, cls]) => {
                if (!v) return null;
                const h = y(v); cur -= h;
                return <rect key={cls} className={cls} x={x} y={cur} width={bw} height={Math.max(h - 1, 1)} rx={1.5} />;
              })}
              {extra > 0 && (
                <rect className="vt-extra" x={x + 0.5} y={cur - y(extra)} width={bw - 1} height={Math.max(y(extra) - 1, 1)} rx={1.5}>
                  <title>{`${d.given_back} given back, ${d.reads} message reads: not counted`}</title>
                </rect>
              )}
              <text x={x + bw / 2} y={H - 4} textAnchor="middle" className={i === r.days.length - 1 ? "vt-now" : ""}>
                {new Date(`${d.day}T12:00:00Z`).toLocaleDateString(undefined, { weekday: "short", timeZone: "UTC" })}
              </text>
            </g>
          );
        })}
      </svg>
      <div className="vt-legend">
        <span><i className="vt-ok" />Finished</span><span><i className="vt-bad" />Failed</span>
        <span><i className="vt-extra-key" />Given back or message read: not counted</span>
        <span className="grow" />
        {!showCap && <span>daily limit {cap}, well above this week</span>}
        <span>{r.at_once === 1 ? "1 run at a time" : `${r.at_once} at once`} · typical {minutes(r.p50_s)} · slowest 5% {minutes(r.p95_s)} · stops at {minutes(r.timeout_s)}</span>
      </div>
    </section>
  );
}

// ── shared, and you ────────────────────────────────────────────────────────────

const WINDOW_NAME: Record<string, string> = { five_hour: "5-hour window", seven_day: "Weekly window" };

/** The Claude plan every subscription project on it draws from. A neighbour
    filling the five-hour window makes this project look broken when it is
    not, and nothing inside one project could show that. Projects the reader
    cannot open are summed, never named. */
export function SharedPlan({ vitals }: { vitals: VitalsView }) {
  const p = vitals.plan;
  if (!p) return null;
  const tone = p.status === "rejected" ? "bad" : p.windows.some((w) => (w.utilization ?? 0) >= 0.8 && !w.expired) ? "warn" : "good";
  const word = tone === "bad" ? "Full" : tone === "warn" ? "Busy" : "Room left";
  const slices = [...p.projects.map((x) => ({ key: x.project_id, name: x.this ? `${x.name} (this)` : x.name, share: x.share, mine: x.this })),
    ...(p.others.count ? [{ key: "others", name: `${p.others.count} other${p.others.count === 1 ? "" : "s"}`, share: p.others.share, mine: false }] : [])]
    .filter((s) => s.share > 0);
  return (
    <section className="card ov-card">
      <div className="card-h"><h3>Shared Claude plan</h3><span className="grow" /><span className={`vt-tag ${tone}`}>{word}</span></div>
      {!p.windows.length ? <p className="help">No reading yet. It arrives with the next run on this plan.</p> : (
        <div className="vt-windows">
          {p.windows.map((w) => {
            const share = Math.round((w.utilization ?? 0) * 100);
            return (
              <div key={w.key}>
                <div className="vt-kv"><span>{WINDOW_NAME[w.key] ?? w.label ?? w.key}</span><b>{w.expired ? "reset" : `${share}%`}</b></div>
                <span className="vt-mini big"><span className={share >= 100 ? "bad" : share >= 80 ? "warn" : ""} style={{ width: `${w.expired ? 0 : Math.min(share, 100)}%` }} /></span>
                {w.resets_at && <span className="help">{w.expired ? "Reset since the last reading" : `Resets ${fmtRoll(w.resets_at)}`}</span>}
              </div>
            );
          })}
        </div>
      )}
      {slices.length > 1 && (
        <>
          <span className="vt-k" style={{ marginTop: 12 }}>Who used it, last 24 hours</span>
          <span className="vt-stack">{slices.map((s, i) => <i key={s.key} className={s.mine ? "mine" : `c${(i % 4) + 1}`} style={{ flex: s.share }} />)}</span>
          <div className="vt-legend">{slices.map((s, i) => <span key={s.key}><i className={s.mine ? "mine" : `c${(i % 4) + 1}`} />{s.name} {Math.round(s.share * 100)}%</span>)}</div>
        </>
      )}
      {p.refused_7d > 0 && <p className="help">{p.refused_7d} run{p.refused_7d === 1 ? " was" : "s were"} turned away by Claude here this week.</p>}
    </section>
  );
}

/** Each curated memory against its cap. At the cap new notes are refused
    and nothing else says so; amber from the project's own warning ratio. */
export function MemoryCard({ dash, base }: { dash: Dashboard; base: string }) {
  const m = dash.memory;
  if (!m?.scopes.length) return null;
  return (
    <section className="card ov-card">
      <div className="card-h"><h3>Team memory</h3><span className="help">notes kept, of the limit</span><span className="grow" />
        <Link className="card-link" to={`${base}/details/activity/memory`}>See notes <Icon name="arrow" size={13} /></Link></div>
      <div className="vt-windows">
        {m.scopes.map((s) => {
          const f = s.cap ? s.live / s.cap : 0;
          const cls = f >= 1 ? "bad" : f >= m.warn_ratio ? "warn" : "";
          return (
            <div key={`${s.scope}:${s.team_id ?? ""}`}>
              <div className="vt-kv"><span>{s.name}</span><b className={cls ? `vt-${cls}-t` : ""}>{s.live} / {s.cap}</b></div>
              <span className="vt-mini"><span className={cls} style={{ width: `${Math.min(100, f * 100)}%` }} /></span>
              {f >= 1 && <span className="help">Full: new notes are refused until it is tidied.</span>}
            </div>
          );
        })}
      </div>
    </section>
  );
}

/** The chairman as a limit. Nothing here is a cap the project hits; all of it
    is work that waited on a person. */
export function YouPanel({ vitals, inline }: { vitals: VitalsView; inline?: boolean }) {
  const y = vitals.you;
  if (inline) {
    // Nothing is waiting on you, so the counts ride on one line rather than
    // a panel of zeros beside an empty box.
    return (
      <span className="vt-you-inline">
        <span>{y.open_asks} of {y.ask_cap} questions open</span>
        <span className={y.approvals_expired_30d ? "vt-bad-t" : ""}>{y.approvals_expired_30d} approvals expired</span>
        <span>{y.asks_lapsed_30d} questions timed out</span>
        <span className="help">last 30 days</span>
      </span>
    );
  }
  return (
    <aside className="vt-you" aria-label="Waiting on you">
      <span className="vt-k">You, last 30 days</span>
      <div>
        <div className="vt-kv"><span>Open questions</span><b>{y.open_asks} of {y.ask_cap}</b></div>
        <span className="vt-pips">{Array.from({ length: y.ask_cap }, (_, i) => <i key={i} className={i < y.open_asks ? "on" : ""} />)}</span>
        {y.open_asks >= y.ask_cap && <span className="help">Full: the CEO can't ask anything new until you answer.</span>}
      </div>
      <div className="vt-kv"><span>Approvals that expired</span><b className={y.approvals_expired_30d ? "vt-bad-t" : ""}>{y.approvals_expired_30d}</b></div>
      <div className="vt-kv"><span>Questions that timed out</span><b>{y.asks_lapsed_30d}</b></div>
      <div className="vt-kv"><span>Typical time to decide</span><b>{y.reply_hours_median === null ? "—" : y.reply_hours_median < 1 ? "under 1 h" : `${Math.round(y.reply_hours_median)} h`}</b></div>
      {y.overruns_unanswered > 0 && <div className="vt-kv"><span>Overruns not answered</span><b className="vt-warn-t">{y.overruns_unanswered}</b></div>}
    </aside>
  );
}

// ── the week ───────────────────────────────────────────────────────────────────

/** The week on lanes, as the mockup drew it: what is scheduled, when the
    daily runs refill, the stretch the project is paused for, and now. Names
    for the next few things sit under it. */
export function WeekTimeline({ agenda, vitals, pausedUntil }: {
  agenda: AgendaView; vitals: VitalsView | null; pausedUntil: string | null;
}) {
  const days = agenda.day_list.slice(0, 7);
  if (!days.length) return null;
  const start = new Date(`${days[0]}T00:00:00`).getTime();
  const span = 7 * DAY_MS;
  const W = 700, L = 4, R = 696, H = 112;
  const x = (t: number) => L + Math.min(1, Math.max(0, (t - start) / span)) * (R - L);
  const now = Date.now();
  const inWeek = (t: number) => t >= start && t < start + span;
  const events = agenda.events.filter((e) => inWeek(new Date(e.at).getTime()));
  const resets: number[] = [];
  if (vitals) for (let t = new Date(vitals.today.resets_at).getTime() - DAY_MS * 7; t < start + span; t += DAY_MS) {
    if (inWeek(t)) resets.push(t);
  }
  const lanes = [["Scheduled", 40], ["Runs refill", 74]] as const;
  const upcoming = events.filter((e) => new Date(e.at).getTime() >= now).slice(0, 3);
  return (
    <>
      <svg className="vt-chart vt-weekchart" viewBox={`0 0 ${W} ${H}`} role="img"
        aria-label={`${events.length} things scheduled this week`}>
        {days.map((d, i) => {
          const dx = x(start + i * DAY_MS);
          const isToday = now >= start + i * DAY_MS && now < start + (i + 1) * DAY_MS;
          return (
            <g key={d}>
              <line className="vt-grid" x1={dx} x2={dx} y1={18} y2={92} />
              <text x={dx + 4} y={12} className={isToday ? "vt-now" : ""}>
                {new Date(`${d}T12:00:00`).toLocaleDateString(undefined, { weekday: "short", day: "numeric" })}
              </text>
            </g>
          );
        })}
        {lanes.map(([name, ly]) => <line key={name} className="vt-lane" x1={L} x2={R} y1={ly} y2={ly} />)}
        {events.map((e, i) => {
          const ex = x(new Date(e.at).getTime());
          return <circle key={`${e.at}-${i}`} className={`vt-ev ag-${e.tone}`} cx={ex} cy={40} r={6}>
            <title>{`${fmtRoll(e.at)} — ${e.label}`}</title></circle>;
        })}
        {resets.map((t) => {
          const rx = x(t);
          return <rect key={t} className="vt-reset" x={rx - 4} y={70} width={8} height={8} transform={`rotate(45 ${rx} 74)`} />;
        })}
        {pausedUntil && inWeek(now) && new Date(pausedUntil).getTime() > now && (
          <g>
            <rect className="vt-paused" x={x(now)} y={84} width={Math.max(3, x(new Date(pausedUntil).getTime()) - x(now))} height={6} />
            <text x={x(new Date(pausedUntil).getTime()) + 4} y={91} className="vt-paused-t">paused</text>
          </g>
        )}
        {inWeek(now) && <g>
          <line className="vt-nowline" x1={x(now)} x2={x(now)} y1={18} y2={100} />
          <text x={x(now) + 3} y={H - 2} className="vt-nowtext">now</text>
        </g>}
      </svg>
      <div className="vt-legend">
        <span><i className="vt-ev-key" />Scheduled</span><span><i className="vt-reset-key" />Runs refill</span>
        {pausedUntil && <span><i className="h3" />Paused</span>}
      </div>
      {upcoming.length > 0 && (
        <ul className="vt-next">
          {upcoming.map((e, i) => (
            <li key={`${e.at}-${i}`}><b>{new Date(e.at).toLocaleDateString(undefined, { weekday: "short", day: "numeric" })}</b> {e.label}</li>
          ))}
        </ul>
      )}
    </>
  );
}
