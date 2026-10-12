import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { getText, post } from "../../api";
import { fmtUsd, fmtWhen } from "../../hooks";
import { firstName, ProjectCtx } from "../../projectCtx";
import { clip, fmtAgo } from "../../ui";
import { Avatar, plainStatus } from "../../ui/kit";
import { Icon } from "../../ui/Icon";
import { heldFor, wokeFor } from "../../ui/team";
import { SeatPill } from "./Team";
import { taskBadge } from "./Work";

/** One person on the project: what they are doing, what they hold, what they
    have run, and who they are.

    Summary first, then the steps, then the raw log — the order a person asks
    in. The trace is fetched only when asked for, because it is large and most
    readers only need the first line.

    Its main button is "Ask the CEO about them", never "Message them": the
    chairman speaks only in the boardroom, and this drawer keeps that rule by
    offering the door that exists rather than one that would have to be
    refused. */

type Tab = "now" | "work" | "runs" | "about";

function Trace({ runId }: { runId: string }) {
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

export default function AgentDrawer({ ctx, agentId, onClose }: {
  ctx: ProjectCtx; agentId: string; onClose: () => void;
}) {
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>("now");
  const seat = ctx.seats.find((s) => s.id === agentId);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  useEffect(() => setTab("now"), [agentId]);

  if (!seat) return null;
  const ceo = firstName(ctx.ceoName);
  const held = (ctx.tasks ?? []).filter((t) => t.claimed_by === seat.id || (!t.claimed_by && t.assignee === seat.id));
  const runs = (ctx.runs ?? []).filter((r) => r.agent_id === seat.id).slice(0, 15);
  const live = ctx.dash.activity.agents.find((a) => a.id === seat.id);
  const t = seat.today;
  const team = ctx.org?.teams.find((x) => x.id === seat.teamId);
  const steer = ctx.dash.can_steer !== false;

  return (
    <>
      <div className="drawer-scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={`${seat.name}`}>
        <div className="drawer-h">
          <span className="av-wrap">
            <Avatar name={seat.name} team={seat.ceo ? "leadership" : seat.team} size="lg" />
            <span className={`seat-dot ring ${seat.state}`} />
          </span>
          <div className="grow">
            <div className="drawer-name"><b>{seat.name}</b><SeatPill state={seat.state} /></div>
            <div className="help">
              {seat.title} · {seat.ceo ? "CEO" : `${seat.team}${seat.lead ? " lead" : ""}`}
            </div>
          </div>
          <button className="icon" onClick={onClose} aria-label="Close" title="Close (Esc)">
            <Icon name="x" size={16} />
          </button>
        </div>
        <div className="drawer-tabs" role="tablist">
          {([["now", "Now"], ["work", `Work · ${held.length}`], ["runs", "Runs"], ["about", "About"]] as [Tab, string][]).map(([k, l]) => (
            <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>
          ))}
        </div>
        <div className="drawer-b">
          {tab === "now" && (
            <>
              <div className="now-card">
                <span className="help">{seat.state === "working" ? "Working on" : "Status"}</span>
                <b>{seat.doing}</b>
                {seat.task && (
                  <span className="now-task">{taskBadge(seat.task.status, seat.task.not_before)}
                    {seat.task.team_name && <span className="help">{seat.task.team_name}</span>}</span>
                )}
              </div>
              {seat.run ? (
                <div className="now-run">
                  <div className="now-run-h">
                    <span className="seat-dot working" />
                    <b>Run in flight</b>
                    <span className="help">for {heldFor(seat.run.started_at)} · woken for {wokeFor(seat.run.trigger)}</span>
                  </div>
                  {seat.run.has_trace && <Trace runId={seat.run.id} />}
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
              <div className="stat-grid">
                <div><span>Runs today</span><b>{t?.runs ?? 0}</b></div>
                <div><span>Tasks closed</span><b>{t?.tasks_completed ?? 0}</b></div>
                <div><span>In hand</span><b>{t?.holding ?? held.filter((x) => x.status !== "done").length}</b></div>
                <div><span>Spent today</span><b>{t ? fmtUsd(t.usd) : "—"}</b></div>
              </div>
              {t && t.refusals > 0 && (
                <p className="help"><span className="badge serious">{t.refusals} refused</span> A rule stopped
                  something they tried today. The reason is on each run.</p>
              )}
            </>
          )}
          {tab === "work" && (
            held.length ? (
              <ul className="drawer-list">
                {held.map((x) => (
                  <li key={x.id}>
                    <span className="grow">{x.title}
                      {x.blocked_reason && <span className="help"> — {clip(x.blocked_reason, 120)}</span>}</span>
                    {taskBadge(x.status, x.not_before)}
                  </li>
                ))}
              </ul>
            ) : <p className="help">Nothing assigned to {seat.name} right now.</p>
          )}
          {tab === "runs" && (
            <>
              {runs.length ? (
                <ul className="drawer-list">
                  {runs.map((r) => (
                    <li key={r.id}>
                      <span className="mono help">{r.started_at ? fmtWhen(r.started_at) : "queued"}</span>
                      <span className="grow">
                        {r.summary ? clip(r.summary, 110) : `Woken for ${wokeFor(r.trigger)}`}
                      </span>
                      <span className="badge">{plainStatus(r.status)}</span>
                      <span className="num help">{r.cost_usd !== null ? fmtUsd(r.cost_usd) : ""}</span>
                    </li>
                  ))}
                </ul>
              ) : <p className="help">No runs yet.</p>}
              <Link className="btnlink small" to={`/projects/${ctx.projectId}/activity/runs`}>Every run, with traces →</Link>
            </>
          )}
          {tab === "about" && (
            <dl className="kv drawer-kv">
              <dt>Role</dt>
              <dd>{seat.ceo ? (ctx.org?.ceo?.role_name ?? "Chief Executive")
                : `${team?.role_name ?? seat.title}${team?.role_version ? ` v${team.role_version}` : ""}`}
                {team && team.role_latest_version && team.role_version && team.role_latest_version > team.role_version
                  ? ` · v${team.role_latest_version} available` : ""}</dd>
              <dt>Reports to</dt>
              <dd>{seat.ceo ? "You, the chair" : seat.lead ? ceo : `${team?.agents.find((a) => a.id === team.lead_agent_id)?.name ?? ceo}, who reports to ${ceo}`}</dd>
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
          )}
        </div>
        <div className="drawer-f">
          {seat.ceo ? (
            <button className="primary" onClick={() => navigate(`/projects/${ctx.projectId}/chat`)}>
              <Icon name="message" size={15} /> Message {ceo}
            </button>
          ) : (
            <button className="primary" onClick={() => navigate(`/projects/${ctx.projectId}/chat`,
              { state: { draft: `About ${seat.name} (${seat.team}), who is “${seat.doing}”: ` } })}>
              <Icon name="message" size={15} /> Ask {ceo} about {seat.name}
            </button>
          )}
          <span className="grow" />
          {steer && seat.state !== "stood_down" && (
            <PauseAction ctx={ctx} agentId={seat.id} name={seat.name} paused={seat.state === "paused"} />
          )}
        </div>
      </aside>
    </>
  );
}
