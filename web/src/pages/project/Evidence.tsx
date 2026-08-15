import { useState } from "react";
import { EvidenceRow, get, HypothesisRow, post } from "../../api";
import { fmtWhen, usePoll } from "../../hooks";

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

export default function EvidenceTab({ projectId }: { projectId: string }) {
  const { data: evidence, reload: reloadEvidence } = usePoll(
    () => get<EvidenceRow[]>(`/observe/projects/${projectId}/evidence`),
    8000,
  );
  const { data: hypotheses, reload: reloadHypotheses } = usePoll(
    () => get<HypothesisRow[]>(`/observe/projects/${projectId}/hypotheses`),
    10000,
  );
  const [audit, setAudit] = useState<SpotAudit | null>(null);

  async function extend(hypothesis: HypothesisRow) {
    const date = prompt("New kill date (YYYY-MM-DD) — you get exactly one extension:");
    if (!date) return;
    const reason = prompt("Reason (recorded, disclosed):");
    if (!reason) return;
    await post(`/admin/hypotheses/${hypothesis.id}/extend`, {
      new_kill_date: new Date(date + "T23:59:59Z").toISOString(),
      reason,
    });
    reloadHypotheses();
  }

  async function auditEvidence(id: string, ok: boolean) {
    const note = prompt(ok ? "Audit note (optional):" : "Why does this fail your audit?") ?? "";
    await post(`/admin/evidence/${id}/audit`, { ok, note });
    reloadEvidence();
    if (audit) runSpotAudit();
  }

  async function auditTask(id: string, ok: boolean) {
    const note = prompt(ok ? "Audit note (optional):" : "Why does this fail your audit?") ?? "";
    await post(`/admin/tasks/${id}/audit`, { ok, note });
    if (audit) runSpotAudit();
  }

  async function runSpotAudit() {
    setAudit(await get<SpotAudit>(`/observe/projects/${projectId}/spot_audit?n=5`));
  }

  const hypothesisBadge = (status: string) => {
    const cls = status === "active" ? "good" : status === "expired" ? "serious" : "critical";
    return <span className={`badge ${cls}`}>{status}</span>;
  };

  return (
    <>
      <h2 style={{ marginTop: 0 }}>Hypotheses</h2>
      <p className="subtitle">
        Falsifiable bets with kill dates. Expiry is automatic; only you can extend, exactly once.
      </p>
      <table>
        <thead>
          <tr>
            <th style={{ width: "40%" }}>Statement</th>
            <th>Kill condition</th>
            <th>Kill date</th>
            <th>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {(hypotheses ?? []).map((hypothesis) => (
            <tr key={hypothesis.id}>
              <td>{hypothesis.statement}</td>
              <td>{hypothesis.kill_condition}</td>
              <td className="mono">{fmtWhen(hypothesis.kill_date)}</td>
              <td>{hypothesisBadge(hypothesis.status)}</td>
              <td>
                {hypothesis.status === "expired" && !hypothesis.extended_once && (
                  <button className="small" onClick={() => extend(hypothesis)}>
                    extend (once)
                  </button>
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

      <h2>Evidence</h2>
      <p className="subtitle">
        Only verified or platform-attested external evidence counts toward the drought and the
        external floor. Agent claims are fetched, hashed, and checked by the platform, never taken
        at their word.
      </p>
      <table>
        <thead>
          <tr>
            <th>When</th>
            <th>Kind</th>
            <th>Verification</th>
            <th style={{ width: "40%" }}>Description</th>
            <th>Source</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {(evidence ?? []).map((row) => (
            <tr key={row.id}>
              <td className="mono">{fmtWhen(row.at)}</td>
              <td className="mono">{row.kind}</td>
              <td>
                {verifyBadge(row.verification)}
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
                <button className="small" onClick={() => auditEvidence(row.id, true)}>✓</button>{" "}
                <button className="small danger" onClick={() => auditEvidence(row.id, false)}>✕</button>
              </td>
            </tr>
          ))}
          {evidence && evidence.length === 0 && (
            <tr>
              <td colSpan={6} className="help">
                Nothing recorded yet. Internal work never appears here — only the outside world
                acting.
              </td>
            </tr>
          )}
        </tbody>
      </table>

      <h2>Spot audit</h2>
      <p className="subtitle">
        A random sample for the human layer — the one reviewer that doesn't approve everything put
        in front of it.
      </p>
      <button onClick={runSpotAudit}>Draw a sample</button>
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
                <button className="small" onClick={() => auditEvidence(row.id, true)}>
                  ✓ passes audit
                </button>
                <button className="small danger" onClick={() => auditEvidence(row.id, false)}>
                  ✕ fails
                </button>
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
                <button className="small" onClick={() => auditTask(task.id, true)}>
                  ✓ passes audit
                </button>
                <button className="small danger" onClick={() => auditTask(task.id, false)}>
                  ✕ fails
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
