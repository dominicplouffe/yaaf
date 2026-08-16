import { Link } from "react-router-dom";
import { DeskProject, DeskView, get } from "../api";
import { usePoll } from "../hooks";
import { statusBadge } from "./Projects";
import {
  clip,
  DecideButtons,
  clockClass,
  fmtAgo,
  fmtLeft,
  fmtUnit,
  fmtWhenShort,
  hoursSince,
  paceDate,
} from "../ui";

/** The landing page: everything that needs the chairman, across every company.
    Zones in triage order — waiting on you → worth watching → while you were
    away → one comparable row per company. The hero state is an empty desk. */
export default function Desk() {
  const { data, error, reload } = usePoll(() => get<DeskView>("/observe/desk"), 10000);

  if (error) return <div className="error">{error}</div>;
  if (!data) return <p className="subtitle">Loading…</p>;

  const projects = data.projects;

  // ── waiting on you: decisions only a human can make ──
  const waiting: React.ReactNode[] = [];
  for (const p of projects) {
    if (p.status === "paused") {
      waiting.push(
        <div className="arow" key={`${p.id}-paused`}>
          <span className="pj">{p.name}</span>
          <span className="chip critical">paused</span>
          <div className="grow">
            <div className="tt">{p.status_reason ?? "Paused — decide what changes, then resume"}</div>
            <div className="sb">the project stays paused until you act</div>
          </div>
          <Link to={`/projects/${p.id}`}>review &amp; resume →</Link>
        </div>,
      );
    }
    for (const a of p.pending_approvals) {
      waiting.push(
        <div className="arow" key={a.id}>
          <span className="pj">{p.name}</span>
          <span className="chip accent">{a.kind.replaceAll("_", " ")}</span>
          <div className="grow">
            <div className="tt">
              {a.title}
              {a.amount_usd !== null && <span className="mono"> — ${a.amount_usd.toLocaleString()}</span>}
            </div>
            <div className="sb">silence denies automatically at the deadline</div>
          </div>
          <span className={`clock ${clockClass(a.expires_at)}`}>
            expires in {fmtLeft(a.expires_at).replace(" left", "")} → auto-deny
          </span>
          <DecideButtons approvalId={a.id} onDone={reload} />
          <Link to={`/projects/${p.id}/approvals`}>context →</Link>
        </div>,
      );
    }
    for (const bet of p.expired_bets) {
      waiting.push(
        <div className="arow" key={bet.id}>
          <span className="pj">{p.name}</span>
          <span className="chip serious">bet expired</span>
          <div className="grow">
            <div className="tt">“{clip(bet.statement)}” hit its kill date {fmtWhenShort(bet.kill_date)}</div>
            <div className="sb">
              {bet.blocked_tasks ?? 0} task(s) auto-blocked · you hold the one extension — or let it
              stand and the CEO pivots
            </div>
          </div>
          <Link to={`/projects/${p.id}/evidence`}>decide →</Link>
        </div>,
      );
    }
    if (p.audit.unaudited >= 3 && p.status !== "stopped") {
      waiting.push(
        <div className="arow" key={`${p.id}-audit`}>
          <span className="pj">{p.name}</span>
          <span className="chip warn">audit due</span>
          <div className="grow">
            <div className="tt">{p.audit.unaudited} completions have never been audited</div>
            <div className="sb">the platform picks the sample — you're the reviewer that doesn't approve everything</div>
          </div>
          <Link to={`/projects/${p.id}/evidence`}>deal a sample →</Link>
        </div>,
      );
    }
  }

  // ── worth watching: the system will handle these, but you'd rather know now ──
  const watch: React.ReactNode[] = [];
  for (const p of projects) {
    const b = p.budget;
    const frac = b.cap > 0 ? b.spent / b.cap : 0;
    const pace = b.unit === "usd" ? paceDate(b.spent, b.cap) : null;
    if (frac >= 1) {
      // Exhausted budgets pause the project, which already lands in "waiting".
    } else if (frac >= b.warn_ratio || pace) {
      watch.push(
        <div className="wrow" key={`${p.id}-budget`}>
          <span className="pj">{p.name}</span>
          <span className="badge warn">budget {Math.round(frac * 100)}%</span>
          <div className="grow">
            {fmtUnit(b.spent, b.unit)} of {fmtUnit(b.cap, b.unit)}
            {pace && (
              <>
                {" "}— at this pace the cap lands <strong>{fmtWhenShort(pace.toISOString())}</strong>; the
                platform pauses the project at 100%
              </>
            )}
          </div>
          <span className="mini-meter warn"><i style={{ width: `${Math.min(frac, 1) * 100}%` }} /></span>
          <Link to={`/projects/${p.id}/settings`}>raise or steer →</Link>
        </div>,
      );
    }
    const d = p.drought;
    if (d.active) {
      watch.push(
        <div className="wrow" key={`${p.id}-drought`}>
          <span className="pj">{p.name}</span>
          <span className="badge serious">drought</span>
          <div className="grow">
            No verified external evidence in {d.window_hours}h — the platform is refusing new
            internal work until proof lands
          </div>
          <Link to={`/projects/${p.id}/evidence`}>see evidence →</Link>
        </div>,
      );
    } else if (d.applies && d.last_countable_at) {
      const elapsed = hoursSince(d.last_countable_at);
      if (elapsed >= d.window_hours * 0.5) {
        watch.push(
          <div className="wrow" key={`${p.id}-drought`}>
            <span className="pj">{p.name}</span>
            <span className="badge warn">drought {Math.floor(elapsed)}h / {d.window_hours}h</span>
            <div className="grow">
              {Math.floor(elapsed)} hours without verified external evidence — at {d.window_hours}h
              the platform starts refusing internal work
            </div>
            <Link to={`/projects/${p.id}/evidence`}>see evidence →</Link>
          </div>,
        );
      }
    }
    for (const bet of p.kill_dates_soon) {
      watch.push(
        <div className="wrow" key={bet.id}>
          <span className="pj">{p.name}</span>
          <span className="badge warn">kill date {fmtLeft(bet.kill_date).replace(" left", "")}</span>
          <div className="grow">“{clip(bet.statement)}” — {fmtWhenShort(bet.kill_date)}</div>
          <Link to={`/projects/${p.id}/evidence`}>see the bet →</Link>
        </div>,
      );
    }
    if (p.frozen_threads > 0) {
      watch.push(
        <div className="wrow" key={`${p.id}-frozen`}>
          <span className="pj">{p.name}</span>
          <span className="badge serious">thread frozen</span>
          <div className="grow">
            {p.frozen_threads} frozen thread(s) — the pair breaker tripped; those agents are stuck
            until you unfreeze
          </div>
          <Link to={`/projects/${p.id}/channels`}>review thread →</Link>
        </div>,
      );
    }
  }

  // The next clock makes "nothing" verifiably nothing rather than silence.
  const clocks: { at: string; label: string }[] = [];
  for (const p of projects) {
    for (const a of p.pending_approvals) clocks.push({ at: a.expires_at, label: `${p.name}'s approval deadline` });
    for (const bet of p.kill_dates_soon) clocks.push({ at: bet.kill_date, label: `${p.name}'s kill date` });
  }
  clocks.sort((x, y) => x.at.localeCompare(y.at));
  const running = projects.filter((p) => p.status === "running").length;

  return (
    <>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <h1>Your desk</h1>
        <div className="spacer" />
        <span className="asof">
          as of {new Date(data.as_of).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}
        </span>
      </div>
      <p className="subtitle">Everything that needs you, across every company you chair.</p>

      {waiting.length === 0 && watch.length === 0 && projects.length > 0 && (
        <div className="card" style={{ textAlign: "center", padding: "36px 20px" }}>
          <div style={{ fontSize: 19, fontWeight: 700 }}>Nothing needs you.</div>
          <div className="help" style={{ marginTop: 6 }}>
            {running} compan{running === 1 ? "y is" : "ies are"} working.
            {clocks.length > 0 && <> Next clock: {clocks[0].label}, {fmtLeft(clocks[0].at)}.</>}
          </div>
        </div>
      )}
      {projects.length === 0 && (
        <div className="card">
          No projects yet. <Link to="/projects/new">Create the first one.</Link>
        </div>
      )}

      {waiting.length > 0 && (
        <>
          <h2 className="zone hot">Waiting on you <span className="cnt">{waiting.length}</span></h2>
          <div className="arows">{waiting}</div>
        </>
      )}

      {watch.length > 0 && (
        <>
          <h2 className="zone">Worth watching <span className="cnt">{watch.length}</span></h2>
          <div className="wbox">{watch}</div>
        </>
      )}

      {projects.length > 0 && (
        <>
          <h2 className="zone">
            While you were away
            <span style={{ letterSpacing: 0, textTransform: "none" }}>· last 24h</span>
          </h2>
          <div className="stats-row">
            <div className="stat">
              <div className="v">{data.away.tasks_completed}</div>
              <div className="l">tasks completed</div>
            </div>
            <div className="stat">
              <div className="v">{data.away.evidence_verified}</div>
              <div className="l">external evidence verified</div>
            </div>
            <div className="stat">
              <div className="v">${data.away.usd_spent.toLocaleString(undefined, { maximumFractionDigits: 2 })}</div>
              <div className="l">spent across {data.away.runs} runs</div>
            </div>
            <div className="stat">
              <div className="v">{data.away.refusal_storms}</div>
              <div className="l">refusal storm(s) — runs terminated</div>
            </div>
          </div>

          <h2 className="zone">Your companies <span className="cnt">{projects.length}</span></h2>
          <table>
            <thead>
              <tr>
                <th>Company</th>
                <th>Status</th>
                <th className="num">Needs you</th>
                <th className="num">Open tasks</th>
                <th className="num">Evidence 7d</th>
                <th className="num">CEO judgment</th>
                <th>Spend this month</th>
                <th>Last activity</th>
              </tr>
            </thead>
            <tbody>
              {projects.map((p) => (
                <CompanyRow key={p.id} p={p} />
              ))}
            </tbody>
          </table>
        </>
      )}
    </>
  );
}

function CompanyRow({ p }: { p: DeskProject }) {
  const needsYou =
    p.pending_approvals.length +
    p.expired_bets.length +
    (p.audit.unaudited >= 3 ? 1 : 0) +
    (p.status === "paused" ? 1 : 0);
  const frac = p.budget.cap > 0 ? p.budget.spent / p.budget.cap : 0;
  const meterCls = frac >= 1 ? "over" : frac >= p.budget.warn_ratio ? "warn" : "";
  return (
    <tr>
      <td>
        <Link to={`/projects/${p.id}`}><strong>{p.name}</strong></Link>
        <div className="help">{p.goal.length > 80 ? p.goal.slice(0, 80) + "…" : p.goal}</div>
      </td>
      <td>{statusBadge(p.status)}</td>
      <td className="num">{needsYou > 0 ? <strong>{needsYou}</strong> : <span className="help">—</span>}</td>
      <td className="num">{p.open_tasks} / {p.queue_ceiling}</td>
      <td className="num">{p.evidence_7d}</td>
      <td className="num">
        {p.ceo_judgment.conversion === null ? (
          <span className="help">no bets yet</span>
        ) : (
          `${Math.round(p.ceo_judgment.conversion * 100)}%`
        )}
      </td>
      <td>
        <span className={`mini-meter ${meterCls}`}><i style={{ width: `${Math.min(frac, 1) * 100}%` }} /></span>
        <span className="mono">
          {fmtUnit(p.budget.spent, p.budget.unit)}/{fmtUnit(p.budget.cap, p.budget.unit)}
        </span>
      </td>
      <td>{fmtAgo(p.last_run_at)}</td>
    </tr>
  );
}
