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
import { Link, useSearchParams } from "react-router-dom";
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

/** The same steps as one strip: a chip per step, the current one ringed, and
    "I'm good" for a step the project can run without. The chairman liked it
    on Settings and asked for it as soon as he opens a project, so it sits at
    the top of the project home too; there the step it acts on is the next one
    still to do. "Skip" read as dodging a chore — what he is saying is that
    the project is fine without it, and the step stays in Settings for later.

    `section` is the Settings section on screen, when there is one. */
export function SetupStrip({ ctx, section }: { ctx: ProjectCtx; section?: string }) {
  const { setup, projectId, dash } = ctx;
  const [busy, setBusy] = useState(false);
  const [params] = useSearchParams();
  if (!setup?.open) return null;
  // A promised credential's step opens its own card in Settings › Credentials,
  // so on that page the one in view is the one named in the link.
  const need = params.get("need");
  const current = section === "credentials" && need ? `credentials&need=${need}` : section ?? setup.next?.section;
  // Several steps share one section (Integrations holds the repo, the agents'
  // key, where it deploys and the bridges). The step this page acts on is the
  // one still to do there: picking the first one found showed "GitHub
  // repository ✓" and no button, while "Where it deploys" waited with no way
  // to say the project is fine without it.
  const inSection = setup.steps.filter((s) => s.section === current);
  const step = inSection.find((s) => s.key === setup.next?.key)
    ?? inSection.find((s) => s.state === "todo") ?? inSection[0];
  const canWave = !!step && step.state === "todo" && !step.required && dash.can_steer !== false;
  const failed = setup.steps.find((s) => s.state === "failed");
  return (
    <nav className="setup-strip" aria-label="Setup steps">
      <span className="setup-strip-t">Setup {setup.settled} of {setup.total}</span>
      {setup.steps.map((st) => (
        <Link key={st.key} to={`/projects/${projectId}/settings?s=${st.section}`} replace={!!section}
          className={`setup-chip ${st.credential === "saved" ? "saved" : st.state}${current === st.section ? " on" : ""}`}
          aria-current={current === st.section ? "step" : undefined}
          title={st.detail}>
          {chipText(st)}
          <span className="sr-only">{st.state === "todo" ? " — to do" : ` — ${st.state}`}</span>
        </Link>
      ))}
      {canWave && (
        <button className="small setup-skip" disabled={busy}
          title={`The project is fine without ${step!.label}. It stays in Settings if you want it later.`}
          onClick={async () => {
            setBusy(true);
            try {
              await post(`/admin/projects/${projectId}/setup`, { action: "skip", step: step!.key });
              ctx.reloadSetup();
            } finally {
              setBusy(false);
            }
          }}>
          I'm good without {step!.label}
        </button>
      )}
      {failed && (
        <Link className="btnlink small" to={`/projects/${projectId}/settings?s=${failed.section}`}>Fix {failed.label}</Link>
      )}
    </nav>
  );
}

/** A step's chip text. A promised credential says where it stands in words:
    "✗ Needed: Beehiiv", "○ Beehiiv · not tested", "! Google Analytics stopped
    working". */
function chipText(st: SetupStep): string {
  if (st.credential === "failed") return `! ${st.label} stopped working`;
  if (st.credential === "saved") return `○ ${st.label} · not tested`;
  if (st.credential === "needed") return `✗ Needed: ${st.label}`;
  return `${st.state === "done" ? "✓ " : ""}${st.label}`;
}
