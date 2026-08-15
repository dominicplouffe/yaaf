import { Link } from "react-router-dom";
import { get, ProjectSummary } from "../api";
import { usePoll } from "../hooks";

export function statusBadge(status: string) {
  const cls =
    status === "running" ? "good" : status === "paused" ? "warn" : status === "stopped" ? "critical" : "accent";
  return <span className={`badge ${cls}`}>{status}</span>;
}

export default function Projects() {
  const { data, error } = usePoll(() => get<ProjectSummary[]>("/observe/projects"), 8000);

  return (
    <>
      <h1>Projects</h1>
      <p className="subtitle">Every project has a CEO, teams, a constitution — and you as chairman.</p>
      {error && <div className="error">{error}</div>}
      <div className="list-grid">
        {(data ?? []).map((project) => (
          <Link key={project.id} to={`/projects/${project.id}`} style={{ color: "inherit" }}>
            <div className="card" style={{ height: "100%" }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                <strong>{project.name}</strong>
                {statusBadge(project.status)}
              </div>
              <div className="mono" style={{ color: "var(--muted)", margin: "4px 0" }}>
                {project.slug}
              </div>
              <div style={{ color: "var(--ink-2)", fontSize: 13 }}>{project.goal}</div>
            </div>
          </Link>
        ))}
        {data && data.length === 0 && (
          <div className="card">
            No projects yet. <Link to="/projects/new">Create the first one.</Link>
          </div>
        )}
      </div>
    </>
  );
}
