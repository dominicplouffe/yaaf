import { ReactNode, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { get, post, ResultCheck, ResultRow, ResultsView } from "../../api";
import { GoalChain } from "../../chain";
import { WhatTheyDid } from "../../did";
import { fmtUsd, fmtWhen, usePoll } from "../../hooks";
import { firstName, ProjectCtx } from "../../projectCtx";
import { clip, fmtAgo, Skeleton } from "../../ui";
import { PageHead } from "../../ui/kit";
import { Icon } from "../../ui/Icon";
import { Seal, sealOf } from "../../ui/signal";
import "./results.css";

/** What the team has delivered, and how far each piece can be trusted.

    The second question anyone asks of a team, after "who is on it?", is
    "what have they made?" — and the answer used to be a file explorer that
    put this morning's published report beside forty working files. Results
    is the published shelf only: one row per deliverable, each with its seal
    (Verified, Judged, Agent's word — never merged into a score), and beside it
    why that seal was earned, in the platform's words. A judgement is never
    shown as a verification. Drafts and working files stay in Details ›
    Deliverables. */

/** Strongest first: what the platform ran, then who read it, then a
    judgement, then a claim that has not settled. */
const CHECK_ORDER: Record<ResultCheck["kind"], number> = { platform: 0, reviewer: 1, judged: 2, pending: 3 };

const MARK: Record<ResultCheck["kind"], StepMark> = {
  platform: "verified", reviewer: "judged", judged: "judged", pending: "open",
};

type StepMark = "verified" | "judged" | "open";

function sentence(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/** The project says it is done: closed, or a Deliver project with nothing
    left between it and the chairman's confirmation. */
function hasDelivered(ctx: ProjectCtx): boolean {
  const { dash } = ctx;
  return dash.status === "completed"
    || (dash.mode === "deliver" && Array.isArray(dash.delivery_outstanding) && dash.delivery_outstanding.length === 0);
}

function SealCounts({ data }: { data: ResultsView }) {
  const { counts } = data;
  return (
    <div className="rs-counts">
      <span className="rs-seals">
        {/* Strings, not numbers: a bare 0 as a child would print without its separator. */}
        <Seal level="verified">{String(counts.platform)}</Seal>
        <Seal level="judged">{String(counts.reviewer + counts.judged)}</Seal>
        <Seal level="word">{String(counts.unchecked)}</Seal>
      </span>
      {data.total > data.listed && <span className="help">newest {data.listed} of {data.total}</span>}
      <span className="rs-trust">Three levels of trust, never merged into a score.</span>
    </div>
  );
}

function ResultLine({ row, on, onPick }: { row: ResultRow; on: boolean; onPick: () => void }) {
  const strongest = row.checks.find((c) => c.kind === row.checked);
  return (
    <li>
      <button type="button" className={`rs-row${on ? " on" : ""}`} aria-pressed={on} aria-controls="rs-why"
        onClick={onPick}>
        <Icon name="file" size={18} />
        <span className="rs-row-main">
          <span className="rs-row-name">{row.name}</span>
          <span className="rs-row-sub">
            {row.made_by?.name ?? "maker not recorded"}
            {" · "}<span title={fmtWhen(row.published_at)}>{fmtAgo(row.published_at)}</span>
            {row.serves.length > 0 && ` · for ${row.serves.map((s) => s.key).join(", ")}`}
          </span>
        </span>
        <span title={strongest ? sentence(strongest.label) : "Nothing outside the agent checked it"}>
          <Seal level={sealOf(row.checked)} />
        </span>
      </button>
    </li>
  );
}

function Step({ mark, title, children }: { mark: StepMark; title: ReactNode; children?: ReactNode }) {
  return (
    <li className="rs-step">
      <span className={`rs-mark ${mark}`} aria-hidden>{mark !== "open" && <Icon name="check" size={11} />}</span>
      <div className="rs-step-body">
        <span className="rs-step-t">{title}</span>
        {children}
      </div>
    </li>
  );
}

function producedLine(row: ResultRow): string {
  const bits: string[] = [];
  if (row.cost.runs > 0) {
    bits.push(plural(row.cost.runs, "run") + (row.cost.failed > 0 ? `, ${row.cost.failed} failed` : ""));
    bits.push(fmtUsd(row.cost.usd));
  }
  if (row.task) bits.push(`task ${clip(row.task.title, 90)}`);
  if (row.versions > 1) bits.push(`version ${row.version} of ${row.versions}`);
  return bits.join(" · ");
}

function WhyTrust({ ctx, row, goal }: { ctx: ProjectCtx; row: ResultRow; goal: string }) {
  const ceo = firstName(ctx.ceoName);
  const [wrong, setWrong] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const open = `/projects/${ctx.projectId}/details/deliverables?open=${encodeURIComponent(row.key)}`;
  const roadmap = `/projects/${ctx.projectId}/details/roadmap`;
  const checks = [...row.checks].sort((a, b) => CHECK_ORDER[a.kind] - CHECK_ORDER[b.kind]);
  const produced = producedLine(row);
  return (
    <section className="card rs-why" id="rs-why" aria-labelledby="rs-why-h">
      <header className="rs-why-head">
        <h2 id="rs-why-h">Why you can trust {row.name}</h2>
        <Link className="btnlink small" to={open}><Icon name="file" size={13} /> Open file</Link>
      </header>
      <ol className="rs-trail">
        {checks.map((c, i) => c.kind === "pending" ? (
          // An unsettled claim names what is unsettled ("R-04 not settled yet").
          <Step key={i} mark="open" title={sentence(c.note || c.label)} />
        ) : (
          <Step key={i} mark={MARK[c.kind]} title={sentence(c.label)}>
            {c.note && (
              <span className="rs-step-x">
                {c.kind === "reviewer" ? `“${clip(c.note, 240)}”` : sentence(clip(c.note, 240))}
              </span>
            )}
          </Step>
        ))}
        {sealOf(row.checked) === "word" && <Step mark="open" title="Nothing outside the agent checked this." />}
        <Step mark="open" title={<>Produced by {row.made_by
          ? <button className="linkish inline" onClick={() => ctx.openAgent(row.made_by!.id)}>{row.made_by.name}</button>
          : "an agent not recorded"}</>}>
          {produced && <span className="rs-step-x">{produced}</span>}
          {row.run_id && <WhatTheyDid runId={row.run_id} projectId={ctx.projectId} label="What they did to make it" />}
        </Step>
      </ol>
      <div className="rs-serves">
        <h3 className="eyebrow-h">Serves</h3>
        {/* What it is for, from the project's goal down: the question "why does
            this file exist?" answered before the list of outcomes it serves. */}
        <p className="rs-chain"><GoalChain goal={goal} serves={row.serves} task={row.task} /></p>
        {row.serves.length === 0 ? <span>No outcome on the roadmap.</span> : (
          <ul>
            {row.serves.map((s) => (
              <li key={s.key}><Link className="mono" to={roadmap}>{s.key}</Link>{clip(s.statement, 140)}</li>
            ))}
          </ul>
        )}
      </div>
      {row.checked === "platform" && (
        <p className="rs-limit">Verified means the check passed, not that it is what you wanted. That part is yours.</p>
      )}
      {(ctx.dash.can_steer !== false || sent) && (
        <div className="actions">
          {ctx.dash.can_steer !== false && wrong === null && !sent && (
            <button className="small" onClick={() => setWrong("")}>Wrong because…</button>
          )}
          {sent && <span className="help">Sent to {ceo} in Chat.</span>}
        </div>
      )}
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

/** Nothing published. On a project that has delivered, that is not "nothing
    yet": the outcomes were checked on the workspace and no file was the
    product, and the page says so instead of contradicting the project's state. */
function NothingPublished({ ctx }: { ctx: ProjectCtx }) {
  if (!hasDelivered(ctx)) {
    return (
      <div className="card rs-empty">
        <Icon name="file" size={16} />
        <div>
          <p><b>Nothing published yet.</b></p>
          <p>Each result will show who made it and what checked it.</p>
        </div>
      </div>
    );
  }
  if (!ctx.plan) return <Skeleton rows={1} />;
  const met = (ctx.plan.requirements ?? []).filter((r) => r.status === "met").length;
  if (met === 0) {
    return (
      <div className="card rs-empty">
        <Icon name="file" size={16} />
        <div>
          <p><b>Delivered, no file published.</b></p>
          <p>Drafts and working files are in{" "}
            <Link to={`/projects/${ctx.projectId}/details/deliverables`}>Details › Deliverables</Link>.</p>
        </div>
      </div>
    );
  }
  return (
    <div className="card rs-empty delivered">
      <Icon name="shield" size={16} />
      <div>
        <p><b>{plural(met, "outcome")} verified, no file published.</b></p>
        <p>The checks ran on the project's workspace.{" "}
          <Link to={`/projects/${ctx.projectId}/details/roadmap`}>See what was checked</Link></p>
      </div>
    </div>
  );
}

export default function Results({ ctx }: { ctx: ProjectCtx }) {
  const { projectId } = ctx;
  const { data, error } = usePoll(() => get<ResultsView>(`/observe/projects/${projectId}/results`), 30000, [projectId]);
  const [picked, setPicked] = useState<string | null>(null);
  const side = useRef<HTMLDivElement>(null);
  const rows = data?.results ?? [];
  const row = rows.find((r) => r.key === picked) ?? rows[0];

  function pick(key: string) {
    setPicked(key);
    // Stacked on a narrow screen, the panel sits under the whole list: bring it
    // to the reader. Side by side it is already in view and nothing moves.
    requestAnimationFrame(() => {
      const el = side.current;
      if (!el) return;
      const box = el.getBoundingClientRect();
      if (box.top < window.innerHeight - 120 && box.bottom > 0) return;
      const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      el.scrollIntoView({ behavior: still ? "auto" : "smooth", block: "start" });
    });
  }

  return (
    <>
      <PageHead title="Results" lede={<>
        What the team published, newest first. Drafts are in{" "}
        <Link to={`/projects/${projectId}/details/deliverables`}>Details › Deliverables</Link>.
      </>} />
      {error && <div className="error">{error}</div>}
      {!data ? <Skeleton rows={5} /> : rows.length === 0 ? <NothingPublished ctx={ctx} /> : (
        <>
          <SealCounts data={data} />
          <div className="rs">
            <div className="rs-grid">
              <ul className="card rs-list" aria-label="Results">
                {rows.map((r) => (
                  <ResultLine key={r.key} row={r} on={row?.key === r.key} onPick={() => pick(r.key)} />
                ))}
              </ul>
              <div className="rs-side" ref={side}>
                {row && <WhyTrust key={row.key} ctx={ctx} row={row} goal={data.goal} />}
              </div>
            </div>
          </div>
        </>
      )}
    </>
  );
}
