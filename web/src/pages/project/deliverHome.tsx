import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AgendaView, get, PlanRequirement, requirementName, VitalsView } from "../../api";
import Agenda from "../../agenda";
import { usePoll } from "../../hooks";
import { firstName, ProjectCtx } from "../../projectCtx";
import { fmtUnit } from "../../ui";
import { Icon } from "../../ui/Icon";
import { chairmanDecides, deliveryVerdict } from "../../ui/project";
import "./deliverHome.css";

/** A Deliver project's home, as the approved mockup draws it: how close it is
    to done first, then each goal as a row of a checklist, then goals done
    over time against the date. A Deliver chairman's first question is "are
    we nearly there?", and the old home answered it in the fourth tile of a
    row that the window cut off. The numbers that answer "how busy was the
    team?" are the Runs page's. */

const DAY_MS = 86_400_000;

type GoalState = "done" | "you" | "work" | "todo";

export interface Goal {
  r: PlanRequirement;
  key: string;
  name: string;
  state: GoalState;
  /** Met by the platform: counts toward done even while it waits on your OK. */
  met: boolean;
  /** The CEO's estimate, or the average of the others when it gave none, so a
      goal without one still takes room in the bar. */
  weight: number;
  holders: string[];
  /** Goals it waits on that are not met yet. */
  after: string[];
}

export interface Goals {
  list: Goal[];
  done: number;
  working: number;
  pct: number;
  runsLeft: number;
  estimated: boolean;
  /** Running, nothing in progress, nothing ready, and a goal left open. */
  stalled: boolean;
  allMet: boolean;
  idleSince: string | null;
}

export function goalsOf(ctx: ProjectCtx, verdicts: Set<string>): Goals {
  const { dash, plan } = ctx;
  const scopeYours = chairmanDecides(dash, "plan_authority");
  const reqs = (plan?.requirements ?? []).filter((r) => r.status !== "cut");
  const known = reqs.map((r) => r.est_runs).filter((v): v is number => v !== null && v > 0);
  const avg = known.length ? known.reduce((a, b) => a + b, 0) / known.length : 1;
  const metKeys = new Set(reqs.filter((r) => r.status === "met").map((r) => r.key));
  const list: Goal[] = reqs.map((r) => {
    const met = r.status === "met";
    const state: GoalState = met ? (verdicts.has(r.key) ? "you" : "done")
      : r.status === "proposed" && scopeYours ? "you"
        : r.status === "in_progress" || r.status === "checking" ? "work" : "todo";
    const holders = [...new Set(r.tasks.filter((t) => t.holder && !["done", "killed"].includes(t.status))
      .map((t) => firstName(t.holder!)))];
    return {
      r, key: r.key, name: requirementName(r), state, met, holders,
      weight: r.est_runs && r.est_runs > 0 ? r.est_runs : avg,
      after: r.depends_on.filter((k) => !metKeys.has(k)),
    };
  });
  const total = list.reduce((s, g) => s + g.weight, 0);
  const doneW = list.filter((g) => g.met).reduce((s, g) => s + g.weight, 0);
  const runsLeft = Math.round(list.filter((g) => !g.met)
    .reduce((s, g) => s + Math.max(0, g.weight - g.r.spent.runs), 0));
  const a = dash.activity;
  const working = list.filter((g) => g.state === "work").length;
  const open = list.filter((g) => g.state === "todo").length;
  const stalled = dash.status === "running" && open > 0 && working === 0
    && dash.runs.running === 0 && a.blocked_by === null
    && a.waiting.claimable_tasks === 0 && a.waiting.gated_tasks === 0;
  const finished = (ctx.runs ?? []).map((r) => r.finished_at).filter((t): t is string => !!t).sort();
  return {
    list, working, runsLeft, stalled,
    done: list.filter((g) => g.met).length,
    pct: total ? Math.round((doneW / total) * 100) : 0,
    estimated: known.length > 0,
    allMet: list.length > 0 && list.every((g) => g.met),
    idleSince: finished.length ? finished[finished.length - 1] : null,
  };
}

const weekday = (iso: string) => new Date(iso).toLocaleDateString(undefined, { weekday: "short" });
const fmtDay = (iso: string) =>
  new Date(iso).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
const shortDay = (iso: string) => new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
const clock = (iso: string) => new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
/** "Wed 4:12 pm" inside the week, "Nov 18" beyond it. */
const since = (iso: string) => Date.now() - new Date(iso).getTime() < 6 * DAY_MS
  ? `${weekday(iso)} ${clock(iso)}` : shortDay(iso);
const list = (keys: string[]) =>
  keys.length <= 1 ? keys.join("") : `${keys.slice(0, -1).join(", ")} and ${keys[keys.length - 1]}`;
const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

// ── how close to done ──────────────────────────────────────────────────────────

export function DeliverHero({ ctx, goals }: { ctx: ProjectCtx; goals: Goals }) {
  const { dash, plan, projectId } = ctx;
  const navigate = useNavigate();
  const base = `/projects/${projectId}`;
  const ceo = firstName(ctx.ceoName);
  const g = goals;
  const c = plan?.commitment ?? null;
  const n = g.list.length;

  if (!n) {
    return (
      <section className="card dh-hero" aria-labelledby="dh-prog">
        <div className="dh-top">
          <div className="dh-pct-col">
            <h2 id="dh-prog" className="vt-k">How close to done</h2>
            <div className="dh-pct"><span className="dh-big dh-none">No plan yet</span></div>
            <span className="help">{ceo} is writing it. You'll approve it here.</span>
          </div>
        </div>
      </section>
    );
  }

  // ── expected finish ──
  let when: string;
  let whenSub: string;
  const lastMet = g.list.map((x) => x.r.met_at).filter((t): t is string => !!t).sort().pop() ?? null;
  if (dash.status === "completed") {
    when = "Delivered";
    whenSub = lastMet ? `Finished ${fmtDay(lastMet)}` : "Your results are final";
  } else if (g.allMet) {
    const today = lastMet && new Date(lastMet).toDateString() === new Date().toDateString();
    when = !lastMet || today ? "Today" : fmtDay(lastMet);
    const early = c && lastMet ? Math.round((new Date(c.deliver_by).getTime() - new Date(lastMet).getTime()) / DAY_MS) : null;
    whenSub = early === null ? "Every goal is done"
      : early > 0 ? `${early} ${plural(early, "day", "days")} before the ${shortDay(c!.deliver_by)} date`
        : early === 0 ? `On the ${shortDay(c!.deliver_by)} date` : `${-early} ${plural(-early, "day", "days")} after the ${shortDay(c!.deliver_by)} date`;
  } else if (c && c.status === "proposed") {
    when = fmtDay(c.deliver_by);
    whenSub = `${ceo} proposed this date. Confirm it in your decisions.`;
  } else if (c && c.projected_done_at) {
    when = fmtDay(c.projected_done_at);
    whenSub = `At this pace · you agreed to ${fmtDay(c.deliver_by)}`;
  } else if (c) {
    when = fmtDay(c.deliver_by);
    whenSub = "The date you agreed to · too early to project";
  } else {
    when = "No date yet";
    whenSub = "The CEO hasn't proposed one";
  }

  // ── what is happening, in one sentence ──
  const verdict = deliveryVerdict(dash, plan);
  const todo = g.list.filter((x) => x.state === "todo");
  const work = g.list.filter((x) => x.state === "work");
  let next: { tone: "warn" | "ok"; lead: string; text: string; act?: { label: string; to?: string; draft?: string } } | null = null;
  if (dash.status === "completed" || dash.status === "stopped" || verdict.daysLate !== null) {
    next = null;
  } else if (g.stalled) {
    const keys = todo.map((x) => x.key);
    const one = todo.length === 1;
    next = {
      tone: "warn", lead: "Nothing is moving.",
      text: `${one ? `${todo[0].key}, ${todo[0].name.replace(/\.$/, "")},` : list(keys)} ${one ? "hasn't" : "haven't"} started and no one has picked ${one ? "it" : "them"} up.`
        + (g.idleSince ? ` The team has been idle since ${since(g.idleSince)}.` : ""),
      act: { label: `Ask the CEO to start ${one ? "it" : "them"}`,
        draft: `Nothing has been worked on since ${g.idleSince ? since(g.idleSince) : "a while"}. Please start ${list(keys)}${one ? `: ${todo[0].name}` : ""}.` },
    };
  } else if (g.allMet) {
    next = g.list.some((x) => x.state === "you") || (dash.delivery_outstanding?.length === 0)
      ? { tone: "ok", lead: "Everything is done.",
          text: "Check the results and say if they're what you wanted. The project finishes when you accept them.",
          act: { label: "Review the results", to: `${base}/results` } }
      : { tone: "ok", lead: "Everything is done.", text: "" };
  } else if (work.length) {
    const who = [...new Set(work.flatMap((x) => x.holders))];
    const later = todo.filter((x) => x.after.length > 0).map((x) => x.key);
    const free = todo.filter((x) => x.after.length === 0).map((x) => x.key);
    next = {
      tone: "ok", lead: "On track.",
      text: `${who.length ? `${list(who)} ${plural(who.length, "is", "are")} working on` : "Work is under way on"} ${list(work.map((x) => x.key))}.`
        + (later.length ? ` ${list(later)} ${plural(later.length, "starts", "start")} after ${plural(work.length, "it", "them")}.` : "")
        + (free.length ? ` ${list(free)} ${plural(free.length, "hasn't", "haven't")} started yet.` : ""),
    };
  } else if (todo.length && dash.status === "running") {
    next = { tone: "ok", lead: "On track.", text: `Work for ${list(todo.map((x) => x.key))} is lined up and starts when a run is free.` };
  }

  const stuck = g.stalled ? new Set(todo.map((x) => x.key)) : new Set<string>();
  const seg = (x: Goal) => x.state === "you" && x.met ? ["you", " done · needs your OK"]
    : x.met ? ["done", ""] : x.state === "work" ? ["doing", " in progress"]
      : stuck.has(x.key) ? ["stuck", " not started"] : ["", ""];

  return (
    <section className="card dh-hero" aria-labelledby="dh-prog">
      <div className="dh-top">
        <div className="dh-pct-col">
          <h2 id="dh-prog" className="vt-k">How close to done</h2>
          <div className="dh-pct">
            <span className="dh-big">{g.pct}%</span>
            <span className="dh-of">
              <b>{g.done} of {n} {plural(n, "goal", "goals")}</b> done
              {g.allMet ? " and checked" : <>
                {g.working > 0 && ` · ${g.working} being worked on`}
                {g.estimated && g.runsLeft > 0 && ` · about ${g.runsLeft} ${plural(g.runsLeft, "run", "runs")} of work left`}
              </>}
            </span>
          </div>
        </div>
        <div className="dh-finish">
          <span className="vt-k">Expected finish</span>
          <span className="dh-when">{when}</span>
          <span className="help">{whenSub}</span>
        </div>
      </div>
      <div className="dh-workbar" role="img"
        aria-label={g.list.map((x) => `${x.key} ${x.met ? "done" : x.state === "work" ? "in progress" : "not started"}`).join(", ")}>
        {g.list.map((x) => {
          const [cls, word] = seg(x);
          return <span key={x.key} className={cls} style={{ flex: x.weight }} title={`${x.key} · ${x.name}`}>
            <span className="dh-seg-key">{x.key}</span>{word && <span className="dh-seg-word">{word}</span>}</span>;
        })}
      </div>
      <div className="dh-scale">
        <span>Start · {shortDay(dash.created_at)}</span>
        <span>Each block is sized by how much work the goal takes</span>
        <span>Done</span>
      </div>
      {next && (
        <div className={`dh-next ${next.tone}`}>
          <span className="dh-next-ic" aria-hidden="true">{next.tone === "warn" ? "!" : "✓"}</span>
          <p><b>{next.lead}</b>{next.text && ` ${next.text}`}</p>
          {next.act && dash.can_steer !== false && (next.act.to
            ? <Link className="btnlink primary" to={next.act.to}>{next.act.label}</Link>
            : <button className="primary" onClick={() => navigate(`${base}/chat`, { state: { draft: next!.act!.draft } })}>
                {next.act.label}</button>)}
        </div>
      )}
    </section>
  );
}

// ── the checklist ──────────────────────────────────────────────────────────────

export function GoalChecklist({ ctx, goals }: { ctx: ProjectCtx; goals: Goals }) {
  const base = `/projects/${ctx.projectId}`;
  const ceo = firstName(ctx.ceoName);
  const lastMet = goals.list.map((x) => x.r.met_at).filter((t): t is string => !!t).sort().pop() ?? null;
  if (!goals.list.length) return null;
  return (
    <section className="card ov-card" aria-labelledby="dh-goals">
      <div className="card-h"><h3 id="dh-goals">What has to be true at the end</h3><span className="grow" />
        <Link className="card-link" to={`${base}/details/roadmap`}>The plan <Icon name="arrow" size={13} /></Link></div>
      <ul className="dh-goals">
        {goals.list.map((g) => {
          const r = g.r;
          const est = r.est_runs;
          const spent = r.spent.runs;
          const stuck = goals.stalled && g.state === "todo";
          let why: string;
          let pill: [string, string];
          if (g.state === "you" && g.met) {
            why = "Done and checked by yaaf · waiting for your OK";
            pill = ["you", "Needs your OK"];
          } else if (g.state === "you") {
            why = `Proposed by ${ceo} · waiting for your OK`;
            pill = ["you", "Needs your OK"];
          } else if (g.met) {
            why = r.met_at ? `Checked by yaaf ${shortDay(r.met_at)}` : "Checked by yaaf";
            pill = ["done", "Done"];
          } else if (r.status === "checking") {
            why = "yaaf is checking it";
            pill = ["work", "Checking"];
          } else if (g.state === "work") {
            why = (g.holders.length ? `${list(g.holders)} ${plural(g.holders.length, "is", "are")} on it` : "In progress")
              + (est ? ` · ${spent} of ${est} runs used` : "");
            pill = ["work", "Working"];
          } else if (g.after.length) {
            why = `Starts after ${list(g.after)}`;
            pill = ["", "Not started"];
          } else if (stuck) {
            why = `Not started. No one has picked it up.${lastMet ? ` Waiting since ${weekday(lastMet)}` : ""}`;
            pill = ["todo", "Not started"];
          } else {
            const queued = r.tasks.filter((t) => !["done", "killed"].includes(t.status)).length;
            why = queued ? `${queued} ${plural(queued, "task", "tasks")} lined up` : "Not started";
            pill = ["", "Not started"];
          }
          const size = est
            ? g.met ? `${est} runs · took ${spent}` : g.state === "work" ? `${est} runs · ${spent} used`
              : stuck ? `${est} runs · ${spent ? `${spent} used` : "none used"}` : `${est} runs`
            : spent ? `${spent} runs used` : "Not estimated";
          const fill = g.met ? 100 : est ? Math.min(100, (spent / est) * 100) : 0;
          return (
            <li key={r.id} className={`dh-goal ${stuck ? "stuck" : g.met && g.state !== "you" ? "done" : g.state}`}>
              <span className="dh-tick" aria-hidden="true">{g.met && g.state !== "you" ? "✓" : g.state === "you" ? "?" : ""}</span>
              <div className="dh-goal-main">
                <span className="dh-goal-t"><span className="dh-key">{r.key}</span>{g.name}</span>
                <span className="dh-why">{why}</span>
              </div>
              <span className={`dh-pill ${pill[0]}`}>{pill[1]}</span>
              <div className="dh-size">
                <span>{size}</span>
                <span className="dh-bar"><span className={g.met ? "done" : est && spent > est ? "over" : ""} style={{ width: `${fill}%` }} /></span>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

// ── goals done over time ───────────────────────────────────────────────────────

export function GoalsOverTime({ ctx, goals }: { ctx: ProjectCtx; goals: Goals }) {
  const { dash, plan } = ctx;
  const n = goals.list.length;
  if (!n) return null;
  const c = plan?.commitment ?? null;
  const agreed = c && c.status !== "proposed" ? new Date(c.deliver_by).getTime() : null;
  const projected = c?.projected_done_at && !goals.allMet && !goals.stalled ? new Date(c.projected_done_at).getTime() : null;
  const now = Date.now();
  const start = new Date(dash.created_at).getTime();
  const end = Math.max(agreed ?? 0, projected ?? 0, now + 2 * DAY_MS) + DAY_MS / 2;
  const W = 640, H = 196, L = 40, R = 624, top = 22, floor = 166;
  const x = (t: number) => L + ((t - start) / Math.max(end - start, 1)) * (R - L);
  const y = (v: number) => floor - (v / n) * (floor - top);
  const met = goals.list.map((g) => g.r.met_at).filter((t): t is string => !!t)
    .map((t) => new Date(t).getTime()).sort((a, b) => a - b);
  const pts: [number, number][] = [[x(start), y(0)]];
  met.forEach((t, i) => { pts.push([x(t), y(i)]); pts.push([x(t), y(i + 1)]); });
  pts.push([x(now), y(met.length)]);
  const line = pts.map(([px, py]) => `${px.toFixed(1)},${py.toFixed(1)}`).join(" ");
  const ticks = n <= 8 ? Array.from({ length: n + 1 }, (_, i) => i) : [0, Math.round(n / 2), n];
  const days = Math.max(1, Math.round((end - start) / DAY_MS));
  const every = Math.max(1, Math.ceil(days / 6));
  const xticks: number[] = [];
  const first = new Date(start); first.setHours(12, 0, 0, 0);
  for (let t = first.getTime(); t <= end; t += every * DAY_MS) xticks.push(t);
  // The month is named on the first tick and wherever it changes.
  const label = (t: number, i: number) => {
    const d = new Date(t);
    const prev = i > 0 ? new Date(xticks[i - 1]) : null;
    return !prev || prev.getMonth() !== d.getMonth()
      ? d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) : String(d.getDate());
  };
  const near = (t: number) => Math.abs(x(t) - x(now)) < 22;
  const lastMet = met.length ? met[met.length - 1] : null;
  const stuckKeys = goals.list.filter((g) => g.state === "todo").map((g) => g.key);
  return (
    <section className="card ov-card" aria-labelledby="dh-burn">
      <div className="card-h"><h3 id="dh-burn">Goals done over time</h3><span className="grow" />
        <span className="help">since {shortDay(dash.created_at)}</span></div>
      <div className="dh-chartwrap">
        <svg className="vt-chart dh-burn" viewBox={`0 0 ${W} ${H}`} role="img"
          aria-label={`${met.length} of ${n} goals done${projected ? `; at this pace all by ${shortDay(new Date(projected).toISOString())}` : ""}`}>
          {ticks.map((v) => <g key={v}>
            <line className="vt-grid" x1={L} x2={R} y1={y(v)} y2={y(v)} />
            <text x={L - 8} y={y(v) + 4} textAnchor="end">{v}</text>
          </g>)}
          {xticks.map((t, i) => near(t) ? null : <text key={t} x={x(t)} y={H - 6} textAnchor="middle">{label(t, i)}</text>)}
          <text x={x(now)} y={H - 6} textAnchor="middle" className="vt-now">{new Date(now).getDate()}</text>
          {/* Each label sits on the side of its line away from the other, so
              a date a day or two from today never prints over "today". */}
          {agreed !== null && <g>
            <line className="dh-agreed" x1={x(agreed)} x2={x(agreed)} y1={top - 8} y2={floor} />
            <text x={x(agreed) + (agreed > now ? 4 : -4)} y={top - 10} textAnchor={agreed > now ? "start" : "end"}
              className="dh-agreed-t">You agreed: {shortDay(c!.deliver_by)}</text>
          </g>}
          <line className="dh-today" x1={x(now)} x2={x(now)} y1={top - 8} y2={floor} />
          <text x={x(now) + (agreed !== null && agreed > now ? -4 : 4)} y={top - 10}
            textAnchor={agreed !== null && agreed > now ? "end" : "start"} className="dh-today-t">today</text>
          <polyline className="dh-line" points={line} />
          {goals.stalled && <>
            <line className="dh-flat" x1={x(now)} x2={R} y1={y(met.length)} y2={y(met.length)} />
            <text x={x(now) + 8} y={y(met.length) - 8} className="dh-flat-t">
              {lastMet ? `flat since ${weekday(new Date(lastMet).toISOString())}` : "flat"}: {list(stuckKeys)} {plural(stuckKeys.length, "hasn't", "haven't")} started
            </text>
          </>}
          {projected !== null && <>
            <line className="dh-proj" x1={x(now)} y1={y(met.length)} x2={x(projected)} y2={y(n)} />
            <circle className="dh-proj-pt" cx={x(projected)} cy={y(n)} r={4} />
            <text x={x(projected) - 8} y={y(n) + 16} textAnchor="end" className="dh-proj-t">at this pace: {shortDay(c!.projected_done_at!)}</text>
          </>}
          <circle className="dh-pt" cx={x(now)} cy={y(met.length)} r={4} />
        </svg>
      </div>
      <div className="vt-legend">
        <span><i className="dh-key-done" />Goals done (checked by yaaf)</span>
        {projected !== null && <span><i className="dh-key-proj" />At the current pace</span>}
        <span><i className="dh-key-today" />Today</span>
      </div>
    </section>
  );
}

// ── the numbers, small ─────────────────────────────────────────────────────────

export function DeliverNumbers({ ctx, goals, vitals }: { ctx: ProjectCtx; goals: Goals; vitals: VitalsView | null }) {
  const base = `/projects/${ctx.projectId}`;
  if (!vitals) return null;
  const s = vitals.spend;
  const t = vitals.today;
  const tag = s.spent > s.cap ? ["bad", "Over budget"] : s.projected > s.cap ? ["warn", "Over at this pace"]
    : s.spent >= s.warn_at ? ["warn", "Past the warning"] : ["good", "On pace"];
  const out = t.used >= t.wall;
  const perRun = ctx.plan?.plan?.arithmetic.budget.usd_per_run ?? null;
  const done = ctx.dash.status === "completed" || goals.allMet;
  const totalRuns = goals.list.reduce((sum, g) => sum + g.r.spent.runs, 0);
  const totalUsd = goals.list.reduce((sum, g) => sum + g.r.spent.usd, 0);
  const left = s.cap - s.spent;
  const cost = perRun !== null ? goals.runsLeft * perRun : null;
  // Hours a limit held the project in the last two weeks: one line when it
  // happened, nothing when it did not. The grid is still on the Runs page.
  const held = vitals.limits.rows.map((r) => ({ r, h: r.hours.reduce((a, b) => a + b, 0) }))
    .filter((x) => x.h > 0).sort((a, b) => b.h - a.h);
  const heldH = Math.round(held.reduce((a, b) => a + b.h, 0));
  return (
    <section className="card ov-card dh-nums" aria-label="Numbers">
      <Link className="dh-num" to={`${base}/details/spend`}>
        <span className="vt-k">{s.unit === "usd" ? "Spending" : "Tokens"} this month</span>
        <span className="dh-v">{fmtUnit(s.spent, s.unit)} <small>of {fmtUnit(s.cap, s.unit)}</small> <span className={`vt-tag ${tag[0]}`}>{tag[1]}</span></span>
        <span className="dh-meter"><span className={tag[0] === "good" ? "" : tag[0]} style={{ width: `${Math.min(100, (s.spent / Math.max(s.cap, 1e-9)) * 100)}%` }} /></span>
        <span className="help">Ends near {fmtUnit(s.projected, s.unit)} at this pace</span>
      </Link>
      <Link className="dh-num" to={`${base}/details/activity/runs`}>
        <span className="vt-k">Runs today</span>
        <span className="dh-v">{t.used} <small>of {t.wall}</small> <span className={`vt-tag ${out ? "warn" : "good"}`}>{out ? "Used up" : `${t.wall - t.used} left`}</span></span>
        <span className="dh-meter"><span className={out ? "warn" : ""} style={{ width: `${Math.min(100, (t.used / Math.max(t.wall, 1)) * 100)}%` }} /></span>
        <span className="help">Resets {clock(t.resets_at)}</span>
      </Link>
      {goals.list.length > 0 && (
        <div className="dh-num">
          <span className="vt-k">Cost to finish</span>
          {done ? (
            <>
              <span className="dh-v">{s.unit === "usd" ? `$${totalUsd.toFixed(2)}` : `${totalRuns}`} <small>{s.unit === "usd" ? `total, ${totalRuns} runs` : "runs in total"}</small></span>
              <span className="help">What the whole project cost</span>
            </>
          ) : cost !== null && s.unit === "usd" ? (
            <>
              <span className="dh-v">≈ ${cost.toFixed(2)} <small>{goals.runsLeft} {plural(goals.runsLeft, "run", "runs")}</small></span>
              <span className="help">{cost <= left * 0.25 ? "Fits your budget easily" : cost <= left ? "Fits what is left this month" : "More than is left this month"}</span>
            </>
          ) : (
            <>
              <span className="dh-v">{goals.runsLeft} <small>{plural(goals.runsLeft, "run", "runs")} left</small></span>
              <span className="help">{goals.estimated ? "Not enough runs yet to price them" : "Your CEO hasn't estimated the goals yet"}</span>
            </>
          )}
        </div>
      )}
      {heldH > 0 && (
        <p className="dh-held">
          Limits held work back about <b>{heldH} {plural(heldH, "hour", "hours")}</b> in the last two weeks, most at "{held[0].r.label.toLowerCase()}".{" "}
          <Link to={`${base}/settings?s=rules&t=${encodeURIComponent("Pace & brakes")}`}>Limits →</Link>
        </p>
      )}
    </section>
  );
}

// ── coming up ──────────────────────────────────────────────────────────────────

/** Dated events only. The daily refill happens every day, so it is not news. */
export function DeliverComingUp({ projectId }: { projectId: string }) {
  const [full, setFull] = useState(false);
  const { data } = usePoll(() => get<AgendaView>(`/observe/projects/${projectId}/agenda?days=14`), 60000, [projectId]);
  const now = Date.now();
  const thisMonth = new Date().getMonth();
  const events = (data?.events ?? []).filter((e) => new Date(e.at).getTime() >= now).slice(0, 5);
  const when = (iso: string) => {
    const d = new Date(iso);
    return d.getMonth() === thisMonth
      ? d.toLocaleDateString(undefined, { weekday: "short", day: "numeric" })
      : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  };
  const said = (kind: string, label: string) => kind === "deliver_by" ? "Delivery date you agreed to" : label;
  return (
    <section className="card ov-card" aria-labelledby="dh-dates">
      <div className="card-h"><h3 id="dh-dates">Coming up</h3><span className="grow" />
        <button className="card-link" onClick={() => setFull(!full)} aria-expanded={full}>
          {full ? "Hide calendar" : <>Calendar <Icon name="arrow" size={13} /></>}</button></div>
      {full ? <Agenda projectId={projectId} /> : !data ? <p className="help">Loading…</p> : events.length ? (
        <ul className="dh-dates">
          {events.map((e, i) => <li key={`${e.at}-${i}`}><b>{when(e.at)}</b><span>{said(e.kind, e.label)}</span></li>)}
        </ul>
      ) : <p className="help">Nothing dated in the next two weeks.</p>}
    </section>
  );
}
