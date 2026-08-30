import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  ApprovalRow,
  ChannelRow,
  Dashboard,
  get,
  HypothesisRow,
  MessageRow,
  post,
  RunRow,
} from "../../api";
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

/** The chat stays sacred — the only speaking channel. The rail beside it is
    read-only context: what the CEO is doing, what needs a decision, and the
    bets on the table. It adds no new speaking channel, so the asymmetry
    survives intact — and every "what are you up to?" it answers is a paid
    model call not spent. */
export default function Boardroom({ projectId, dash }: { projectId: string; dash: Dashboard }) {
  const [channelId, setChannelId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    get<ChannelRow[]>(`/observe/projects/${projectId}/channels`).then((channels) => {
      const boardroom = channels.find((c) => c.kind === "boardroom");
      setChannelId(boardroom?.id ?? null);
    });
  }, [projectId]);

  const { data: messages, reload } = usePoll(
    () =>
      channelId
        ? get<MessageRow[]>(`/observe/channels/${channelId}/messages?limit=200`)
        : Promise.resolve([] as MessageRow[]),
    dash.chat_live ? 2000 : 4000,
  );
  const { data: typing } = usePoll(
    () =>
      channelId && dash.chat_live
        ? get<{ typing: boolean }>(`/observe/channels/${channelId}/typing`)
        : Promise.resolve({ typing: false }),
    2000,
  );
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

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages?.length, typing?.typing]);

  async function send() {
    if (!draft.trim()) return;
    setBusy(true);
    try {
      await post(`/admin/projects/${projectId}/boardroom`, { body: draft.trim() });
      setDraft("");
      reload();
    } finally {
      setBusy(false);
    }
  }

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
      <p className="subtitle">
        Your only speaking channel. Don't ask questions the CEO is authorized to decide —
        give direction, and decide approvals.
      </p>
      <div className="board-flex">
        <div className="board-chat">
          {/* With a live chat session the boardroom IS a chat: the typing dots
              below carry presence. The wait-explainer banners only matter in
              fallback mode, when replies ride the run queue. */}
          {!dash.chat_live && dash.runs.running > 0 ? (
            <div className="notice-box working">
              <span className="working-dot" aria-hidden="true" />
              The CEO is working — {dash.runs.running === 1 ? "a run is" : `${dash.runs.running} runs are`} in
              flight. Replies land here when its run finishes; first planning runs take a few minutes.
            </div>
          ) : !dash.chat_live && dash.runs.queued > 0 ? (
            <div className="notice-box working">
              <span className="working-dot" aria-hidden="true" />
              The CEO is waking up — a run is queued to start.
            </div>
          ) : null}
          <div className="chat">
            {(messages ?? []).map((message) => (
              <div key={message.id} className={"msg" + (message.from_user ? " me" : "")}>
                <div className="who">{message.from_user ? "You (chairman)" : "CEO"}</div>
                <div className="body">
                  <Markdown>{message.body}</Markdown>
                </div>
                <div className="when">{fmtWhen(message.at)}</div>
              </div>
            ))}
            {messages && messages.length === 0 && (
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
            <div ref={endRef} />
          </div>
          {/* Presence at the composer, where the "did it hear me?" doubt occurs. */}
          {ceoRun && !typing?.typing && (
            <div className="presence">
              <span className="working-dot" aria-hidden="true" />
              The CEO is in a run now — a reply lands here when it finishes.
            </div>
          )}
          <div className="composer">
            <textarea
              value={draft}
              placeholder="Speak as chairman…"
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
            <div className="h">The CEO right now</div>
            <div style={{ fontSize: 13 }}>
              {ceoRun ? (
                <span className="badge accent">in a run</span>
              ) : dash.chat_live ? (
                <span className="badge good">chat live — replies in seconds</span>
              ) : (
                <span className="badge">idle</span>
              )}
              <div className="help" style={{ marginTop: 6 }}>
                today: {todayUsd > 0
                  ? `$${todayUsd.toLocaleString(undefined, { maximumFractionDigits: 2 })}`
                  : `${fmtUnit(todayTokens, "tokens")} tokens`}{" "}
                across {todayRuns.length} run{todayRuns.length === 1 ? "" : "s"}
              </div>
              {lastRefusal && (
                <div className="help" style={{ marginTop: 8 }}>
                  <span className="chip serious">last refused</span>{" "}
                  <span style={{ display: "block", marginTop: 4 }}>
                    {clip(lastRefusal, 220)}
                  </span>
                  <span style={{ display: "block", marginTop: 4, opacity: 0.75 }}>
                    A rule stopped something it tried. If it told you the thing was
                    done, check the board before believing it.
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
                    {fmtLeft(a.expires_at).replace(" left", "")} → auto-deny
                  </div>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    <DecideButtons approvalId={a.id} onDone={reloadApprovals} />
                  </div>
                </div>
              ))}
              <Link to={`/projects/${projectId}/approvals`} style={{ fontSize: 12 }}>
                all approvals →
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
              <Link to={`/projects/${projectId}/evidence`} style={{ fontSize: 12 }}>
                all bets &amp; evidence →
              </Link>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
