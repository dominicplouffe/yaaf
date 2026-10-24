import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import {
  ApprovalRow,
  AskRow,
  ChannelRow,
  Dashboard,
  get,
  HypothesisRow,
  MessageRow,
  OrgView,
  post,
  RunRow,
} from "../../api";
import { WhatTheyDid } from "../../did";
import { fmtWhen, usePoll } from "../../hooks";
import Markdown from "../../Markdown";
import {
  clip,
  clockClass,
  DecideButtons,
  fmtLeft,
  fmtUnit,
  killChip,
  SEND_HINT,
  sendOnEnter,
} from "../../ui";
import { useProjectFrame } from "../../projectCtx";
import { Avatar } from "../../ui/kit";

/** What the poll re-fetches, and what one scroll back up costs. The tail is
    deliberately small: it is re-fetched every two seconds for as long as the
    tab is open, and a chairman reading the room needs the last exchange, not
    the last fortnight. Everything older arrives because he scrolled to it. */
const TAIL = 30;

const SUGGESTIONS = [
  "What changed since we last spoke?",
  "Why did the date move?",
  "What is blocked, and on whom?",
  "From now on, ask me before buying any data.",
];
const OLDER_PAGE = 50;

/** One conversation out of two sources: the tail the poll keeps refreshing, and
    the older pages fetched once each as the reader scrolls back. Keyed by id,
    later wins — so a `frozen` flag flipping on a message we already hold is
    picked up from the fresher copy — then ordered by `(at, id)`, which is the
    same total order the server pages with. The two orderings have to agree: if
    they drift, a message lands on the wrong side of a page seam and the reader
    sees it twice or not at all.

    Returns `prev` untouched when nothing moved, so an idle boardroom stops
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

/** "Today" / "Yesterday" / "Tue 4 Mar" — the anchors that make a long scroll
    back readable instead of one undifferentiated wall of bubbles. */
function dayLabel(iso: string): string {
  const d = new Date(iso);
  const midnight = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((midnight(new Date()) - midnight(d)) / 86_400_000);
  return days === 0 ? "Today" : days === 1 ? "Yesterday" : DAY.format(d);
}

/** One bubble, memoised on primitives only. The messages array is rebuilt from
    fresh JSON on every poll, so nothing about a message keeps its identity —
    but its text does keep its *value*, and that is what `memo` compares. Pass
    an object here and the memo turns off silently: the room re-renders (and
    re-parses its markdown) every two seconds and on every keystroke in the
    composer, which is the state this page was rescued from. */
/** A message from a person, with that person's face on it. It used to be an
    unlabelled dark box over the word "CEO" — which is a job title, not
    somebody you are talking to. */
/** A line nobody typed — an assist, a capacity case, a need — is the
    platform's, and says so. It used to sit under the CEO's name and face, so
    the chairman read the platform's wording as the CEO talking. */
const MessageBubble = memo(function MessageBubble(
  { body, at, mine, platform, who, team, runId, projectId }: {
    body: string; at: string; mine: boolean; platform: boolean; who: string;
    team: string | null; runId?: string | null; projectId: string;
  },
) {
  const name = platform ? "Platform notice" : who;
  return (
    <div className={"msgrow" + (mine ? " me" : "")}>
      {!mine && <Avatar name={platform ? "Platform" : who} team={platform ? null : team} />}
      <div className={"msg" + (mine ? " me" : "") + (platform ? " sys" : "")}>
        <div className="who">{mine ? "You" : name}</div>
        <div className="body">
          <Markdown>{body}</Markdown>
        </div>
        <div className="when">{fmtWhen(at)}</div>
        {/* How the CEO got to what it says: the run that wrote it, opened in
            place. Only for the CEO's own words — yours have no run behind
            them, and a platform notice is written by no run at all. */}
        {!mine && !platform && runId && (
          <WhatTheyDid runId={runId} projectId={projectId} label="What they did" />
        )}
      </div>
    </div>
  );
});

/** The chat stays sacred — the only speaking channel. The rail beside it is
    read-only context: what the CEO is doing, what needs a decision, and the
    bets on the table. It adds no new speaking channel, so the asymmetry
    survives intact — and every "what are you up to?" it answers is a paid
    model call not spent. */
export default function Boardroom({ projectId, dash, org }: {
  projectId: string;
  dash: Dashboard;
  org: OrgView | null;
}) {
  const ceoName = org?.ceo?.name ?? "your CEO";
  const ceoFirst = ceoName.split(" ")[0];
  const ceoTitle = org?.ceo?.role_name ?? "Chief Executive";
  const [channelId, setChannelId] = useState<string | null>(null);
  // A screen that sends you here to ask about something it is showing hands
  // over the sentence, already quoting it, for you to edit or throw away.
  // Router state rather than a query parameter on purpose: the text is long,
  // and it must not survive a reload and re-fill a box you deliberately
  // cleared.
  const handedOver = (useLocation().state as { draft?: string } | null)?.draft;
  const [draft, setDraft] = useState(handedOver ?? "");
  const [busy, setBusy] = useState(false);
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
  const restoreRef = useRef<{ height: number; top: number } | null>(null);
  const wantBottomRef = useRef(false);

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
  const { data: typing } = usePoll(
    () =>
      channelId && dash.chat_live
        ? get<{ typing: boolean }>(`/observe/channels/${channelId}/typing`)
        : Promise.resolve({ typing: false }),
    2000,
    [channelId, dash.chat_live],
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
  // observed nothing at all.
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
  }, [channelId, hasSentinel]);
  const { data: runs } = usePoll(
    () => get<RunRow[]>(`/observe/projects/${projectId}/runs?limit=50`),
    8000,
  );
  const { data: approvals, reload: reloadApprovals } = usePoll(
    () => get<ApprovalRow[]>(`/observe/projects/${projectId}/approvals`),
    6000,
  );
  const { data: hypotheses } = usePoll(
    () => get<HypothesisRow[]>(`/observe/projects/${projectId}/hypotheses`),
    15000,
  );
  // The questions it is waiting on you for. The Desk used to send the chairman
  // here to "answer in the boardroom" and the boardroom did not show them.
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
  }, [lastId, typing?.typing]);

  // Tell the server how far the reader has got, so the sidebar's unread count
  // clears. The newest message they were present for, not "now": a reply that
  // lands between the render and this call is still news. Only while the tab
  // is in front of them — a page left open behind other windows reads nothing.
  const reloadFrame = useProjectFrame()?.ctx?.reload;
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

  async function send() {
    if (!draft.trim()) return;
    setBusy(true);
    try {
      await post(`/admin/projects/${projectId}/boardroom`, { body: draft.trim() });
      setDraft("");
      // You always follow your own message.
      pinnedRef.current = true;
      setPinned(true);
      reload();
    } finally {
      setBusy(false);
    }
  }

  // Day dividers, so a long scroll back reads as days rather than one wall.
  const rows = useMemo(() => {
    type Row =
      | { kind: "day"; key: string; label: string }
      | { kind: "msg"; key: string; body: string; at: string; mine: boolean;
          platform: boolean; runId: string | null };
    const out: Row[] = [];
    let day = "";
    for (const m of loaded) {
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
      out.push({ kind: "msg", key: m.id, body: m.body, at: m.at, mine: !!m.from_user,
        platform: !m.from_user && !m.from_agent, runId: run?.id ?? null });
    }
    return out;
  }, [loaded, runs]);

  // The CEO is the agent with no team, and these are the two kinds of run that
  // mean it is working on something a reply could come out of.
  //
  // Named rather than excluded. The old test was "any running run of the CEO's
  // that is not a success_check", and a live chat session is exactly that: a
  // resident run, no team, running for as long as the session is adopted —
  // days. So the banner said "the CEO is in a run now" permanently, on the one
  // page where presence is the whole point, and the rail badge read "in a run"
  // while the truth was "chat live — replies in seconds". A blocklist gets this
  // wrong again the next time a run kind is added; a list of what counts does
  // not.
  const ceoRun = (runs ?? []).find(
    (r) => r.status === "running" && !r.team_name
      && (r.kind === "agent_run" || r.kind === "inbox_run"),
  );
  // The wall the live session last hit. A refused task_create looks exactly
  // like a CEO that decided not to bother, and the difference only existed in
  // the run row nobody on this page was reading — which is how "add a task"
  // could be answered with "done" and leave the board unchanged.
  const chatSession = (runs ?? []).find(
    (r) => r.kind === "chat_session" && r.status === "running",
  );
  const lastRefusal = chatSession?.last_refusal ?? null;
  const refusedAt = chatSession?.last_refusal_at ?? null;
  // The CEO retries. A brief refused for length and refiled shorter left the
  // warning below sitting beside a true "done" — so once the same call has
  // gone through, say that instead of telling him to doubt it.
  const refusalCleared = chatSession?.last_refusal_cleared_at ?? null;
  const today = new Date().toDateString();
  const todayRuns = (runs ?? []).filter(
    (r) => r.started_at && new Date(r.started_at).toDateString() === today,
  );
  const todayUsd = todayRuns.reduce((sum, r) => sum + (r.cost_usd ?? 0), 0);
  const todayTokens = todayRuns.reduce((sum, r) => sum + r.tokens_out + r.tokens_in, 0);
  const pending = (approvals ?? []).filter((a) => a.status === "pending");
  const bets = (hypotheses ?? [])
    .filter((h) => h.status === "active")
    .sort((a, b) => a.kill_date.localeCompare(b.kill_date))
    .slice(0, 3);

  return (
    <>
      <div className="board-flex">
        <div className="board-chat">
          <div className="chat-id">
            <Avatar name={ceoName} team="leadership" size="lg" />
            <div className="ci">
              <b>{ceoName}</b>
              <span>
                {ceoTitle} — runs this project, and is the one person here you speak to
              </span>
            </div>
            {dash.chat_live ? (
              <span className="badge good">replies in seconds</span>
            ) : dash.runs.running > 0 ? (
              <span className="badge accent">working</span>
            ) : (
              <span className="badge">idle</span>
            )}
          </div>
          {/* With a live chat session the boardroom IS a chat: the typing dots
              below carry presence. The wait-explainer banners only matter in
              fallback mode, when replies ride the run queue. */}
          {!dash.chat_live && dash.runs.running > 0 ? (
            <div className="notice-box working">
              <span className="working-dot" aria-hidden="true" />
              {ceoFirst} is working on{" "}
              {dash.runs.running === 1 ? "something" : `${dash.runs.running} things`} right now. The
              reply lands here when they finish; the first one takes a few minutes.
            </div>
          ) : !dash.chat_live && dash.runs.queued > 0 ? (
            <div className="notice-box working">
              <span className="working-dot" aria-hidden="true" />
              {ceoFirst} is waking up — the reply starts in a moment.
            </div>
          ) : null}
          <div className="chat-wrap">
          <div className="chat" ref={chatRef} onScroll={onScroll}>
            {/* Older messages load themselves as the reader arrives at them —
                the sentinel below is what the observer watches. It is a real
                button so that a keyboard or screen reader gets the same
                affordance a scroll wheel does; with a mouse you never see it
                fire. */}
            {loaded.length > 0 && !atOldest && (
              <button
                ref={topRef}
                className="chat-earlier"
                onClick={() => loadOlderRef.current()}
                disabled={loadingOlder}
              >
                {loadingOlder ? "loading earlier messages…" : "earlier messages"}
              </button>
            )}
            {loaded.length > 0 && atOldest && (
              <div className="chat-start">the start of the conversation</div>
            )}
            {rows.map((row) =>
              row.kind === "day" ? (
                <div className="chat-day" key={row.key}>
                  <span>{row.label}</span>
                </div>
              ) : (
                <MessageBubble
                  key={row.key}
                  body={row.body}
                  at={row.at}
                  mine={row.mine}
                  platform={row.platform}
                  who={ceoName}
                  team="leadership"
                  runId={row.runId}
                  projectId={projectId}
                />
              ),
            )}
            {tail && loaded.length === 0 && (
              <p className="help">Nothing yet. Open with the goal, constraints, and how you want to work.</p>
            )}
            {typing?.typing && (
              <div className="msg typing" aria-label="The CEO is typing">
                <div className="who">CEO</div>
                <div className="body">
                  <span className="typing-dot" />
                  <span className="typing-dot" />
                  <span className="typing-dot" />
                </div>
              </div>
            )}
          </div>
          {/* Nothing arriving moves a reader who has scrolled up; this is how
              they get back, and how they find out something did arrive. It
              floats over the foot of the chat, not the composer below it. */}
          {!pinned && loaded.length > 0 && (
            <button className="chat-jump" onClick={jumpToLatest}>
              ↓ {unseen > 0 ? `${unseen} new` : "latest"}
            </button>
          )}
          </div>
          {/* Presence at the composer, where the "did it hear me?" doubt occurs. */}
          {ceoRun && !typing?.typing && (
            <div className="presence">
              <span className="working-dot" aria-hidden="true" />
              {ceoFirst} is working — a reply lands here when they are done.
            </div>
          )}
          {/* What a new chairman does not know he may ask. Each fills the box
              rather than sending — the words are a suggestion, not a script. */}
          {!draft && (
            <div className="quick" style={{ margin: "0 0 8px" }}>
              {SUGGESTIONS.map((q) => (
                <button key={q} className="qchip" onClick={() => setDraft(q)}>{q}</button>
              ))}
            </div>
          )}
          <div className="composer">
            <textarea
              value={draft}
              placeholder={`Tell ${ceoFirst} what you want changed…`}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={sendOnEnter(send, { disabled: busy || !draft.trim() })}
            />
            <button className="primary" onClick={send} disabled={busy || !draft.trim()}>
              Send
            </button>
          </div>
          <p className="help">{SEND_HINT}</p>
        </div>

        <div className="rail">
          <div className="card">
            <div className="h">{ceoFirst} today</div>
            <div style={{ fontSize: 13 }}>
              {ceoRun ? (
                <span className="badge accent">in a run</span>
              ) : dash.chat_live ? (
                <span className="badge good">chat live — replies in seconds</span>
              ) : (
                <span className="badge">idle</span>
              )}
              <div className="help" style={{ marginTop: 6 }}>
                spent today: {todayUsd > 0
                  ? `$${todayUsd.toLocaleString(undefined, { maximumFractionDigits: 2 })}`
                  : `${fmtUnit(todayTokens, "tokens")} tokens`}{" "}
                across {todayRuns.length} piece{todayRuns.length === 1 ? "" : "s"} of work
              </div>
              {lastRefusal && (
                <div className="help" style={{ marginTop: 8 }}>
                  <span className={`chip ${refusalCleared ? "" : "serious"}`}>
                    {refusalCleared ? "refused, then got through" : "last refused"}
                  </span>{" "}
                  {refusedAt && <span>{fmtWhen(refusedAt)}</span>}
                  <span style={{ display: "block", marginTop: 4 }}>
                    {clip(lastRefusal, 220)}
                  </span>
                  <span style={{ display: "block", marginTop: 4 }}>
                    {refusalCleared
                      ? `It made the same call again (${fmtWhen(refusalCleared)}) and that `
                        + "one went through, so this wall is behind it."
                      : "A rule stopped something it tried. If it told you the thing was "
                        + "done, check the board before believing it."}
                  </span>
                </div>
              )}
            </div>
          </div>

          {/* Why the project is quiet, in the room where that gets asked. It was
              only ever on the Overview tab, so the chairman's way to find out
              was to ask the CEO — which spends a run to get a guess. */}
          {dash.status === "running" && dash.activity.brief.banner && (
            <div className="card">
              <div className="h">Why it is quiet</div>
              <div style={{ fontSize: 13 }}>
                <span className={`chip ${dash.activity.brief.tone}`}>
                  {dash.activity.brief.chip}
                </span>
                <div style={{ marginTop: 6 }}>{dash.activity.brief.headline}</div>
                <div className="help" style={{ marginTop: 4 }}>
                  {dash.activity.brief.detail}
                </div>
              </div>
            </div>
          )}

          {openAsks.length > 0 && (
            <div className="card">
              <div className="h">{ceoFirst} is asking you · {openAsks.length}</div>
              {openAsks.slice(0, 3).map((a) => (
                <div key={a.id} style={{ marginBottom: 10, fontSize: 13 }}>
                  <div style={{ fontWeight: 600 }}>{a.title}</div>
                  <div className="help" style={{ marginTop: 3 }}>
                    If you say nothing, {fmtLeft(a.answer_by).replace(" left", "")} from now: {clip(a.default_action, 120)}
                  </div>
                  <button className="small" style={{ marginTop: 6 }}
                    onClick={() => setDraft(`Re: “${a.title}” — `)}>Answer</button>
                </div>
              ))}
              <div className="help" style={{ fontSize: 12 }}>Any message from you answers every question asked before it.</div>
            </div>
          )}

          {pending.length > 0 && (
            <div className="card">
              <div className="h">Needs a decision · {pending.length}</div>
              {pending.slice(0, 2).map((a) => (
                <div key={a.id} style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>
                    {a.title}
                    {a.amount_usd !== null && <span className="mono"> — ${a.amount_usd.toLocaleString()}</span>}
                  </div>
                  <div className={`clock ${clockClass(a.expires_at)}`} style={{ margin: "3px 0 6px" }}>
                    Denies itself in {fmtLeft(a.expires_at).replace(" left", "")}
                  </div>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    <DecideButtons approval={a} onDone={reloadApprovals} />
                  </div>
                </div>
              ))}
              <Link to="/inbox" style={{ fontSize: 12 }}>
                everything in your Inbox →
              </Link>
            </div>
          )}

          {bets.length > 0 && (
            <div className="card">
              <div className="h">The bets</div>
              {bets.map((h) => (
                <div key={h.id} style={{ marginBottom: 8, fontSize: 13 }}>
                  {clip(h.statement, 120)}
                  <div style={{ marginTop: 3 }}>{killChip(h.kill_date, h.status)}</div>
                </div>
              ))}
              <Link to={`/projects/${projectId}/details/tasks?view=list`} style={{ fontSize: 12 }}>
                all bets &amp; evidence →
              </Link>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
