import { useState } from "react";
import { NavLink } from "react-router-dom";
import { InboxReading, post } from "./api";
import { Btn } from "./ui/kit";

/** The agents' inbox, explained in words a chairman reads once and keeps.

    The banner this sits under used to say "the reading allowance is spent …
    stopped until 00:00 UTC" on a project whose allowance was zero — a setting,
    not a fault, and one that midnight does nothing about. What was missing was
    not a better sentence but the three facts underneath it: what an inbox is,
    how an agent comes to read it, and what zero buys and costs. */

/** Reading wakes are switched off when either ceiling is zero: a daily
    allowance with no hourly room can never be spent. */
export function readingOff(r: InboxReading): boolean {
  return r.cap_today === 0 || r.cap_this_hour === 0;
}

/** No wake may be bought just to read right now: reading is off, or today's
    or this hour's reads are used. Mail alone moves nobody until it clears. */
export function readingBlocked(r: InboxReading): boolean {
  return readingOff(r) || r.used_today >= r.cap_today || r.used_this_hour >= r.cap_this_hour;
}

/** One line: how much reading the project may do, and how much it has. */
export function readingLine(r: InboxReading): string {
  if (readingOff(r)) return "Inbox reads are off: agents read messages only when they wake up for work.";
  return `Inbox reads: ${r.used_today} of ${r.cap_today} used today, ` +
    `${r.used_this_hour} of ${r.cap_this_hour} this hour.`;
}

/** The working-hours tempo card's numbers. Not invented for this button: they
    are the smallest reading allowance any card ships, so turning reading on
    lands the project somewhere a chairman already chose once. */
const ON_PER_DAY = 12;
const ON_PER_HOUR = 4;

interface Step {
  label: string;
  confirm: string;
  changes: Record<string, number>;
}

/** The one change that unblocks reading right now, or null when nothing is
    blocking it. Each case moves only the ceiling that is actually binding:
    raising the daily number does nothing for a project held by the hourly one. */
export function nextInboxStep(r: InboxReading): Step | null {
  if (readingOff(r)) {
    const day = Math.max(r.cap_today, ON_PER_DAY);
    const hour = Math.max(r.cap_this_hour, ON_PER_HOUR);
    return {
      label: `Allow ${day} inbox reads a day`,
      confirm: `Agents will be able to wake up to ${day} times a day (${hour} an hour) just to ` +
        "read their messages. Each wake is a paid AI call.",
      changes: { inbox_runs_per_project_per_day: day, inbox_runs_per_project_per_hour: hour },
    };
  }
  if (r.used_today >= r.cap_today) {
    const day = r.cap_today * 2;
    return {
      label: `Raise to ${day} inbox reads a day`,
      confirm: `Agents will be able to wake up to ${day} times a day just to read their ` +
        "messages, starting now. Each wake is a paid AI call.",
      changes: { inbox_runs_per_project_per_day: day },
    };
  }
  if (r.used_this_hour >= r.cap_this_hour) {
    const hour = r.cap_this_hour * 2;
    return {
      label: `Raise to ${hour} inbox reads an hour`,
      confirm: `Agents will be able to wake up to ${hour} times an hour just to read their ` +
        "messages. Each wake is a paid AI call.",
      changes: { inbox_runs_per_project_per_hour: hour },
    };
  }
  return null;
}

/** The button, and the plain statement of what it costs before it lands. It
    is a constitution amendment like any other — recorded with a reason and
    disclosed to the CEO — so it says what is about to be different first,
    the same promise the Settings save makes. */
export function RaiseInboxReads({
  projectId, reading, onDone,
}: {
  projectId: string;
  reading: InboxReading;
  onDone: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const step = nextInboxStep(reading);

  if (done) return <span className="help">{done}</span>;
  if (!step) return null;

  if (!confirming) {
    return <Btn kind="primary" small icon="inbox" onClick={() => setConfirming(true)}>{step.label}</Btn>;
  }
  return (
    <div className="inbox-confirm">
      <span>{step.confirm}</span>
      <span className="inbox-confirm-actions">
        <Btn kind="primary" small busy={busy} onClick={async () => {
          setBusy(true);
          setError(null);
          try {
            await post(`/admin/projects/${projectId}/amendments`, {
              reason: "Let agents wake up to read their messages (from the inbox banner).",
              changes: step.changes,
            });
            setDone("Done — agents can wake up to read their messages now.");
            onDone();
          } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
          } finally {
            setBusy(false);
          }
        }}>
          Confirm
        </Btn>
        <Btn small onClick={() => setConfirming(false)}>Cancel</Btn>
      </span>
      {error && <span className="error">{error}</span>}
    </div>
  );
}

/** What the inbox is, in four short answers. Open by default nowhere: the
    banner carries the sentence, this carries the reasons for a reader who
    asks. */
export function InboxExplainer({ projectId, reading, onInbox }: {
  projectId: string;
  reading: InboxReading;
  /** Already on the agent inbox, so the link to it would go nowhere. */
  onInbox?: boolean;
}) {
  const off = readingOff(reading);
  return (
    <details className="inbox-help">
      <summary>What does this mean?</summary>
      <dl>
        <dt>What is the inbox?</dt>
        <dd>
          Agents send each other messages in their team rooms and in the leadership room. An
          agent's inbox is the messages it hasn't read yet.
        </dd>
        <dt>When do agents read their messages?</dt>
        <dd>
          Whenever they wake up. An agent wakes up for real work — a task, or the CEO's
          scheduled check-in — and reads its messages at the same time. If the project allows
          it, an agent can also wake up <i>just</i> to read. That is an <b>inbox read</b>.
        </dd>
        {off ? (
          <>
            <dt>What does 0 inbox reads per day mean?</dt>
            <dd>
              The project never wakes an agent just to read. Messages wait until the agent
              wakes up for real work, then it reads them all at once. This saves money, because
              every wake is a paid AI call. The catch: when nobody has work, messages can sit
              unread for a while.
            </dd>
            <dt>Should I change it?</dt>
            <dd>
              Leave it at 0 if the project is fine moving a few times a day. Allow some inbox
              reads if agents need to answer each other quickly — for example, when one is
              waiting on another to carry on.
            </dd>
          </>
        ) : (
          <>
            <dt>What happens when they're used up?</dt>
            <dd>
              This project allows {reading.cap_today} inbox reads a day and{" "}
              {reading.cap_this_hour} an hour. Once they're used, messages wait until the count
              resets — daily at 00:00 UTC, hourly as the hour rolls on — or until the agent wakes
              up for real work.
            </dd>
            <dt>Should I change it?</dt>
            <dd>
              Raise it if agents need to answer each other quickly. If they run out every day,
              it is also worth asking why the project talks so much instead of working.
            </dd>
          </>
        )}
      </dl>
      <p className="help">
        {!onInbox && (
          <>
            <NavLink to={`/projects/${projectId}/details/activity/inbox`}>See each agent's unread messages</NavLink>
            {" · "}
          </>
        )}
        <NavLink to={`/projects/${projectId}/details/settings?s=rules&t=${encodeURIComponent("Pace & brakes")}`}>
          Set an exact number in Settings
        </NavLink>
      </p>
    </details>
  );
}
