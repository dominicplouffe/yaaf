import { Fragment, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
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
}

function taskBadge(status: string) {
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

function verifyBadge(verification: string) {
  const cls =
    verification === "verified" || verification === "not_applicable"
      ? "good"
      : verification === "pending"
        ? "warn"
        : "critical";
  const label = verification === "not_applicable" ? "attested/internal" : verification;
  return <span className={`badge ${cls}`}>{label}</span>;
}

const FILTERS = [
  { key: "open", label: "Open", match: (s: string) => ["ready", "claimed", "checking"].includes(s) },
  { key: "blocked", label: "Blocked", match: (s: string) => s === "blocked" },
  { key: "done", label: "Done", match: (s: string) => s === "done" },
  { key: "killed", label: "Killed", match: (s: string) => s === "killed" },
  { key: "all", label: "All", match: () => true },
] as const;

const PROOF_FILTERS = [
  { key: "all", label: "All", match: (_r: EvidenceRow) => true },
  {
    key: "attested",
    label: "Platform-attested",
    match: (r: EvidenceRow) => r.provenance === "platform_attested",
  },
  {
    key: "claims",
    label: "Verified claims",
    match: (r: EvidenceRow) => r.provenance !== "platform_attested" && r.verification === "verified",
  },
  { key: "pending", label: "Pending", match: (r: EvidenceRow) => r.verification === "pending" },
  { key: "failed", label: "Failed checks", match: (r: EvidenceRow) => r.verification === "failed" },
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
export default function Work({ ctx }: { ctx: ProjectCtx }) {
  const { projectId, hypotheses } = ctx;
  // "?audit=1" arrives from the Overview attention row. Landing the chairman
  // on this page and making him hunt for the section, then press a button, is
  // two steps too many for something he was already told needed him — so the
  // link does the scrolling and the dealing.
  const [params, setParams] = useSearchParams();
  const auditRef = useRef<HTMLHeadingElement>(null);
  const { data: tasks, error } = usePoll(
    () => get<TaskRow[]>(`/observe/projects/${projectId}/board`),
    6000,
  );
  const { data: evidence, reload: reloadEvidence } = usePoll(
    () => get<EvidenceRow[]>(`/observe/projects/${projectId}/evidence?limit=500`),
    8000,
  );
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["key"]>("all");
  const [proofFilter, setProofFilter] = useState<(typeof PROOF_FILTERS)[number]["key"]>("all");
  const [showAll, setShowAll] = useState(false);
  const [audit, setAudit] = useState<SpotAudit | null>(null);

  async function auditEvidence(id: string, ok: boolean, note: string) {
    await post(`/admin/evidence/${id}/audit`, { ok, note });
    reloadEvidence();
    if (audit) runSpotAudit();
  }
  async function auditTask(id: string, ok: boolean, note: string) {
    await post(`/admin/tasks/${id}/audit`, { ok, note });
    if (audit) runSpotAudit();
  }
  async function runSpotAudit() {
    setAudit(await get<SpotAudit>(`/observe/projects/${projectId}/spot_audit?n=5`));
  }

  useEffect(() => {
    if (params.get("audit") !== "1") return;
    // Consume the parameter so a later reload doesn't silently re-deal a
    // different sample under him while he is part-way through this one.
    setParams({}, { replace: true });
    runSpotAudit().then(() =>
      auditRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

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

  const activeProofFilter = PROOF_FILTERS.find((f) => f.key === proofFilter)!;
  const matching = rows.filter(activeProofFilter.match);
  const shown = showAll ? matching : matching.slice(0, 50);
  const failedCount = rows.filter((r) => r.verification === "failed").length;

  function TaskDetails({ task }: { task: TaskRow }) {
    return (
      <details>
        <summary className="help">details</summary>
        <dl className="kv" style={{ margin: "8px 0" }}>
          <dt>success test</dt>
          <dd className="mono">
            [{task.success_test.type}] {task.success_test.command ?? task.success_test.target}
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
          {taskBadge(task.status)}
          {/* Blocked reasons on the row, in plain words — a blocked task is
              precisely the row that must be understood without clicking. */}
          {task.blocked_reason && (
            <div className="help" style={{ marginTop: 2 }}>{task.blocked_reason}</div>
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

      {/* ── the human layer: prompted, measured, still random ── */}
      <h2 className="zone" ref={auditRef}>Spot-check the agents' work</h2>
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
            Agents mark their own work finished and write their own evidence for it. This
            picks {completed.length > 0 ? "5" : "a few"} at random and shows you what each task
            had to prove next to what the agent claimed — you say whether you believe it. Two
            minutes. Nothing breaks if you skip it.
          </div>
        </div>
        <button className="primary" onClick={runSpotAudit} disabled={completed.length === 0 && rows.length === 0}>
          Show me 5 to check
        </button>
      </div>
      {audit && (
        <p className="help" style={{ margin: "10px 0 0" }}>
          For each one: <b>does the claim actually prove what the test asked for?</b> Passing
          records that you looked. Failing needs one line saying why, and the CEO is woken with
          it — that is the only one of the two that changes what the company does next.
        </p>
      )}
      {audit && (
        <div className="list-grid" style={{ marginTop: 12 }}>
          {audit.evidence.map((row) => (
            <div className="card" key={row.id}>
              <div className="pill-row">
                <span className="badge accent">evidence</span>
                {verifyBadge(row.verification)}
              </div>
              <p>{row.description}</p>
              {row.source_url && (
                <a href={row.source_url} target="_blank" rel="noreferrer">
                  source ↗
                </a>
              )}
              <div className="actions">
                <NoteAction
                  label="✓ passes audit"
                  className="small"
                  placeholder="Note (optional)"
                  onSubmit={(note) => auditEvidence(row.id, true, note)}
                />
                <NoteAction
                  label="✕ fails"
                  className="small danger"
                  placeholder="Why does this fail? — the CEO is told"
                  requireNote
                  onSubmit={(note) => auditEvidence(row.id, false, note)}
                />
              </div>
            </div>
          ))}
          {audit.completed_tasks.map((task) => (
            <div className="card" key={task.id}>
              <div className="pill-row">
                <span className="badge accent">completed task</span>
                {task.audit_status && (
                  <span className={`badge ${task.audit_status === "passed" ? "good" : "critical"}`}>
                    audit: {task.audit_status}
                  </span>
                )}
              </div>
              <strong>{task.title}</strong>
              {/* Test, claim, and platform check stacked — the gap between
                  them is what the chairman is judging. */}
              <dl className="kv" style={{ marginTop: 8 }}>
                <dt>test</dt>
                <dd className="mono">
                  [{task.success_test.type}] {task.success_test.command ?? task.success_test.target}
                </dd>
                <dt>claimed</dt>
                <dd>{task.evidence ?? "—"}</dd>
                {task.check_output && (
                  <>
                    <dt>check output</dt>
                    <dd>
                      <pre className="block">{task.check_output}</pre>
                    </dd>
                  </>
                )}
                {task.audit_note && (
                  <>
                    <dt>audit note</dt>
                    <dd>{task.audit_note}</dd>
                  </>
                )}
              </dl>
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
      )}

      <h2 className="zone">All proof <span className="cnt">{rows.length}</span></h2>
      <p className="subtitle" style={{ marginBottom: 8 }}>
        Only verified or platform-attested external evidence counts toward the drought and the
        external floor. Agent claims are fetched, hashed, and checked by the platform, never taken
        at their word.
      </p>
      {/* Failed proof is not a footnote: the platform fetched what an agent
          cited and did not find it. That work counted for nothing. */}
      {failedCount > 0 && failedCount >= rows.length / 4 && (
        <div className="error">
          <strong>{failedCount} of {rows.length} pieces of evidence failed verification.</strong>{" "}
          The platform fetched the source an agent cited and got a 404 or a mismatch. Failed proof
          counts for nothing — which is why the drought clock keeps running while work looks busy.
        </div>
      )}
      <div className="pills">
        {PROOF_FILTERS.map((f) => {
          const count = rows.filter(f.match).length;
          if (f.key !== "all" && count === 0) return null;
          return (
            <button
              key={f.key}
              className={proofFilter === f.key ? "active" : ""}
              onClick={() => setProofFilter(f.key)}
            >
              {f.label} {count}
            </button>
          );
        })}
        <ExportCsv
          onClick={() =>
            downloadCsv(
              "evidence.csv",
              ["when", "kind", "provenance", "verification", "description", "source_url"],
              rows.map((r) => [r.at, r.kind, r.provenance, r.verification, r.description, r.source_url]),
            )
          }
        />
      </div>
      <table>
        <thead>
          <tr>
            <th>When</th>
            <th>Proof</th>
            <th style={{ width: "38%" }}>What happened</th>
            <th>Source</th>
            <th>Your audit</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((row) => (
            <tr key={row.id}>
              <td className="mono">{fmtWhen(row.at)}</td>
              <td>
                {verifyBadge(row.verification)}
                <div className="help mono" style={{ marginTop: 2 }}>
                  {row.provenance.replaceAll("_", " ")} · {row.kind}
                </div>
                {row.verifier_notes && (
                  <details>
                    <summary className="help">notes</summary>
                    <pre className="block">{row.verifier_notes}</pre>
                  </details>
                )}
              </td>
              <td>{row.description}</td>
              <td>
                {row.source_url ? (
                  <a href={row.source_url} target="_blank" rel="noreferrer">
                    link ↗
                  </a>
                ) : (
                  "—"
                )}
              </td>
              <td>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  <NoteAction
                    label="✓"
                    className="small"
                    placeholder="Note (optional)"
                    confirmLabel="✓ passes"
                    onSubmit={(note) => auditEvidence(row.id, true, note)}
                  />
                  <NoteAction
                    label="✕"
                    className="small danger"
                    placeholder="Why does this fail?"
                    confirmLabel="✕ fails"
                    requireNote
                    onSubmit={(note) => auditEvidence(row.id, false, note)}
                  />
                </div>
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td colSpan={5} className="help">
                Nothing recorded yet. Internal work never appears here — only the outside world
                acting.
              </td>
            </tr>
          )}
          {rows.length > 0 && shown.length === 0 && (
            <tr>
              <td colSpan={5} className="help">Nothing matches this filter.</td>
            </tr>
          )}
        </tbody>
      </table>
      {matching.length > shown.length && (
        <div style={{ marginTop: 8 }}>
          <button className="small" onClick={() => setShowAll(true)}>
            show all {matching.length} rows
          </button>
        </div>
      )}
    </>
  );
}
