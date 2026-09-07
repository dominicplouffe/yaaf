import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AmendmentRow, ApprovalRow, AskRow, CeoDecisionRow, get, post } from "../../api";
import { fmtUsd, fmtWhen, usePoll } from "../../hooks";
import { grantRoles, RoleGrants } from "../../roles";
import { clockClass, downloadCsv, ExportCsv, fmtLeft } from "../../ui";
import type { ProjectCtx } from "../Project";

/** Which constitutional rule put this in front of the human, in one sentence. */
function whyYours(kind: string): string {
  switch (kind) {
    case "spend":
      return "It exceeds the approval threshold in the constitution — money above that line is never an agent's call.";
    case "legal":
      return "Anything with legal weight is a chairman decision by rule, regardless of amount.";
    case "owner_decision":
      return "The CEO judged this a direction change, not an execution detail. On a project whose authority dial hands it the plan, the scope and the date, this kind is refused instead — it decides and tells you.";
    case "tool_request":
      return "An agent is asking for a capability it does not have. Only you can grant one.";
    case "capacity":
      return "The project is asking for more room to work — runs, concurrency, queue depth or money. It is the one kind where the CEO argues for itself, so it stays yours at every autonomy setting. It is not blocking: the project keeps working at the cap it has, and if you say nothing the cap simply stays.";
    case "assist":
      return "A step only a human can do — a captcha, a 2FA code, a form. Your note travels back verbatim (a code, a 'done', a 'no') and wakes the agent. The platform hands over security challenges; it never defeats them.";
    default:
      return "The constitution routes this kind of decision to the chairman.";
  }
}

function outcomeBadge(a: ApprovalRow) {
  if (a.status === "approved") return <span className="badge good">approved</span>;
  if (a.status === "denied") return <span className="badge critical">denied</span>;
  if (a.status === "expired") return <span className="badge critical">expired → denied</span>;
  return <span className="badge warn">{a.status}</span>;
}

function amendmentBadge(status: AmendmentRow["status"]) {
  const cls = status === "applied" ? "good" : status === "cancelled" ? "critical" : "warn";
  return <span className={`badge ${cls}`}>{status}</span>;
}

/** One decision, with the whole case laid out: what was asked, why it is
    yours, what each answer costs. Silence is an answer here, so it says so. */
function DecisionCard({
  approval,
  dash,
  org,
  onDecided,
}: {
  approval: ApprovalRow;
  dash: ProjectCtx["dash"];
  /** The roster — already polled for the whole project page, so the picker
      below costs no extra request. */
  org: ProjectCtx["org"];
  onDecided: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  // The one-motion fulfillment for a need (kind=tool_request): credential
  // stored, roles scoped, need approved — one form instead of three screens.
  const [secretName, setSecretName] = useState("");
  const [secretValue, setSecretValue] = useState("");
  const [roles, setRoles] = useState<string[]>([]);

  // The agent that filed the need is on a team, and that team's role is the
  // answer nine times out of ten. Proposing it makes this a confirmation
  // rather than a question — the same relationship the CEO has with him.
  useEffect(() => {
    if (approval.kind !== "tool_request" || roles.length > 0 || !org) return;
    const asked = grantRoles(org, approval.requested_by).find((r) => r.asked);
    if (asked) setRoles([asked.slug]);
  }, [org, approval.kind, approval.requested_by]);

  const usdMode = dash.billing_mode === "api_key";
  const spent = dash.month_to_date.usd;
  const cap = dash.caps.monthly_usd_cap;
  const after = spent + (approval.amount_usd ?? 0);
  const overruns = usdMode && approval.amount_usd !== null && after > cap;

  async function decide(approved: boolean) {
    if (!approved && note.trim().length === 0) {
      setError("A denial needs a reason — the agent reads it and re-plans from it.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await post(`/admin/approvals/${approval.id}/decide`, {
        approve: approved,
        note: note.trim(),
      });
      onDecided();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div className="pill-row" style={{ margin: 0 }}>
        <span className="chip accent">{approval.kind.replaceAll("_", " ")}</span>
        {approval.amount_usd !== null && approval.amount_usd > 0 && (
          <span className="chip">{fmtUsd(approval.amount_usd)}</span>
        )}
        <div className="spacer" />
        <span className={`clock ${clockClass(approval.expires_at)}`}>
          {fmtLeft(approval.expires_at)} → auto-deny
        </span>
      </div>
      <h3 style={{ margin: "10px 0 8px", fontSize: 15 }}>{approval.title}</h3>
      <dl className="kv">
        <dt>The CEO's case</dt>
        <dd style={{ whiteSpace: "pre-wrap" }}>{approval.body}</dd>
        <dt>Why it's yours</dt>
        <dd>{whyYours(approval.kind)}</dd>
        {usdMode && approval.amount_usd !== null && approval.amount_usd > 0 && (
          <>
            <dt>If you approve</dt>
            <dd>
              {fmtUsd(spent)} spent this month becomes {fmtUsd(after)} of {fmtUsd(cap)}.
              {overruns && (
                <strong style={{ color: "var(--critical)" }}>
                  {" "}That is over the cap — the project pauses itself when it lands.
                </strong>
              )}
            </dd>
          </>
        )}
        <dt>If you deny or stay silent</dt>
        <dd>
          The request is refused and the CEO re-plans around it. Silence counts as a denial when
          the clock runs out — a vacationing chairman degrades the project, never deadlocks it.
        </dd>
      </dl>
      {approval.kind === "tool_request" && (
        <div style={{ marginTop: 12, padding: "10px 12px", borderRadius: 8,
          border: "1px solid var(--border)", background: "var(--surface)" }}>
          <div style={{ fontWeight: 650, fontSize: 13, marginBottom: 6 }}>
            Fulfill in one motion
          </div>
          <p className="help" style={{ marginTop: 0 }}>
            Store the credential, choose who gets it, approve the need — one press.
            The value is encrypted and arrives as an environment variable in the
            sandboxes of the people you tick below. It never appears in chat, and
            you never see it again either.
          </p>
          <div className="row">
            <input value={secretName} placeholder="Credential name (e.g. aws-key)"
              onChange={(e) => setSecretName(e.target.value)} />
            <input value={secretValue} type="password" placeholder="Value"
              onChange={(e) => setSecretValue(e.target.value)} />
          </div>
          <div className="grant-label">Who receives it</div>
          <RoleGrants
            org={org}
            selected={roles}
            onChange={setRoles}
            askedByAgentId={approval.requested_by}
            secretName={secretName}
          />
          <div className="actions" style={{ marginTop: 8 }}>
            <button className="primary"
              disabled={busy || !secretName.trim() || !secretValue || roles.length === 0}
              onClick={async () => {
                setBusy(true);
                setError(null);
                try {
                  await post(`/admin/approvals/${approval.id}/fulfill`, {
                    secret_name: secretName.trim(),
                    value: secretValue,
                    roles,
                    note: note.trim(),
                  });
                  onDecided();
                } catch (e) {
                  setError((e as Error).message);
                  setBusy(false);
                }
              }}>
              🔑 Fulfill the need
            </button>
            <span className="help">Or approve/deny below without a credential.</span>
          </div>
        </div>
      )}
      <input
        style={{ marginTop: 12 }}
        value={note}
        placeholder="Your note — the agent reads this (required to deny)"
        onChange={(e) => setNote(e.target.value)}
      />
      {error && <div className="error">{error}</div>}
      <div className="actions">
        <button className="primary" disabled={busy} onClick={() => decide(true)}>
          ✓ Approve
        </button>
        <button className="danger" disabled={busy} onClick={() => decide(false)}>
          ✕ Deny
        </button>
        <span className="help">No undo — the decision is recorded and disclosed.</span>
      </div>
    </div>
  );
}

/** What the CEO decided on its own authority.

    Autonomy was always audited and always emailed, and had nowhere to be READ:
    a chairman who had delegated the plan, the date and the verdicts could open
    this screen and find no trace of a single decision made in his name. The
    one row that did appear was the approval the CEO filed when it ran out of
    verbs — so the more he delegated, the emptier the screen and the more
    misleading the thing on it.

    Deliberately NOT under "Waiting on you": nothing here is pending and
    nothing expires. It is a record, and every line of it is reversible from
    the Plan screen or Settings. */
function CeoDecisions({ projectId }: { projectId: string }) {
  const { data } = usePoll(
    () => get<CeoDecisionRow[]>(`/observe/projects/${projectId}/ceo_decisions`),
    15000,
  );
  const rows = data ?? [];
  return (
    <>
      <h2 className="zone">
        What the CEO decided <span className="cnt">{rows.length}</span>
      </h2>
      <p className="subtitle" style={{ marginBottom: 8 }}>
        Decisions the constitution puts in the CEO's hands, taken without you and disclosed
        here in full. Nothing on this list is waiting on you and nothing expires — but every
        line of it is reversible, and the dial that granted it is yours to turn back in{" "}
        Settings → Rules in force.
      </p>
      {rows.length === 0 ? (
        <p className="help">
          Nothing yet. Under the Supervised dial this list stays empty by design — the CEO
          proposes and you decide, so its decisions are the approvals above.
        </p>
      ) : (
        <>
          <div className="pills">
            <ExportCsv
              onClick={() =>
                downloadCsv(
                  "ceo-decisions.csv",
                  ["at", "about", "what", "reason"],
                  rows.map((d) => [d.at, d.about, d.what, d.reason]),
                )
              }
            />
          </div>
          <table>
            <thead>
              <tr>
                <th>When</th>
                <th>About</th>
                <th style={{ width: "38%" }}>What it did</th>
                <th style={{ width: "34%" }}>Its reason</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((d) => (
                <tr key={d.id}>
                  <td className="mono">{fmtWhen(d.at)}</td>
                  <td className="mono">{d.about}</td>
                  <td>{d.what}</td>
                  <td>{d.reason || <span className="help">no reason recorded</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </>
  );
}


/** Everything you decided, and everything that decided itself while you were
    away. Approvals used to be a tab that stood empty most days; the live ones
    now travel with you in the strip, and this is where they come to rest. */
/** A question the CEO put to you. It is not an approval: nothing is blocked
    while it waits, and if you say nothing it does not fail closed — the CEO
    does the thing it wrote down before it asked. So there are no decide
    buttons here. You answer it by talking to it, which is the point. */
function AskCard({ ask, to }: { ask: AskRow; to: string }) {
  const live = ask.status === "open";
  return (
    <div className={`card${live ? " hot" : ""}`} style={{ margin: "10px 0" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
        <strong>{ask.title}</strong>
        <span className={`badge ${live ? "warn" : "good"}`}>{ask.status}</span>
        {live && (
          <span className="help">
            stops waiting {new Date(ask.answer_by).toLocaleString()}
          </span>
        )}
      </div>
      <p style={{ whiteSpace: "pre-wrap", margin: "8px 0" }}>{ask.body}</p>
      <p className="help" style={{ margin: "6px 0" }}>
        {live ? "If you say nothing, it will: " : "Its stated default was: "}
        <strong>{ask.default_action}</strong>
      </p>
      <Link className="act pri" to={`${to}/boardroom`}>
        {live ? "Answer in the boardroom" : "Read the thread"}
      </Link>
    </div>
  );
}

export default function Decisions({ ctx }: { ctx: ProjectCtx }) {
  const { projectId, dash, approvals, asks, reloadApprovals } = ctx;
  const { data: amendments } = usePoll(
    () => get<AmendmentRow[]>(`/admin/projects/${projectId}/amendments`),
    15000,
  );

  const pending = approvals
    .filter((a) => a.status === "pending")
    .sort((a, b) => a.expires_at.localeCompare(b.expires_at));
  const history = approvals
    .filter((a) => a.status !== "pending")
    .sort((a, b) => (b.decided_at ?? "").localeCompare(a.decided_at ?? ""));
  const rows = amendments ?? [];
  const openAsks = asks.filter((a) => a.status === "open");
  const settledAsks = asks.filter((a) => a.status !== "open");
  const to = `/projects/${projectId}`;

  return (
    <>
      <p className="subtitle">
        Approvals expire to <strong>DENY</strong>. That is the whole design: a chairman who never
        logs in slows the project down, but can never wedge it. Questions below them work the
        other way round — nothing is blocked while one waits, and silence hands the decision
        back to the CEO on the terms it wrote down before it asked.
      </p>

      {openAsks.length > 0 && (
        <>
          <h2 className="zone hot">
            Your CEO asked <span className="cnt">{openAsks.length}</span>
          </h2>
          {openAsks.map((a) => (
            <AskCard key={a.id} ask={a} to={to} />
          ))}
        </>
      )}

      {pending.length > 0 && (
        <>
          <h2 className="zone hot">
            Waiting on you <span className="cnt">{pending.length}</span>
          </h2>
          {pending.map((a) => (
            <DecisionCard
              key={a.id}
              approval={a}
              dash={dash}
              org={ctx.org}
              onDecided={() => {
                reloadApprovals();
                ctx.reload();
              }}
            />
          ))}
        </>
      )}

      {settledAsks.length > 0 && (
        <>
          <h2 className="zone">
            Questions — the record <span className="cnt">{settledAsks.length}</span>
          </h2>
          {settledAsks.map((a) => (
            <AskCard key={a.id} ask={a} to={to} />
          ))}
        </>
      )}

      <h2 className="zone">
        Approvals — the record <span className="cnt">{history.length}</span>
      </h2>
      {history.length === 0 ? (
        <p className="help">Nothing decided yet.</p>
      ) : (
        <>
          <div className="pills">
            <ExportCsv
              onClick={() =>
                downloadCsv(
                  "decisions.csv",
                  ["decided", "title", "kind", "amount_usd", "outcome", "note"],
                  history.map((a) => [
                    a.decided_at,
                    a.title,
                    a.kind,
                    a.amount_usd,
                    a.status,
                    a.decision_note,
                  ]),
                )
              }
            />
          </div>
          <table>
            <thead>
              <tr>
                <th>Decided</th>
                <th style={{ width: "34%" }}>What was asked</th>
                <th>Kind</th>
                <th className="num">Amount</th>
                <th>Outcome</th>
                <th style={{ width: "26%" }}>Your note</th>
              </tr>
            </thead>
            <tbody>
              {history.map((a) => (
                <tr key={a.id}>
                  <td className="mono">{a.decided_at ? fmtWhen(a.decided_at) : "—"}</td>
                  <td>{a.title}</td>
                  <td className="mono">{a.kind.replaceAll("_", " ")}</td>
                  <td className="num">{a.amount_usd ? fmtUsd(a.amount_usd) : "—"}</td>
                  <td>{outcomeBadge(a)}</td>
                  <td>{a.decision_note || <span className="help">no note</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <CeoDecisions projectId={projectId} />

      <h2 className="zone">
        Rule changes <span className="cnt">{rows.length}</span>
      </h2>
      <p className="subtitle" style={{ marginBottom: 8 }}>
        Every rule change is in force on the next agent call, disclosed to the CEO with the
        reason you gave, and kept here — so a limit never moves without a record of who moved
        it and why.
      </p>
      {rows.length === 0 ? (
        <p className="help">
          The constitution stands as it was frozen at creation. Propose a change in{" "}
          Settings → Rules.
        </p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Status</th>
              <th style={{ width: "30%" }}>Reason you gave</th>
              <th style={{ width: "28%" }}>Changes</th>
              <th>Proposed</th>
              <th>Effective</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((a) => (
              <tr key={a.id}>
                <td>
                  {amendmentBadge(a.status)}
                  {a.disclosed && <div className="help">disclosed to CEO</div>}
                </td>
                <td>{a.reason}</td>
                <td>
                  <pre className="block">{JSON.stringify(a.changes, null, 1)}</pre>
                </td>
                <td className="mono">{fmtWhen(a.proposed_at)}</td>
                <td className="mono">{fmtWhen(a.effective_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
