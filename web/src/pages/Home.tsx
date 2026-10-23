import { Link } from "react-router-dom";
import { DeskProject, get } from "../api";
import { DeskBrief } from "../briefing";
import { ItemActions, needsYouCount, soonestClock, useDesk } from "../desk";
import { usePoll } from "../hooks";
import { useMe } from "../me";
import { clip, fmtAgo, fmtSoon, fmtUnit } from "../ui";
import { Avatar, EmptyState } from "../ui/kit";
import { Icon } from "../ui/Icon";
import { statusBadge } from "./Projects";

/** The first screen: anything for me, across everything?

    One sentence answers it, then the decisions themselves, then every
    project's state side by side, then what happened while you were away. The
    old landing page was the Desk, which answered the first question very well
    and nothing else — a chairman with four healthy projects opened the app to
    a page that said "nothing needs you" and had to go elsewhere to learn
    whether anything was happening at all. */

function greeting(): string {
  const h = new Date().getHours();
  return h < 5 ? "Good evening" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

function ProjectCard({ p }: { p: DeskProject }) {
  const b = p.budget;
  const frac = b.cap > 0 ? b.spent / b.cap : 0;
  const needs = needsYouCount(p);
  return (
    <Link className="hcard" to={`/projects/${p.id}`}>
      <span className="hcard-h">
        <Avatar name={p.name} team={p.name} />
        <span className="grow"><b>{p.name}</b><span className="help">{p.last_run_at ? `last ran ${fmtAgo(p.last_run_at)}` : "has not run yet"}</span></span>
        {statusBadge(p.status, p.unpaid)}
      </span>
      {/* Is it OK, and if not why — before what it is for. The goal is a
          paragraph the reader wrote; the sentence is what they came to learn. */}
      {p.status_line
        ? <span className={`hcard-goal${["blocked", "late", "at_risk", "stalled", "needs_you"]
            .includes(p.status_line.verdict) ? " warn" : ""}`} title={p.goal}>
            {p.status_line.sentence}</span>
        : <span className="hcard-goal">{clip(p.goal, 160)}</span>}
      <span className="hcard-stats">
        <span><b>{p.runs_running}</b> running now</span>
        <span><b>{fmtUnit(b.spent, b.unit)}</b> of {fmtUnit(b.cap, b.unit)}</span>
        {needs > 0 ? <span className="warn"><b>{needs}</b> need{needs === 1 ? "s" : ""} you</span>
          : <span className="help">nothing needs you</span>}
      </span>
      <span className="meterline">
        <i className={frac >= 1 ? "over" : frac >= b.warn_ratio ? "warn" : ""} style={{ width: `${Math.min(frac, 1) * 100}%` }} />
        <b style={{ left: `${b.warn_ratio * 100}%` }} />
      </span>
      {p.status === "paused" && p.status_reason && <span className="help">Paused: {clip(p.status_reason, 120)}</span>}
    </Link>
  );
}

export default function Home() {
  const { me } = useMe();
  const desk = useDesk();
  const { data: brief } = usePoll(() => get<DeskBrief>("/observe/briefing"), 120000);
  const projects = desk.data?.projects ?? [];
  const running = projects.filter((p) => p.status === "running");
  const inFlight = running.reduce((n, p) => n + p.runs_running, 0);
  const soonest = soonestClock(desk.mine);
  const name = me?.display_name.split(" ")[0] ?? "";
  const needingProjects = new Set(desk.mine.map((i) => i.project.id)).size;
  const briefs = (brief?.projects ?? []).filter((b) => !b.quiet);

  if (!desk.data) return desk.error ? <div className="error">{desk.error}</div> : <p className="subtitle">Loading…</p>;

  if (projects.length === 0) {
    return (
      <>
        <div className="pagehead"><div className="ph"><h1>{greeting()}{name ? `, ${name}` : ""}</h1></div></div>
        <EmptyState icon="plus" title={desk.data.finished > 0 ? "Every project has finished." : "You don't chair a project yet."}
          foot={<Link className="btnlink primary" to="/projects/new">Start a project</Link>}>
          {desk.data.finished > 0
            ? `All ${desk.data.finished} of your projects were completed or stopped — for a Deliver project that is the end state, not a fault.`
            : "A project is a CEO agent, a team, a goal and a budget. Describe what you want done in a paragraph and yaaf drafts the rest; once it runs, this page tells you what it needs and what it did."}
        </EmptyState>
      </>
    );
  }

  return (
    <>
      <div className="pagehead">
        <div className="ph">
          <h1>{greeting()}{name ? `, ${name}` : ""}</h1>
          <p className="lede">
            {desk.mine.length > 0 ? (
              <>
                <b>{desk.mine.length} thing{desk.mine.length === 1 ? "" : "s"} need{desk.mine.length === 1 ? "s" : ""} you
                {needingProjects > 1 ? ` across ${needingProjects} projects` : ""}.</b>
                {soonest && <> The soonest decides itself {fmtSoon(new Date(soonest.at).toISOString())}.</>}
              </>
            ) : <b>Nothing needs a decision from you.</b>}{" "}
            {running.length === 0
              ? "No project is running right now."
              : inFlight > 0
                ? `${inFlight} run${inFlight === 1 ? " is" : "s are"} in flight across ${running.length} running project${running.length === 1 ? "" : "s"}.`
                : `${running.length} project${running.length === 1 ? " is" : "s are"} running; nothing is in flight this minute.`}
          </p>
        </div>
        <div className="pacts"><Link className="btnlink" to="/projects/new"><Icon name="plus" size={14} /> New project</Link></div>
      </div>

      <div className="home-grid">
        <div className="ov-col">
          {desk.mine.length > 0 && (
            <section className="card ov-card">
              <div className="card-h">
                <h3>Needs you</h3>
                <span className="count warn">{desk.mine.length}</span>
                <span className="grow" />
                <Link className="card-link" to="/inbox">Inbox <Icon name="arrow" size={13} /></Link>
              </div>
              <div className="needs">
                {desk.mine.slice(0, 5).map((it) => (
                  <div className="need" key={it.key}>
                    <span className={`kind-ic k-${it.type}`}><Icon name={it.type === "approval" ? "check" : it.type === "ask" ? "help" : "flag"} size={15} /></span>
                    <div className="grow">
                      <Link className="need-t" to={`/inbox?item=${encodeURIComponent(it.key)}`}>{it.title}</Link>
                      <div className="need-m"><span>{it.kind}</span><span>{it.project.name}</span>
                        <span><Icon name="clock" size={12} /> {it.consequence}</span></div>
                    </div>
                    <div className="need-a"><ItemActions item={it} /></div>
                  </div>
                ))}
                {desk.mine.length > 5 && (
                  <Link className="more-row" to="/inbox">{desk.mine.length - 5} more in your Inbox</Link>
                )}
              </div>
            </section>
          )}
          <div className="pgrid">
            {projects.map((p) => <ProjectCard key={p.id} p={p} />)}
            <Link className="hcard add" to="/projects/new">
              <Icon name="plus" size={20} />
              <b>Start a project</b>
              <span className="help">Describe it in a paragraph, or fill in the form.</span>
            </Link>
          </div>
        </div>
        <aside className="ov-col">
          <section className="card ov-card">
            <div className="card-h">
              <h3>While you were away</h3>
              <span className="grow" />
              <Link className="card-link" to="/inbox?tab=updates">Updates <Icon name="arrow" size={13} /></Link>
            </div>
            {!brief ? <p className="help">Loading…</p> : briefs.length === 0 ? (
              <p className="help">Nothing happened since you last looked that changes anything.</p>
            ) : (
              <ul className="home-brief">
                {briefs.map((b) => (
                  <li key={b.project_id}>
                    <Link to={`/inbox?tab=updates&item=brief-${b.project_id}`}><b>{b.name}</b></Link>
                    <span>{b.headline}</span>
                  </li>
                ))}
              </ul>
            )}
            <p className="help" style={{ marginTop: 10 }}>The same briefing reaches your email each morning if you set it to.</p>
          </section>
          <section className="card ov-card">
            <div className="card-h"><h3>Finding your way</h3></div>
            <ul className="tips">
              <li><kbd>⌘K</kbd> search for any page, project or person</li>
              <li><b>Inbox</b> holds everything that needs you — the badge counts only what decides itself if you leave it</li>
              <li><b>Chat</b> is where you talk to a project's CEO, the one agent that speaks to you</li>
              <li><b>Team</b> shows who is working on what, right now</li>
              <li><kbd>?</kbd> lists every keyboard shortcut</li>
            </ul>
          </section>
        </aside>
      </div>
    </>
  );
}
