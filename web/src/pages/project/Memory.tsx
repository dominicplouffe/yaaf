import { Dashboard, get, MemoryRow, OrgView } from "../../api";
import { fmtWhen, usePoll } from "../../hooks";

function kindBadge(kind: string) {
  const cls =
    kind === "lesson" ? "warn" : kind === "preference" ? "accent" : kind === "contact" ? "good" : "";
  return <span className={`badge ${cls}`}>{kind}</span>;
}

function MemoryList({ rows }: { rows: MemoryRow[] }) {
  if (rows.length === 0)
    return <p className="help">Nothing remembered yet.</p>;
  return (
    <table>
      <thead>
        <tr>
          <th>Kind</th>
          <th style={{ width: "28%" }}>Subject</th>
          <th style={{ width: "45%" }}>What is remembered</th>
          <th>Since</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((memory) => (
          <tr key={memory.id}>
            <td>{kindBadge(memory.kind)}</td>
            <td>
              <strong>{memory.subject}</strong>
            </td>
            <td style={{ whiteSpace: "pre-wrap" }}>{memory.body}</td>
            <td className="mono">{fmtWhen(memory.at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function Memory({ projectId, dash }: { projectId: string; dash: Dashboard }) {
  const { data: memories, error } = usePoll(
    () => get<MemoryRow[]>(`/observe/projects/${projectId}/memories`),
    10000,
  );
  const { data: org } = usePoll(() => get<OrgView>(`/observe/projects/${projectId}/org`), 60000);
  if (error) return <div className="error">{error}</div>;

  const rows = memories ?? [];
  const projectMemory = rows.filter((m) => m.scope === "project");
  const capProject = Number(dash.constitution["memory_cap_project"] ?? 0) || "?";
  const capTeam = Number(dash.constitution["memory_cap_team"] ?? 0) || "?";

  return (
    <>
      <p className="subtitle">
        What the company has chosen to remember — the CEO curates project memory, each team's
        lead curates its own. The caps force curation: a full memory refuses new entries until
        something is superseded or forgotten, so what you read here is what they consider worth
        keeping.
      </p>

      <h2>
        Project memory — curated by the CEO ({projectMemory.length} of {capProject} slots)
      </h2>
      <div className="card">
        <MemoryList rows={projectMemory} />
      </div>

      {(org?.teams ?? []).map((team) => {
        const teamMemory = rows.filter((m) => m.scope === "team" && m.team_id === team.id);
        return (
          <div key={team.id}>
            <h2>
              Team {team.name} — curated by its lead ({teamMemory.length} of {capTeam} slots)
            </h2>
            <div className="card">
              <MemoryList rows={teamMemory} />
            </div>
          </div>
        );
      })}
    </>
  );
}
