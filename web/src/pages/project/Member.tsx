import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { get, getText, post, ResultsView } from "../../api";
import { TaskChain } from "../../chain";
import { WhatTheyDid } from "../../did";
import { fmtUsd, fmtWhen, usePoll } from "../../hooks";
import { firstName, ProjectCtx } from "../../projectCtx";
import { clip, fmtAgo } from "../../ui";
import { Avatar, plainStatus } from "../../ui/kit";
import { Icon } from "../../ui/Icon";
import { heldFor, wokeFor } from "../../ui/team";
import { SeatPill } from "./Team";
import { taskBadge } from "./Work";

/** One person on the team: what they are doing now, what is next, what
    they have made, and how they work.

    It was a drawer over whatever page you were on, with four tabs. A drawer
    says "a detail of this page"; a person on the team you hired is not a
    detail, and the one link you would want to send somebody — "look at what
    Ada has been doing" — had no address. Now it is a page, in the order a
    person is asked about: now, next, this week, what they made, how they
    work.

    Its main button is "Ask the CEO about them", never "Message them": the
    chairman speaks only in the boardroom, and this page keeps that rule by
    offering the door that exists rather than one that would be refused. */

function RawLog({ runId }: { runId: string }) {
  const [text, setText] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  async function toggle() {
    if (!open && text === null) {
      try {
        setText(await getText(`/observe/runs/${runId}/trace`));
      } catch (e) {
        setText(`The trace could not be read: ${(e as Error).message}`);
      }
    }
    setOpen(!open);
  }
  return (
    <>
      <button className="small" onClick={toggle}>{open ? "Hide the raw log" : "Show the raw log"}</button>
      {open && <pre className="block trace">{text ?? "Loading…"}</pre>}
    </>
  );
}

function PauseAction({ ctx, agentId, name, paused }: {
  ctx: ProjectCtx; agentId: string; name: string; paused: boolean;
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!confirming) {
    return (
      <button className="small" onClick={() => setConfirming(true)}>
        <Icon name={paused ? "play" : "pause"} size={13} />
        {paused ? `Resume ${name}` : `Pause ${name}`}
      </button>
    );
  }
  return (
    <span className="confirm-inline">
      <span className="help">
        {paused
          ? `${name} wakes for work again.`
          : `${name}'s run in flight is stopped and nothing new wakes them until you resume.`}
      </span>
      <button className={`small${paused ? " primary" : " danger"}`} disabled={busy} onClick={async () => {
        setBusy(true);
        setError(null);
        try {
          await post(`/admin/agents/${agentId}/pause`, { paused: !paused });
          setConfirming(false);
          ctx.reloadOrg();
          ctx.reload();
        } catch (e) {
          setError((e as Error).message);
        } finally {
          setBusy(false);
        }
      }}>
        {paused ? "Resume" : "Pause"}
      </button>
      <button className="small" onClick={() => setConfirming(false)}>Cancel</button>
      {error && <span className="help" style={{ color: "var(--critical)" }}>{error}</span>}
    </span>
  );
}

const WEEK_MS = 7 * 86_400_000;

export default function Member({ ctx }: { ctx: ProjectCtx }) {
  const { agentId = "" } = useParams();
  const navigate = useNavigate();
  const { projectId } = ctx;
  const { data: made } = usePoll(
    () => get<ResultsView>(`/observe/projects/${projectId}/results?agent_id=${encodeURIComponent(agentId)}`),
    30000, [projectId, agentId]);
  const seat = ctx.seats.find((s) => s.id === agentId);
  const base = `/projects/${projectId}`;
  if (!seat) {
    return (
      <>
        <Link to={`${base}/team`} className="back-link">← Your team</Link>
        <p className="subtitle">{ctx.org ? "Nobody by that id is on this project." : "Loading…"}</p>
      </>
    );
  }

  const ceo = firstName(ctx.ceoName);
  const steer = ctx.dash.can_steer !== false;
  const team = ctx.org?.teams.find((x) => x.id === seat.teamId);
  const work = seat.ceo ? ctx.org?.ceo ?? undefined : team?.agents.find((a) => a.id === seat.id);
  const live = ctx.dash.activity.agents.find((a) => a.id === seat.id);
  const tasks = ctx.tasks ?? [];
  const held = tasks.filter((t) => t.claimed_by === seat.id || (!t.claimed_by && t.assignee === seat.id));
  const open = held.filter((t) => !["done", "killed"].includes(t.status));
  const next = open.filter((t) => t.id !== seat.task?.id);
  const runs = (ctx.runs ?? []).filter((r) => r.agent_id === seat.id);
  const since = Date.now() - WEEK_MS;
  const weekRuns = runs.filter((r) => r.started_at && new Date(r.started_at).getTime() >= since);
  const weekUsd = weekRuns.reduce((n, r) => n + (r.cost_usd ?? 0), 0);
  const finished = held.filter((t) => t.status === "done" && t.updated_at && new Date(t.updated_at).getTime() >= since);
  const t = seat.today;

  // Who they answer to and who answers to them, said once, in words.
  const leadName = team?.agents.find((a) => a.id === team.lead_agent_id)?.name;
  const reportsTo = seat.ceo ? "reports to you" : seat.lead || !leadName ? `reports to ${ceo}` : `reports to ${leadName}`;
  const reports = seat.ceo
    ? (ctx.org?.teams ?? []).filter((x) => x.running).map((x) => x.agents.find((a) => a.id === x.lead_agent_id)?.name ?? x.name)
    : seat.lead && team ? team.agents.filter((a) => a.id !== seat.id && a.status !== "stood_down").map((a) => a.name) : [];
  const where = seat.ceo ? "Runs the project" : seat.lead ? `Leads ${seat.team}` : `On ${seat.team}`;

  return (
    <>
      <Link to={`${base}/team`} className="back-link">← Your team</Link>
      <header className="member-head">
        <span className="av-wrap">
          <Avatar name={seat.name} team={seat.ceo ? "leadership" : seat.team} size="lg" />
          <span className={`seat-dot ring ${seat.state}`} />
        </span>
        <div className="grow">
          <h1>{seat.name} <SeatPill state={seat.state} /></h1>
          <p className="help">
            {seat.title} · {where} · {reportsTo}
            {reports.length > 0 && ` · ${reports.join(", ")} ${reports.length === 1 ? "reports" : "report"} to ${seat.name}`}
          </p>
        </div>
        <div className="member-acts">
          {seat.ceo ? (
            <button className="primary" onClick={() => navigate(`${base}/chat`)}>
              <Icon name="message" size={15} /> Message {ceo}
            </button>
          ) : (
            <button className="primary" onClick={() => navigate(`${base}/chat`,
              { state: { draft: `About ${seat.name} (${seat.team}), who is “${seat.doing}”: ` } })}>
              <Icon name="message" size={15} /> Ask {ceo} about {seat.name}
            </button>
          )}
          {steer && seat.state !== "stood_down" && (
            <PauseAction ctx={ctx} agentId={seat.id} name={seat.name} paused={seat.state === "paused"} />
          )}
        </div>
      </header>

      <div className="member-grid">
        <div className="member-col">
          <section className="card member-now">
            <h2 className="eyebrow-h">Now</h2>
            <b className="member-doing">{seat.doing}</b>
            {seat.task && (
              <div className="help member-for">
                For: <TaskChain ctx={ctx} task={seat.task} withTask={false} />
              </div>
            )}
            {seat.task?.blocked_reason && <div className="warn-line">{clip(seat.task.blocked_reason, 240)}</div>}
            {seat.run ? (
              <div className="now-run">
                <div className="now-run-h">
                  <span className="seat-dot working" />
                  <b>Run in flight</b>
                  <span className="help">for {heldFor(seat.run.started_at)} · woken for {wokeFor(seat.run.trigger)}</span>
                </div>
                <WhatTheyDid runId={seat.run.id} projectId={projectId} label="What they have done so far" />
                {seat.run.has_trace && <RawLog runId={seat.run.id} />}
              </div>
            ) : (
              <p className="help">
                No run in flight.{" "}
                {live?.batching_until
                  ? `The next wake is held until ${fmtWhen(live.batching_until)} to batch messages.`
                  : live && live.pending > 0
                    ? `${live.pending} thing${live.pending === 1 ? "" : "s"} waiting will wake them next.`
                    : runs[0]?.started_at ? `Last ran ${fmtAgo(runs[0].started_at)}.` : "They have not run yet."}
              </p>
            )}
          </section>

          <section className="card">
            <h2 className="eyebrow-h">Next</h2>
            {next.length === 0 ? <p className="help">Nothing else in hand. {ceo} hands out the next piece of work.</p> : (
              <ul className="drawer-list">
                {next.slice(0, 6).map((x) => (
                  <li key={x.id}>
                    <span className="grow">{x.title}
                      <span className="help member-for"> <TaskChain ctx={ctx} task={x} withTask={false} /></span>
                    </span>
                    {taskBadge(x.status, x.not_before)}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="card">
            <h2 className="eyebrow-h">This week</h2>
            <div className="stat-grid">
              <div><span>Finished</span><b>{finished.length}</b></div>
              <div><span>Runs</span><b>{weekRuns.length}</b></div>
              <div><span>Cost</span><b>{fmtUsd(weekUsd)}</b></div>
              <div><span>Runs today</span><b>{t?.runs ?? 0}</b></div>
            </div>
            {t && t.refusals > 0 && (
              <p className="help"><span className="badge serious">{t.refusals} refused</span> A rule stopped
                something they tried today. The reason is on each run.</p>
            )}
          </section>
        </div>

        <div className="member-col">
          <section className="card">
            <h2 className="eyebrow-h">What {seat.name} has made</h2>
            {!made ? <p className="help">Loading…</p> : made.results.length === 0 ? (
              <p className="help">Nothing published yet.</p>
            ) : (
              <ul className="drawer-list">
                {made.results.slice(0, 6).map((r) => (
                  <li key={r.key}>
                    <span className="grow">{r.name} <span className="help">v{r.version}</span></span>
                    <span className={`help results-${r.checked}`}>
                      {r.checked === "unchecked" ? "not checked" : `✓ ${r.checks.find((c) => c.kind === r.checked)?.label ?? ""}`}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            <Link className="help" to={`${base}/results`}>All results →</Link>
          </section>

          <section className="card">
            <h2 className="eyebrow-h">How {seat.name} works</h2>
            <dl className="kv">
              <dt>Role</dt>
              <dd>{seat.ceo ? (ctx.org?.ceo?.role_name ?? "Chief Executive")
                : `${team?.role_name ?? seat.title}${team?.role_version ? ` v${team.role_version}` : ""}`}
                {team && team.role_latest_version && team.role_version && team.role_latest_version > team.role_version
                  ? ` · v${team.role_latest_version} available` : ""}</dd>
              {work?.model && (
                <>
                  <dt>Model</dt>
                  <dd>{work.model}{work.provider ? ` · ${work.provider}` : ""}</dd>
                  <dt>This month</dt>
                  <dd>{fmtUsd(work.month_usd ?? 0)}{work.month_share ? ` · ${Math.round(work.month_share * 100)}% of the project` : ""}</dd>
                </>
              )}
              <dt>Talks to you</dt>
              <dd>{seat.ceo ? "Yes — in the chat." : `No. You reach ${seat.name} through ${ceo}.`}</dd>
              {live && (
                <>
                  <dt>Wakes</dt>
                  <dd>{live.wakes_last_hour} of {live.wake_cap} wakes used this hour</dd>
                </>
              )}
              <dt>Its role definition</dt>
              <dd><Link to="/roles">Open the role library →</Link></dd>
            </dl>
          </section>

          <section className="card">
            <h2 className="eyebrow-h">Recent runs</h2>
            {runs.length === 0 ? <p className="help">No runs yet.</p> : (
              <ul className="drawer-list">
                {runs.slice(0, 8).map((r) => (
                  <li key={r.id} className="member-run">
                    <span className="mono help">{r.started_at ? fmtWhen(r.started_at) : "queued"}</span>
                    <span className="grow">
                      {r.summary ? clip(r.summary, 110) : `Woken for ${wokeFor(r.trigger)}`}
                      <WhatTheyDid runId={r.id} projectId={projectId} />
                    </span>
                    <span className="badge">{plainStatus(r.status)}</span>
                    <span className="num help">{r.cost_usd !== null ? fmtUsd(r.cost_usd) : ""}</span>
                  </li>
                ))}
              </ul>
            )}
            <Link className="help" to={`${base}/details/activity/runs?agent=${encodeURIComponent(seat.id)}`}>
              Every run {seat.name} has done →
            </Link>
          </section>
        </div>
      </div>
    </>
  );
}
