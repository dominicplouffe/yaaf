import { useState } from "react";
import { EvidenceRow, get, HypothesisRow, post, TaskRow } from "../../api";
import { fmtWhen, usePoll } from "../../hooks";
import { downloadCsv, ExportCsv, fmtWhenShort, killChip, NoteAction } from "../../ui";

interface SpotAudit {
  evidence: { id: string; kind: string; provenance: string; verification: string;
    description: string; source_url: string | null }[];
  completed_tasks: { id: string; title: string;
    success_test: { type: string; command?: string; target?: string };
    evidence: string | null; check_output: string | null;
    audit_status: string | null; audit_note: string | null }[];
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

export default function EvidenceTab({ projectId }: { projectId: string }) {
  const { data: evidence, reload: reloadEvidence } = usePoll(
    () => get<EvidenceRow[]>(`/observe/projects/${projectId}/evidence`),
    8000,
  );
  const { data: hypotheses, reload: reloadHypotheses } = usePoll(
    () => get<HypothesisRow[]>(`/observe/projects/${projectId}/hypotheses`),
    10000,
  );
  const { data: tasks } = usePoll(
    () => get<TaskRow[]>(`/observe/projects/${projectId}/board`),
    10000,
  );
  const [audit, setAudit] = useState<SpotAudit | null>(null);
  const [proofFilter, setProofFilter] = useState<(typeof PROOF_FILTERS)[number]["key"]>("all");

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

  const completed = (tasks ?? []).filter((t) => t.status === "done");
  const audited = completed.filter((t) => t.audit_status).length;
  const coveragePct = completed.length > 0 ? Math.round((audited / completed.length) * 100) : null;

  const rows = (evidence ?? []);
  const activeFilter = PROOF_FILTERS.find((f) => f.key === proofFilter)!;
  const shown = rows.filter(activeFilter.match);

  return (
    <>
      {/* ── the human layer: prompted, measured, still random ── */}
      <h2 className="zone" style={{ marginTop: 6 }}>Your audit</h2>
      <div className="arow">
        <div className="grow">
          <div className="tt">
            {completed.length === 0
              ? "No completions to audit yet"
              : `Coverage: ${audited} of ${completed.length} completions ever audited${
                  coveragePct !== null ? ` (${coveragePct}%)` : ""
                }`}
          </div>
          <div className="sb">
            {completed.length > audited
              ? `${completed.length - audited} never audited · the platform draws the sample — you judge it`
              : "the platform draws the sample — you're the reviewer that doesn't approve everything"}
          </div>
        </div>
        <button className="primary" onClick={runSpotAudit} disabled={completed.length === 0 && rows.length === 0}>
          Deal 5 cards
        </button>
      </div>
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

      <h2 className="zone">Hypotheses — the CEO's bets</h2>
      <p className="subtitle" style={{ marginBottom: 8 }}>
        Falsifiable bets with kill dates. Expiry is automatic; only you can extend, exactly once.
      </p>
      <table>
        <thead>
          <tr>
            <th style={{ width: "36%" }}>Statement</th>
            <th>Kill condition</th>
            <th>Kill date</th>
            <th>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {(hypotheses ?? []).map((hypothesis) => (
            <tr key={hypothesis.id}>
              <td><strong>{hypothesis.statement}</strong></td>
              <td>{hypothesis.kill_condition}</td>
              <td className="mono">{fmtWhenShort(hypothesis.kill_date)}</td>
              <td>{killChip(hypothesis.kill_date, hypothesis.status)}</td>
              <td>
                {hypothesis.status === "expired" && !hypothesis.extended_once && (
                  <ExtendOnce hypothesis={hypothesis} onDone={reloadHypotheses} />
                )}
                {hypothesis.extended_once && <span className="help">extension used</span>}
              </td>
            </tr>
          ))}
          {hypotheses && hypotheses.length === 0 && (
            <tr>
              <td colSpan={5} className="help">
                None yet — the CEO states bets before spending effort on them.
              </td>
            </tr>
          )}
        </tbody>
      </table>

      <h2 className="zone">Evidence — the outside world acting</h2>
      <p className="subtitle" style={{ marginBottom: 8 }}>
        Only verified or platform-attested external evidence counts toward the drought and the
        external floor. Agent claims are fetched, hashed, and checked by the platform, never taken
        at their word.
      </p>
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
    </>
  );
}
