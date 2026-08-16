import { Link } from "react-router-dom";
import { DeskProject, DeskView, get } from "../api";
import { usePoll } from "../hooks";
import { clip, fmtAgo, fmtUnit } from "../ui";

export function statusBadge(status: string) {
  const cls =
    status === "running"
      ? "good"
      : status === "paused"
        ? "warn"
        : status === "stopped"
          ? "critical"
          : "accent";
  return <span className={`badge ${cls}`}>{status}</span>;
}

/** The portfolio directory: what companies exist and how each is doing — the
    Desk answers "what needs me now?"; this page answers "what do I own?".
    Goals are clipped to their opening (some are memo-length); the numbers a
    chairman compares ride on every card. */
export default function Projects() {
  const { data, error } = usePoll(() => get<DeskView>("/observe/desk"), 10000);
  if (error) return <div className="error">{error}</div>;
  if (!data) return <p className="subtitle">Loading…</p>;

  const projects = data.projects;
  // Live companies first, then the ones paused mid-flight, then not-yet-started
  // drafts; the stopped archive reads last.
  const sections: { key: string; title: string; items: DeskProject[] }[] = [
    { key: "running", title: "Running", items: [] },
    { key: "paused", title: "Paused", items: [] },
    { key: "draft", title: "Drafts — never started", items: [] },
    { key: "stopped", title: "Stopped — the archive stays readable", items: [] },
  ];
  for (const p of projects) {
    (sections.find((s) => s.key === p.status) ?? sections[2]).items.push(p);
  }

  return (
    <>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <h1>Projects</h1>
        <div className="spacer" />
        <Link to="/projects/new"><button className="small primary">+ New project</button></Link>
      </div>
      <p className="subtitle">
        Every project has a CEO, teams, a constitution — and you as chairman.
      </p>

      {projects.length === 0 && (
        <div className="card">
          No projects yet. <Link to="/projects/new">Create the first one.</Link>
        </div>
      )}

      {sections.map(
        (section) =>
          section.items.length > 0 && (
            <div key={section.key}>
              <h2 className="zone">
                {section.title} <span className="cnt">{section.items.length}</span>
              </h2>
              <div className="list-grid wide">
                {section.items.map((p) => (
                  <ProjectCard key={p.id} p={p} />
                ))}
              </div>
            </div>
          ),
      )}
    </>
  );
}

function ProjectCard({ p }: { p: DeskProject }) {
  const needsYou =
    p.pending_approvals.length +
    p.expired_bets.length +
    (p.audit.unaudited >= 3 && p.status !== "stopped" ? 1 : 0) +
    (p.status === "paused" ? 1 : 0);
  const frac = p.budget.cap > 0 ? p.budget.spent / p.budget.cap : 0;
  const meterCls = frac >= 1 ? "over" : frac >= p.budget.warn_ratio ? "warn" : "";
  const judgment =
    p.ceo_judgment.conversion === null ? null : Math.round(p.ceo_judgment.conversion * 100);
  const stopped = p.status === "stopped";

  return (
    <div className="card" style={stopped ? { opacity: 0.65 } : undefined}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <Link to={`/projects/${p.id}`}>
          <strong style={{ fontSize: 15 }}>{p.name}</strong>
        </Link>
        {statusBadge(p.status)}
        {needsYou > 0 && !stopped && (
          <Link to="/desk"><span className="chip warn">{needsYou} waiting on you</span></Link>
        )}
        <div className="spacer" />
        <span className="mono help">{p.slug}</span>
      </div>

      <p style={{ margin: "8px 0 0", fontSize: 13, color: "var(--ink-2)", lineHeight: 1.5 }}>
        {clip(p.goal, 220)}
      </p>
      {p.status_reason && p.status !== "running" && (
        <p className="help" style={{ margin: "6px 0 0" }}>
          {p.status}: {clip(p.status_reason, 120)}
        </p>
      )}

      <div className="pcard-vitals">
        <span>
          <b>{judgment === null ? "—" : `${judgment}%`}</b> CEO judgment
        </span>
        <span>
          <b>{p.evidence_7d}</b> evidence · 7d
        </span>
        <span>
          <b>{p.open_tasks}/{p.queue_ceiling}</b> open tasks
        </span>
        <span>
          <span className={`mini-meter ${meterCls}`}>
            <i style={{ width: `${Math.min(frac, 1) * 100}%` }} />
          </span>
          <span className="mono" style={{ fontSize: 11 }}>
            {fmtUnit(p.budget.spent, p.budget.unit)}/{fmtUnit(p.budget.cap, p.budget.unit)}
          </span>
        </span>
        <span className="help">active {fmtAgo(p.last_run_at)}</span>
      </div>

      <div style={{ display: "flex", gap: 16, marginTop: 10, fontSize: 12.5 }}>
        <Link to={`/projects/${p.id}`}>Today →</Link>
        <Link to={`/projects/${p.id}/boardroom`}>Boardroom →</Link>
        <Link to={`/projects/${p.id}/evidence`}>Evidence →</Link>
        <Link to={`/projects/${p.id}/settings`}>Settings →</Link>
      </div>
    </div>
  );
}
