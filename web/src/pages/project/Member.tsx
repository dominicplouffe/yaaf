import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { get, getText, patch, post, ResultsView, SeatWork } from "../../api";
import { TaskChain } from "../../chain";
import { WhatTheyDid } from "../../did";
import { fmtUsd, fmtWhen, usePoll } from "../../hooks";
import { firstName, ProjectCtx } from "../../projectCtx";
import { isClass, ModelSelect, providerOf, useModels, useProviders } from "../../providers";
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

/** One person moved onto another model, effort or harness than their role's.

    The role is the team's charter and changes for everyone on it; this is the
    one seat. Each choice can be put back to "the role's", which is the only
    way an override ever goes away — the server refuses a seat that cannot run
    (a provider the project does not pay, an effort the model does not take)
    with the same sentence every other door gives. */
function SeatEditor({ ctx, agentId, name, work, onDone }: {
  ctx: ProjectCtx; agentId: string; name: string; work: Partial<SeatWork>; onDone: () => void;
}) {
  const catalog = useModels();
  const registry = useProviders();
  const [harnesses, setHarnesses] = useState<{ name: string; label: string }[]>([]);
  useEffect(() => {
    get<{ name: string; label: string }[]>("/admin/harnesses")
      .then(setHarnesses)
      .catch(() => setHarnesses([]));
  }, []);
  const [model, setModel] = useState(work.overrides?.model ?? "");
  const [effort, setEffort] = useState(work.overrides?.effort ?? "");
  const [harness, setHarness] = useState(work.overrides?.harness ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const credentials = [...(ctx.dash.credentials ?? []),
    ...(ctx.dash.install_payer ? [ctx.dash.install_payer] : [])];
  const paid = credentials.map((c) => c.provider);
  const billing = credentials.find((c) => c.billing)?.provider ?? catalog?.default_provider;
  const role = work.role_seat;
  // A model on a provider nothing pays here: the server would refuse it (R5).
  // The door to the app's own provider takes the place of Save, so the refusal
  // is met before it happens rather than answered by it.
  const modelProvider = model && paid.length > 0 ? providerOf(model, catalog, registry, "") : "";
  const unpaidProvider = modelProvider && !paid.includes(modelProvider) ? modelProvider : null;
  const sellerLabel = unpaidProvider
    ? registry.find((p) => p.name === unpaidProvider)?.label ?? unpaidProvider : "";
  const paidNames = credentials.map((c) => c.label).join(", ");
  const harnessLabel = (h: string | null | undefined) =>
    h ? harnesses.find((x) => x.name === h)?.label ?? h : "whatever the paying credential runs on";

  return (
    <div className="addform">
      <label className="field">Model
        <ModelSelect value={model} onChange={setModel} info={catalog} registry={registry}
          primary={billing} providersShown={paid.length ? paid : undefined}
          unpaidShown={paid.length ? registry.map((p) => p.name).filter((n) => !paid.includes(n)) : undefined}
          qualified={paid.length > 1} disabled={busy}
          empty={`The role's — ${role?.model && !isClass(role.model) ? role.model : "the app's model"}`} />
      </label>
      {unpaidProvider && (
        <div className="seat-door">
          <p>This project pays <b>{paidNames}</b>. <b>{model}</b> is a {sellerLabel} model.
            To run a seat on it, make {sellerLabel} the app&apos;s provider — or pick a
            model of a provider it pays.</p>
        </div>
      )}
      <label className="field">Effort
        <select value={effort} disabled={busy} onChange={(e) => setEffort(e.target.value)}>
          <option value="">The role&apos;s — {role?.effort ?? "the default"}</option>
          {(catalog?.efforts ?? ["low", "medium", "high", "xhigh", "max"]).map((level) => (
            <option key={level} value={level}>{level}</option>
          ))}
        </select>
      </label>
      <label className="field">Harness
        <select value={harness} disabled={busy} onChange={(e) => setHarness(e.target.value)}>
          <option value="">The role&apos;s — {harnessLabel(role?.harness)}</option>
          {harnesses.map((h) => <option key={h.name} value={h.name}>{h.label}</option>)}
        </select>
      </label>
      <p className="help">
        Only {name} changes; the rest of the team stays on the role. A run in flight
        finishes on what it has — the change takes effect from {name}&apos;s next run.
      </p>
      <div className="reason-acts">
        {unpaidProvider ? (
          <>
            <Link className="btnlink small primary"
              to="/settings?section=providers" target="_blank" rel="noreferrer">
              Change the app&apos;s provider
            </Link>
            <button className="small" onClick={onDone}>Cancel</button>
          </>
        ) : (
          <>
            <button className="small primary" disabled={busy} onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                await patch(`/admin/agents/${agentId}/seat`,
                  { model: model || null, effort: effort || null, harness: harness || null });
                ctx.reloadOrg();
                onDone();
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}>Save</button>
            <button className="small" disabled={busy} onClick={onDone}>Cancel</button>
          </>
        )}
      </div>
      {error && <div className="help" style={{ color: "var(--critical)" }}>{error}</div>}
    </div>
  );
}

const WEEK_MS = 7 * 86_400_000;

export default function Member({ ctx }: { ctx: ProjectCtx }) {
  const { agentId = "" } = useParams();
  const navigate = useNavigate();
  const registry = useProviders();
  const { projectId } = ctx;
  const [changingSeat, setChangingSeat] = useState(false);
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
                  <dd>{work.model}{work.provider
                    ? ` · ${registry.find((p) => p.name === work.provider)?.label ?? work.provider}`
                    : ""}{work.effort ? ` · ${work.effort} effort` : ""}
                    {work.overrides && Object.values(work.overrides).some(Boolean) && (
                      <span className="help"> · set for {seat.name}, not the role</span>
                    )}
                    {steer && seat.state !== "stood_down" && !changingSeat && (
                      <> <button className="small" onClick={() => setChangingSeat(true)}>Change…</button></>
                    )}
                    {changingSeat && (
                      <SeatEditor ctx={ctx} agentId={seat.id} name={seat.name} work={work}
                        onDone={() => setChangingSeat(false)} />
                    )}</dd>
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
