import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { EvidenceRow, get, HypothesisRow, post, TaskRow } from "../../api";
import { fmtWhen, usePoll } from "../../hooks";
import {
  downloadCsv,
  ExportCsv,
  fmtWhenShort,
  killChip,
  NoteAction,
  Skeleton,
} from "../../ui";
import type { ProjectCtx } from "../Project";

interface SpotAudit {
  evidence: { id: string; kind: string; provenance: string; verification: string;
    description: string; source_url: string | null }[];
  completed_tasks: { id: string; title: string;
    success_test: { type: string; command?: string; target?: string };
    evidence: string | null; check_output: string | null;
    audit_status: string | null; audit_note: string | null }[];
  /** How many of each nobody has judged, and how many exist. The lists above
      are capped, so the page has to be able to say what it is not showing. */
  unjudged: { evidence: number; tasks: number };
  totals: { evidence: number; tasks: number };
}

/** A labelled block of agent prose, clamped to three lines until asked.
    Evidence text is unbounded — one claim in the wild runs forty lines — and a
    card that long next to a card of four made the row look broken and buried
    the one question being asked. */
function Block({ title, children, clampable = true }: {
  title: string;
  children: React.ReactNode;
  clampable?: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="ab">
      <div className="ab-h">{title}</div>
      <div className={`ab-b${clampable && !open ? " clamp" : ""}`}>{children}</div>
      {clampable && (
        <button className="more" onClick={() => setOpen(!open)}>
          {open ? "show less" : "show all"}
        </button>
      )}
    </div>
  );
}

/** A gated task is stored as READY, and rendering it as "ready" is exactly how
    a project sat silent for two hours behind a start time nobody could see:
    the row said work was in hand, and it was — just not yet claimable by
    anyone. "scheduled" is the honest word for it. */
function taskBadge(status: string, notBefore?: string | null) {
  if (status === "ready" && notBefore && new Date(notBefore) > new Date())
    return <span className="badge warn">scheduled</span>;
  const cls =
    status === "done"
      ? "good"
      : status === "blocked"
        ? "serious"
        : status === "killed"
          ? "critical"
          : status === "checking"
            ? "warn"
            : "accent";
  return <span className={`badge ${cls}`}>{status}</span>;
}

const FILTERS = [
  { key: "open", label: "Open",
    match: (s: string) => ["ready", "claimed", "checking", "review"].includes(s) },
  { key: "blocked", label: "Blocked", match: (s: string) => s === "blocked" },
  { key: "done", label: "Done", match: (s: string) => s === "done" },
  { key: "killed", label: "Killed", match: (s: string) => s === "killed" },
  { key: "all", label: "All", match: () => true },
] as const;

/** Inline one-time extension: date + reason, recorded and disclosed.
    The chairman's single lever over a dead bet — never a window.prompt(). */
function ExtendOnce({ hypothesis, onDone }: { hypothesis: HypothesisRow; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [date, setDate] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open) {
    return (
      <button className="small" onClick={() => setOpen(true)}>
        extend (once)…
      </button>
    );
  }
  async function go() {
    setBusy(true);
    setError(null);
    try {
      await post(`/admin/hypotheses/${hypothesis.id}/extend`, {
        new_kill_date: new Date(date + "T23:59:59Z").toISOString(),
        reason: reason.trim(),
      });
      setOpen(false);
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <span className="note-action">
      <input type="date" style={{ width: 140 }} value={date} onChange={(e) => setDate(e.target.value)} />
      <input
        value={reason}
        placeholder="Why? — recorded, disclosed to the CEO"
        onChange={(e) => setReason(e.target.value)}
      />
      <button className="small primary" disabled={busy || !date || reason.trim().length < 3} onClick={go}>
        extend
      </button>
      <button className="small" onClick={() => setOpen(false)}>cancel</button>
      {error && <span className="help" style={{ color: "var(--critical)" }}>{error}</span>}
    </span>
  );
}

/** The plan and its proof, as one page. A bet owns tasks; a task produces
    evidence. These were two tabs, so answering "has this bet been proven?"
    meant holding the join in your head and switching back and forth. */
interface AuditSampleRow {
  id: string;
  task_id: string;
  status: string;
  drawn_at: string;
  expires_at: string | null;
  task_title: string;
  test_target: string;
  claimed_evidence: string | null;
}

/** The sampled audit's desk. The platform drew these observable closes at the
    rate the constitution sets; unanswered ones expire to "not reviewed" and
    never block — the buttons are the whole ceremony. */
function AuditSamples({ projectId }: { projectId: string }) {
  const { data, reload } = usePoll(
    () => get<{ pending: AuditSampleRow[] }>(
      `/observe/projects/${projectId}/audit_samples`),
    20000,
  );
  const pending = data?.pending ?? [];
  if (pending.length === 0) return null;
  return (
    <div className="strip" style={{ marginBottom: 14 }}>
      <div className="grow">
        <b>The platform sampled {pending.length} close{pending.length === 1 ? "" : "s"} for
          your look.</b>
        <div className="sb">
          Confirm the claim held or fail it. Unanswered samples expire to “not reviewed”
          and nothing blocks either way.
        </div>
        {pending.map((s) => (
          <div key={s.id} style={{ display: "flex", gap: 8, alignItems: "baseline",
            flexWrap: "wrap", marginTop: 8 }}>
            <span style={{ flex: 1, minWidth: 240 }}>
              <b>{s.task_title}</b>
              <span className="help"> — test: {s.test_target || "(none)"} · claimed: {" "}
                {(s.claimed_evidence ?? "").slice(0, 160) || "(no evidence text)"}</span>
            </span>
            <NoteAction label="Held ✓" className="small primary"
              placeholder="What you checked (optional)" confirmLabel="✓ It held"
              onSubmit={async (note) => {
                await post(`/admin/audit_samples/${s.id}/decide`, { ok: true, note });
                reload();
              }} />
            <NoteAction label="Failed ✗" className="small danger"
              placeholder="What was wrong — the CEO reads this" confirmLabel="✗ It failed"
              requireNote
              onSubmit={async (note) => {
                await post(`/admin/audit_samples/${s.id}/decide`, { ok: false, note });
                reload();
              }} />
          </div>
        ))}
      </div>
    </div>
  );
}

export default function Work({ ctx }: { ctx: ProjectCtx }) {
  const { projectId, hypotheses } = ctx;
  const { data: tasks, error } = usePoll(
    () => get<TaskRow[]>(`/observe/projects/${projectId}/board`),
    6000,
  );
  // Evidence still feeds this page, but only as the bet-level tally: "has this
  // bet been proven?" is the join the page exists for. The rows themselves are
  // a record and read in Records → Evidence.
  const { data: evidence } = usePoll(
    () => get<EvidenceRow[]>(`/observe/projects/${projectId}/evidence?limit=500`),
    8000,
  );
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["key"]>("all");
  const [audit, setAudit] = useState<SpotAudit | null>(null);

  async function auditTask(id: string, ok: boolean, note: string) {
    await post(`/admin/tasks/${id}/audit`, { ok, note });
    if (audit) runSpotAudit();
  }
  // No `n`: everything, unjudged first. Five at random was the old behaviour
  // and it read as a slot machine — a different five on every verdict, no way
  // to tell a finished backlog from a lucky draw.
  async function runSpotAudit() {
    setAudit(await get<SpotAudit>(`/observe/projects/${projectId}/spot_audit`));
  }

  const all = tasks ?? [];
  const rows = evidence ?? [];
  const active = FILTERS.find((f) => f.key === filter)!;
  const top = all.filter((t) => !t.parent_task_id);
  const subtasksOf = (id: string) => all.filter((t) => t.parent_task_id === id);
  // A group survives the filter if the bet's top task or any subtask matches —
  // a blocked subtask should never hide under a filtered-out parent.
  const visible = top.filter(
    (t) => active.match(t.status) || subtasksOf(t.id).some((s) => active.match(s.status)),
  );

  // evidence -> task -> hypothesis. The chip this produces is the whole reason
  // these two tabs became one: it answers the question the bet asks.
  const betOfTask = new Map(all.map((t) => [t.id, t.hypothesis_id]));
  const proofByBet = new Map<string, { verified: number; failed: number; pending: number }>();
  for (const e of rows) {
    if (!e.task_id) continue;
    const betId = betOfTask.get(e.task_id);
    if (!betId) continue;
    const tally = proofByBet.get(betId) ?? { verified: 0, failed: 0, pending: 0 };
    if (e.verification === "verified" || e.verification === "not_applicable") tally.verified += 1;
    else if (e.verification === "failed") tally.failed += 1;
    else tally.pending += 1;
    proofByBet.set(betId, tally);
  }

  const byHypothesis = new Map<string | null, TaskRow[]>();
  for (const t of visible) {
    byHypothesis.set(t.hypothesis_id, [...(byHypothesis.get(t.hypothesis_id) ?? []), t]);
  }
  // Every live bet gets a card even with no tasks on it — a bet nobody is
  // working, with a kill date approaching, is exactly what must not be silent.
  for (const h of hypotheses) {
    if (h.status === "active" && !byHypothesis.has(h.id)) byHypothesis.set(h.id, []);
  }
  const hypothesisById = new Map(hypotheses.map((h) => [h.id, h]));
  const groups = [...byHypothesis.entries()].sort(([a], [b]) => {
    if (!a) return 1;
    if (!b) return -1;
    const ha = hypothesisById.get(a);
    const hb = hypothesisById.get(b);
    const rank = (h?: HypothesisRow) => (h?.status === "active" ? 0 : 1);
    if (rank(ha) !== rank(hb)) return rank(ha) - rank(hb);
    return (ha?.kill_date ?? "").localeCompare(hb?.kill_date ?? "");
  });

  const completed = all.filter((t) => t.status === "done");
  const audited = completed.filter((t) => t.audit_status).length;
  const coveragePct = completed.length > 0 ? Math.round((audited / completed.length) * 100) : null;

  function TaskDetails({ task }: { task: TaskRow }) {
    return (
      <details>
        <summary className="help">details</summary>
        <dl className="kv" style={{ margin: "8px 0" }}>
          <dt>success test</dt>
          <dd>
            {/* pre.block wraps and scrolls; a bare mono cell could not shrink
                below one long `python3 -c "…"` and pushed the row wide. */}
            <pre className="block" style={{ margin: 0 }}>
              [{task.success_test.type}] {task.success_test.command ?? task.success_test.target}
            </pre>
          </dd>
          {task.evidence && (
            <>
              <dt>completion evidence</dt>
              <dd>{task.evidence}</dd>
            </>
          )}
          {task.check_output && (
            <>
              <dt>platform check output</dt>
              <dd>
                <pre className="block">{task.check_output}</pre>
              </dd>
            </>
          )}
          {task.audit_status && (
            <>
              <dt>your audit</dt>
              <dd>{task.audit_status}</dd>
            </>
          )}
        </dl>
      </details>
    );
  }

  function row(task: TaskRow, sub: boolean) {
    return (
      <tr key={task.id} className={sub ? "subtask" : undefined}>
        <td>
          {sub ? <>↳ {task.title}</> : <strong>{task.title}</strong>}
          <TaskDetails task={task} />
        </td>
        <td>{sub ? "" : task.team_name ?? "—"}</td>
        <td>
          {taskBadge(task.status, task.not_before)}
          {/* Blocked reasons on the row, in plain words — a blocked task is
              precisely the row that must be understood without clicking. */}
          {task.blocked_reason && (
            <div className="help" style={{ marginTop: 2 }}>{task.blocked_reason}</div>
          )}
          {task.status === "ready" && task.not_before &&
            new Date(task.not_before) > new Date() && (
              <div className="help" style={{ marginTop: 2 }}>
                nobody may claim it until {fmtWhen(task.not_before)}
              </div>
            )}
        </td>
        <td>{sub ? "" : task.external ? "✓ external" : "internal"}</td>
        <td>{task.holder_name ?? "—"}</td>
      </tr>
    );
  }

  function proofChips(betId: string) {
    const tally = proofByBet.get(betId);
    if (!tally || tally.verified + tally.failed + tally.pending === 0) {
      return <span className="chip">no proof yet</span>;
    }
    return (
      <>
        {tally.verified > 0 && <span className="chip ok">{tally.verified} verified</span>}
        {tally.failed > 0 && <span className="chip critical">{tally.failed} failed</span>}
        {tally.pending > 0 && <span className="chip warn">{tally.pending} pending</span>}
      </>
    );
  }

  if (error && !tasks) return <div className="error">{error}</div>;

  return (
    <>
      <p className="subtitle">
        The plan and whether it has been proven. Bets set the direction, tasks do the work, and
        evidence decides whether any of it counted.
      </p>

      {/* ── the human layer: prompted, measured, still random ──
          Two audits used to sit at opposite ends of this page, separated by
          the whole task board, asking the chairman the same question. One
          zone: the platform's samples first because they expire, then the
          draw he asks for himself. */}
      <h2 className="zone">Check the agents' work</h2>
      <AuditSamples projectId={projectId} />
      <div className="arow">
        <div className="grow">
          <div className="tt">
            {completed.length === 0
              ? "Nothing finished yet — there is nothing to check"
              : `You have checked ${audited} of ${completed.length} finished tasks${
                  coveragePct !== null ? ` (${coveragePct}%)` : ""
                }`}
          </div>
          <div className="sb">
            Agents mark their own work finished and write their own evidence for it. This puts
            every finished task next to what it had to prove — you say whether you believe it.
            Nothing breaks if you skip it, and nothing is lost if you stop half way: each
            verdict is recorded as you give it.
          </div>
        </div>
        <button className="primary" onClick={runSpotAudit} disabled={completed.length === 0}>
          {audit ? "Reload the list" : "Show me everything to check"}
        </button>
      </div>
      {audit && (
        <p className="help" style={{ margin: "10px 0 0" }}>
          For each one: <b>does the claim actually prove what the test asked for?</b> Passing
          records that you looked. Failing needs one line saying why, and the CEO is woken with
          it — that is the only one of the two that changes what the project does next.
        </p>
      )}
      {audit && audit.completed_tasks.length > 0 && (
        <>
        <div className="audit-h">
          Finished tasks — {audit.unjudged.tasks} unchecked of {audit.totals.tasks}: does the
          claim prove the test?
          {audit.completed_tasks.length < audit.totals.tasks && (
            <span className="dim"> · showing the first {audit.completed_tasks.length}</span>
          )}
        </div>
        <div className="audit-grid">
          {audit.completed_tasks.map((task) => (
            <div className="card audit-card" key={task.id}>
              <div className="pill-row">
                <span className="badge accent">completed task</span>
                {task.audit_status && (
                  <span className={`badge ${task.audit_status === "passed" ? "good" : "critical"}`}>
                    audit: {task.audit_status}
                  </span>
                )}
              </div>
              <strong>{task.title}</strong>
              {/* The gap between what the test asked for and what the agent
                  says it did is the whole judgement, so those two sit adjacent
                  and equally weighted. The platform's own check is corroboration
                  and folds away. */}
              <Block title="what it had to prove" clampable={false}>
                <pre className="block" style={{ margin: 0 }}>
                  [{task.success_test.type}]{" "}
                  {task.success_test.command ?? task.success_test.target}
                </pre>
              </Block>
              <Block title="what the agent claimed">{task.evidence ?? "—"}</Block>
              {(task.check_output || task.audit_note) && (
                <details style={{ marginTop: 8 }}>
                  <summary className="help">platform check &amp; notes</summary>
                  <dl className="kv" style={{ marginTop: 6 }}>
                    {task.check_output && (
                      <>
                        <dt>check output</dt>
                        <dd><pre className="block">{task.check_output}</pre></dd>
                      </>
                    )}
                    {task.audit_note && (
                      <>
                        <dt>audit note</dt>
                        <dd>{task.audit_note}</dd>
                      </>
                    )}
                  </dl>
                </details>
              )}
              <div className="actions">
                <NoteAction
                  label="✓ passes audit"
                  className="small"
                  placeholder="Note (optional)"
                  onSubmit={(note) => auditTask(task.id, true, note)}
                />
                <NoteAction
                  label="✕ fails"
                  className="small danger"
                  placeholder="Why? — flags this agent's stream to the CEO"
                  requireNote
                  onSubmit={(note) => auditTask(task.id, false, note)}
                />
              </div>
            </div>
          ))}
        </div>
        </>
      )}

      {/* The board was the page's first section and needed no heading. Now that
          the audit sits above it, these filter pills butted straight against the
          last audit card — two borders on one line, and nothing saying the
          filters belong to the tasks below rather than the check above. */}
      <h2 className="zone">The board <span className="cnt">{all.length}</span></h2>
      <div className="pills">
        {FILTERS.map((f) => {
          const count = all.filter((t) => f.match(t.status)).length;
          return (
            <button
              key={f.key}
              className={filter === f.key ? "active" : ""}
              onClick={() => setFilter(f.key)}
            >
              {f.label} {count}
            </button>
          );
        })}
        <ExportCsv
          onClick={() =>
            downloadCsv(
              "tasks.csv",
              ["title", "parent", "team", "status", "external", "holder", "success_test", "blocked_reason", "audit"],
              all.map((t) => [
                t.title,
                t.parent_task_id ?? "",
                t.team_name,
                t.status,
                t.external ? "external" : "internal",
                t.holder_name,
                `[${t.success_test.type}] ${t.success_test.command ?? t.success_test.target ?? ""}`,
                t.blocked_reason,
                t.audit_status,
              ]),
            )
          }
        />
      </div>

      {!tasks ? (
        <Skeleton rows={4} />
      ) : (
        groups.map(([hypothesisId, groupTasks]) => {
          const h = hypothesisId ? hypothesisById.get(hypothesisId) : null;
          return (
            <div className="bet" key={hypothesisId ?? "standing"}>
              <div className="bh">
                <div className="top">
                  <span className="tag">{h ? "BET" : "STANDING WORK"}</span>
                  {h && killChip(h.kill_date, h.status)}
                  <div className="spacer" />
                  {h && proofChips(h.id)}
                </div>
                {h ? (
                  <>
                    <div className="st">{h.statement}</div>
                    <div className="conseq">
                      If it dies: {h.kill_condition}
                      {h.status === "expired" && !h.extended_once && (
                        <> <ExtendOnce hypothesis={h} onDone={ctx.reload} /></>
                      )}
                      {h.extended_once && <span className="help"> · extension used</span>}
                    </div>
                  </>
                ) : (
                  <div className="st">Work that serves no stated bet</div>
                )}
              </div>
              {groupTasks.length === 0 ? (
                <div className="empty">
                  Nothing is assigned to this bet
                  {h && h.status === "active" && <> — and it dies {fmtWhenShort(h.kill_date)}</>}.{" "}
                  <Link to={`/projects/${projectId}/boardroom`}>ask the CEO why →</Link>
                </div>
              ) : (
                <table>
                  <thead>
                    <tr>
                      <th style={{ width: "44%" }}>Task</th>
                      <th>Team</th>
                      <th>Status</th>
                      <th>Proof type</th>
                      <th>Holder</th>
                    </tr>
                  </thead>
                  <tbody>
                    {groupTasks.map((task) => (
                      <Fragment key={task.id}>
                        {row(task, false)}
                        {subtasksOf(task.id).map((subtask) => row(subtask, true))}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          );
        })
      )}
      {tasks && visible.length === 0 && groups.length === 0 && (
        <p className="help">
          {all.length === 0
            ? "No tasks yet — the CEO plans on its heartbeat, or immediately after you speak in the boardroom."
            : "Nothing matches this filter."}
        </p>
      )}
      <p className="help" style={{ marginTop: 12 }}>
        Read-only by design: only the CEO creates tasks, only leads decompose, only the platform
        marks command-tested work done. Redirect the plan from the{" "}
        <Link to={`/projects/${projectId}/boardroom`}>Boardroom →</Link>
      </p>

      <p className="help" style={{ marginTop: 12 }}>
        Every claim the agents made and what the platform could check about it is the record in{" "}
        <Link to={`/projects/${projectId}/records/evidence`}>Records → Evidence →</Link>
      </p>
    </>
  );
}
