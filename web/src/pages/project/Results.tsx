import { useState } from "react";
import { Link } from "react-router-dom";
import { get, post, ResultRow, ResultsView } from "../../api";
import { GoalChain } from "../../chain";
import { WhatTheyDid } from "../../did";
import { fmtUsd, fmtWhen, usePoll } from "../../hooks";
import { firstName, ProjectCtx } from "../../projectCtx";
import { clip, fmtAgo, Skeleton } from "../../ui";
import { PageHead } from "../../ui/kit";
import { Icon } from "../../ui/Icon";

/** What the team has delivered, and who checked it.

    The second question anyone asks of a team, after "who is on it?", is
    "what have they made?" — and the answer used to be a file explorer that
    put this morning's published report beside forty working files. Results
    is the published shelf only: one row per deliverable, who made it, and
    who or what looked at it before it reached you, in the platform's words
    (a judgement is never shown as a verification). Drafts and working files
    stay in Details › Deliverables. */

const CHECK_WORD: Record<ResultRow["checked"], string> = {
  platform: "verified by YAAF",
  reviewer: "read by a second agent",
  judged: "met by judgement",
  unchecked: "not checked",
};

function checkLine(r: ResultRow): string {
  const strongest = r.checks.find((c) => c.kind === r.checked);
  return strongest ? strongest.label : "not checked";
}

function Evidence({ ctx, row, goal }: { ctx: ProjectCtx; row: ResultRow; goal: string }) {
  const ceo = firstName(ctx.ceoName);
  const [wrong, setWrong] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const open = `/projects/${ctx.projectId}/details/deliverables?open=${encodeURIComponent(row.key)}`;
  return (
    <section className="card results-evidence" aria-label={`${row.name} — the evidence`}>
      <h3>{row.name} — the evidence</h3>
      <dl className="kv">
        <dt>Made by</dt>
        <dd>{row.made_by ? <button className="linkish inline" onClick={() => ctx.openAgent(row.made_by!.id)}>{row.made_by.name}</button> : "—"}
          {row.versions > 1 ? ` · version ${row.version} of ${row.versions}` : ""}</dd>
        <dt>Checked</dt>
        <dd>
          {row.checks.length === 0 ? "Nothing checked it before it reached you." : (
            <ul className="plain">
              {row.checks.map((c, i) => (
                <li key={i}><b>{c.label}</b>{c.note ? ` — “${clip(c.note, 240)}”` : ""}</li>
              ))}
            </ul>
          )}
        </dd>
        <dt>For</dt>
        <dd><GoalChain goal={goal} serves={row.serves} task={row.task} /></dd>
        <dt>Cost to make</dt>
        <dd>
          {row.cost.runs === 0 ? "—" : `${fmtUsd(row.cost.usd)} · ${row.cost.runs} run${row.cost.runs === 1 ? "" : "s"}`}
          {row.cost.failed > 0 && `, ${row.cost.failed} of them failed`}
        </dd>
      </dl>
      {row.run_id && <WhatTheyDid runId={row.run_id} projectId={ctx.projectId} label="What they did to make it" />}
      <div className="actions">
        <Link className="btnlink small primary" to={open}><Icon name="file" size={13} /> Open it</Link>
        {ctx.dash.can_steer !== false && wrong === null && !sent && (
          <button className="small" onClick={() => setWrong("")}>Wrong because…</button>
        )}
        {sent && <span className="help">Sent to {ceo} in Chat.</span>}
      </div>
      {wrong !== null && (
        <div className="reason-form">
          <input autoFocus value={wrong} onChange={(e) => setWrong(e.target.value)}
            placeholder={`What is wrong with it? ${ceo} reads this in Chat and decides what to redo.`}
            onKeyDown={(e) => { if (e.key === "Escape") setWrong(null); }} />
          <div className="reason-acts">
            <button className="small primary" disabled={wrong.trim().length < 5} onClick={async () => {
              setError(null);
              try {
                await post(`/admin/projects/${ctx.projectId}/boardroom`, {
                  body: `Re: the result “${row.name}” (v${row.version}) — wrong because ${wrong.trim()}`,
                });
                setWrong(null);
                setSent(true);
              } catch (e) {
                setError((e as Error).message);
              }
            }}>Tell {ceo}</button>
            <button className="small" onClick={() => setWrong(null)}>Cancel</button>
          </div>
          {error && <div className="help" style={{ color: "var(--critical)" }}>{error}</div>}
        </div>
      )}
    </section>
  );
}

export default function Results({ ctx }: { ctx: ProjectCtx }) {
  const { projectId } = ctx;
  const { data, error } = usePoll(() => get<ResultsView>(`/observe/projects/${projectId}/results`), 30000, [projectId]);
  const [picked, setPicked] = useState<string | null>(null);
  const rows = data?.results ?? [];
  const row = rows.find((r) => r.key === picked) ?? rows[0];
  return (
    <>
      <PageHead title="Results" lede={<>
        The deliverables your team has published, newest first: who made each one, and who checked it
        before it reached you. Drafts and working files are in{" "}
        <Link to={`/projects/${projectId}/details/deliverables`}>Details › Deliverables</Link>.
      </>} />
      {error && <div className="error">{error}</div>}
      {!data ? <Skeleton rows={5} /> : rows.length === 0 ? (
        <div className="card quiet-line">
          <span>
            <b>Nothing published yet.</b>{" "}
            <span className="help">Results appear here the moment the team publishes one, with who made it and who checked it.</span>
          </span>
        </div>
      ) : (
        <>
          <div className="results-counts">
            {(["platform", "reviewer", "judged", "unchecked"] as const).filter((k) => data.counts[k] > 0).map((k) => (
              <span key={k} className={`chip results-${k}`}>{data.counts[k]} {CHECK_WORD[k]}</span>
            ))}
            {data.total > data.listed && <span className="help">newest {data.listed} of {data.total}</span>}
          </div>
          <div className="card results-table" role="table" aria-label="Results">
            <div className="rt-row rt-head" role="row">
              <span role="columnheader">Result</span>
              <span role="columnheader">Made by · checked by</span>
              <span role="columnheader">Updated</span>
            </div>
            {rows.map((r) => (
              <button key={r.key} type="button" role="row"
                className={`rt-row${row?.key === r.key ? " on" : ""}`} onClick={() => setPicked(r.key)}>
                <span role="cell" className="rt-name">
                  <b>{r.name}</b>
                  <span className="help">
                    v{r.version}{r.serves.length > 0 && ` · for ${r.serves.map((s) => s.key).join(", ")}`}
                  </span>
                </span>
                <span role="cell">
                  <b>{r.made_by?.name ?? "—"}</b>{" "}
                  <span className={`rt-check results-${r.checked}`}>· {r.checked !== "unchecked" ? "✓ " : ""}{checkLine(r)}</span>
                </span>
                <span role="cell" className="help" title={fmtWhen(r.published_at)}>{fmtAgo(r.published_at)}</span>
              </button>
            ))}
          </div>
          {row && <Evidence ctx={ctx} row={row} goal={data.goal} />}
        </>
      )}
    </>
  );
}
