import { Link } from "react-router-dom";
import { DeskProject, get } from "../api";
import { DeskBrief } from "../briefing";
import { Item, ItemActions, needsYouCount, soonestClock, useDesk, WARN_VERDICTS } from "../desk";
import { usePoll } from "../hooks";
import { useMe } from "../me";
import { SetupChecklist } from "../setupChecklist";
import { fmtSoon } from "../ui";
import { EmptyState } from "../ui/kit";
import { Icon } from "../ui/Icon";
import { milestoneLine, MoneyMeter, PathDots, pathWords, rowPath, verdictBadge } from "../ui/signal";
import "./home.css";

/** The first screen: does anything need me, and how is everything else?

    Five seconds, four answers, in this order: one line counting each state,
    the decisions themselves with their verb on the row, every project as one
    comparable row — state, sentence, outcomes, next date, money — and what
    happened since you last looked. The cards this replaced gave every project
    the same weight and the same shape, so a project that needed you and one
    that was fine looked alike until you read both. */

function greeting(): string {
  const h = new Date().getHours();
  return h < 5 ? "Good evening" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

/** The order a reader wants the rows in: what needs them, then what is in
    trouble, then what is fine, then what has not started. */
const RANK: Record<string, number> = {
  needs_you: 0, blocked: 1, failing: 1, late: 1, at_risk: 2, stalled: 2, spinning: 2,
  paused: 3, on_track: 4, idle: 5, done: 6, not_started: 7, stopped: 8,
};

function stateCounts(projects: DeskProject[], decisions: number): string[] {
  const n = (vs: string[]) => projects.filter((p) => vs.includes(p.status_line?.verdict ?? "")).length;
  const out: string[] = [];
  const trouble = n(["blocked", "failing"]);
  const late = n(["late"]);
  const risk = n(["at_risk", "stalled", "spinning"]);
  const fine = n(["on_track"]);
  const idle = n(["idle"]);
  if (trouble) out.push(`${trouble} blocked`);
  if (late) out.push(`${late} late`);
  if (risk) out.push(`${risk} at risk`);
  if (fine) out.push(`${fine} on track`);
  if (idle) out.push(`${idle} idle`);
  const done = n(["done"]);
  if (done) out.push(`${done} delivered`);
  return [decisions ? `${decisions} decision${decisions === 1 ? "" : "s"} need${decisions === 1 ? "s" : ""} you` : "", ...out]
    .filter(Boolean);
}

function NeedRow({ it }: { it: Item }) {
  return (
    <div className="hn-row">
      <div className="hn-body">
        <div className="hn-top">
          <span className="badge you hn-kind">{it.kind}</span>
          <Link className="hn-t" to={`/inbox?item=${encodeURIComponent(it.key)}`}>{it.title}</Link>
        </div>
        <div className="hn-m">
          <span>{it.project.name}</span>
          {it.clocked && <span className="hn-clock"><Icon name="clock" size={12} /> {fmtSoon(new Date(it.at).toISOString())}</span>}
          <span>{it.consequence}</span>
        </div>
      </div>
      <div className="hn-a"><ItemActions item={it} /></div>
    </div>
  );
}

function ProjectRow({ p }: { p: DeskProject }) {
  const b = p.budget;
  const m = milestoneLine(p);
  const warn = WARN_VERDICTS.includes(p.status_line?.verdict ?? "") && p.status_line?.verdict !== "needs_you";
  return (
    <Link className="hp-row" to={`/projects/${p.id}`}>
      <span className="hp-state">{verdictBadge(p, needsYouCount(p))}</span>
      <span className="hp-name">
        <b>{p.name}</b>
        <span className={warn ? "warn" : ""}>{p.status_line?.headline ?? p.status}</span>
      </span>
      <span className="hp-path">
        <PathDots nodes={rowPath(p)} />
        <span className="help">{pathWords(p)}</span>
      </span>
      <span className="hp-next">
        <b>{m.what}</b>
        <span className={m.warn ? "warn" : "help"}>{m.when}</span>
      </span>
      <span className="hp-money">
        <MoneyMeter compact spent={b.spent} cap={b.cap} wall={b.cap * b.hard_stop_multiple}
          unit={b.unit} warnRatio={b.warn_ratio} />
      </span>
    </Link>
  );
}

export default function Home() {
  const { me } = useMe();
  const desk = useDesk();
  const { data: brief } = usePoll(() => get<DeskBrief>("/observe/briefing"), 120000);
  const projects = desk.data?.projects ?? [];
  const soonest = soonestClock(desk.mine);
  const name = me?.display_name.split(" ")[0] ?? "";
  const briefs = (brief?.projects ?? []).filter((b) => !b.quiet);

  if (!desk.data) return desk.error ? <div className="error">{desk.error}</div> : <p className="subtitle">Loading…</p>;

  if (projects.length === 0) {
    return (
      <>
        <div className="pagehead"><div className="ph"><h1>{greeting()}{name ? `, ${name}` : ""}</h1></div></div>
        {me?.is_admin && <SetupChecklist hasProjects={false} />}
        <EmptyState icon="plus" title={desk.data.finished > 0 ? "Every project has finished." : "You don't chair a project yet."}
          foot={<Link className="btnlink primary" to="/projects/new">Start a project</Link>}>
          {desk.data.finished > 0
            ? `All ${desk.data.finished} of your projects were completed or stopped — for a Deliver project that is the end state, not a fault.`
            : "Describe what you want done in a paragraph; yaaf drafts the team, the budget and the date. Once it runs, this page shows what it needs from you and how it is going."}
        </EmptyState>
      </>
    );
  }

  const counts = stateCounts(projects, desk.mine.length);
  const ordered = [...projects].sort((a, b) =>
    (RANK[a.status_line?.verdict ?? ""] ?? 9) - (RANK[b.status_line?.verdict ?? ""] ?? 9)
    || needsYouCount(b) - needsYouCount(a) || a.name.localeCompare(b.name));
  const lastLooked = (brief?.projects ?? []).map((b) => b.since).sort()[0];

  return (
    <>
      <div className="pagehead">
        <div className="ph">
          <h1>{greeting()}{name ? `, ${name}` : ""}</h1>
          <p className="lede hm-lede">
            {counts.length ? counts.map((c, i) => (
              <span key={c}>{i > 0 && <span className="hm-sep"> · </span>}{i === 0 && desk.mine.length ? <b>{c}</b> : c}</span>
            )) : <b>Nothing needs you.</b>}
          </p>
        </div>
        <div className="pacts"><Link className="btnlink" to="/projects/new"><Icon name="plus" size={14} /> New project</Link></div>
      </div>

      <div className={`hm-grid${desk.mine.length ? "" : " solo"}`}>
        {desk.mine.length > 0 && (
        <section className="hm-sec">
          <div className="hm-h">
            <h2>Needs you</h2>
            <span className="help">{soonest ? `soonest decides itself ${fmtSoon(new Date(soonest.at).toISOString())}` : "soonest consequence first"}</span>
            <span className="grow" />
            <Link className="card-link" to="/inbox">Inbox <Icon name="arrow" size={13} /></Link>
          </div>
          <div className="card hn">
            {desk.mine.slice(0, 5).map((it) => <NeedRow key={it.key} it={it} />)}
            {desk.mine.length > 5 && <Link className="more-row" to="/inbox">{desk.mine.length - 5} more in your Inbox</Link>}
          </div>
        </section>
      )}

        <aside className="hm-sec">
          <div className="hm-h">
            <h2>Since you looked</h2>
            {lastLooked && <span className="help">{new Date(lastLooked).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" })}</span>}
            <span className="grow" />
            <Link className="card-link" to="/inbox?tab=updates">Updates <Icon name="arrow" size={13} /></Link>
          </div>
          <div className="card hb">
            {!brief ? <p className="help">Loading…</p> : briefs.length === 0 ? (
              <p className="help">Nothing changed that needs a second look.</p>
            ) : (
              <ul>
                {briefs.map((b) => (
                  <li key={b.project_id}>
                    <Link to={`/inbox?tab=updates&item=brief-${b.project_id}`}><b>{b.name}</b></Link>
                    <span>{b.headline}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <p className="help hb-foot">Written from the record — no agent wrote it, no run paid for it. <kbd>⌘K</kbd> finds anything; <kbd>?</kbd> lists shortcuts.</p>
        </aside>
      </div>

      {me?.is_admin && <SetupChecklist hasProjects />}

      <section className="hm-sec">
          <div className="hm-h"><h2>Projects</h2><span className="grow" />
            <Link className="card-link" to="/projects">All projects <Icon name="arrow" size={13} /></Link></div>
          <div className="card hp" aria-label="Your projects">
            <div className="hp-row hp-head" aria-hidden>
              <span>State</span><span>Project</span><span>Outcomes</span><span>Next</span><span>Money · this month</span>
            </div>
            {ordered.map((p) => <ProjectRow key={p.id} p={p} />)}
            <Link className="hp-add" to="/projects/new"><Icon name="plus" size={14} /> Start a project</Link>
          </div>
      </section>
    </>
  );
}
