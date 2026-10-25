// A new project's "Finish setting up": every Settings section a project needs
// in its first days, on its Inbox, each one click from the exact section.
//
// The sibling of Home's "Get set up" (setupChecklist.tsx), one level down:
// that one is what an install needs before any project can run, this one is
// what one project needs before it does its best work. Same shape, same rule —
// each step ticks itself off from the project's own state (the server reads it,
// yaaf.server.checklist), and the list goes away once nothing is left, or when
// somebody who steers the project puts it away.

import { useState } from "react";
import { Link } from "react-router-dom";
import { post, SetupStep } from "./api";
import { ProjectCtx } from "./projectCtx";

export function ProjectSetup({ ctx }: { ctx: ProjectCtx }) {
  const { setup, projectId, dash } = ctx;
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (!setup?.open) return null;
  const steer = dash.can_steer !== false;
  const base = `/projects/${projectId}`;

  async function act(action: string, step?: string) {
    setBusy(step ?? action);
    setError(null);
    try {
      await post(`/admin/projects/${projectId}/setup`, { action, step });
      ctx.reloadSetup();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  const left = setup.steps.filter((s) => s.state === "todo").length;
  const mark = (s: SetupStep, i: number) => s.state === "done" ? "✓" : s.state === "skipped" ? "–" : i + 1;
  return (
    <section className="card ov-card setup-card project-setup" aria-label="Finish setting up">
      <div className="card-h">
        <h3>Finish setting up</h3>
        <span className="count warn">{left} to go</span>
        <span className="grow" />
        {steer && (
          <button className="linkish small" disabled={busy !== null} onClick={() => void act("close")}
            title="Put the checklist away. Every section stays in Settings, under the gear beside the project's name.">
            Done for now
          </button>
        )}
      </div>
      <div className="setup-bar" role="progressbar" aria-valuemin={0} aria-valuemax={setup.total}
        aria-valuenow={setup.settled} aria-label={`${setup.settled} of ${setup.total} set up`}>
        <i style={{ width: `${(setup.settled / setup.total) * 100}%` }} />
      </div>
      {error && <div className="error">{error}</div>}
      <ol className="setup-steps">
        {setup.steps.map((s, i) => (
          <li key={s.key} className={`setup-step${s.state === "todo" ? "" : " done"}${s.state === "skipped" ? " skipped" : ""}`}>
            <span className="mark" aria-hidden="true">{mark(s, i)}</span>
            <div>
              <span className="st-t">
                {s.state === "todo"
                  ? <Link to={`${base}/settings?s=${s.section}`}>{s.label}</Link>
                  : s.label}
                {s.required && s.state === "todo" && <span className="setup-req"> · needed to run</span>}
                <span className="sr-only">{s.state === "todo" ? " — to do" : ` — ${s.state}`}</span>
              </span>
              <span className="st-d">{s.state === "skipped" ? "Skipped. It stays in Settings for when you want it." : s.detail}</span>
            </div>
            <span className="st-go">
              {s.state === "todo" && (
                <Link className="btnlink small" to={`${base}/settings?s=${s.section}`}>Open</Link>
              )}
              {steer && s.state === "todo" && !s.required && (
                <button className="small" disabled={busy !== null} onClick={() => void act("skip", s.key)}>Skip</button>
              )}
              {steer && s.state === "skipped" && (
                <button className="small" disabled={busy !== null} onClick={() => void act("unskip", s.key)}>Undo</button>
              )}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}
