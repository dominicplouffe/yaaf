import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ApprovalRow, AskRow, DeskProject, get, post, splitWithout } from "../api";
import { BriefBody, DeskBrief, ProjectBrief } from "../briefing";
import { isLive, Item, ItemType, needsYou, soonestClock, useDesk } from "../desk";
import { fmtWhen, usePoll } from "../hooks";
import { clip, DecideButtons, fmtAgo, fmtSoon, NoteAction, SEND_HINT, sendOnEnter } from "../ui";
import { Avatar, EmptyState, Seg } from "../ui/kit";
import { Icon, IconName } from "../ui/Icon";
import { flagPlain } from "../ui/project";

/** One place for everything that wants the chairman, across every project.

    It replaces the Desk, the action strip on every tab, the Overview's
    decision list and the asks hidden under Records → Decisions — four places,
    three definitions of "needs you", and a badge that disagreed with the page
    under it. The Desk's rule survives intact: **Needs you** is exactly the
    rows only the chairman can settle, each with what happens if he does
    nothing, ranked by time to consequence, and it is the only tab the sidebar
    badge counts.

    The other tabs ask for nothing. **Updates** is news — what the CEO has been
    made to bring you, and what happened while you were away. **Watching** is
    every clock the CEO is running down on its own. **Done** is what you
    decided. They exist so nothing happens silently; they carry no badge so
    the one number that matters stays honest. */

type Tab = "needs" | "updates" | "watching" | "done";

interface FlagRow {
  id: string; kind: string; status: string; summary: string; proposal: string | null;
  raised_at: string | null; acknowledged_at: string | null; acknowledgement: string | null;
}

type Update =
  | { key: string; type: "flag"; at: string; project: DeskProject; flag: FlagRow }
  | { key: string; type: "brief"; at: string; project: DeskProject; brief: ProjectBrief };

type Decided =
  | { key: string; type: "approval"; at: string; project: DeskProject; row: ApprovalRow }
  | { key: string; type: "ask"; at: string; project: DeskProject; row: AskRow };

const ICON: Record<ItemType, IconName> = {
  approval: "check", ask: "help", scope: "list", commitment: "calendar", verdict: "eye",
  bet: "target", frozen: "alert", kill: "target", drought: "shield", budget: "wallet", noproof: "shield",
};

/** Why this one is the chairman's, in a clause — true at every dial setting,
    because each names something the dial does not delegate. */
function whyYours(kind: string): string {
  if (kind.includes("spend")) return "Above your approval threshold, so it is never an agent's call.";
  if (kind.includes("legal")) return "Anything with legal weight is yours by rule, at any autonomy setting.";
  if (kind.includes("tool")) return "A capability the project does not have — you hold the values.";
  if (kind.includes("assist")) return "A step only a human can take. The platform hands these over, never defeats them.";
  if (kind.includes("owner")) return "The CEO judged this yours rather than its own.";
  return "The constitution routes this kind of decision to you.";
}

function ProjectTag({ p }: { p: DeskProject }) {
  return (
    <span className="ptag"><Avatar name={p.name} team={p.name} size="sm" />{p.name}</span>
  );
}

/** The full request behind an approval: the desk row carries the title and
    the clock, the project's approvals carry the case. Fetched when opened. */
function ApprovalCase({ item }: { item: Item }) {
  const { data } = usePoll(
    () => get<ApprovalRow[]>(`/observe/projects/${item.project.id}/approvals`), 30000,
    [item.project.id]);
  const row = data?.find((a) => a.id === item.approvalId);
  const [theCase, without] = row ? splitWithout(row.body) : ["", null];
  return (
    <dl className="case">
      <div><dt>Why it is yours</dt><dd>{whyYours(item.kind.toLowerCase())}</dd></div>
      <div><dt>The case</dt><dd className="pre">{row ? theCase || "No case was written." : "Loading…"}</dd></div>
      {without && <div><dt>If you say no</dt><dd className="pre">{without}</dd></div>}
      {item.amountUsd !== null && item.amountUsd !== undefined && (
        <div><dt>Amount</dt><dd>${item.amountUsd.toLocaleString()}</dd></div>
      )}
      <div className="silence"><dt>If you do nothing</dt><dd>{item.consequence}</dd></div>
    </dl>
  );
}

/** Answering a question is speaking in the boardroom: the CEO reads every
    message there, and any reply of yours settles the questions asked before
    it. Said plainly when there are others, because one answer closing three
    questions is the platform's rule and should never be a surprise. */
function AskAnswer({ item, others, onDone }: { item: Item; others: number; onDone: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function send() {
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await post(`/admin/projects/${item.project.id}/boardroom`, {
        body: `Re: “${item.title}” — ${text.trim()}`,
      });
      setText("");
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="answer">
      <label className="field" htmlFor="ib-answer">Your answer — it goes to the CEO in the chat</label>
      <textarea id="ib-answer" value={text} onChange={(e) => setText(e.target.value)}
        onKeyDown={sendOnEnter(send, { disabled: busy || !text.trim() })}
        placeholder="e.g. Split it out, and footnote the reconciliation to the filing." />
      <div className="answer-acts">
        <button className="primary small" disabled={busy || !text.trim()} onClick={send}>
          <Icon name="send" size={13} /> Send answer
        </button>
        <span className="help">{SEND_HINT}</span>
      </div>
      {others > 0 && (
        <p className="help">
          The CEO has {others} other open question{others === 1 ? "" : "s"} on this project asked
          before this one. Any message from you answers them too — it will say what it did about each.
        </p>
      )}
      {error && <div className="error">{error}</div>}
    </div>
  );
}

function ItemDetail({ item, desk }: { item: Item; desk: ReturnType<typeof useDesk> }) {
  const otherAsks = item.type === "ask"
    ? desk.items.filter((i) => i.type === "ask" && i.project.id === item.project.id && i.key !== item.key).length
    : 0;
  return (
    <div className="ib-doc">
      <div className="ib-meta">
        <span className={`kind-ic k-${item.type}`}><Icon name={ICON[item.type]} size={15} /></span>
        <span className="ib-kind">{item.kind}</span>
        <ProjectTag p={item.project} />
        <span className="grow" />
        {item.clocked && (
          <span className={`clockpill${item.at - Date.now() < 6 * 3_600_000 ? " warn" : ""}`}>
            <Icon name="clock" size={13} />
            {item.type === "approval" ? "Denies itself" : item.type === "ask" ? "Falls back" : "Resolves"} {fmtSoon(new Date(item.at).toISOString())}
          </span>
        )}
      </div>
      <h2 className="ib-title">{item.what}</h2>
      {item.type === "approval" ? <ApprovalCase item={item} /> : (
        <dl className="case">
          {item.ask && <div><dt>The question</dt><dd className="pre">{item.ask.body || item.title}</dd></div>}
          {item.ask && <div><dt>If you say nothing</dt><dd>{item.ask.defaultAction}</dd></div>}
          {!item.ask && (
            <div className={item.yours ? "silence" : ""}>
              <dt>{item.yours ? "If you do nothing" : "What happens"}</dt><dd>{item.consequence}</dd>
            </div>
          )}
          {!item.yours && (
            <div><dt>Whose it is</dt><dd>The CEO's. It resolves without you — it is here so nothing happens silently.</dd></div>
          )}
        </dl>
      )}
      {item.type === "approval" && item.approvalId && (
        <div className="ib-acts">
          <DecideButtons approvalId={item.approvalId} onDone={desk.reload} />
          <span className="grow" />
          <Link to={`/projects/${item.project.id}/chat`}><Icon name="message" size={13} /> Discuss with the CEO</Link>
        </div>
      )}
      {item.type === "ask" && <AskAnswer item={item} others={otherAsks} onDone={desk.reload} />}
      {item.type !== "approval" && item.type !== "ask" && (
        <div className="ib-acts">
          <Link className={`btnlink${item.yours ? " primary" : ""}`} to={item.to}>{item.toLabel} <Icon name="arrow" size={13} /></Link>
        </div>
      )}
      <p className="ib-foot-note help">
        <Link to={`/projects/${item.project.id}`}>Open {item.project.name} →</Link>
      </p>
    </div>
  );
}

function UpdateDetail({ u, onChanged }: { u: Update; onChanged: () => void }) {
  if (u.type === "flag") {
    const plain = flagPlain(u.flag.kind);
    return (
      <div className="ib-doc">
        <div className="ib-meta">
          <span className="kind-ic k-flag"><Icon name="flag" size={15} /></span>
          <span className="ib-kind">From the CEO</span>
          <ProjectTag p={u.project} />
          <span className="grow" />
          {u.flag.raised_at && <span className="help">{fmtAgo(u.flag.raised_at)}</span>}
        </div>
        <h2 className="ib-title">{plain.title}</h2>
        <dl className="case">
          <div><dt>What it means</dt><dd>{plain.means}</dd></div>
          <div><dt>The CEO proposes</dt><dd className="pre">{u.flag.proposal ?? "Nothing yet."}</dd></div>
          <div><dt>In the platform's words</dt><dd>{u.flag.summary}</dd></div>
          <div><dt>If you say nothing</dt><dd>The proposal goes ahead. Nothing here blocks or expires.</dd></div>
        </dl>
        <div className="ib-acts">
          {u.flag.acknowledged_at ? (
            <span className="help">
              You noted this {fmtAgo(u.flag.acknowledged_at)}
              {u.flag.acknowledgement ? `: “${clip(u.flag.acknowledgement, 120)}”` : ""}. It stays until the numbers turn.
            </span>
          ) : (
            <NoteAction label="Noted" className="small primary" confirmLabel="Noted"
              placeholder="Anything to say back? — the CEO reads this"
              onSubmit={async (note) => { await post(`/admin/flags/${u.flag.id}/ack`, { note }); onChanged(); }} />
          )}
          <span className="grow" />
          <Link to={`/projects/${u.project.id}/chat`}><Icon name="message" size={13} /> Talk it through</Link>
        </div>
      </div>
    );
  }
  return (
    <div className="ib-doc">
      <div className="ib-meta">
        <span className="kind-ic k-brief"><Icon name="file" size={15} /></span>
        <span className="ib-kind">While you were away</span>
        <ProjectTag p={u.project} />
      </div>
      <BriefBody brief={u.brief} />
      <div className="ib-acts">
        {!u.brief.quiet && (
          <button className="small primary" onClick={async () => {
            await post(`/admin/projects/${u.project.id}/briefing/seen`, { until: u.brief.until });
            onChanged();
          }}>Mark as read</button>
        )}
        <span className="grow" />
        <Link to={`/projects/${u.project.id}/activity/summary`}>Everything that happened →</Link>
      </div>
    </div>
  );
}

function DecidedDetail({ d }: { d: Decided }) {
  return (
    <div className="ib-doc">
      <div className="ib-meta">
        <span className={`kind-ic k-${d.type}`}><Icon name={d.type === "approval" ? "check" : "help"} size={15} /></span>
        <span className="ib-kind">{d.type === "approval" ? "Approval" : "Question"}</span>
        <ProjectTag p={d.project} />
        <span className="grow" />
        <span className="help">{fmtWhen(d.at)}</span>
      </div>
      <h2 className="ib-title">{d.row.title}</h2>
      <dl className="case">
        {d.type === "approval" ? (
          <>
            <div><dt>Outcome</dt><dd>{d.row.status === "approved" ? "You approved it" : d.row.status === "denied" ? "You denied it" : "It ran out of time"}</dd></div>
            {d.row.decision_note && <div><dt>Your note</dt><dd>{d.row.decision_note}</dd></div>}
            <div><dt>The case</dt><dd className="pre">{d.row.body}</dd></div>
          </>
        ) : (
          <>
            <div><dt>Outcome</dt><dd>{d.row.status === "answered" ? "You answered it in the chat" : d.row.status === "lapsed" ? `No answer — the CEO took its default: ${d.row.default_action}` : "Closed"}</dd></div>
            <div><dt>The question</dt><dd className="pre">{d.row.body}</dd></div>
          </>
        )}
      </dl>
      <div className="ib-acts">
        <Link to={`/projects/${d.project.id}/activity/decisions`}>The full record →</Link>
      </div>
    </div>
  );
}

/** What the CEO has been made to bring you, and what happened while you were
    away — one request for the briefings, one per live project for its flags. */
function useUpdates(projects: DeskProject[]) {
  const ids = projects.map((p) => p.id).join(",");
  const { data: brief, reload: reloadBrief } = usePoll(() => get<DeskBrief>("/observe/briefing"), 60000);
  const { data: flags, reload: reloadFlags } = usePoll(
    () => Promise.all(projects.map((p) =>
      get<{ flags: FlagRow[] }>(`/observe/projects/${p.id}/flags`)
        .then((v) => v.flags.map((flag) => ({ project: p, flag })))
        .catch(() => []))).then((all) => all.flat()),
    30000, [ids]);
  const updates = useMemo(() => {
    const byId = new Map(projects.map((p) => [p.id, p]));
    const out: Update[] = [];
    for (const { project, flag } of flags ?? []) {
      if (flag.status !== "raised") continue;
      out.push({ key: `flag-${flag.id}`, type: "flag", at: flag.raised_at ?? "", project, flag });
    }
    for (const b of brief?.projects ?? []) {
      const p = byId.get(b.project_id);
      if (!p || b.quiet) continue;
      out.push({ key: `brief-${b.project_id}`, type: "brief", at: b.until, project: p, brief: b });
    }
    return out.sort((a, b) => b.at.localeCompare(a.at));
  }, [brief, flags, projects]);
  const unread = updates.filter((u) => u.type === "brief" || !u.flag.acknowledged_at).length;
  return { updates, unread, reload: () => { reloadBrief(); reloadFlags(); } };
}

/** What you decided in the last week. Asked only when the tab is opened:
    history is read on purpose, not polled in case. */
function useDecided(projects: DeskProject[], active: boolean) {
  const [rows, setRows] = useState<Decided[] | null>(null);
  const ids = projects.map((p) => p.id).join(",");
  useEffect(() => {
    if (!active) return;
    let gone = false;
    const since = Date.now() - 7 * 86_400_000;
    Promise.all(projects.map(async (p) => {
      const [approvals, asks] = await Promise.all([
        get<ApprovalRow[]>(`/observe/projects/${p.id}/approvals`).catch(() => [] as ApprovalRow[]),
        get<AskRow[]>(`/observe/projects/${p.id}/asks`).catch(() => [] as AskRow[]),
      ]);
      const out: Decided[] = [];
      for (const a of approvals) {
        if (a.status !== "pending" && a.decided_at && new Date(a.decided_at).getTime() >= since) {
          out.push({ key: `a-${a.id}`, type: "approval", at: a.decided_at, project: p, row: a });
        }
      }
      for (const a of asks) {
        const at = a.answered_at ?? a.lapsed_at ?? a.closed_at;
        if (a.status !== "open" && at && new Date(at).getTime() >= since) {
          out.push({ key: `q-${a.id}`, type: "ask", at, project: p, row: a });
        }
      }
      return out;
    })).then((all) => { if (!gone) setRows(all.flat().sort((a, b) => b.at.localeCompare(a.at))); });
    return () => { gone = true; };
    // `ids` stands in for `projects`, whose identity changes on every poll.
  }, [active, ids]);
  return rows;
}

const latestRun = (ps: DeskProject[]) =>
  ps.map((p) => p.last_run_at).filter((v): v is string => v !== null).sort().at(-1) ?? null;

/** An empty Needs-you tab proves itself rather than asserting it. *Nothing is
    wrong* and *nothing is happening* are different screens — a chairman whose
    projects are all paused must not be told they are caught up. Carried over
    from the Desk, which learnt it first. The next clock is any clock, the
    CEO's included: its job is to show the platform is awake and counting. */
function AllClear({ projects, items }: { projects: DeskProject[]; items: Item[] }) {
  const live = projects.filter(isLive);
  const nextClock = soonestClock(items);
  if (live.length === 0) {
    return (
      <div className="quiet bad">
        <div className="mark">Nothing is running.</div>
        <div className="sub">
          You have {projects.length} project{projects.length === 1 ? "" : "s"} that could be
          working and {projects.length === 1 ? "it is not" : "none is"}. No agent is doing
          anything right now — which is <b>not</b> the same as all clear.
        </div>
        <div className="proof">
          <div className="pf"><div className="pv">{projects.filter((p) => p.status === "paused").length}</div><div className="pl">paused</div></div>
          <div className="pf"><div className="pv">{projects.filter((p) => p.status === "draft").length}</div><div className="pl">never started</div></div>
          <div className="pf"><div className="pv">{fmtAgo(latestRun(projects))}</div><div className="pl">last run</div></div>
        </div>
        <div style={{ marginTop: 14 }}><Link to="/projects">See them on Projects →</Link></div>
      </div>
    );
  }
  return (
    <div className="quiet ok">
      <div className="mark">Nothing needs you.</div>
      <div className="sub">
        No decision is blocked, nothing has stopped, and nothing is waiting on a
        click from you. The proof it is actually working:
      </div>
      <div className="proof">
        <div className="pf"><div className="pv">{live.length}</div><div className="pl">project{live.length === 1 ? "" : "s"} working</div></div>
        <div className="pf"><div className="pv">{fmtAgo(latestRun(live))}</div><div className="pl">last run</div></div>
        <div className="pf"><div className="pv">{nextClock ? fmtSoon(new Date(nextClock.at).toISOString()) : "none"}</div><div className="pl">next clock</div></div>
      </div>
    </div>
  );
}

export default function Inbox() {
  const desk = useDesk();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab | null) ?? "needs";
  const only = params.get("project");
  const projects = desk.data?.projects ?? [];
  const { updates, unread, reload: reloadUpdates } = useUpdates(projects);
  const decided = useDecided(projects, tab === "done");
  const listRef = useRef<HTMLDivElement>(null);

  const inProject = <T extends { project: DeskProject }>(xs: T[]) => (only ? xs.filter((x) => x.project.id === only) : xs);
  const needs = inProject(desk.mine);
  const watching = inProject(desk.items.filter((i) => !needsYou(i)));
  const news = inProject(updates);
  const done = inProject(decided ?? []);

  type Row = { key: string; render: () => React.ReactNode; detail: () => React.ReactNode };
  const rows: Row[] = tab === "needs" || tab === "watching"
    ? (tab === "needs" ? needs : watching).map((it) => ({
        key: it.key,
        render: () => (
          <>
            <span className={`kind-ic k-${it.type}`}><Icon name={ICON[it.type]} size={15} /></span>
            <span className="grow">
              <span className="ib-r1"><span className="ib-kind">{it.kind}</span><span className="grow" />
                {it.clocked && <span className={`help${it.at - Date.now() < 6 * 3_600_000 ? " warn" : ""}`}>{fmtSoon(new Date(it.at).toISOString())}</span>}</span>
              <span className="ib-t">{it.title}{it.amountUsd ? <span className="amt">${it.amountUsd.toLocaleString()}</span> : null}</span>
              <span className="ib-r3"><ProjectTag p={it.project} /></span>
            </span>
          </>
        ),
        detail: () => <ItemDetail item={it} desk={desk} />,
      }))
    : tab === "updates"
      ? news.map((u) => ({
          key: u.key,
          render: () => (
            <>
              <span className={`kind-ic k-${u.type}`}><Icon name={u.type === "flag" ? "flag" : "file"} size={15} /></span>
              <span className="grow">
                <span className="ib-r1"><span className="ib-kind">{u.type === "flag" ? "From the CEO" : "While you were away"}</span>
                  <span className="grow" /><span className="help">{u.at ? fmtAgo(u.at) : ""}</span></span>
                <span className="ib-t">{u.type === "flag" ? flagPlain(u.flag.kind).title : clip(u.brief.headline, 120)}</span>
                <span className="ib-r3"><ProjectTag p={u.project} />
                  {u.type === "flag" && u.flag.acknowledged_at && <span className="help">noted</span>}</span>
              </span>
            </>
          ),
          detail: () => <UpdateDetail u={u} onChanged={reloadUpdates} />,
        }))
      : done.map((d) => ({
          key: d.key,
          render: () => (
            <>
              <span className={`kind-ic k-${d.type}`}><Icon name={d.type === "approval" ? "check" : "help"} size={15} /></span>
              <span className="grow">
                <span className="ib-r1"><span className="ib-kind">{d.type === "approval" ? "Approval" : "Question"}</span>
                  <span className="grow" /><span className="help">{fmtAgo(d.at)}</span></span>
                <span className="ib-t">{d.row.title}</span>
                <span className="ib-r3"><ProjectTag p={d.project} /><span className="help">{d.row.status}</span></span>
              </span>
            </>
          ),
          detail: () => <DecidedDetail d={d} />,
        }));

  const wanted = params.get("item");
  const selected = rows.find((r) => r.key === wanted) ?? rows[0];
  const select = (key: string) => {
    const next = new URLSearchParams(params);
    next.set("item", key);
    setParams(next, { replace: true });
  };
  const setTab = (t: Tab) => {
    const next = new URLSearchParams();
    if (t !== "needs") next.set("tab", t);
    if (only) next.set("project", only);
    setParams(next, { replace: true });
  };

  // j / k walk the list — reading is the one thing safe to do from the keys.
  const keyState = useRef({ rows, selected, select });
  keyState.current = { rows, selected, select };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const el = document.activeElement as HTMLElement | null;
      if (el && (/^(input|textarea|select)$/i.test(el.tagName) || el.isContentEditable)) return;
      if (e.key !== "j" && e.key !== "k") return;
      const { rows: rs, selected: sel, select: pick } = keyState.current;
      const i = rs.findIndex((r) => r.key === sel?.key);
      const n = rs[Math.max(0, Math.min(rs.length - 1, i + (e.key === "j" ? 1 : -1)))];
      if (n) pick(n.key);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => {
    listRef.current?.querySelector(".ib-row.on")?.scrollIntoView({ block: "nearest" });
  }, [selected?.key]);

  const soonest = soonestClock(desk.mine);
  const withItems = projects.filter((p) => desk.items.some((i) => i.project.id === p.id) || updates.some((u) => u.project.id === p.id));
  // The proof an empty tab gives is about what you are looking at: filtered to
  // a paused project, "2 projects are running" would be true and beside the point.
  const scoped = only ? projects.filter((p) => p.id === only) : projects;
  const running = scoped.filter(isLive).length;

  if (desk.error && !desk.data) return <div className="error">{desk.error}</div>;

  return (
    <div className="inbox">
      <div className="ib-list">
        <div className="ib-head">
          <h1>Inbox</h1>
          <p className="ib-sub">
            {desk.mine.length === 0 ? "Nothing needs a decision from you."
              : <><b>{desk.mine.length} thing{desk.mine.length === 1 ? "" : "s"} need{desk.mine.length === 1 ? "s" : ""} you.</b>
                {soonest && <> The soonest decides itself {fmtSoon(new Date(soonest.at).toISOString())}.</>}</>}
          </p>
          <Seg<Tab>
            value={tab}
            onPick={setTab}
            options={[
              { key: "needs", label: "Needs you", count: needs.length || undefined },
              { key: "updates", label: "Updates", count: unread || undefined },
              { key: "watching", label: "Watching", count: watching.length || undefined },
              { key: "done", label: "Done" },
            ]}
          />
          {withItems.length > 1 && (
            <div className="ib-filter">
              <button className={`qchip${!only ? " on" : ""}`} onClick={() => { const n = new URLSearchParams(params); n.delete("project"); n.delete("item"); setParams(n, { replace: true }); }}>All projects</button>
              {withItems.map((p) => (
                <button key={p.id} className={`qchip${only === p.id ? " on" : ""}`}
                  onClick={() => { const n = new URLSearchParams(params); n.set("project", p.id); n.delete("item"); setParams(n, { replace: true }); }}>
                  {p.name}
                </button>
              ))}
            </div>
          )}
          <p className="ib-hint help">
            {tab === "needs" && "Only what you alone can settle, soonest consequence first. Each says what happens if you do nothing."}
            {tab === "updates" && "News to read: what the CEO was made to bring you, and what happened while you were away. Nothing here expires."}
            {tab === "watching" && "Clocks the CEO is handling on its own. Nothing here waits on you."}
            {tab === "done" && "What you decided in the last seven days."}
          </p>
        </div>
        <div className="ib-rows" ref={listRef}>
          {!desk.data ? <p className="help" style={{ padding: 16 }}>Loading…</p>
            : tab === "done" && decided === null ? <p className="help" style={{ padding: 16 }}>Loading…</p>
              : rows.length === 0 ? (
                <EmptyState good={tab === "needs"} icon={tab === "needs" ? "check" : tab === "updates" ? "file" : tab === "watching" ? "eye" : "history"}
                  title={tab === "needs" ? "Nothing needs you" : tab === "updates" ? "No news" : tab === "watching" ? "Nothing running down a clock" : "Nothing decided this week"}>
                  {tab === "needs"
                    ? running > 0
                      ? `${running} project${running === 1 ? " is" : "s are"} running, and nothing is waiting on a decision from you.`
                      : "No project is running right now — which is not the same as all clear."
                    : tab === "updates" ? "Nothing new since you last looked." : tab === "watching" ? "No budget, proof or bet clocks are close." : "Decisions you make show up here with your note."}
                </EmptyState>
              ) : rows.map((r) => (
                <button key={r.key} className={`ib-row${selected?.key === r.key ? " on" : ""}`} onClick={() => select(r.key)}>
                  {r.render()}
                </button>
              ))}
        </div>
        <div className="ib-keys help"><kbd>J</kbd><kbd>K</kbd> move · decisions take a click, never a key</div>
      </div>
      <div className="ib-detail">
        {selected ? selected.detail() : tab === "needs" && projects.length > 0 ? (
          <div className="ib-empty"><AllClear projects={scoped} items={inProject(desk.items)} /></div>
        ) : (
          <div className="ib-empty">
            <EmptyState good icon="inbox" title={projects.length ? "You're all caught up" : "You don't chair a project yet"}
              foot={!projects.length ? <button className="primary" onClick={() => navigate("/projects/new")}>Start a project</button> : undefined}>
              {projects.length
                ? "Pick anything on the left to read it in full."
                : "A project is a CEO agent, a team, a goal and a budget. Once one is running, this is where it asks you for what only you can decide."}
            </EmptyState>
          </div>
        )}
      </div>
    </div>
  );
}
