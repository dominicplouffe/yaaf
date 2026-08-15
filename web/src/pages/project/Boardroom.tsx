import { useEffect, useRef, useState } from "react";
import { ChannelRow, Dashboard, get, MessageRow, post } from "../../api";
import { fmtWhen, usePoll } from "../../hooks";
import Markdown from "../../Markdown";

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

  return (
    <>
      <p className="subtitle">
        Your only speaking channel. Don't ask questions the CEO is authorized to decide —
        give direction, and decide approvals.
      </p>
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
      <div className="composer">
        <textarea
          value={draft}
          placeholder="Speak as chairman…"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send();
          }}
        />
        <button className="primary" onClick={send} disabled={busy || !draft.trim()}>
          Send
        </button>
      </div>
      <p className="help">Ctrl/⌘+Enter to send.</p>
    </>
  );
}
