import { useState } from "react";
import { get, InboxView } from "../../api";
import { fmtWhen, usePoll } from "../../hooks";
import { InboxExplainer, RaiseInboxReads, readingLine } from "../../inbox";
import Markdown from "../../Markdown";
import type { ProjectCtx } from "../../projectCtx";
import { Skeleton } from "../../ui";
import { Avatar, Badge, EmptyState } from "../../ui/kit";

/** Enough to see what an agent is sitting on; the rest is one click away. */
const FIRST = 5;

type Seat = InboxView["agents"][number];

/** One agent's unread messages, newest first. */
function AgentMail({ seat, onOpen }: { seat: Seat; onOpen: () => void }) {
  const [all, setAll] = useState(false);
  const shown = all ? seat.messages : seat.messages.slice(0, FIRST);
  const hidden = seat.messages.length - shown.length;
  // The server sends the newest fifty; the count above them stays exact.
  const beyond = seat.unread - seat.messages.length;
  return (
    <div className="card inbox-agent">
      <div className="inbox-who">
        <Avatar name={seat.name} team={seat.team_name ?? "leadership"} />
        <button className="inbox-name" onClick={onOpen} title="Open this agent">
          {seat.name}
        </button>
        <span className="help">{seat.kind === "ceo" ? "CEO" : seat.team_name}</span>
        <span className="grow" />
        <Badge tone="accent">{seat.unread} unread</Badge>
      </div>
      <ul className="inbox-list">
        {shown.map((m) => (
          <li key={m.id}>
            <div className="inbox-meta">
              <b>{m.from_name ?? "someone"}</b> in {m.channel} · {fmtWhen(m.at)}
            </div>
            <div className="inbox-body"><Markdown>{m.body}</Markdown></div>
          </li>
        ))}
      </ul>
      {hidden > 0 && (
        <button className="small" onClick={() => setAll(true)}>Show {hidden} more</button>
      )}
      {all && beyond > 0 && (
        <p className="help">
          {beyond} older unread message{beyond === 1 ? " isn't" : "s aren't"} listed here. They
          are in Activity → Messages.
        </p>
      )}
    </div>
  );
}

/** Every agent's unread messages — the number the "inbox" banner quotes, opened
    up so it can be checked. Activity → Messages already showed every channel;
    what it could not say is who has read what, which is the only question the
    banner raises. A message in a shared room shows under each agent who has
    not read it yet, because each of them still has to. */
export default function AgentInbox({ ctx }: { ctx: ProjectCtx }) {
  const { projectId, dash } = ctx;
  const { data, error, reload } = usePoll(
    () => get<InboxView>(`/observe/projects/${projectId}/inbox`), 15000, [projectId]);

  if (!data) return error ? <div className="error">{error}</div> : <Skeleton rows={4} />;
  const holding = data.agents.filter((a) => a.unread > 0);
  const clear = data.agents.filter((a) => a.unread === 0);

  return (
    <>
      <div className="card inbox-summary">
        <div className="inbox-who">
          <b>
            {data.unread} unread message{data.unread === 1 ? "" : "s"}
            {holding.length > 0 && ` across ${holding.length} agent${holding.length === 1 ? "" : "s"}`}
          </b>
          <span className="grow" />
          {dash.can_steer && (
            <RaiseInboxReads projectId={projectId} reading={data.reading}
              onDone={() => { reload(); ctx.reload(); }} />
          )}
        </div>
        <p className="help" style={{ margin: "4px 0 0" }}>
          {readingLine(data.reading)} A message sent to a room counts once for each agent who
          hasn't read it yet.
        </p>
        <InboxExplainer projectId={projectId} reading={data.reading} onInbox />
      </div>

      {error && <div className="error">Not refreshing — {error}.</div>}

      {holding.length === 0 ? (
        <EmptyState good title="Every agent has read all of its messages." />
      ) : (
        holding.map((seat) => (
          <AgentMail key={seat.id} seat={seat} onOpen={() => ctx.openAgent(seat.id)} />
        ))
      )}

      {clear.length > 0 && holding.length > 0 && (
        <p className="help">Nothing unread: {clear.map((a) => a.name).join(", ")}.</p>
      )}
    </>
  );
}
