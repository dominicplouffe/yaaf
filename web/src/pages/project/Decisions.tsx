import { useEffect, useMemo, useState } from "react";
import { plainStatus } from "../../ui/kit";
import { Link } from "react-router-dom";
import {
  AmendmentRow, ApprovalDisclosure, ApprovalRow, AskRow, BriefDraft, CeoDecisionRow, get,
  post, splitWithout,
} from "../../api";
import { fmtUsd, fmtWhen, usePoll } from "../../hooks";
import { grantRoles, RoleGrants } from "../../roles";
import { clockClass, DecideButtons, downloadCsv, ExportCsv, fmtLeft, SplitAction } from "../../ui";
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
      return "The project is asking for more room to work — runs, concurrency, queue depth or money. It is the one kind where the CEO argues for itself, so it stays yours at every autonomy setting. Saying yes raises the dial at once. It is not blocking: the project keeps working at the cap it has, and if you say nothing the cap simply stays.";
    case "brief":
      return "Only you decide what this project is for. Until you confirm, every agent keeps working from the brief in force; once you do, this one replaces it in every agent's instructions, on every run.";
    case "assist":
      return "A step only a human can do — a captcha, a 2FA code, placing an order. Press Done when it is done, with what happened if it matters (the code, the fill); it wakes the agent. The platform hands over security challenges; it never defeats them.";
    default:
      return "The constitution routes this kind of decision to the chairman.";
  }
}

const SHOWN_CHANGES = 12;

function cell(v: string | number | boolean | null): string {
  return v === null ? "—" : String(v);
}

/** What a data change would actually do. The CEO is told to fill this "ALWAYS
    — it is what he decides on", and it was stored on the row and drawn
    nowhere, so he decided on the paragraph instead. */
function DisclosureRows({ d }: { d: ApprovalDisclosure }) {
  const changes = d.change_set ?? [];
  const counts = Object.entries(d.scope_counts ?? {});
  return (
    <>
      {(changes.length > 0 || counts.length > 0) && (
        <>
          <dt>What changes</dt>
          <dd>
            {counts.length > 0 && (
              <div className="help">{counts.map(([k, n]) => `${n} ${k.replaceAll("_", " ")}`).join(" · ")}</div>
            )}
            {changes.length > 0 && (
              <table style={{ marginTop: 4 }}>
                <tbody>
                  {changes.slice(0, SHOWN_CHANGES).map((c, i) => (
                    <tr key={i}>
                      <td>{c.entity}</td>
                      <td>{c.field.replaceAll("_", " ")}</td>
                      <td>{cell(c.before)} → <b>{cell(c.after)}</b></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {changes.length > SHOWN_CHANGES && (
              <div className="help">and {changes.length - SHOWN_CHANGES} more changes like these</div>
            )}
          </dd>
        </>
      )}
      {d.estimate && (
        <>
          <dt>Expected effect</dt>
          <dd>
            {d.estimate.label}: <b>{d.estimate.value}</b>{" "}
            <span className="help">(an estimate{d.estimate.basis ? ` — ${d.estimate.basis}` : ""})</span>
          </dd>
        </>
      )}
      {d.spend_affected_usd_month != null && (
        <>
          <dt>Spend it touches</dt>
          <dd>{fmtUsd(d.spend_affected_usd_month)} a month</dd>
        </>
      )}
      {d.reversible != null && (
        <>
          <dt>Can it be undone?</dt>
          <dd>{d.reversible ? "Yes" : <b>No — this cannot be undone</b>}</dd>
        </>
      )}
      {d.counter_case && (
        <>
          <dt>The case against</dt>
          <dd style={{ whiteSpace: "pre-wrap" }}>{d.counter_case}</dd>
        </>
      )}
    </>
  );
}

function outcomeBadge(a: ApprovalRow) {
  if (a.status === "approved") return <span className="badge good">approved</span>;
  if (a.status === "denied") return <span className="badge critical">denied</span>;
  if (a.status === "expired") return <span className="badge critical">expired → denied</span>;
  return <span className="badge warn">{a.status}</span>;
}

function amendmentBadge(status: AmendmentRow["status"]) {
  const cls = status === "applied" ? "good" : status === "cancelled" ? "critical" : "warn";
  return <span className={`badge ${cls}`}>{plainStatus(status)}</span>;
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
  // A capacity request: the number he raises the dial to, the CEO's by default.
  const [raiseTo, setRaiseTo] = useState(approval.capacity ? String(approval.capacity.to) : "");
  const deciding = approval.capacity && Number(raiseTo) > 0
    ? { ...approval, capacity: { ...approval.capacity, to: Number(raiseTo) } } : approval;
  // The one-motion fulfillment for a need (kind=tool_request): credential
  // stored, roles scoped, need approved — one form instead of three screens.
  const [secretName, setSecretName] = useState("");
  const [secretValue, setSecretValue] = useState("");
  const [roles, setRoles] = useState<string[]>([]);
  // A data write files as a tool_request too, but what it needs is a yes, not
  // a key: it carries the call it would make, and a need carries none.
  const isNeed = approval.kind === "tool_request" && !approval.grant;

  // The agent that filed the need is on a team, and that team's role is the
  // answer nine times out of ten. Proposing it makes this a confirmation
  // rather than a question — the same relationship the CEO has with him.
  useEffect(() => {
    if (!isNeed || roles.length > 0 || !org) return;
    const asked = grantRoles(org, approval.requested_by).find((r) => r.asked);
    if (asked) setRoles([asked.slug]);
  }, [org, isNeed, approval.requested_by]);

  const usdMode = dash.billing_mode === "api_key";
  const spent = dash.month_to_date.usd;
  const cap = dash.caps.monthly_usd_cap;
  const after = spent + (approval.amount_usd ?? 0);
  const overruns = usdMode && approval.amount_usd !== null && after > cap;
  const [theCase, without] = splitWithout(approval.body);

  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div className="pill-row" style={{ margin: 0 }}>
        <span className="chip accent">{approval.kind.replaceAll("_", " ")}</span>
        {approval.amount_usd !== null && approval.amount_usd > 0 && (
          <span className="chip">{fmtUsd(approval.amount_usd)}</span>
        )}
        <div className="spacer" />
        <span className={`clock ${clockClass(approval.expires_at)}`}>
          Denies itself in {fmtLeft(approval.expires_at).replace(" left", "")}
        </span>
      </div>
      <h3 style={{ margin: "10px 0 8px", fontSize: 15 }}>{approval.title}</h3>
      <dl className="kv">
        <dt>The CEO's case</dt>
        <dd style={{ whiteSpace: "pre-wrap" }}>{theCase}</dd>
        {approval.disclosure && <DisclosureRows d={approval.disclosure} />}
        <dt>Why it's yours</dt>
        <dd>{approval.grant
          ? "It changes your live account. The platform lets the agents read it on their own; a change is always yours."
          : whyYours(approval.kind)}</dd>
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
        {without ? (
          <dd>
            <span style={{ whiteSpace: "pre-wrap" }}>{without}</span>
            <div className="help">Silence counts as a denial when the clock runs out.</div>
          </dd>
        ) : (
          <dd>
            The request is refused and the CEO re-plans around it. Silence counts as a denial when
            the clock runs out — a vacationing chairman degrades the project, never deadlocks it.
          </dd>
        )}
      </dl>
      {isNeed && (
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
                  });
                  onDecided();
                } catch (e) {
                  setError((e as Error).message);
                  setBusy(false);
                }
              }}>
              🔑 Fulfill the need
            </button>
            <span className="help">Or answer below without a credential.</span>
          </div>
        </div>
      )}
      {approval.capacity && (
        <label className="field" style={{ marginTop: 12, display: "block" }}>
          Raise it to{" "}
          <input type="number" value={raiseTo} min={approval.capacity.from}
            style={{ width: 140 }} onChange={(e) => setRaiseTo(e.target.value)} />
          <span className="help"> — the CEO asked for {approval.capacity.to.toLocaleString()}; it is {approval.capacity.from.toLocaleString()} now. Saying yes changes it at once.</span>
        </label>
      )}
      {error && <div className="error">{error}</div>}
      <div className="actions">
        <DecideButtons small={false} approval={deciding} onDone={onDecided} />
        <span className="help">No undo — the decision is recorded and disclosed.</span>
      </div>
    </div>
  );
}

const lines = (text: string) =>
  text.split("\n").map((l) => l.replace(/^\s*[-•*]\s*/, "").trim()).filter(Boolean);

type Piece = { op: "same" | "in" | "out"; text: string };

/** Word-level a → b. Common ends are trimmed first, so a one-word edit in a
    thousand-word goal costs a handful of cells, not a million. */
function wordDiff(a: string, b: string): Piece[] {
  const x = a.split(/\s+/).filter(Boolean);
  const y = b.split(/\s+/).filter(Boolean);
  let head = 0;
  while (head < x.length && head < y.length && x[head] === y[head]) head++;
  let tail = 0;
  while (tail < x.length - head && tail < y.length - head
    && x[x.length - 1 - tail] === y[y.length - 1 - tail]) tail++;
  const xs = x.slice(head, x.length - tail);
  const ys = y.slice(head, y.length - tail);
  const n = xs.length, m = ys.length;
  const lcs = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      lcs[i][j] = xs[i] === ys[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
  const out: Piece[] = [];
  const push = (op: Piece["op"], w: string) => {
    const last = out[out.length - 1];
    if (last && last.op === op) last.text += " " + w;
    else out.push({ op, text: w });
  };
  x.slice(0, head).forEach((w) => push("same", w));
  let i = 0, j = 0;
  while (i < n || j < m) {
    if (i < n && j < m && xs[i] === ys[j]) { push("same", xs[i]); i++; j++; }
    else if (j < m && (i >= n || lcs[i][j + 1] >= lcs[i + 1][j])) push("in", ys[j++]);
    else push("out", xs[i++]);
  }
  x.slice(x.length - tail).forEach((w) => push("same", w));
  // Struck before inserted, so a replacement reads "old → new".
  for (let k = 1; k < out.length; k++) {
    if (out[k].op === "out" && out[k - 1].op === "in") [out[k - 1], out[k]] = [out[k], out[k - 1]];
  }
  return out;
}

/** Share of words two orders have in common — difflib's ratio, which the
    server's boardroom post pairs edits by (yaaf.server.brief.changes). */
function likeness(a: string, b: string): number {
  const d = wordDiff(a.toLowerCase(), b.toLowerCase());
  const same = d.filter((p) => p.op === "same").reduce((k, p) => k + p.text.split(" ").length, 0);
  const total = a.split(/\s+/).filter(Boolean).length + b.split(/\s+/).filter(Boolean).length;
  return total ? (2 * same) / total : 1;
}

type Change =
  | { kind: "goal"; was: string; now: string }
  | { kind: "added" | "dropped" | "retired"; text: string }
  | { kind: "edited"; was: string; now: string };

/** What confirming would change, against the brief in force. Same rules as
    the boardroom post: a reworded order is one edit, not a drop and an add;
    retired orders carry forward on confirm, so only the newly retired count. */
function briefChanges(
  was: { goal: string; orders: string[]; retired: string[] },
  now: { goal: string; orders: string[]; retired: string[] },
) {
  const low = (l: string[]) => new Set(l.map((o) => o.toLowerCase()));
  const oldSet = low(was.orders), newSet = low(now.orders), retiredBefore = low(was.retired);
  const gone = was.orders.filter((o) => !newSet.has(o.toLowerCase()));
  const came = now.orders.filter((o) => !oldSet.has(o.toLowerCase()));
  const pairs = gone.flatMap((g) => came.map((c) => ({ g, c, s: likeness(g, c) })))
    .filter((p) => p.s >= 0.5).sort((a, b) => b.s - a.s);
  const editedFrom = new Map<string, string>();
  const used = new Set<string>();
  for (const p of pairs) {
    if (used.has(p.g) || editedFrom.has(p.c)) continue;
    editedFrom.set(p.c, p.g);
    used.add(p.g);
  }
  const list: Change[] = [];
  if (was.goal.trim() !== now.goal.trim()) list.push({ kind: "goal", was: was.goal, now: now.goal });
  for (const c of came) {
    const g = editedFrom.get(c);
    list.push(g ? { kind: "edited", was: g, now: c } : { kind: "added", text: c });
  }
  for (const g of gone) if (!used.has(g)) list.push({ kind: "dropped", text: g });
  for (const r of now.retired) if (!retiredBefore.has(r.toLowerCase())) list.push({ kind: "retired", text: r });
  return {
    list,
    goalSame: was.goal.trim() === now.goal.trim(),
    sameOrders: now.orders.filter((o) => oldSet.has(o.toLowerCase())),
    stillRetired: now.retired.filter((r) => retiredBefore.has(r.toLowerCase())),
  };
}

/** Context kept around an edit in a long goal; the rest folds to "…". */
const GOAL_CONTEXT = 8;

function Inline({ pieces, context }: { pieces: Piece[]; context?: number }) {
  return (
    <>
      {pieces.map((p, i) => {
        const gap = i > 0 ? " " : "";
        if (p.op === "in") return <span key={i}>{gap}<ins className="bd">{p.text}</ins></span>;
        if (p.op === "out") return <span key={i}>{gap}<del className="bd">{p.text}</del></span>;
        const words = p.text.split(" ");
        if (context === undefined || words.length <= 2 * context + 1) return <span key={i}>{gap}{p.text}</span>;
        const head = i > 0 ? words.slice(0, context).join(" ") + " " : "";
        const tail = i < pieces.length - 1 ? " " + words.slice(-context).join(" ") : "";
        return <span key={i}>{gap}{head}<span className="bd-fold">…</span>{tail}</span>;
      })}{" "}
    </>
  );
}

const CHANGE_LABEL: Record<Change["kind"], [string, string]> = {
  goal: ["Goal", "accent"],
  added: ["New order", "ok"],
  edited: ["Edited", "accent"],
  dropped: ["Dropped", "critical"],
  retired: ["Now retired", "warn"],
};

function ChangeRow({ c }: { c: Change }) {
  const [whole, setWhole] = useState(false);
  const [label, tone] = CHANGE_LABEL[c.kind];
  return (
    <li className={`bd-row bd-${tone}`}>
      <span className={`chip ${tone}`}>{label}</span>
      <div className="bd-text">
        {c.kind === "goal" ? (
          <>
            <Inline pieces={wordDiff(c.was, c.now)} context={whole ? undefined : GOAL_CONTEXT} />
            <button className="linkish" onClick={() => setWhole(!whole)}>
              {whole ? "Only the edits" : "The whole goal"}
            </button>
          </>
        ) : c.kind === "edited" ? (
          <Inline pieces={wordDiff(c.was, c.now)} />
        ) : c.kind === "dropped" ? (
          <del className="bd">{c.text}</del>
        ) : (
          c.text
        )}
      </div>
    </li>
  );
}

/** Your direction, written back by the CEO for you to confirm. Everything on
    it is editable, because "yes, but 12% not 15%" should be one click, not
    another round through the boardroom. Confirming replaces the goal and the
    standing orders every agent reads first.

    It opens on what the draft CHANGES, not on the draft: asked to move one
    sentence, the CEO writes back all ten, and reading ten to find the one is
    how the one he did not ask for slips through. The whole text is the other
    lens, and editing it there redraws the changes. */
function BriefCard({
  approval,
  dash,
  onDecided,
}: {
  approval: ApprovalRow;
  dash: ProjectCtx["dash"];
  onDecided: () => void;
}) {
  const draft: BriefDraft = approval.brief ?? { goal: "", standing_orders: [], retired: [] };
  const [goal, setGoal] = useState(draft.goal);
  const [orders, setOrders] = useState(draft.standing_orders.join("\n"));
  const [retired, setRetired] = useState(draft.retired.join("\n"));
  const [lens, setLens] = useState<"changes" | "whole">("changes");
  const proposed = lines(orders);
  const diff = useMemo(() => briefChanges(
    { goal: dash.goal, orders: dash.standing_orders ?? [], retired: dash.retired_orders ?? [] },
    { goal, orders: proposed, retired: lines(retired) },
  ), [goal, orders, retired, dash.goal, dash.standing_orders, dash.retired_orders]);
  const youEdited = goal !== draft.goal || orders !== draft.standing_orders.join("\n")
    || retired !== draft.retired.join("\n");
  const since = dash.brief_confirmed_at
    ? `the brief you confirmed ${fmtWhen(dash.brief_confirmed_at)}`
    : (dash.standing_orders ?? []).length ? "the brief in force" : "the goal the project was founded with";
  const unchanged = [
    diff.goalSame && "the goal",
    diff.sameOrders.length > 0
      && `${diff.sameOrders.length} standing order${diff.sameOrders.length === 1 ? "" : "s"}`,
    diff.stillRetired.length > 0 && `${diff.stillRetired.length} retired`,
  ].filter(Boolean).join(" · ");

  async function decide(approve: boolean, note: string) {
    await post(`/admin/approvals/${approval.id}/decide`, {
      approve,
      note,
      ...(approve ? {
        brief: { goal: goal.trim(), standing_orders: proposed, retired: lines(retired) },
      } : {}),
    });
    onDecided();
  }

  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div className="pill-row" style={{ margin: 0 }}>
        <span className="chip accent">brief</span>
        <div className="spacer" />
        <span className={`clock ${clockClass(approval.expires_at)}`}>
          Lapses in {fmtLeft(approval.expires_at).replace(" left", "")} — the current brief stays
        </span>
      </div>
      <h3 style={{ margin: "10px 0 4px", fontSize: 15 }}>Is this what you want now?</h3>
      <p className="help" style={{ marginTop: 0 }}>{whyYours("brief")}</p>
      {draft.what_changed && (
        <dl className="kv">
          <dt>What you told the CEO</dt>
          <dd style={{ whiteSpace: "pre-wrap" }}>{draft.what_changed}</dd>
        </dl>
      )}
      <div className="pill-row" style={{ margin: "14px 0 8px", alignItems: "center" }}>
        <div className="seg" role="tablist">
          <button role="tab" aria-selected={lens === "changes"} className={lens === "changes" ? "on" : ""}
            onClick={() => setLens("changes")}>
            What changes<span className="n">{diff.list.length}</span>
          </button>
          <button role="tab" aria-selected={lens === "whole"} className={lens === "whole" ? "on" : ""}
            onClick={() => setLens("whole")}>
            The whole brief
          </button>
        </div>
        {youEdited && <span className="help">· includes your edits</span>}
      </div>
      {lens === "changes" ? (
        <>
          <p className="help" style={{ margin: "0 0 6px" }}>Against {since}.</p>
          {diff.list.length === 0 ? (
            <p className="help">Word for word the brief in force — confirming changes nothing.</p>
          ) : (
            <ul className="bd-list">
              {diff.list.map((c, i) => <ChangeRow key={`${c.kind}-${i}`} c={c} />)}
            </ul>
          )}
          {unchanged && (
            <details style={{ margin: "8px 0 0" }}>
              <summary className="help">Unchanged: {unchanged}</summary>
              {diff.sameOrders.length > 0 && (
                <ul className="bd-same">{diff.sameOrders.map((o) => <li key={o}>{o}</li>)}</ul>
              )}
              {diff.stillRetired.length > 0 && (
                <>
                  <div className="grant-label">Still retired</div>
                  <ul className="bd-same">{diff.stillRetired.map((o) => <li key={o}>{o}</li>)}</ul>
                </>
              )}
            </details>
          )}
          <p className="help" style={{ margin: "8px 0 0" }}>
            Want to change a word? <button className="linkish" onClick={() => setLens("whole")}>
              Edit the whole brief</button> — the changes here follow your edits.
          </p>
        </>
      ) : (
        <>
          <label className="grant-label">The goal</label>
          <textarea value={goal} rows={5} onChange={(e) => setGoal(e.target.value)} />
          <label className="grant-label">Standing orders — one per line, with the number</label>
          <textarea value={orders} rows={Math.max(3, proposed.length + 1)}
            onChange={(e) => setOrders(e.target.value)} />
          <label className="grant-label">No longer applies — agents told to ignore it wherever they find it</label>
          <textarea value={retired} rows={Math.max(2, lines(retired).length + 1)}
            onChange={(e) => setRetired(e.target.value)} />
        </>
      )}
      <div className="actions">
        <SplitAction small={false} label="Confirm — this is the brief"
          disabled={goal.trim().length < 10} run={() => decide(true, "")} choices={[
            { label: "Confirm, with a note…", hint: "The CEO answers it", send: "Confirm",
              placeholder: "e.g. revisit the 12% in January", run: (n) => decide(true, n) },
            { label: "Not right…", hint: "Say what is wrong; the CEO re-drafts from it",
              send: "Not right", placeholder: "What is wrong with it?", requireNote: true,
              danger: true, run: (n) => decide(false, n) },
          ]} />
        <span className="help">
          Confirming restarts the chat so the CEO you talk to reads it too.
        </span>
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
            stops waiting {fmtWhen(ask.answer_by)}
          </span>
        )}
      </div>
      <p style={{ whiteSpace: "pre-wrap", margin: "8px 0" }}>{ask.body}</p>
      <p className="help" style={{ margin: "6px 0" }}>
        {live ? "If you say nothing, it will: " : "Its stated default was: "}
        <strong>{ask.default_action}</strong>
      </p>
      <Link className="act pri" to={`${to}/chat`}>
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
          {pending.map((a) => a.kind === "brief" ? (
            <BriefCard
              key={a.id}
              approval={a}
              dash={dash}
              onDecided={() => {
                reloadApprovals();
                ctx.reload();
              }}
            />
          ) : (
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
                <th style={{ width: "26%" }}>Your note · the CEO's answer</th>
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
                  <td>
                    {a.held_note ? (
                      <>
                        {a.held_note.note}
                        <div className="help">
                          {a.held_note.reply
                            ? `CEO: ${a.held_note.reply}${a.held_note.standing ? " (into the brief)" : ""}`
                            : "CEO has not answered yet"}
                        </div>
                      </>
                    ) : a.decision_note || <span className="help">no note</span>}
                  </td>
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
