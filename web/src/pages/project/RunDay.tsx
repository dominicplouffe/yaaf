import { Fragment, ReactNode, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { get, RunDayView } from "../../api";
import { usePoll } from "../../hooks";
import { AsOf, fmtUnit } from "../../ui";
import "./runday.css";

/** Details › Activity › Runs: what used today's runs, who ran them, why they
    woke, whether that is normal, and what went wrong — the approved mockup,
    section for section.

    Every number here is one day's, and it is the limit's day: 00:00 UTC to
    00:00 UTC, said in the reader's own clock at the top. The page this
    replaced counted from the reader's midnight, every kind of run, out of the
    newest fifty, and so told a chairman 29 on a day his Inbox said 40 of 40. */

const time = (iso: string) =>
  new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }).toLowerCase();
const weekdayTime = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });
const length = (s: number | null) =>
  s === null ? "—" : s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`;
const tokens = (n: number) => (n ? fmtUnit(n, "tokens") : "—");
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** One colour per reason, in the order the mockup's legend gives them. */
const REASON_CLASS: Record<string, string> = {
  task: "rd-r-task", msg: "rd-r-msg", plan: "rd-r-plan", notice: "rd-r-notice", review: "rd-r-review", other: "rd-r-other",
};
const REASON_ORDER = ["task", "msg", "plan", "notice", "review", "other"];
const AGENT_COLORS = ["var(--c4)", "var(--c1)", "var(--c2)", "var(--c3)", "var(--c5)"];

type Filter = "all" | "counted" | "given_back" | "early" | "read" | `agent:${string}`;

export default function RunDay({ projectId, detail }: {
  projectId: string;
  /** The trace and context of one run, opened under its row. */
  detail: (runId: string, hasTrace: boolean, hasContext: boolean) => ReactNode;
}) {
  const navigate = useNavigate();
  const { data, error, at } = usePoll(() => get<RunDayView>(`/observe/projects/${projectId}/runs/day`), 15000, [projectId]);
  const [filter, setFilter] = useState<Filter>("all");
  const [showAll, setShowAll] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  if (error && !data) return <div className="error">{error}</div>;
  if (!data) return <p className="help">Loading…</p>;

  const base = `/projects/${projectId}`;
  const chat = (draft: string) => navigate(`${base}/chat`, { state: { draft } });
  const { limit, started, problems } = data;
  const reached = limit.reached_at !== null;
  const color = Object.fromEntries(data.who.map((w, i) => [w.agent_id, AGENT_COLORS[i % AGENT_COLORS.length]]));
  const family = (k: string) => data.families[k]?.label ?? k;
  const ceo = data.who.find((w) => w.kind === "ceo");

  // ── the log's filters ──
  const early = data.runs.filter((r) => r.tone === "bad");
  const matches = (f: Filter) => (r: RunDayView["runs"][number]) =>
    f === "all" ? true : f === "early" ? r.tone === "bad" : f.startsWith("agent:") ? r.agent_id === f.slice(6) : r.lane === f;
  const rows = data.runs.filter(matches(filter));
  const shown = showAll ? rows : rows.slice(0, 12);
  const worth = [problems.given_back.count, problems.ended_early.count, problems.plan_refused.count, problems.timeouts.count]
    .filter((n) => n > 0).length;

  return (
    <div className="rd">
      <header className="rd-head">
        <h1>Runs</h1>
        <p>What used today's runs, who ran them, why they woke, and whether that is normal for this project.</p>
        <span className="rd-day">Today's runs: <b>{weekdayTime(data.day.starts_at)} → {weekdayTime(data.day.ends_at)}</b> your time · the limit resets at 00:00 UTC</span>
      </header>

      {/* ── the one count ── */}
      <section className="rd-panel">
        <div className="rd-count">
          <div>
            <span className="rd-k">Counted against today's limit</span>
            <div className="rd-big">{limit.counted} <small>of {limit.wall}</small></div>
            <span className={`rd-tag ${reached ? "warn" : "good"}`} style={{ display: "inline-block", marginTop: 8 }}>
              {reached ? `Limit reached ${time(limit.reached_at!)}` : `${Math.max(0, limit.wall - limit.counted)} left`}
            </span>
          </div>
          <div style={{ minWidth: 0 }}>
            <span className="rd-k">Every run started today: {started.total}</span>
            <div className="rd-stack" role="img"
              aria-label={`${started.counted} counted, ${started.given_back} given back, ${started.reads} message reads, ${started.checks} checks, ${started.chat} chat`}>
              {started.counted > 0 && <span className="rd-s-counted" style={{ flex: started.counted }}>{started.counted >= 4 ? `${started.counted} counted` : started.counted}</span>}
              {started.given_back > 0 && <span className="rd-s-given" style={{ flex: started.given_back }}>{started.given_back}</span>}
              {started.reads > 0 && <span className="rd-s-reads" style={{ flex: started.reads }}>{started.reads}</span>}
              {started.checks > 0 && <span className="rd-s-chat" style={{ flex: started.checks }} />}
              {started.chat > 0 && <span className="rd-s-chat" style={{ flex: started.chat }} />}
            </div>
            <div className="rd-leg">
              <span><i className="rd-s-counted" /><b>{started.counted}</b> counted: work that changed something</span>
              <span><i className="rd-s-given" /><b>{started.given_back}</b> given back: woke and changed nothing</span>
              <span><i className="rd-s-reads" /><b>{started.reads}</b> message reads: their own allowance, {limit.reads} of {limit.reads_cap}</span>
              {started.checks > 0 && <span><i className="rd-s-chat" /><b>{started.checks}</b> platform checks: never counted</span>}
              <span><i className="rd-s-chat" /><b>{started.chat}</b> chat with you: never counted</span>
            </div>
            <div className="rd-why29">
              Only work that changed something counts against the limit. Every screen counts this same day, from {time(data.day.starts_at)} your
              time, and this same way{limit.queued ? `; ${plural(limit.queued, "run")} waiting to start already count` : ""}.
            </div>
          </div>
        </div>
      </section>

      {/* ── what happened ── */}
      <section className="rd-panel">
        <div className="rd-ph"><h2>What happened</h2>
          <span className={`rd-tag ${data.verdict.includes("×") ? "warn" : "good"}`}>{data.verdict.charAt(0).toUpperCase() + data.verdict.slice(1)}</span>
          <span className="grow" /><span className="help">written from the numbers below</span></div>
        <p className="rd-story">
          {data.story.map((part, i) => part.strong ? <b key={i}>{part.text}</b> : <Fragment key={i}>{part.text}</Fragment>)}
        </p>
        <div className="rd-asks">
          {ceo && (ceo.reasons.plan ?? 0) >= 2 && (
            <button className="rd-ask" onClick={() => chat(`Why ${plural(ceo.reasons.plan, "planning check-in")} today, against a usual ${ceo.usual} runs a day?`)}>
              Ask the CEO: why {plural(ceo.reasons.plan ?? 0, "planning check-in")} today?
            </button>
          )}
          {reached && (
            <button className="rd-ask" onClick={() => chat(`We reached the ${limit.wall}-run limit at ${time(limit.reached_at!)}. What would you cut to fit in ${limit.wall}?`)}>
              Ask the CEO: what would you cut to fit in {limit.wall}?
            </button>
          )}
          <button className="rd-ask" onClick={() => chat("What used today's runs, and was it worth it?")}>
            Ask the CEO: what used today's runs?
          </button>
          {reached && (
            <Link className="rd-ask" to={`${base}/settings?s=rules&t=${encodeURIComponent("Pace & brakes")}`}>Raise the daily limit…</Link>
          )}
        </div>
      </section>

      {/* ── who + why ── */}
      <div className="rd-two">
        <section className="rd-panel">
          <div className="rd-ph"><h2>Who used them</h2><span className="help">{started.counted} counted runs, by why they woke</span></div>
          <WhoBars data={data} />
          <div className="rd-leg" style={{ marginTop: 12 }}>
            {REASON_ORDER.filter((k) => k !== "other" || data.by_reason.other).map((k) => (
              <span key={k}><i className={REASON_CLASS[k]} />{family(k)}</span>
            ))}
          </div>
        </section>
        <section className="rd-panel">
          <div className="rd-ph"><h2>Why they woke</h2><span className="help">counted runs today</span></div>
          {REASON_ORDER.filter((k) => k !== "other" || data.by_reason.other).map((k) => (
            <div className="rd-why-row" key={k}>
              <i className={REASON_CLASS[k]} />
              <div><b>{family(k)}</b><span>{data.families[k]?.means}</span></div>
              <span className="rd-n">{data.by_reason[k] ?? 0}</span>
            </div>
          ))}
        </section>
      </div>

      {/* ── hour by hour ── */}
      <section className="rd-panel">
        <div className="rd-ph"><h2>Today, hour by hour</h2>
          <span className="help">each block is one run · {data.limit.at_once === 1 ? "one run at a time on this project" : `up to ${data.limit.at_once} at once`}</span></div>
        <div className="rd-tl-wrap"><Timeline data={data} /></div>
        <div className="rd-leg">
          <span><i className="rd-s-counted" />Counted</span><span><i className="rd-s-given" />Given back</span>
          <span><i className="rd-s-reads" />Message read</span><span><i style={{ background: "var(--critical)" }} />Ended early</span>
          <span><i style={{ background: "rgba(var(--warning-rgb), 0.35)" }} />No runs left</span>
        </div>
      </section>

      {/* ── normal? ── */}
      <section className="rd-panel">
        <div className="rd-ph"><h2>Is this normal?</h2><span className="help">counted runs per run day, last 14 days</span></div>
        <div className="rd-normal">
          <div>
            <Days data={data} />
            <p className="help" style={{ margin: "6px 0 0" }}>
              Usual day: <b style={{ color: "var(--ink)" }}>{plural(data.usual.counted, "run")}</b> (middle of the last 14).
              {data.usual.counted > 0 && limit.counted >= data.usual.counted * 2 && ` Today is ${Math.round(limit.counted / data.usual.counted)}× that.`}
              {" "}The limit has been reached {data.limit_days === 0 ? "on no day" : data.limit_days === 1 ? "once" : `${data.limit_days} times`} in 14 days{data.limit_days && reached ? ", counting today" : ""}.
            </p>
          </div>
          <div>
            <table className="rd-cmp">
              <thead><tr><th>Who</th><th>Today</th><th>Usual</th></tr></thead>
              <tbody>
                {data.who.map((w) => (
                  <tr key={w.agent_id}><td>{w.name}{w.role && w.kind !== "ceo" ? ` · ${w.role}` : ""}</td>
                    <td className={high(w.counted, w.usual) ? "rd-up" : ""}>{w.counted}</td><td>{w.usual}</td></tr>
                ))}
              </tbody>
            </table>
            <table className="rd-cmp" style={{ marginTop: 14 }}>
              <thead><tr><th>Why</th><th>Today</th><th>Usual</th></tr></thead>
              <tbody>
                {REASON_ORDER.filter((k) => (data.by_reason[k] ?? 0) > 0 || (data.usual.by_reason[k] ?? 0) > 0).map((k) => (
                  <tr key={k}><td>{family(k)}</td>
                    <td className={high(data.by_reason[k] ?? 0, data.usual.by_reason[k] ?? 0) ? "rd-up" : ""}>{data.by_reason[k] ?? 0}</td>
                    <td>{data.usual.by_reason[k] ?? 0}</td></tr>
                ))}
                <tr><td>Given back</td><td className={high(started.given_back, data.usual.given_back) ? "rd-up" : ""}>{started.given_back}</td>
                  <td>{data.usual.given_back}</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      {/* ── anything wrong ── */}
      <section className="rd-panel">
        <div className="rd-ph"><h2>Anything wrong?</h2>
          <span className={`rd-tag ${worth ? "warn" : "good"}`}>{worth ? `${plural(worth, "thing")} worth a look` : "Nothing"}</span></div>
        <div className="rd-prob">
          <span className="rd-n" style={{ color: problems.given_back.count ? "var(--warning)" : "var(--good)" }}>{problems.given_back.count}</span>
          <div><b>Runs given back: woke and changed nothing</b>
            <p>{problems.given_back.count
              ? `${Object.entries(problems.given_back.by_agent).map(([n, c]) => `${n} ${c}`).join(", ")} (${Object.entries(problems.given_back.why).map(([w, c]) => `${c} for ${w.toLowerCase()}`).join(", ")}). They don't count against the limit, but they cost tokens: ${tokens(problems.given_back.tokens)} today.`
              : "Every run today changed something."}</p></div>
          {problems.given_back.count > 0 ? <button className="linkish rd-act" onClick={() => setFilter("given_back")}>See them →</button> : <span />}
        </div>
        <div className="rd-prob">
          <span className="rd-n" style={{ color: problems.ended_early.count ? "var(--critical)" : "var(--good)" }}>{problems.ended_early.count}</span>
          <div><b>Ended early</b>
            <p>{problems.ended_early.count
              ? problems.ended_early.causes.map((c) => `${c.count} ${c.label.toLowerCase()} (${Object.entries(c.agents).map(([n, k]) => k > 1 ? `${n} ${k}` : n).join(", ")})${c.retried_ok ? ", retried and finished" : ""}`).join(". ") + "."
              : "No run stopped before it finished."}</p></div>
          {problems.ended_early.count > 0 ? <button className="linkish rd-act" onClick={() => setFilter("early")}>See them →</button> : <span />}
        </div>
        <div className="rd-prob">
          <span className="rd-n" style={{ color: problems.plan_refused.count ? "var(--critical)" : "var(--good)" }}>{problems.plan_refused.count}</span>
          <div><b>Turned away by Claude</b>
            <p>{!problems.plan_refused.metered ? "This project doesn't run on a Claude plan, so there is no shared window to fill."
              : problems.plan_refused.count
                ? `The shared plan's window was full${problems.plan_refused.peak_five_hour !== null ? `; its 5-hour window peaked at ${Math.round(problems.plan_refused.peak_five_hour * 100)}%` : ""}. Every project on the plan stops until it resets.`
                : problems.plan_refused.peak_five_hour !== null
                  ? `The shared plan had room all day. Its 5-hour window peaked at ${Math.round(problems.plan_refused.peak_five_hour * 100)}%.`
                  : "The shared plan turned nothing away. No reading has come in yet today."}</p></div>
          <span />
        </div>
        <div className="rd-prob">
          <span className="rd-n" style={{ color: problems.timeouts.count ? "var(--critical)" : "var(--good)" }}>{problems.timeouts.count}</span>
          <div><b>Hit the {length(problems.timeouts.timeout_s)} timeout</b>
            <p>{problems.timeouts.longest_s !== null
              ? `Longest run today: ${length(problems.timeouts.longest_s)} (${problems.timeouts.longest_by}). Typical: ${length(problems.timeouts.typical_s)}.`
              : "No run has finished today yet."}</p></div>
          <span />
        </div>
      </section>

      {/* ── the log ── */}
      <section className="rd-panel" id="rd-log">
        <div className="rd-ph"><h2>Every run today</h2><span className="help">{plural(data.runs.length, "run")} · newest first</span></div>
        <div className="rd-filters" role="group" aria-label="Filter runs">
          {([["all", `All ${data.runs.length}`], ["counted", `Counted ${started.counted - limit.queued}`],
            ["given_back", `Given back ${started.given_back}`], ["early", `Ended early ${early.length}`],
            ["read", `Message reads ${started.reads}`]] as [Filter, string][]).map(([f, label]) => (
            <button key={f} className="rd-chip" aria-pressed={filter === f} onClick={() => { setFilter(f); setShowAll(false); }}>{label}</button>
          ))}
          <span style={{ width: 12 }} />
          {data.who.filter((w) => data.runs.some((r) => r.agent_id === w.agent_id)).map((w) => (
            <button key={w.agent_id} className="rd-chip" aria-pressed={filter === `agent:${w.agent_id}`}
              onClick={() => { setFilter(`agent:${w.agent_id}`); setShowAll(false); }}>{w.name}</button>
          ))}
        </div>
        <div className="rd-log-wrap">
          <table className="rd-log">
            <thead><tr><th>When</th><th>Who</th><th>Why it woke</th><th>Result</th><th>Counted?</th>
              <th className="num">Length</th><th className="num">Tokens</th><th /></tr></thead>
            <tbody>
              {shown.map((r) => (
                <Fragment key={r.id}>
                  <tr className={`${r.has_trace || r.has_context ? "rd-openable" : ""}${open === r.id ? " rd-open" : ""}`}
                    onClick={(e) => {
                      // The whole row opens its trace; the button is the
                      // keyboard's way in and the visible sign that it can.
                      if ((r.has_trace || r.has_context) && !(e.target as HTMLElement).closest("button, a")) {
                        setOpen(open === r.id ? null : r.id);
                      }
                    }}>
                    <td>{time(r.started_at ?? r.created_at)}</td>
                    <td><span className="rd-dot" style={{ background: color[r.agent_id ?? ""] ?? "var(--muted)" }} />{r.agent}</td>
                    <td>{r.why}</td>
                    <td><span className={`rd-res ${r.tone}`} title={r.cause ?? undefined}>{r.result}</span></td>
                    <td><span className={`rd-cnt ${r.lane === "counted" ? "yes" : "no"}`}>{LANE_WORD[r.lane]}</span></td>
                    <td className="num">{length(r.seconds)}</td>
                    <td className="num">{tokens(r.tokens)}</td>
                    <td>{(r.has_trace || r.has_context) && (
                      <button className="rd-open-btn" aria-expanded={open === r.id}
                        aria-label={`${open === r.id ? "Hide" : "Show"} what this run did`}
                        onClick={() => setOpen(open === r.id ? null : r.id)}>
                        {open === r.id ? "Hide" : "Details"}<span aria-hidden="true">{open === r.id ? "▴" : "▾"}</span>
                      </button>)}</td>
                  </tr>
                  {open === r.id && <tr className="rd-detail"><td colSpan={8}>{detail(r.id, r.has_trace, r.has_context)}</td></tr>}
                </Fragment>
              ))}
              {rows.length === 0 && <tr><td colSpan={8} className="help">No runs match.</td></tr>}
            </tbody>
          </table>
        </div>
        {rows.length > 12 && (
          <button className="rd-chip" style={{ marginTop: 10 }} onClick={() => setShowAll(!showAll)}>
            {showAll ? "Show fewer" : `Show all ${rows.length}`}
          </button>
        )}
        <p className="help" style={{ margin: "12px 0 0" }}>
          Earlier days: <Link to="?all=1">every run, newest first →</Link>
        </p>
      </section>
      <AsOf at={at} error={error} />
    </div>
  );
}

const LANE_WORD: Record<string, string> = {
  counted: "counted", given_back: "given back", read: "message read", check: "never", chat: "never",
};

/** More than double the usual, and enough runs that doubling means something. */
function high(today: number, usual: number): boolean {
  return today >= 3 && today >= usual * 2;
}

function WhoBars({ data }: { data: RunDayView }) {
  const max = Math.max(...data.who.map((w) => w.counted), 1);
  return (
    <>
      {data.who.map((w) => (
        <div className="rd-who-row" key={w.agent_id}>
          <div className="rd-name"><b>{w.name}</b><span>{w.role}</span></div>
          <div className="rd-bar">
            {REASON_ORDER.filter((k) => w.reasons[k]).map((k) => (
              <span key={k} className={REASON_CLASS[k]} style={{ width: `${(w.reasons[k] / max) * 100}%` }}
                title={`${data.families[k]?.label}: ${w.reasons[k]}`} />
            ))}
          </div>
          <span className="rd-n">{w.counted}</span>
          <span />
          <span className="rd-usual">
            {w.counted === 0 && w.usual === 0
              ? (w.days_since_run === null ? "no runs yet" : `no runs in ${w.days_since_run} days`)
              : <>usual {w.usual} a day{w.usual > 0 && w.counted >= w.usual * 2 && w.counted >= 3
                  ? <> · <b>{(w.counted / w.usual).toFixed(1)}× today</b></> : null}</>}
          </span>
        </div>
      ))}
    </>
  );
}

/** One lane per person across the run day: a block per run, placed at its
    start and as long as it took, the stretch after the limit shaded, your
    pauses marked, and now. */
function Timeline({ data }: { data: RunDayView }) {
  const start = new Date(data.day.starts_at).getTime();
  const span = new Date(data.day.ends_at).getTime() - start;
  const lanes = data.who.slice(0, 8);
  const L = 120, R = 990, top = 30, lane = 38, H = top + lane * Math.max(lanes.length, 1) + 40;
  const x = (t: number) => L + Math.min(1, Math.max(0, (t - start) / span)) * (R - L);
  const now = new Date(data.as_of).getTime();
  const bottom = top + lane * lanes.length;
  const reached = data.limit.reached_at ? new Date(data.limit.reached_at).getTime() : null;
  const ticks = Array.from({ length: 9 }, (_, i) => start + (i * span) / 8);
  return (
    <svg className="rd-chart rd-tl" viewBox={`0 0 1000 ${H}`} width="100%" role="img" aria-label="Runs per person across the run day">
      {ticks.map((t, i) => (
        <g key={t}>
          <line className="rd-grid" x1={x(t)} x2={x(t)} y1={18} y2={bottom} />
          <text x={x(t)} y={12} textAnchor={i === 0 ? "start" : i === 8 ? "end" : "middle"}>{time(new Date(t).toISOString())}</text>
        </g>
      ))}
      {lanes.map((w, i) => (
        <g key={w.agent_id}>
          <rect className="rd-lane" x={L} y={top + i * lane + 4} width={R - L} height={lane - 8} rx={4} />
          <text x={0} y={top + i * lane + lane / 2 + 4} className="rd-strong">{w.name}</text>
        </g>
      ))}
      {reached !== null && (
        <g>
          <rect className="rd-noruns" x={x(reached)} y={20} width={Math.max(2, x(Math.min(now, start + span)) - x(reached))} height={bottom - 8} />
          <text x={x(reached) + 6} y={bottom + 18} className="rd-warn-t">
            limit reached {time(data.limit.reached_at!)} · nothing counted can start until {time(data.day.ends_at)}
          </text>
        </g>
      )}
      {data.runs.filter((r) => r.lane !== "chat" && r.lane !== "check").map((r) => {
        const li = lanes.findIndex((w) => w.agent_id === r.agent_id);
        if (li < 0) return null;
        const s = new Date(r.started_at ?? r.created_at).getTime();
        const e = r.finished_at ? new Date(r.finished_at).getTime() : now;
        const w = Math.max(3, x(e) - x(s));
        const cls = r.tone === "bad" ? "rd-b-bad" : r.lane === "given_back" ? "rd-b-given" : r.lane === "read" ? "rd-b-read" : "rd-b-counted";
        return (
          <rect key={r.id} className={cls} x={x(s)} y={top + li * lane + 8} width={w} height={lane - 16} rx={2}>
            <title>{`${time(r.started_at ?? r.created_at)} · ${r.why} · ${r.result}`}</title>
          </rect>
        );
      })}
      {data.pauses.filter((p) => p.action === "pause").map((p) => (
        <g key={p.at}>
          <line className="rd-pause" x1={x(new Date(p.at).getTime())} x2={x(new Date(p.at).getTime())} y1={20} y2={bottom} />
          <text x={x(new Date(p.at).getTime()) + 4} y={26} className="rd-bad-t">you paused {time(p.at)}</text>
        </g>
      ))}
      <line className="rd-now" x1={x(now)} x2={x(now)} y1={18} y2={H - 4} />
      <text x={x(now) + 4} y={H - 6} className="rd-now-t">now</text>
    </svg>
  );
}

/** Counted runs per run day for the fortnight, the usual and the limit as
    lines, today in the warning colour. */
function Days({ data }: { data: RunDayView }) {
  const v = data.days;
  const wall = data.limit.wall;
  const W = 560, L = 26, base = 140, top = 10;
  const max = Math.max(wall * 1.1, ...v.map((d) => d.counted), 1);
  const y = (n: number) => base - (n / max) * (base - top);
  const bw = (W - L) / v.length;
  const grid = [0, Math.round(wall / 2), wall];
  return (
    <svg className="rd-chart" viewBox={`0 0 ${W} 170`} width="100%" role="img" aria-label="Counted runs per day for 14 days">
      <line className="rd-usual-line" x1={L} x2={W} y1={y(data.usual.counted)} y2={y(data.usual.counted)} />
      <text x={L + 4} y={y(data.usual.counted) - 4}>usual {data.usual.counted}</text>
      <line className="rd-limit-line" x1={L} x2={W} y1={y(wall)} y2={y(wall)} />
      <text x={W} y={y(wall) - 4} textAnchor="end" className="rd-bad-t">limit {wall}</text>
      {grid.map((g) => <text key={g} x={0} y={y(g) + 4}>{g}</text>)}
      {v.map((d, i) => {
        const today = i === v.length - 1;
        const h = Math.max(d.counted, 0.3);
        return (
          <g key={d.day}>
            <rect className={today ? "rd-today" : "rd-past"} x={L + i * bw + 4} y={y(h)} width={bw - 8} height={base - y(h)} rx={2}>
              <title>{`${d.day}: ${d.counted}`}</title>
            </rect>
            <text x={L + i * bw + bw / 2} y={158} textAnchor="middle" className={today ? "rd-strong" : ""}>{Number(d.day.slice(8))}</text>
          </g>
        );
      })}
    </svg>
  );
}
