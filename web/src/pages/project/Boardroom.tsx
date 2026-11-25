import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import {
  ApprovalRow,
  AskRow,
  ChannelRow,
  Dashboard,
  get,
  MessageRow,
  OrgView,
  patch,
  post,
  RunRow,
} from "../../api";
import { WhatTheyDid } from "../../did";
import { DeliverableReader } from "./Deliverables";
import { fmtWhen, usePoll } from "../../hooks";
import Markdown from "../../Markdown";
import { clip, fmtSoon, SEND_HINT, sendOnEnter } from "../../ui";
import { useProjectFrame } from "../../projectCtx";
import { Avatar } from "../../ui/kit";
import { Icon, IconName } from "../../ui/Icon";
import { holdDecision, useHeldKeys } from "../../undo";
import "./boardroom.css";

/** What the poll re-fetches, and what one scroll back up costs. The tail is
    deliberately small: it is re-fetched every two seconds for as long as the
    tab is open, and a chairman reading the room needs the last exchange, not
    the last fortnight. Everything older arrives because he scrolled to it. */
const TAIL = 30;
const OLDER_PAGE = 50;

/** What a new chairman does not know he may ask. Each fills the box rather
    than sending — the words are a suggestion, not a script. The first set is
    for an empty conversation, where the useful opening is what matters to you. */
const SUGGESTIONS = [
  "What changed since we last talked?",
  "What's blocking the team?",
  "Why did the date change?",
  "From now on, ",
];
const OPENERS = [
  "Here's what matters most to me: ",
  "How will you approach this?",
  "From now on, ask me before buying any data.",
];

/** One conversation out of two sources: the tail the poll keeps refreshing, and
    the older pages fetched once each as the reader scrolls back. Keyed by id,
    later wins — so a `frozen` flag flipping on a message we already hold is
    picked up from the fresher copy — then ordered by `(at, id)`, which is the
    same total order the server pages with. The two orderings have to agree: if
    they drift, a message lands on the wrong side of a page seam and the reader
    sees it twice or not at all.

    Returns `prev` untouched when nothing moved, so an idle chat stops
    allocating a new list (and re-rendering the room) every two seconds. */
function merge(prev: MessageRow[], incoming: MessageRow[]): MessageRow[] {
  const by = new Map(prev.map((m) => [m.id, m]));
  for (const m of incoming) by.set(m.id, m);
  const next = [...by.values()].sort((a, b) => a.at.localeCompare(b.at) || a.id.localeCompare(b.id));
  // By value, not by identity: every row in `incoming` is a fresh object out of
  // fresh JSON, so an identity check would call an idle conversation "changed"
  // on every tick and defeat the whole point of this comparison.
  const same =
    next.length === prev.length &&
    next.every((m, i) => {
      const was = prev[i];
      return was.id === m.id && was.body === m.body && was.at === m.at && was.frozen === m.frozen;
    });
  return same ? prev : next;
}

const DAY = new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric" });
const TIME = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });

/** "Today" / "Yesterday" / "Tue, Mar 4" — the anchors that make a long scroll
    back readable instead of one undifferentiated wall of messages. */
function dayLabel(iso: string): string {
  const d = new Date(iso);
  const midnight = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((midnight(new Date()) - midnight(d)) / 86_400_000);
  return days === 0 ? "Today" : days === 1 ? "Yesterday" : DAY.format(d);
}

/** One message, memoised on primitives only. The messages array is rebuilt
    from fresh JSON on every poll, so nothing about a message keeps its
    identity — but its text does keep its *value*, and that is what `memo`
    compares. Pass an object here and the memo turns off silently: the room
    re-renders (and re-parses its markdown) every two seconds and on every
    keystroke in the composer, which is the state this page was rescued from.

    Three shapes, so nobody's words are mistaken for anybody else's. The CEO's
    are prose under its name and face, the way an assistant reads in any chat
    you already know — no box, because they are most of what is on the page.
    Yours sit in your own bubble on the right. A line nobody typed — an assist,
    a capacity case, a need — is yaaf's, centred and labelled as such: it used
    to sit under the CEO's name and face, so the chairman read the platform's
    wording as the CEO talking. */
const MessageItem = memo(function MessageItem(
  { body, at, mine, platform, who, team, runId, projectId, speaker }: {
    body: string; at: string; mine: boolean; platform: boolean; who: string;
    team: string | null; runId?: string | null; projectId: string; speaker: string | null;
  },
) {
  const when = TIME.format(new Date(at));
  if (platform) {
    return (
      <div className="cr-note" role="note">
        <Icon name="info" size={15} />
        <div className="cr-note-b">
          <span className="cr-note-who">yaaf</span>
          <Markdown>{body}</Markdown>
        </div>
      </div>
    );
  }
  if (mine) {
    return (
      <div className="cr-me">
        <div className="msg me">
          <span className="sr-only">{speaker && speaker !== "chairman" ? speaker : "You"}: </span>
          <div className="body"><Markdown>{body}</Markdown></div>
        </div>
        <div className="cr-when" title={fmtWhen(at)}>
          {speaker && speaker !== "chairman" ? `${speaker} · ` : ""}{when}
        </div>
      </div>
    );
  }
  const first = who.split(" ")[0];
  return (
    <div className="cr-ai">
      <Avatar name={who} team={team} />
      <div className="cr-ai-b">
        <div className="cr-ai-h"><b>{first}</b> <span title={fmtWhen(at)}>{when}</span></div>
        <div className="cr-prose"><Markdown>{body}</Markdown></div>
        {/* How the CEO got to what it says: the run that wrote it, opened in
            place. Only for the CEO's own words — yours have no run behind
            them, and a platform notice is written by no run at all. */}
        {runId && <WhatTheyDid runId={runId} projectId={projectId} label={`How ${first} did this`} />}
      </div>
    </div>
  );
});

/** A question the CEO asked, under the message it asked it in, answerable in
    one click. The answer reaches the CEO as the same chat line typing it would
    have sent, and is held for a few seconds so it can be taken back — the same
    as on the Inbox, which draws the same question from the same list. */
function QuestionCard({ ask, projectId, ceoFirst, onOwnWords }: {
  ask: AskRow; projectId: string; ceoFirst: string; onOwnWords: (prefix: string) => void;
}) {
  const held = useHeldKeys().has(ask.id);
  if (ask.status !== "open" || held) {
    return (
      <div className="cr-q done">
        <Icon name="check" size={14} />
        <span>
          {held ? "Answer sending…" : ask.status === "answered" ? "You answered this."
            : ask.status === "lapsed" ? `No answer by the deadline, so ${ceoFirst} went with: ${clip(ask.default_action, 140)}`
              : "This question was closed."}
        </span>
      </div>
    );
  }
  const rec = (ask.recommendation ?? "").toLowerCase();
  const recommended = (o: string) => !!rec && (rec.includes(o.toLowerCase()) || o.toLowerCase().includes(rec));
  const options = [...(ask.options ?? [])].sort((x, y) => Number(recommended(y)) - Number(recommended(x)));
  const answer = (option: string) => holdDecision({
    key: ask.id,
    label: `Answered: ${clip(option, 60)}`,
    path: `/admin/projects/${projectId}/boardroom`,
    body: { body: `Re: “${ask.title}” — ${option}` },
  });
  return (
    <div className="cr-q" role="group" aria-label={`Question from ${ceoFirst}: ${ask.title}`}>
      <div className="cr-q-k">
        <span><Icon name="help" size={14} /> Question for you</span>
        <span className="cr-q-due"><Icon name="clock" size={13} /> Answer {fmtSoon(ask.answer_by)}</span>
      </div>
      <div className="cr-q-t">{ask.title}</div>
      {ask.recommendation && <p><b>{ceoFirst} recommends:</b> {ask.recommendation}</p>}
      <p className="help">If you don't answer, {ceoFirst} goes with: {clip(ask.default_action, 200)}</p>
      <div className="cr-q-a">
        {options.map((o) => (
          <button key={o} className={`small${recommended(o) ? " primary" : ""}`} onClick={() => answer(o)}>{o}</button>
        ))}
        <button className="small linkish" onClick={() => onOwnWords(`Re: “${ask.title}” — `)}>
          Answer in my own words
        </button>
      </div>
    </div>
  );
}

/** The rules the CEO and every agent work under — the brief's standing
    orders — beside the conversation where they are set. Saying "From now on…"
    adds one; removing one here does what Settings › Brief does: it moves to
    "No longer applies", so an agent that meets the old rule knows it was
    taken back, and the CEO is told the brief changed. Held a few seconds
    first, so a misclick is an Undo rather than a second edit. */
function RulesPanel({ dash, projectId, onClose, onChanged }: {
  dash: Dashboard; projectId: string; onClose: () => void; onChanged: () => void;
}) {
  const orders = dash.standing_orders ?? [];
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);
  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);
  const remove = (rule: string) => {
    if (timer.current) window.clearTimeout(timer.current);
    setError(null);
    setPending(rule);
    timer.current = window.setTimeout(async () => {
      try {
        await patch(`/admin/projects/${projectId}`, {
          standing_orders: (dash.standing_orders ?? []).filter((o) => o !== rule),
          retired_orders: [...(dash.retired_orders ?? []).filter((o) => o !== rule), rule],
        });
        onChanged();
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setPending(null);
        timer.current = null;
      }
    }, 8000);
  };
  const keep = () => {
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = null;
    setPending(null);
  };
  return (
    <aside className="cr-panel" aria-label="Rules for your CEO">
      <div className="cr-panel-h">
        <h2>Your rules</h2>
        <button className="icon small" aria-label="Close" onClick={onClose}><Icon name="x" size={16} /></button>
      </div>
      <p className="help">
        Start a message with “From now on” to add one. Everyone on the team follows them.
      </p>
      {error && <div className="error">Couldn't remove that rule: {error}</div>}
      {orders.length ? (
        <ul className="cr-rules">
          {orders.map((o) => (
            <li key={o} className={pending === o ? "removing" : ""}>
              <span>{o}</span>
              {pending === o ? (
                <button className="small" onClick={keep}>Undo</button>
              ) : dash.can_steer !== false && (
                <button className="icon small" aria-label={`Remove rule: ${o}`} onClick={() => remove(o)}>
                  <Icon name="x" size={14} />
                </button>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="help">No rules yet.</p>
      )}
      {pending && <p className="help" role="status">Removing in a few seconds. It moves to “No longer applies”.</p>}
      <h2 className="cr-panel-h2">Always asks you first</h2>
      <ul className="cr-asks">
        <li><Icon name="check" size={13} /> Spending above your approval limit</li>
        <li><Icon name="check" size={13} /> Anything with legal weight</li>
        <li><Icon name="check" size={13} /> Passwords, keys and new data access</li>
      </ul>
      <Link className="help" to={`/projects/${projectId}/settings?s=brief`}>See the full brief in Settings →</Link>
    </aside>
  );
}

/** The file a link in a message points at, when it is one of this project's
    deliverables: the "Deliverable published" line links to
    `#/projects/<id>/deliverables?open=<key>`, and a message can carry the
    same under `details/`. */
function deliverableIn(href: string | null, projectId: string): string | null {
  const m = href?.match(/^#\/projects\/([^/?#]+)\/(?:details\/)?deliverables\?(.*)$/);
  if (!m || decodeURIComponent(m[1]) !== projectId) return null;
  return new URLSearchParams(m[2]).get("open");
}

type Presence = { tone: "good" | "work" | "off" | "bad"; icon: IconName; word: string };

/** The chat is the only speaking channel, and it reads like one: a single
    centred column of conversation, the composer at its foot. What used to be
    a rail of six cards beside it — spend in tokens, the last refusal, the
    decisions, the bets — is either in the conversation where it happened (a
    refused action, a question), one strip above it (what waits in the Inbox,
    why the CEO is quiet), or behind the Rules button. Nothing it showed is
    lost; none of it competes with the conversation any more. */
export default function Boardroom({ projectId, dash, org }: {
  projectId: string;
  dash: Dashboard;
  org: OrgView | null;
}) {
  const ceoName = org?.ceo?.name ?? "your CEO";
  const ceoFirst = ceoName.split(" ")[0];
  const [channelId, setChannelId] = useState<string | null>(null);
  // The deliverable opened from a link in the chat, read over the chat.
  const [reading, setReading] = useState<string | null>(null);
  const stopReading = useCallback(() => setReading(null), []);
  // A screen that sends you here to ask about something it is showing hands
  // over the sentence, already quoting it, for you to edit or throw away.
  // Router state rather than a query parameter on purpose: the text is long,
  // and it must not survive a reload and re-fill a box you deliberately
  // cleared.
  const handedOver = (useLocation().state as { draft?: string } | null)?.draft;
  const [draft, setDraft] = useState(handedOver ?? "");
  const [busy, setBusy] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [rulesOpen, setRulesOpen] = useState(false);
  const [searching, setSearching] = useState(false);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<MessageRow[] | null>(null);
  // The conversation this page holds, oldest first: the live tail merged with
  // whatever older pages the reader has scrolled back to. It only ever grows,
  // which is what keeps the two sources contiguous — a static "older" cache
  // beside a sliding tail is contiguous only at the moment it is fetched, and
  // opens a silent hole in the middle of the conversation once the tail moves
  // past where the cache ended.
  const [loaded, setLoaded] = useState<MessageRow[]>([]);
  const [atOldest, setAtOldest] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  // Pinned means "following the conversation". Unpinned means the reader is
  // reading something further up and must not be dragged away from it.
  const [pinned, setPinned] = useState(true);
  // The newest message the reader was present for. Everything after it is what
  // the pill counts — a counter incremented per update would say "1 new" when
  // three replies landed in the same tick.
  const [seenId, setSeenId] = useState<string | null>(null);
  const pinnedRef = useRef(true);
  const chatRef = useRef<HTMLDivElement>(null);
  const topRef = useRef<HTMLButtonElement>(null);
  const boxRef = useRef<HTMLTextAreaElement>(null);
  const restoreRef = useRef<{ height: number; top: number } | null>(null);
  const wantBottomRef = useRef(false);
  const frame = useProjectFrame();
  const reloadFrame = frame?.ctx?.reload;

  useEffect(() => {
    setLoaded([]);
    setAtOldest(false);
    setSeenId(null);
    pinnedRef.current = true;
    setPinned(true);
    get<ChannelRow[]>(`/observe/projects/${projectId}/channels`).then((channels) => {
      const boardroom = channels.find((c) => c.kind === "boardroom");
      setChannelId(boardroom?.id ?? null);
    });
  }, [projectId]);

  // Only the tail is polled. Everything older is fetched once, on the way past
  // it, and then left alone — re-reading the whole conversation every two
  // seconds is what made this page expensive to leave open.
  const { data: tail, reload } = usePoll(
    () =>
      channelId
        ? get<MessageRow[]>(`/observe/channels/${channelId}/messages?limit=${TAIL}`)
        : Promise.resolve([] as MessageRow[]),
    dash.chat_live ? 2000 : 4000,
    [channelId],
  );
  // Polled in both modes: without a resident session the server reads
  // "typing" off the queued run that is on its way to the reply.
  const { data: typing } = usePoll(
    () =>
      channelId
        ? get<{ typing: boolean }>(`/observe/channels/${channelId}/typing`)
        : Promise.resolve({ typing: false }),
    2000,
    [channelId],
  );

  useEffect(() => {
    if (!tail) return;
    setLoaded((prev) => merge(prev, tail));
    // A page shorter than we asked for is the whole conversation — there is
    // nothing above it to scroll to. An EMPTY page is not that claim: until the
    // boardroom channel id resolves the fetcher answers `[]`, and this effect
    // re-runs the moment it does, still holding that empty answer. Reading it
    // as "shorter than a page" declared a 200-message conversation fully
    // loaded before its first message had arrived, and the scroll then found
    // nothing above it, ever.
    if (tail.length > 0 && tail.length < TAIL) setAtOldest(true);
  }, [tail]);

  const lastId = loaded.length ? loaded[loaded.length - 1].id : null;
  const lastIdRef = useRef<string | null>(null);
  lastIdRef.current = lastId;

  const loadOlder = useCallback(async () => {
    if (!channelId || loadingOlder || atOldest || loaded.length === 0) return;
    setLoadingOlder(true);
    try {
      const page = await get<MessageRow[]>(
        `/observe/channels/${channelId}/messages?limit=${OLDER_PAGE}&before=${loaded[0].id}`,
      );
      // Measured here, after the await and immediately before the state
      // change: a poll tick can land while the request is in flight, and a
      // measurement taken before it would be stale by exactly the jump this is
      // meant to prevent.
      const el = chatRef.current;
      if (el) restoreRef.current = { height: el.scrollHeight, top: el.scrollTop };
      if (page.length < OLDER_PAGE) setAtOldest(true);
      if (page.length) setLoaded((prev) => merge(prev, page));
    } finally {
      setLoadingOlder(false);
    }
  }, [channelId, loadingOlder, atOldest, loaded]);

  // The observer is built once per channel and calls through a ref, because
  // `loadOlder` changes identity every time a message lands and rebuilding an
  // IntersectionObserver every two seconds would be its own small waste.
  const loadOlderRef = useRef(loadOlder);
  loadOlderRef.current = loadOlder;

  // Re-run when the sentinel appears, not only when the channel does: the
  // button is drawn once the first page has landed, which is strictly after
  // this effect first ran, and an observer built while it was still absent
  // observed nothing at all. And again when search closes: the stream (and
  // its sentinel) is a new element then.
  const hasSentinel = loaded.length > 0 && !atOldest;
  useEffect(() => {
    const sentinel = topRef.current;
    const root = chatRef.current;
    if (!sentinel || !root) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) loadOlderRef.current();
      },
      // Ahead of the edge, so history is already there when the reader gets to
      // where it should be.
      { root, rootMargin: "200px 0px 0px 0px" },
    );
    io.observe(sentinel);
    return () => io.disconnect();
  }, [channelId, hasSentinel, searching]);
  const { data: runs } = usePoll(
    () => get<RunRow[]>(`/observe/projects/${projectId}/runs?limit=50`),
    8000,
  );
  const { data: approvals } = usePoll(
    () => get<ApprovalRow[]>(`/observe/projects/${projectId}/approvals`),
    6000,
  );
  // The questions it is waiting on you for, drawn under the message that
  // asked each one. The Desk used to send the chairman here to "answer in the
  // boardroom" and the boardroom did not show them.
  const { data: asks } = usePoll(
    () => get<AskRow[]>(`/observe/projects/${projectId}/asks`),
    10000,
  );
  const openAsks = (asks ?? []).filter((a) => a.status === "open");

  // Follow the conversation, but only for a reader who is at the foot of it.
  // Keyed on the newest message rather than the count, because the count also
  // changes when older messages are prepended — and scrolling someone to the
  // bottom the instant they ask to read upwards is the whole complaint.
  //
  // `scrollTop`, not `scrollIntoView`: the latter walks every ancestor scroller
  // (it used to move the whole page), and its smooth animation emits scroll
  // events that transiently report "not at the bottom", which races the pin.
  useLayoutEffect(() => {
    const el = chatRef.current;
    if (el && pinnedRef.current) {
      el.scrollTop = el.scrollHeight;
      setSeenId(lastId);
    }
  }, [lastId, typing?.typing, searching]);

  // Tell the server how far the reader has got, so the sidebar's unread count
  // clears. The newest message they were present for, not "now": a reply that
  // lands between the render and this call is still news. Only while the tab
  // is in front of them — a page left open behind other windows reads nothing.
  const reportedRef = useRef<string | null>(null);
  useEffect(() => {
    const report = () => {
      if (!seenId || reportedRef.current === seenId || document.hidden) return;
      const message = loaded.find((m) => m.id === seenId);
      if (!message) return;
      reportedRef.current = seenId;
      post(`/admin/projects/${projectId}/boardroom/seen`, { until: message.at })
        .then(() => reloadFrame?.())
        .catch(() => { reportedRef.current = null; });
    };
    report();
    document.addEventListener("visibilitychange", report);
    return () => document.removeEventListener("visibilitychange", report);
  }, [seenId, loaded, projectId, reloadFrame]);

  const unseen = useMemo(() => {
    if (pinned || !seenId) return 0;
    const i = loaded.findIndex((m) => m.id === seenId);
    return i < 0 ? 0 : loaded.length - 1 - i;
  }, [pinned, seenId, loaded]);

  useLayoutEffect(() => {
    const el = chatRef.current;
    if (!el) return;
    if (wantBottomRef.current) {
      wantBottomRef.current = false;
      restoreRef.current = null;
      el.scrollTop = el.scrollHeight;
      return;
    }
    // Hold the reader's place: everything above them just got taller by the
    // height of the page we prepended. Before paint, or it shows as a flash.
    const saved = restoreRef.current;
    if (saved) {
      restoreRef.current = null;
      el.scrollTop = el.scrollHeight - saved.height + saved.top;
    }
  }, [loaded]);

  const onScroll = useCallback(() => {
    const el = chatRef.current;
    if (!el) return;
    const bottom = el.scrollHeight - el.scrollTop - el.clientHeight < 64;
    pinnedRef.current = bottom;
    // Assigned only on a change: a scroll fires this dozens of times a second.
    setPinned((was) => (was === bottom ? was : bottom));
    if (bottom) setSeenId(lastIdRef.current);
  }, []);

  /** Back to now — and the history the reader scrolled through goes with it.
      Coming back to the live end of the conversation is the natural moment to
      hand that memory back, and it costs nothing: the tail is already here. */
  function jumpToLatest() {
    setLoaded(tail ?? []);
    setAtOldest((tail?.length ?? 0) < TAIL);
    setSeenId(lastIdRef.current);
    pinnedRef.current = true;
    setPinned(true);
    wantBottomRef.current = true;
  }

  // The box grows with what you write, to a point, the way every chat does.
  useLayoutEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`;
  }, [draft]);

  const fillBox = (text: string) => {
    setDraft(text);
    requestAnimationFrame(() => {
      const el = boxRef.current;
      if (el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); }
    });
  };

  async function send() {
    if (!draft.trim() || busy) return;
    setBusy(true);
    setSendError(null);
    try {
      await post(`/admin/projects/${projectId}/boardroom`, { body: draft.trim() });
      setDraft("");
      // You always follow your own message.
      pinnedRef.current = true;
      setPinned(true);
      reload();
    } catch (e) {
      // What you wrote stays in the box. A failed send that cleared it would
      // be the one mistake a chat cannot make.
      setSendError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  // Search is the server's, over the whole conversation: the page only holds
  // the tail and what was scrolled through, and "not found" about the rest
  // would read as "you never said it".
  useEffect(() => {
    if (!searching || !channelId) return;
    const q = query.trim();
    if (!q) { setHits(null); return; }
    const t = window.setTimeout(() => {
      get<MessageRow[]>(`/observe/channels/${channelId}/search?q=${encodeURIComponent(q)}`)
        .then(setHits).catch(() => setHits([]));
    }, 250);
    return () => window.clearTimeout(t);
  }, [query, searching, channelId]);

  // The CEO is the agent with no team, and these are the kinds of run that
  // mean it is working on something a reply could come out of — boardroom_run
  // most of all: on an install with no resident session, it IS the reply.
  //
  // Named rather than excluded. The old test was "any running run of the CEO's
  // that is not a success_check", and a live chat session is exactly that: a
  // resident run, no team, running for as long as the session is adopted —
  // days. So the page said "the CEO is in a run now" permanently, on the one
  // page where presence is the whole point. A blocklist gets this wrong again
  // the next time a run kind is added; a list of what counts does not.
  const ceoRun = (runs ?? []).find(
    (r) => r.status === "running" && !r.team_name
      && (r.kind === "agent_run" || r.kind === "inbox_run" || r.kind === "boardroom_run"),
  );
  // The wall the live session last hit. A refused task_create looks exactly
  // like a CEO that decided not to bother, and the difference only existed in
  // the run row nobody on this page was reading — which is how "add a task"
  // could be answered with "done" and leave the board unchanged. It is said
  // in the conversation now, at the moment it happened.
  const chatSession = (runs ?? []).find(
    (r) => r.kind === "chat_session" && r.status === "running",
  );
  const lastRefusal = chatSession?.last_refusal ?? null;
  const refusedAt = chatSession?.last_refusal_at ?? null;
  // The CEO retries. A brief refused for length and refiled shorter left the
  // warning sitting beside a true "done" — so once the same call has gone
  // through, say that instead of telling him to doubt it.
  const refusalCleared = chatSession?.last_refusal_cleared_at ?? null;
  const pending = (approvals ?? []).filter((a) => a.status === "pending");
  const quiet = dash.status === "running" && dash.activity.brief.banner ? dash.activity.brief : null;

  // One word on the question you have while waiting: when will it answer?
  //
  // Read off the chat lane, never off the activity banner. The banner says why
  // *work* is not starting — out of runs, pacing, a paused seat, a blocked
  // board — and the chairman's message passes every one of those brakes. This
  // used to turn any serious banner into "Can't reply right now", which on a
  // project out of runs sat beside a strip saying "your messages in the
  // boardroom are still answered". The one real "can't" is the reply lane's
  // own backstop, and the server says when that is tripped.
  const presence: Presence = dash.status === "paused" || dash.status === "stopped"
    ? { tone: "off", icon: "pause", word: dash.status === "paused" ? "Team paused" : "Project stopped" }
    : dash.chat_held
      ? { tone: "bad", icon: "alert", word: "Can't reply right now" }
      : typing?.typing
          ? { tone: "work", icon: "message", word: "Typing…" }
          : dash.chat_live
            ? { tone: "good", icon: "check", word: "Replies in seconds" }
            : { tone: "work", icon: "clock", word: ceoRun || dash.runs.running > 0 ? "Finishing other work" : "Replies in a few minutes" };

  // The conversation, with day dividers, each question under the message that
  // asked it, and the last refused action at the moment it was refused.
  const rows = useMemo(() => {
    type Row =
      | { kind: "day"; key: string; label: string }
      | { kind: "msg"; key: string; m: MessageRow; runId: string | null }
      | { kind: "ask"; key: string; ask: AskRow }
      | { kind: "refusal"; key: string };
    const byMessage = new Map((asks ?? []).map((a) => [a.asked_in_message_id, a]));
    const placed = new Set<string>();
    const out: Row[] = [];
    let day = "";
    let refusalPlaced = !lastRefusal || !refusedAt;
    for (const m of loaded) {
      if (!refusalPlaced && refusedAt && m.at > refusedAt) {
        out.push({ kind: "refusal", key: "refusal" });
        refusalPlaced = true;
      }
      const label = dayLabel(m.at);
      if (label !== day) {
        day = label;
        out.push({ kind: "day", key: `day-${m.id}`, label });
      }
      // The run that wrote it: the sender's run whose window holds the moment
      // the message landed. Matched here rather than stored on the message,
      // because every message an agent posts is posted from inside a run.
      const at = new Date(m.at).getTime();
      const run = m.from_agent ? (runs ?? []).find((r) => r.agent_id === m.from_agent && r.started_at
        && new Date(r.started_at).getTime() <= at + 1000
        && (!r.finished_at || new Date(r.finished_at).getTime() + 5000 >= at)) : undefined;
      out.push({ kind: "msg", key: m.id, m, runId: run?.id ?? null });
      const ask = byMessage.get(m.id);
      if (ask) {
        out.push({ kind: "ask", key: `ask-${ask.id}`, ask });
        placed.add(ask.id);
      }
    }
    if (!refusalPlaced) out.push({ kind: "refusal", key: "refusal" });
    // An open question whose message is further back than the page holds is
    // still waiting on you, so it is drawn at the foot rather than not at all.
    for (const a of openAsks) {
      if (!placed.has(a.id) && atOldest === false) out.push({ kind: "ask", key: `ask-${a.id}`, ask: a });
    }
    return out;
  }, [loaded, runs, asks, openAsks, atOldest, lastRefusal, refusedAt]);

  const onChatClick = (e: React.MouseEvent) => {
    // A deliverable named in the chat opens over the chat, the way a file in
    // any chat app does, instead of taking the reader to another page and
    // losing their place in the conversation. A modified click is still the
    // browser's: a new tab, a window.
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const a = (e.target as HTMLElement).closest("a");
    const key = a ? deliverableIn(a.getAttribute("href"), projectId) : null;
    if (!key) return;
    e.preventDefault();
    setReading(key);
  };

  const empty = !!tail && loaded.length === 0;
  const speakerOf = (m: MessageRow) => (m.from_user ? m.from_name ?? null : null);
  const ruleCount = (dash.standing_orders ?? []).length;

  return (
    <div className={`cr${rulesOpen ? " with-panel" : ""}`}>
      <div className="cr-main">
        <header className="cr-bar">
          <Avatar name={ceoName} team="leadership" />
          <div className="cr-who">
            <b>{ceoName}</b>
            <span>Your CEO</span>
          </div>
          <span className={`cr-pres ${presence.tone}`} role="status">
            <Icon name={presence.icon} size={13} />{presence.word}
          </span>
          <span className="grow" />
          <button className={`icon${searching ? " on" : ""}`} aria-label="Search this chat" aria-pressed={searching}
            onClick={() => { setSearching(!searching); setQuery(""); setHits(null); }}>
            <Icon name="search" size={16} />
          </button>
          <button className={`small${rulesOpen ? " on" : ""}`} aria-expanded={rulesOpen} onClick={() => setRulesOpen(!rulesOpen)}
            aria-label={`Your rules${ruleCount ? ` (${ruleCount})` : ""}`}>
            <Icon name="list" size={14} /> <span className="cr-hide-sm">Rules{ruleCount ? ` · ${ruleCount}` : ""}</span>
          </button>
        </header>

        {/* One strip at most: why the project's work is held, else what waits
            for you elsewhere. Questions are not counted here — they are in the
            conversation, where they were asked. */}
        {dash.chat_held && dash.status === "running" ? (
          <div className="cr-strip serious" role="status">
            <Icon name="alert" size={15} />
            <span className="grow"><b>The CEO's last few replies failed.</b>{" "}
              <span className="help">Replies are held for a few minutes so a broken seat doesn't
                loop; what you write is kept and answered once the hold lifts.</span></span>
          </div>
        ) : quiet ? (
          <div className={`cr-strip ${quiet.tone}`} role="status">
            <Icon name={quiet.tone === "critical" || quiet.tone === "serious" ? "alert" : "moon"} size={15} />
            <span className="grow"><b>{quiet.headline}</b> <span className="help">{quiet.detail}</span></span>
          </div>
        ) : pending.length > 0 ? (
          <div className="cr-strip">
            <Icon name="inbox" size={15} />
            <span className="grow">
              {pending.length === 1 ? "1 decision is" : `${pending.length} decisions are`} waiting for you in your Inbox.
            </span>
            <Link className="btnlink small" to={`/projects/${projectId}`}>Open Inbox</Link>
          </div>
        ) : null}

        {searching && (
          <div className="cr-search" role="search">
            <Icon name="search" size={15} />
            <input autoFocus aria-label="Search this chat" placeholder="Search this chat" value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Escape") { setSearching(false); setQuery(""); setHits(null); } }} />
            {hits && query.trim() && <span className="help">{hits.length === 50 ? "50+ found" : `${hits.length} found`}</span>}
            <button className="small" onClick={() => { setSearching(false); setQuery(""); setHits(null); }}>Close</button>
          </div>
        )}

        <div className="cr-wrap">
          {searching && query.trim() ? (
            <div className="cr-scroll">
              <div className="cr-col">
                {hits === null ? <p className="help cr-center">Searching…</p> : hits.length === 0 ? (
                  <div className="cr-empty">
                    <b>No messages match “{query.trim()}”</b>
                    <p className="help">Try a shorter word, or ask {ceoFirst} directly.</p>
                  </div>
                ) : [...hits].reverse().map((m) => (
                  <div className="cr-hit" key={m.id}>
                    <div className="cr-hit-h">
                      <b>{m.from_user ? (m.from_name && m.from_name !== "chairman" ? m.from_name : "You")
                        : m.from_agent ? ceoFirst : "yaaf"}</b>
                      <span className="help">{dayLabel(m.at)}, {TIME.format(new Date(m.at))}</span>
                    </div>
                    <div className="cr-prose"><Markdown>{clip(m.body, 600)}</Markdown></div>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="cr-scroll" ref={chatRef} onScroll={onScroll} onClick={onChatClick}
              role="log" aria-live="polite" aria-label={`Conversation with ${ceoFirst}`}>
              <div className="cr-col">
                {/* Older messages load themselves as the reader arrives at them —
                    the sentinel below is what the observer watches. It is a real
                    button so that a keyboard or screen reader gets the same
                    affordance a scroll wheel does; with a mouse you never see it
                    fire. */}
                {loaded.length > 0 && !atOldest && (
                  <button ref={topRef} className="small cr-earlier" onClick={() => loadOlderRef.current()}
                    disabled={loadingOlder}>
                    {loadingOlder ? "Loading…" : "Load earlier messages"}
                  </button>
                )}
                {loaded.length > 0 && atOldest && (
                  <div className="cr-start">This is the start of your conversation with {ceoFirst}.</div>
                )}
                {!tail && (
                  <div className="cr-skel" aria-busy="true">
                    <span className="sr-only">Loading your conversation with {ceoFirst}</span>
                    <i /><i className="me" /><i /><i className="me" />
                  </div>
                )}
                {empty && (
                  <div className="cr-welcome">
                    <Avatar name={ceoName} team="leadership" size="lg" />
                    <h2>Meet {ceoFirst}, your CEO</h2>
                    <p>
                      {ceoFirst} runs your team and is the one you talk to. Tell {ceoFirst} what matters,
                      ask why something happened, or set a rule that starts with “From now on”.
                    </p>
                  </div>
                )}
                {rows.map((row) =>
                  row.kind === "day" ? (
                    <div className="cr-day" key={row.key}><span>{row.label}</span></div>
                  ) : row.kind === "ask" ? (
                    <QuestionCard key={row.key} ask={row.ask} projectId={projectId} ceoFirst={ceoFirst} onOwnWords={fillBox} />
                  ) : row.kind === "refusal" ? (
                    refusalCleared ? (
                      <div className="cr-note good" key={row.key} role="note">
                        <Icon name="check" size={15} />
                        <div className="cr-note-b">
                          A project rule stopped something {ceoFirst} tried{refusedAt ? ` at ${TIME.format(new Date(refusedAt))}` : ""}.
                          {" "}{ceoFirst} tried again and it went through.
                        </div>
                      </div>
                    ) : (
                      <div className="cr-note warn" key={row.key} role="note">
                        <Icon name="alert" size={15} />
                        <div className="cr-note-b">
                          <b>A project rule stopped something {ceoFirst} tried{refusedAt ? ` at ${TIME.format(new Date(refusedAt))}` : ""}.</b>
                          {" "}It did not happen, even if a message says it did. {clip(lastRefusal ?? "", 220)}
                        </div>
                      </div>
                    )
                  ) : (
                    <MessageItem
                      key={row.key}
                      body={row.m.body}
                      at={row.m.at}
                      mine={!!row.m.from_user}
                      platform={!row.m.from_user && !row.m.from_agent}
                      who={ceoName}
                      team="leadership"
                      runId={row.runId}
                      projectId={projectId}
                      speaker={speakerOf(row.m)}
                    />
                  ),
                )}
                {typing?.typing && (
                  <div className="cr-typing" aria-label={`${ceoFirst} is typing`}>
                    <Avatar name={ceoName} team="leadership" size="sm" />
                    <span className="cr-dots" aria-hidden="true"><i /><i /><i /></span>
                    <span>{ceoFirst} is typing…</span>
                  </div>
                )}
                {!typing?.typing && ceoRun && !dash.chat_live && loaded.length > 0 && loaded[loaded.length - 1].from_user && (
                  <div className="cr-typing">
                    <span className="working-dot" aria-hidden="true" />
                    <span>{ceoFirst} is working on it. The reply will appear here.</span>
                  </div>
                )}
              </div>
            </div>
          )}
          {/* Nothing arriving moves a reader who has scrolled up; this is how
              they get back, and how they find out something did arrive. */}
          {!searching && !pinned && loaded.length > 0 && (
            <button className="cr-jump" onClick={jumpToLatest}>
              <Icon name="down" size={14} /> {unseen > 0 ? `${unseen} new message${unseen === 1 ? "" : "s"}` : "Back to latest"}
            </button>
          )}
        </div>

        <div className="cr-compose">
          <div className="cr-col">
            {/* Said before you send, exactly when it applies: the platform
                reads any message from you as an answer to every question
                asked before it. */}
            {draft.trim() && openAsks.length > 0 && (
              <p className="cr-heads">
                <Icon name="info" size={14} />
                <span>
                  This message also answers {ceoFirst}'s open question{openAsks.length === 1 ? "" : "s"}
                  {openAsks.length === 1 ? `: “${clip(openAsks[0].title, 80)}”` : ""}. To answer a question
                  directly, use its buttons above.
                </span>
              </p>
            )}
            {draft.trim() && dash.status === "paused" && (
              <p className="cr-heads warn">
                <Icon name="pause" size={14} />
                <span>The team is paused, so {ceoFirst} will read this when you resume it.</span>
              </p>
            )}
            {!draft && (
              <div className="quick cr-chips">
                {(empty ? OPENERS : SUGGESTIONS).map((q) => (
                  <button key={q} className="qchip" onClick={() => fillBox(q)}>{q.trim()}</button>
                ))}
              </div>
            )}
            <div className="cr-box">
              <textarea
                ref={boxRef}
                rows={1}
                aria-label={`Message ${ceoFirst}`}
                value={draft}
                placeholder={`Message ${ceoFirst}`}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={sendOnEnter(send, { disabled: busy || !draft.trim() })}
              />
              <button className="primary cr-send" onClick={send} disabled={busy || !draft.trim()}
                aria-label="Send">
                <Icon name="send" size={16} />
              </button>
            </div>
            {sendError ? (
              <p className="error cr-err" role="alert">Your message didn't send. It's still in the box. {sendError}</p>
            ) : (
              <p className="help cr-hint">{SEND_HINT}</p>
            )}
          </div>
        </div>
      </div>
      {rulesOpen && (
        <RulesPanel dash={dash} projectId={projectId} onClose={() => setRulesOpen(false)}
          onChanged={() => reloadFrame?.()} />
      )}
      {reading && <DeliverableReader projectId={projectId} id={reading} onClose={stopReading} />}
    </div>
  );
}
