import { useEffect, useRef, useState } from "react";
import { ChannelRow, get, MessageRow, post } from "../../api";
import { fmtWhen, usePoll } from "../../hooks";
import Markdown from "../../Markdown";

export default function Boardroom({ projectId }: { projectId: string }) {
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
    4000,
  );

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages?.length]);

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
        Your only speaking channel. The CEO wakes within seconds of a message here. Don't ask
        questions it's authorized to decide — give direction, and decide approvals.
      </p>
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
