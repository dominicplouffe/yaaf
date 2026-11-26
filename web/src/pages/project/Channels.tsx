import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Conversation, ConversationsView, get, post } from "../../api";
import { usePoll } from "../../hooks";
import { InboxExplainer, RaiseInboxReads, readingLine } from "../../inbox";
import Markdown from "../../Markdown";
import type { ProjectCtx } from "../Project";
import "./conversations.css";

/** What the agents said to each other, as the approved mockup draws it: the
    rooms, the conversations in a room with how each one ended, and the open
    conversation read like chat. It was a When / From / Message table, where a
    handoff nobody picked up looked exactly like "On it." The server works out
    how each conversation ended (yaaf/server/conversations.py); this page only
    says it. */

const ATTENTION = new Set(["waiting", "stuck", "frozen"]);
const FACES = ["c1", "c3", "c5", "c4", "good"];
const FIXED: Record<string, string> = { you: "you", yaaf: "yaaf" };
const DAY_MS = 86_400_000;

const list = (xs: string[]) =>
  xs.length <= 1 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;
const has = (n: number) => (n === 1 ? "hasn't" : "haven't");
const when = (iso: string) => {
  const d = new Date(iso);
  return Date.now() - d.getTime() < 6 * DAY_MS
    ? `${d.toLocaleDateString(undefined, { weekday: "short" })} ${d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}`
    : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
};
const clock = (iso: string) => new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
const dayOf = (iso: string) => new Date(iso).toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
const ago = (iso: string) => {
  const h = Math.round((Date.now() - new Date(iso).getTime()) / 3_600_000);
  return h < 1 ? "less than an hour ago" : h < 48 ? `${h} hour${h === 1 ? "" : "s"} ago` : `${Math.round(h / 24)} days ago`;
};

function tagOf(c: Conversation): string {
  switch (c.outcome) {
    case "handoff": return "Handed off";
    case "answered": return "Answered";
    case "stuck": return "Handoff not picked up";
    case "frozen": return `Frozen · ${c.messages.length} back-and-forths`;
    case "note": return c.started_by === "yaaf" ? "From yaaf" : "No reply";
    default: return c.unread.length ? `Waiting for ${list(c.unread)}` : "Waiting for a reply";
  }
}

export default function Channels({ ctx }: { ctx: ProjectCtx }) {
  const { projectId, dash } = ctx;
  const base = `/projects/${projectId}`;
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const { data, reload } = usePoll(
    () => get<ConversationsView>(`/observe/projects/${projectId}/conversations`), 15000, [projectId]);
  const [filter, setFilter] = useState<"attention" | "all" | null>(null);
  const [who, setWho] = useState("");
  const [q, setQ] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const people = data?.people ?? {};
  const faceOf = useMemo(() => {
    const ids = Object.keys(people).filter((id) => !FIXED[id]).sort();
    return (id: string | null) => FIXED[id ?? ""] ?? FACES[Math.max(0, ids.indexOf(id ?? "")) % FACES.length];
  }, [people]);
  const nameOf = (id: string | null) => (id && people[id]?.name) || "Someone";

  if (!data) return <p className="help">Loading…</p>;
  const rooms = data.rooms;
  // Opens on the room with the most that needs attention, else the busiest.
  const roomId = params.get("room") && rooms.some((r) => r.id === params.get("room")) ? params.get("room")!
    : [...rooms].sort((a, b) => b.open - a.open || (b.last_at ?? "").localeCompare(a.last_at ?? ""))[0]?.id;
  // Unread by agent: what one agent has not read yet, from every room, and
  // why. This is the old Agent inbox tab, folded in.
  const agentId = params.get("agent") ?? (params.get("view") === "unread" ? data.agents[0]?.id ?? null : null);
  const agent = agentId ? data.agents.find((a) => a.id === agentId) ?? null : null;
  const room = agent ? undefined : rooms.find((r) => r.id === roomId);
  const roomOf = (id: string) => rooms.find((r) => r.id === id)?.name ?? "";
  const inRoom = agent ? data.conversations.filter((c) => c.unread_ids.includes(agent.id))
    : data.conversations.filter((c) => c.room === roomId);
  const open = inRoom.filter((c) => ATTENTION.has(c.outcome));
  const mode = agent ? "all" : filter ?? (open.length ? "attention" : "all");
  const needle = q.trim().toLowerCase();
  const shown = inRoom
    .filter((c) => mode === "all" || ATTENTION.has(c.outcome))
    .filter((c) => !who || c.people.includes(who))
    .filter((c) => !needle || (c.title + " " + c.messages.map((m) => m.body).join(" ")).toLowerCase().includes(needle));
  const current = shown.find((c) => c.id === params.get("c")) ?? shown[0] ?? null;

  const pick = (next: Record<string, string | null>) => {
    const p = new URLSearchParams(params);
    for (const [k, v] of Object.entries(next)) { if (v) p.set(k, v); else p.delete(k); }
    setParams(p, { replace: true });
  };

  const groups = [["team", "Teams"], ["leadership", "Leadership"], ["boardroom", "With you"]] as const;

  return (
    <>
      <p className="subtitle">What your agents said to each other, one conversation at a time. You read everything; you speak only in the Boardroom.</p>
      <div className="cv-app">
        <nav className="cv-pane" aria-label="Rooms">
          <div className="cv-rooms">
            {groups.map(([kind, label]) => {
              const rs = rooms.filter((r) => r.kind === kind);
              if (!rs.length) return null;
              return (
                <div key={kind} className="cv-group">
                  <span className="cv-label">{label}</span>
                  {rs.map((r) => (
                    <button key={r.id} className="cv-room" aria-current={!agent && r.id === roomId}
                      onClick={() => { setFilter(null); setWho(""); pick({ room: r.id, c: null, agent: null, view: null }); }}>
                      <b>{r.name}</b>
                      <span className={`cv-open${r.open ? "" : " zero"}`}>{r.open || "–"}</span>
                      <span className="cv-meta">{r.members.map(nameOf).join(", ")}{r.last_at ? ` · ${when(r.last_at)}` : " · quiet"}</span>
                    </button>
                  ))}
                </div>
              );
            })}
            <div className="cv-group">
              <span className="cv-label">Unread by agent</span>
              {data.agents.length ? data.agents.map((a) => (
                <button key={a.id} className={`cv-room${a.fault ? " fault" : ""}`} aria-current={agent?.id === a.id}
                  onClick={() => { setWho(""); pick({ agent: a.id, view: null, c: null }); }}>
                  <b>{a.name}</b>
                  <span className="cv-open">{a.unread}</span>
                  <span className="cv-meta">{a.why}</span>
                </button>
              )) : <span className="cv-meta cv-allread">Every agent has read its messages.</span>}
            </div>
          </div>
        </nav>

        <section className="cv-pane" aria-label="Conversations">
          <div className="cv-pane-h">
            {agent && (
              <div className={`cv-agent-sum${agent.fault ? " fault" : ""}`}>
                <b>{agent.name} hasn't read {agent.unread} message{agent.unread === 1 ? "" : "s"}</b>
                <span>{agent.why}</span>
                <span className="help">{readingLine(dash.activity.inbox_runs)}</span>
                {dash.can_steer !== false && (
                  <RaiseInboxReads projectId={projectId} reading={dash.activity.inbox_runs} onDone={() => { reload(); ctx.reload(); }} />
                )}
                <InboxExplainer projectId={projectId} reading={dash.activity.inbox_runs} onInbox />
              </div>
            )}
            <input className="cv-search" type="search" placeholder="Search what agents said" aria-label="Search conversations"
              value={q} onChange={(e) => setQ(e.target.value)} />
            {!agent && <div className="cv-filters" role="group" aria-label="Show">
              <button className="cv-chip" aria-pressed={mode === "attention"} onClick={() => setFilter("attention")}>
                Needs attention<span className="n">{open.length}</span></button>
              <button className="cv-chip" aria-pressed={mode === "all"} onClick={() => setFilter("all")}>
                All<span className="n">{inRoom.length}</span></button>
              <select className="cv-who" aria-label="Agent" value={who} onChange={(e) => setWho(e.target.value)}>
                <option value="">Anyone</option>
                {(room?.members ?? []).map((m) => <option key={m} value={m}>{nameOf(m)}</option>)}
              </select>
            </div>}
          </div>
          <div className="cv-list">
            {shown.length ? shown.map((c) => (
              <button key={c.id} className="cv-card" aria-current={c.id === current?.id} onClick={() => pick({ c: c.id })}>
                <span className="cv-card-top">
                  <span className="cv-faces">{c.people.map((p) => <Face key={p} id={p} name={nameOf(p)} cls={faceOf(p)} />)}</span>
                  <span className="cv-whois">{agent && <>{roomOf(c.room)} · </>}{c.people.map(nameOf).join(" ↔ ")} · {c.replies ? `${c.replies} ${c.replies === 1 ? "reply" : "replies"}` : "no replies"}</span>
                  <span className="cv-when">{when(c.last_at)}</span>
                </span>
                <h3>{c.title}</h3>
                <span className={`cv-tag ${c.outcome}`}>{tagOf(c)}</span>
              </button>
            )) : (
              <div className="cv-empty">
                {agent ? "Nothing unread in the last two weeks." : inRoom.length ? (mode === "attention" ? <>Nothing needs attention in this room.<br />
                  <button className="small" onClick={() => setFilter("all")}>Show all conversations</button></>
                  : "Nothing matches.") : `Nobody has said anything here in the last ${data.days} days.`}
              </div>
            )}
          </div>
        </section>

        <section className="cv-pane cv-thread" aria-label="Conversation">
          {current ? (
            <ThreadView c={current} base={base} nameOf={nameOf} faceOf={faceOf} people={people}
              roomName={roomOf(current.room)} whyOf={(name) => data.agents.find((a) => a.name === name)?.why ?? null} expanded={expanded === current.id} onExpand={() => setExpanded(current.id)}
              canSteer={dash.can_steer !== false} error={error}
              onAsk={(draft) => navigate(`${base}/chat`, { state: { draft } })}
              onUnfreeze={async () => {
                setError(null);
                try { await post(`/admin/threads/${current.id}/unfreeze`); reload(); }
                catch (e) { setError((e as Error).message); }
              }} />
          ) : <div className="cv-empty">Pick a conversation.</div>}
        </section>
      </div>
    </>
  );
}

function Face({ id, name, cls, big }: { id: string; name: string; cls: string; big?: boolean }) {
  return <span className={`cv-face f-${cls}${big ? " big" : ""}`} title={name} data-id={id}>{name.charAt(0).toUpperCase()}</span>;
}

/** An @mention, as a link that goes nowhere but reads as one. */
const withMentions = (body: string, names: string[]) =>
  names.reduce((text, n) => text.replace(new RegExp(`(^|[^\\w\\[])@${n}\\b`, "gi"), `$1[@${n}](#@${n})`), body);

function ThreadView({ c, base, nameOf, faceOf, people, roomName, whyOf, expanded, onExpand, canSteer, error, onAsk, onUnfreeze }: {
  c: Conversation; base: string; nameOf: (id: string | null) => string; faceOf: (id: string | null) => string;
  people: ConversationsView["people"]; roomName: string; whyOf: (name: string) => string | null;
  expanded: boolean; onExpand: () => void;
  canSteer: boolean; error: string | null; onAsk: (draft: string) => void; onUnfreeze: () => void;
}) {
  const starter = nameOf(c.started_by);
  const task = c.refs.find((r) => r.kind === "task");
  let box: { text: string; act?: { label: string; primary?: boolean; run?: () => void; to?: string } } | null = null;
  if (c.outcome === "stuck") {
    box = {
      text: `${starter} handed this to ${c.to ?? roomName}. ${c.unread.length ? `${list(c.unread)} never read it` : "Nobody replied"} and no task was created.`,
      act: canSteer ? { label: "Ask the CEO to assign it", primary: true,
        run: () => onAsk(`${starter} handed this over on ${dayOf(c.started_at)} and nobody picked it up: "${c.title}" Please make sure it's assigned.`) } : undefined,
    };
  } else if (c.outcome === "handoff") {
    box = { text: "A task came out of this conversation.", act: task ? { label: "Open the task", to: `${base}/details/tasks?view=list` } : undefined };
  } else if (c.outcome === "waiting") {
    box = { text: `${starter} said this ${ago(c.last_at)}. ${c.asleep.length ? `${list(c.asleep)} ${has(c.asleep.length)} woken up since.`
      : c.unread.length ? `${list(c.unread)} ${has(c.unread.length)} read it yet.` : "Nobody has replied yet."}` };
  } else if (c.outcome === "frozen") {
    box = {
      text: `${list(c.people.map(nameOf))} went back and forth ${c.messages.length} times with nothing done, so yaaf froze the conversation.`,
      act: canSteer ? { label: "Unfreeze", run: onUnfreeze } : undefined,
    };
  }

  // Each reference sits under the message that names it; what was made
  // rather than named sits under the last word.
  const refsFor = new Map<string, Conversation["refs"]>();
  for (const r of c.refs) {
    const at = c.messages.find((m) => m.body.includes(r.id) || (r.kind !== "task" && m.body.includes(r.label)))
      ?? c.messages[c.messages.length - 1];
    refsFor.set(at.id, [...(refsFor.get(at.id) ?? []), r]);
  }
  const hrefOf = (r: Conversation["refs"][number]) => r.kind === "task" ? `${base}/details/tasks?view=list`
    : r.kind === "goal" ? `${base}/details/roadmap` : `${base}/deliverables`;

  // Long conversations show their opening and their end; the middle opens on request.
  const all = c.messages;
  const folded = !expanded && all.length > 8;
  const visible = folded ? [...all.slice(0, 3), null, ...all.slice(-3)] : all;
  const names = Object.values(people).map((p) => p.name).filter((n) => /^[\w-]+$/.test(n));

  const rows: JSX.Element[] = [];
  let lastDay = "";
  let lastFrom: string | null | undefined;
  visible.forEach((m, i) => {
    if (m === null) {
      rows.push(<button key="more" className="cv-day cv-more" onClick={onExpand}>{all.length - 6} more messages</button>);
      lastFrom = undefined;
      return;
    }
    const d = new Date(m.at).toDateString();
    if (d !== lastDay) { rows.push(<div key={`d-${m.id}`} className="cv-day">{dayOf(m.at)}</div>); lastDay = d; lastFrom = undefined; }
    const same = m.from === lastFrom;
    lastFrom = m.from;
    const p = m.from ? people[m.from] : null;
    rows.push(
      <div key={m.id} className={`cv-msg${same ? " cont" : ""}`}>
        {same ? <span /> : <Face id={m.from ?? ""} name={nameOf(m.from)} cls={faceOf(m.from)} big />}
        <div>
          {!same && (
            <div className="cv-msg-h"><b>{nameOf(m.from)}</b>{p && <span className="cv-role">{p.role}</span>}
              {i === 0 && c.to && c.to !== roomName && <span className="cv-to">to {c.to}</span>}
              {i === 0 && c.to === roomName && roomName !== "Boardroom" && <span className="cv-to">to {roomName}</span>}
              <span className="cv-t">{clock(m.at)}</span></div>
          )}
          <div className="cv-bubble"><Markdown>{withMentions(m.body, names)}</Markdown></div>
          {(refsFor.get(m.id) ?? []).map((r) => (
            <Link key={`${r.kind}-${r.id}`} className="cv-ref" to={hrefOf(r)}>
              {r.kind !== "file" && <span className="k">{r.kind === "task" ? "Task" : r.id}</span>}
              {r.kind === "goal" ? r.label.slice(0, 80) : r.label}
            </Link>
          ))}
        </div>
      </div>,
    );
  });

  // Why they haven't, from the same gates that decide the wake: one reason
  // per distinct reason, so three agents held by one setting read as one line.
  const whys = [...new Set(c.unread.map(whyOf).filter((w): w is string => !!w))];
  const readLine = c.unread.length
    ? [`Not read yet by ${list(c.unread)}`, c.read.length ? `${list(c.read)} read it` : null,
        c.asleep.length ? `${list(c.asleep)} ${has(c.asleep.length)} woken up since` : null].filter(Boolean).join(" · ")
    : c.read.length ? "Read by everyone" : "";

  return (
    <>
      <div className="cv-thread-h">
        <h2>{c.title}</h2>
        <div className="cv-row">
          <span className="cv-faces">{c.people.map((p) => <Face key={p} id={p} name={nameOf(p)} cls={faceOf(p)} />)}</span>
          {c.people.map((id, i) => <span key={id}>{nameOf(id)} <span className="cv-dim">({people[id]?.role ?? ""})</span>{i < c.people.length - 1 ? "," : ""}</span>)}
          <span>·</span><span>{roomName}</span>
        </div>
      </div>
      {box && (
        <div className={`cv-outcome ${c.outcome}`}>
          <span className={`cv-tag ${c.outcome}`}>{tagOf(c)}</span>
          <p>{box.text}</p>
          {box.act && (box.act.to
            ? <Link className="btnlink small" to={box.act.to}>{box.act.label}</Link>
            : <button className={`small${box.act.primary ? " primary" : ""}`} onClick={box.act.run}>{box.act.label}</button>)}
        </div>
      )}
      {error && <div className="error" style={{ margin: "8px 18px 0" }}>{error}</div>}
      <div className="cv-msgs">
        {rows}
        {readLine && <div className={`cv-read${c.unread.length ? " unread" : ""}`}>{readLine}
          {whys.map((w) => <span key={w} className="cv-why">{w}</span>)}</div>}
      </div>
    </>
  );
}
