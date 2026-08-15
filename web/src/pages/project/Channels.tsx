import { useState } from "react";
import { ChannelRow, get, MessageRow, post } from "../../api";
import { fmtWhen, usePoll } from "../../hooks";
import Markdown from "../../Markdown";

export default function Channels({ projectId }: { projectId: string }) {
  const { data: channels } = usePoll(
    () => get<ChannelRow[]>(`/observe/projects/${projectId}/channels`),
    30000,
  );
  const [selected, setSelected] = useState<string | null>(null);
  const channelId = selected ?? channels?.[0]?.id ?? null;

  const { data: messages, reload } = usePoll(
    () =>
      channelId
        ? get<MessageRow[]>(`/observe/channels/${channelId}/messages?limit=200`)
        : Promise.resolve([] as MessageRow[]),
    5000,
  );

  async function unfreeze(threadId: string) {
    await post(`/admin/threads/${threadId}/unfreeze`);
    reload();
  }

  const label = (channel: ChannelRow) =>
    channel.kind === "team" ? `team ${channel.team_id?.slice(-6)}` : channel.kind;

  return (
    <>
      <p className="subtitle">
        You read everything; you speak only in the boardroom. That asymmetry is the design.
      </p>
      <div className="pill-row">
        {(channels ?? []).map((channel) => (
          <button
            key={channel.id}
            className={"small" + (channel.id === channelId ? " primary" : "")}
            onClick={() => setSelected(channel.id)}
          >
            {label(channel)}
          </button>
        ))}
      </div>
      <table>
        <thead>
          <tr>
            <th>When</th>
            <th>From</th>
            <th style={{ width: "60%" }}>Message</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {(messages ?? []).map((message) => (
            <tr key={message.id}>
              <td className="mono">{fmtWhen(message.at)}</td>
              <td className="mono">{message.from_user ? "chairman" : message.from_agent?.slice(-6)}</td>
              <td>
                <Markdown>{message.body}</Markdown>
              </td>
              <td>
                {message.frozen && (
                  <>
                    <span className="badge critical">❄ frozen</span>{" "}
                    <button className="small" onClick={() => unfreeze(message.id)}>
                      unfreeze
                    </button>
                  </>
                )}
              </td>
            </tr>
          ))}
          {messages && messages.length === 0 && (
            <tr>
              <td colSpan={4} className="help">
                No messages in this channel yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </>
  );
}
