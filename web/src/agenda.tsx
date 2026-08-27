import { useState } from "react";
import { Link } from "react-router-dom";
import { AgendaEvent, AgendaRhythm, AgendaView, get } from "./api";
import { usePoll } from "./hooks";
import { Skeleton, fmtSoon } from "./ui";

/** The future tense.

    The desk asks which clock runs out soonest, which is the right question
    when something is wrong. Nothing answered the flatter one a chairman asks
    about a thing that runs while he sleeps: what is it going to *do* this
    week? An autonomous system that will not show you its schedule is a haunted
    one.

    Two shapes on one axis, because two things are genuinely different:

    **Rhythms** recur — a 09:00 wake is one fact with fourteen occurrences, and
    drawing each as an event would bury the four dated things that matter. They
    are the faint ticks along the bottom of a day, plus one legend line each.

    **Events** happen once and mostly have a consequence attached. They are the
    dots, coloured by what KIND of date they are, and they are listed in full
    below the strip — a dot nobody can read is decoration.

    The strip never invents a date. Everything comes from the server, computed
    in the constitution's one timezone from the same heartbeats the dispatcher
    reads, so a wake drawn here is a wake that is actually going to run. */

const TONE_LABEL: Record<AgendaEvent["tone"], string> = {
  consequence: "resolves itself",
  deadline: "comes due",
  meeting: "wants you",
  projection: "the platform's arithmetic",
  flat: "happens",
};

const WINDOWS = [7, 14, 30] as const;

/** Short, not narrow. Narrow collapses Saturday and Sunday to one letter and
    Tuesday and Thursday to another, which makes the one thing a chairman
    actually reads off a calendar — is that the weekend? — a guess. */
function weekday(day: string): string {
  return new Date(`${day}T12:00:00Z`).toLocaleDateString(undefined, {
    weekday: "short",
    timeZone: "UTC",
  });
}

function dayNumber(day: string): string {
  return String(Number(day.slice(8, 10)));
}

function monthOf(day: string): string {
  return new Date(`${day}T12:00:00Z`).toLocaleDateString(undefined, {
    month: "short",
    timeZone: "UTC",
  });
}

function dayLabel(day: string): string {
  return new Date(`${day}T12:00:00Z`).toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

/** One column. The day is the unit because that is the resolution a chairman
    plans at — the exact minute lives on the row below, where it can be read. */
function Day({
  day,
  index,
  events,
  rhythms,
}: {
  day: string;
  index: number;
  events: AgendaEvent[];
  rhythms: AgendaRhythm[];
}) {
  const title = [
    dayLabel(day),
    ...events.map((e) => `• ${e.label}`),
    ...rhythms.map((r) => `· ${r.label}`),
  ].join("\n");
  return (
    <div className={`aday${index === 0 ? " today" : ""}`} title={title}>
      <div className="adnum">{dayNumber(day)}</div>
      <div className="adwk">{index === 0 ? "today" : weekday(day)}</div>
      <div className="addots">
        {events.map((event, i) => (
          <span key={`${event.kind}-${i}`} className={`adot ${event.tone}`} />
        ))}
      </div>
      {/* One mark for "somebody is awake this day", however many wakes it
          holds. The rhythms are named in full below; this is only the shadow
          they cast on the calendar. */}
      <div className="adtick">{rhythms.length > 0 ? <i /> : null}</div>
    </div>
  );
}

export default function Agenda({ projectId }: { projectId: string }) {
  const [days, setDays] = useState<number>(14);
  const { data, error } = usePoll(
    () => get<AgendaView>(`/observe/projects/${projectId}/agenda?days=${days}`),
    60000,
  );

  if (!data) {
    return error ? (
      <div className="card">Not refreshing — {error}.</div>
    ) : (
      <Skeleton rows={2} />
    );
  }

  const eventsByDay = new Map<string, AgendaEvent[]>();
  for (const event of data.events) {
    eventsByDay.set(event.day, [...(eventsByDay.get(event.day) ?? []), event]);
  }
  const rhythmsByDay = new Map<string, AgendaRhythm[]>();
  for (const rhythm of data.rhythms) {
    for (const day of rhythm.days) {
      rhythmsByDay.set(day, [...(rhythmsByDay.get(day) ?? []), rhythm]);
    }
  }
  // Month boundaries are the only thing a bare row of day numbers cannot say.
  const months: { label: string; span: number }[] = [];
  for (const day of data.day_list) {
    const label = monthOf(day);
    const last = months.at(-1);
    if (last && last.label === label) last.span += 1;
    else months.push({ label, span: 1 });
  }

  return (
    <div className="agenda">
      <div className="agenda-head">
        <span className="dim">
          {data.timezone}
          {data.live && data.events.length === 0 && data.rhythms.length === 0 && (
            <> · nothing scheduled in this window</>
          )}
        </span>
        <div className="spacer" />
        <div className="viewtog">
          {WINDOWS.map((n) => (
            <button
              key={n}
              className={days === n ? "on" : ""}
              onClick={() => setDays(n)}
            >
              {n}d
            </button>
          ))}
        </div>
      </div>

      {data.live && (
        <>
          <div className="amonths">
            {months.map((month, i) => (
              <span key={`${month.label}-${i}`} style={{ flex: month.span }}>
                {month.label}
              </span>
            ))}
          </div>
          <div className="astrip">
            {data.day_list.map((day, i) => (
              <Day
                key={day}
                day={day}
                index={i}
                events={eventsByDay.get(day) ?? []}
                rhythms={rhythmsByDay.get(day) ?? []}
              />
            ))}
          </div>
        </>
      )}

      {data.rhythms.length > 0 && (
        <div className="arhythms">
          {data.rhythms.map((rhythm) => (
            <div className="arh" key={`${rhythm.kind}-${rhythm.label}`}>
              {/* The same mark the strip paints, so the legend and the
                  calendar are visibly the same thing. */}
              <span className="atick" aria-hidden="true">
                <i />
              </span>
              <b>{rhythm.label}</b>
              <span className="dim">{rhythm.every}</span>
              {rhythm.next && <span className="tick">next {fmtSoon(rhythm.next)}</span>}
              {rhythm.detail && <span className="dim adet">— {rhythm.detail}</span>}
            </div>
          ))}
        </div>
      )}

      {data.events.length > 0 && (
        <div className="wbox aevents">
          {data.events.map((event, i) => (
            <div className="wrow" key={`${event.kind}-${i}`}>
              <span className={`adot ${event.tone}`} aria-hidden="true" />
              <span className="aday-lbl mono">{dayLabel(event.day)}</span>
              <div className="grow">
                <div className="tt">{event.label}</div>
                {event.detail && <div className="sb">{event.detail}</div>}
              </div>
              <span className={`atone ${event.tone}`}>{TONE_LABEL[event.tone]}</span>
              {event.where && (
                <Link to={`/projects/${projectId}/${event.where}`}>look →</Link>
              )}
            </div>
          ))}
        </div>
      )}

      {/* A blank column where a chairman expects his weekly meeting is worse
          than a sentence saying why it is blank. */}
      {data.notes.map((note, i) => (
        <p className="help anote" key={i}>
          {note}
        </p>
      ))}
    </div>
  );
}
