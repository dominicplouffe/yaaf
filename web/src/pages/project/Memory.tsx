import { useState } from "react";
import { Link } from "react-router-dom";
import { get, MemoryRow, MemoryScopeUsage } from "../../api";
import { fmtWhen, usePoll } from "../../hooks";
import { AsOf, ExportCsv, downloadCsv } from "../../ui";
import type { ProjectCtx } from "../Project";

function kindBadge(kind: string) {
  const cls =
    kind === "lesson" ? "warn" : kind === "preference" ? "accent" : kind === "contact" ? "good" : "";
  return <span className={`badge ${cls}`}>{kind}</span>;
}

/** Agents write at length; a table cell has no opinion about that. Three lines
    is enough to recognise an entry, and the rest is one click away — without
    it a dozen remembered facts run past seven thousand pixels. */
function Body({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const long = text.length > 220 || text.split("\n").length > 3;
  if (!long) return <span style={{ whiteSpace: "pre-wrap" }}>{text}</span>;
  return (
    <>
      <div className={open ? undefined : "clamp"} style={{ whiteSpace: "pre-wrap" }}>
        {text}
      </div>
      <button className="small" style={{ marginTop: 6 }} onClick={() => setOpen(!open)}>
        {open ? "less" : "more"}
      </button>
    </>
  );
}

function MemoryList({ rows }: { rows: MemoryRow[] }) {
  if (rows.length === 0) return <p className="help">Nothing remembered yet.</p>;
  return (
    <table>
      <thead>
        <tr>
          <th>Kind</th>
          <th style={{ width: "26%" }}>Subject</th>
          <th style={{ width: "48%" }}>What is remembered</th>
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
            <td>
              <Body text={memory.body} />
            </td>
            <td>{fmtWhen(memory.at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** The slot counter, and — when it matters — what being at it actually costs.
    A full scope is not a statistic: every member's writes are refused, and only
    the named curator can end that. */
function Slots({ usage, projectId }: { usage?: MemoryScopeUsage; projectId: string }) {
  if (!usage) return null;
  const full = usage.live >= usage.cap;
  const tight = usage.live >= usage.cap * 0.85;
  return (
    <>
      <span className={`cnt${full ? " over" : tight ? " warn" : ""}`}>
        {usage.live} of {usage.cap}
      </span>
      {tight && (
        <div className="help" style={{ fontWeight: 400, marginTop: 4 }}>
          {full ? (
            <>
              <span className="badge serious">full</span> New entries here are being refused.{" "}
              {usage.curator
                ? `${usage.curator} curates this and has been asked to compact it.`
                : "This team has no lead, so nobody may compact it — name one, or "}
              {!usage.curator && "raise the cap "}
              <Link to={`/projects/${projectId}/details/settings`}>in Rules in force</Link>.
            </>
          ) : (
            <>
              Nearly full — {usage.curator ?? "its curator"} has been asked to compact. You can
              also <Link to={`/projects/${projectId}/details/settings`}>give it more room</Link>, which
              takes effect on the next run.
            </>
          )}
        </div>
      )}
    </>
  );
}

export default function Memory({ projectId, ctx }: { projectId: string; ctx: ProjectCtx }) {
  const { dash, org } = ctx;
  const { data: memories, error, at } = usePoll(
    () => get<MemoryRow[]>(`/observe/projects/${projectId}/memories`),
    10000,
  );
  const [kind, setKind] = useState<string>("all");

  const rows = memories ?? [];
  const kinds = [...new Set(rows.map((m) => m.kind))].sort();
  const matching = kind === "all" ? rows : rows.filter((m) => m.kind === kind);
  const projectMemory = matching.filter((m) => m.scope === "project");
  // Counted server-side, exactly the way the refusal counts it — a number here
  // that disagreed with the wall an agent hits would be worse than none.
  const usageOf = (scope: string, teamId: string | null) =>
    dash.memory?.scopes.find((s) => s.scope === scope && s.team_id === teamId);

  if (error && !memories) return <div className="error">{error}</div>;

  return (
    <>
      <p className="subtitle">
        What the project has chosen to remember — the CEO curates project memory, each team's
        lead curates its own. The caps force curation: a full memory refuses new entries until
        its curator folds several into one or forgets one, so what you read here is what they
        consider worth keeping. Nothing is ever deleted or auto-evicted — folded entries stay
        readable to the agents, they just stop taking up a slot.
      </p>

      {rows.length > 0 && (
        <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: "var(--s2)" }}>
          <ExportCsv
            onClick={() =>
              downloadCsv(
                "what-the-project-remembers.csv",
                ["Kind", "Scope", "Team", "Subject", "What is remembered", "Since"],
                rows.map((m) => [m.kind, m.scope, m.team_id ?? "", m.subject, m.body, m.at]),
              )
            }
          />
        </div>
      )}

      {kinds.length > 1 && (
        <div className="pills">
          <button className={kind === "all" ? "active" : ""} onClick={() => setKind("all")}>
            All {rows.length}
          </button>
          {kinds.map((k) => (
            <button key={k} className={kind === k ? "active" : ""} onClick={() => setKind(k)}>
              {k} {rows.filter((m) => m.kind === k).length}
            </button>
          ))}
        </div>
      )}

      <h2 className="zone">
        Project memory — curated by the CEO{" "}
        <Slots usage={usageOf("project", null)} projectId={projectId} />
      </h2>
      <div className="wbox">
        <MemoryList rows={projectMemory} />
      </div>

      {(org?.teams ?? []).map((team) => {
        const teamMemory = matching.filter((m) => m.scope === "team" && m.team_id === team.id);
        const usage = usageOf("team", team.id);
        return (
          <div key={team.id}>
            <h2 className="zone">
              {team.name} — curated by {usage?.curator ?? "its lead"}{" "}
              <Slots usage={usage} projectId={projectId} />
            </h2>
            <div className="wbox">
              <MemoryList rows={teamMemory} />
            </div>
          </div>
        );
      })}

      <AsOf at={at} error={error} />
    </>
  );
}
