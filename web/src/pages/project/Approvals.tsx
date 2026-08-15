import { ApprovalRow, get, post } from "../../api";
import { fmtUsd, fmtWhen, usePoll } from "../../hooks";

export default function Approvals({ projectId }: { projectId: string }) {
  const { data: approvals, error, reload } = usePoll(
    () => get<ApprovalRow[]>(`/observe/projects/${projectId}/approvals`),
    6000,
  );
  if (error) return <div className="error">{error}</div>;

  async function decide(approval: ApprovalRow, approve: boolean) {
    const note = prompt(approve ? "Approval note (optional):" : "Why deny? (the agent reads this)") ?? "";
    await post(`/admin/approvals/${approval.id}/decide`, { approve, note });
    reload();
  }

  const pending = (approvals ?? []).filter((a) => a.status === "pending");
  const decided = (approvals ?? []).filter((a) => a.status !== "pending");

  const statusBadge = (status: string) => {
    const cls = status === "approved" ? "good" : status === "pending" ? "warn" : "critical";
    return <span className={`badge ${cls}`}>{status}</span>;
  };

  return (
    <>
      <p className="subtitle">
        The three things that legitimately reach you: big spends, legal ambiguity, owner-only
        decisions — framed as recommendations. Undecided approvals expire to DENY, so silence never
        deadlocks the project.
      </p>
      {pending.length > 0 && <h2>Waiting on you</h2>}
      {pending.map((approval) => (
        <div className="card" key={approval.id} style={{ marginBottom: 10 }}>
          <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
            <strong>{approval.title}</strong>
            <span className="badge accent">{approval.kind}</span>
            {approval.amount_usd !== null && <span className="mono">{fmtUsd(approval.amount_usd)}</span>}
            <div className="spacer" />
            <span className="help">expires {fmtWhen(approval.expires_at)} → deny</span>
          </div>
          <p style={{ whiteSpace: "pre-wrap", margin: "8px 0" }}>{approval.body}</p>
          <div className="actions">
            <button className="primary small" onClick={() => decide(approval, true)}>
              ✓ Approve
            </button>
            <button className="danger small" onClick={() => decide(approval, false)}>
              ✕ Deny
            </button>
          </div>
        </div>
      ))}

      <h2>History</h2>
      <table>
        <thead>
          <tr>
            <th>Title</th>
            <th>Kind</th>
            <th className="num">Amount</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {decided.map((approval) => (
            <tr key={approval.id}>
              <td>{approval.title}</td>
              <td className="mono">{approval.kind}</td>
              <td className="num">{fmtUsd(approval.amount_usd)}</td>
              <td>{statusBadge(approval.status)}</td>
            </tr>
          ))}
          {decided.length === 0 && pending.length === 0 && (
            <tr>
              <td colSpan={4} className="help">
                Nothing yet — agents file approvals only for what the constitution says is yours.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </>
  );
}
