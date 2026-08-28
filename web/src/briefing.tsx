import { useState } from "react";
import { Link } from "react-router-dom";
import { Briefing, BriefingLine, DeskBriefing, get, post } from "./api";
import { fmtWhen, usePoll } from "./hooks";
import { fmtAgo, fmtSoon, fmtUnit } from "./ui";

/** The past tense.

    Every other screen here is in the present: the desk says what needs you
    now, the overview says what is true now. A chairman coming back after three
    days had to read four tables in Records and reconstruct the weekend by
    hand, and the only mail he ever got was an alarm.

    Three things this panel must not do:

    **Move his mark by being looked at.** The desk polls. A window that reset
    on every fetch would always be empty, and "since you were last here" would
    not survive a tab left open overnight — which is the exact situation it
    exists for. So the mark moves on an explicit press, and the press stamps
    the edge the panel actually DREW (`until`), not the instant of the click:
    anything that lands while it is on screen must survive to the next read.

    **Congratulate an empty window.** Nothing wrong and nothing happening are
    different screens — the desk already refuses to conflate them — so a quiet
    project still ships its pulse.

    **Outrank the desk.** What is waiting NOW is the desk's question and it
    answers it better. This panel carries the count and hands off. */

const MARK: Record<BriefingLine["tone"], string> = {
  good: "+",
  bad: "!",
  flat: "·",
};

function windowLabel(brief: Briefing): string {
  const hours = brief.window_hours;
  if (hours < 1) return "the last few minutes";
  if (hours < 48) return `${Math.round(hours)}h`;
  return `${Math.round(hours / 24)} days`;
}

function Money({ brief }: { brief: Briefing }) {
  const { money } = brief;
  const share = money.cap > 0 ? money.month_to_date / money.cap : 0;
  return (
    <div className="bmoney">
      <b>{fmtUnit(money.window, money.unit)}</b> this window ·{" "}
      {fmtUnit(money.month_to_date, money.unit)} of {fmtUnit(money.cap, money.unit)} this
      month
      {money.cap > 0 && <span className="dim"> ({Math.round(share * 100)}%)</span>}
    </div>
  );
}

/** What an empty window still owes the reader. Three facts, and they are the
    three that tell a quiet week from a dead one. */
function Pulse({ brief }: { brief: Briefing }) {
  const { pulse } = brief;
  return (
    <div className="bpulse">
      <span>
        <b className="mono">{pulse.open_tasks}</b> open
      </span>
      <span>
        <b className="mono">{pulse.running}</b> running
      </span>
      <span>
        last run <b className="mono">{fmtAgo(pulse.last_run_at)}</b>
      </span>
    </div>
  );
}

function Waiting({ brief }: { brief: Briefing }) {
  const w = brief.waiting;
  if (!w.total) return null;
  const bits: string[] = [];
  if (w.approvals) bits.push(`${w.approvals} approval${w.approvals === 1 ? "" : "s"}`);
  if (w.plan) bits.push(`${w.plan} proposed requirement${w.plan === 1 ? "" : "s"}`);
  if (w.verdicts) bits.push(`${w.verdicts} observable close${w.verdicts === 1 ? "" : "s"}`);
  if (w.commitment) bits.push("a delivery date");
  if (w.flags_raised) bits.push(`${w.flags_raised} raised flag${w.flags_raised === 1 ? "" : "s"}`);
  if (w.open_reviews) bits.push(`${w.open_reviews} open review${w.open_reviews === 1 ? "" : "s"}`);
  return (
    <div className="bwait">
      <span className="lbl">And now waiting on you</span>
      {bits.join(" · ")}
      {w.soonest_expiry && (
        <span className="tick"> — the soonest auto-decides {fmtSoon(w.soonest_expiry)}</span>
      )}
    </div>
  );
}

/** One project's briefing. `onSeen` is absent on a read-only rendering. */
export function BriefingBlock({
  brief,
  onSeen,
  showName = true,
}: {
  brief: Briefing;
  onSeen?: (brief: Briefing) => void;
  showName?: boolean;
}) {
  const to = `/projects/${brief.project_id}`;
  // `bquiet`, not `quiet`: the desk's empty-state card owns `.quiet`, and a
  // briefing block wearing it inherited a 30px centred card — which centred the
  // headline and the money line while every flex row inside them stayed left. A
  // quiet window should read dimmer, not differently shaped.
  return (
    <div className={`bblock${brief.quiet ? " bquiet" : ""}`}>
      <div className="bhead">
        {showName && (
          <Link className="co" to={to}>
            {brief.name}
          </Link>
        )}
        <span className="bwin">
          {brief.window_source} · {windowLabel(brief)}
        </span>
        <span className="dim mono bedge">
          {fmtWhen(brief.since)} → {fmtWhen(brief.until)}
        </span>
        <div className="spacer" />
        {onSeen && (
          <button className="linkish" onClick={() => onSeen(brief)}>
            Mark read
          </button>
        )}
      </div>
      <div className="bline">{brief.headline}</div>
      {brief.lines.length > 0 ? (
        <ul className="blines">
          {brief.lines.map((line, i) => (
            <li key={`${line.kind}-${i}`} className={`bl ${line.tone}`}>
              <span className="bm" aria-hidden="true">
                {MARK[line.tone]}
              </span>
              <span className="bt">{line.text}</span>
              {line.where && (
                <Link className="bgo" to={`${to}/${line.where}`}>
                  look →
                </Link>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <Pulse brief={brief} />
      )}
      <Money brief={brief} />
      <Waiting brief={brief} />
    </div>
  );
}

/** The Desk's panel: every running project on its own mark.

    Running only, like the rest of the desk. The endpoint returns every
    non-terminal project because the window and the mark are per project and a
    caller may want all of them; the desk is the working set, and a paused
    project's "nothing settled" line is a fact about a project nobody is
    working on.

    Per-project marks rather than one box-wide stamp, because reading one
    project's brief says nothing about having read another's — and a single
    mark would be moved by opening the first and would swallow the three you
    never looked at. */
export function DeskBriefingPanel() {
  const { data, reload } = usePoll(
    () => get<DeskBriefing>("/observe/briefing"),
    30000,
  );
  const [open, setOpen] = useState(true);
  const [busy, setBusy] = useState(false);

  async function markRead(briefs: Briefing[]) {
    setBusy(true);
    try {
      // The window the panel DREW, not the moment of the click: anything that
      // landed while it sat on screen has to survive to the next read.
      await Promise.all(
        briefs.map((b) =>
          post(`/admin/projects/${b.project_id}/briefing/seen`, { until: b.until }),
        ),
      );
      reload();
    } finally {
      setBusy(false);
    }
  }

  if (!data) return null;
  const live = data.projects.filter((b) => b.status === "running");
  if (live.length === 0) return null;
  const news = live.filter((b) => !b.quiet);
  const silent = live.filter((b) => b.quiet);

  // Every project quiet is not nothing to say — it is the proof-of-life
  // sentence — but it does not deserve the top of the desk, so it collapses to
  // one line above the rows that do have consequences attached.
  if (news.length === 0) {
    return (
      <div className="bpanel hushed">
        <div className="bpanel-head">
          <span className="bttl">Since you were last here</span>
          <span className="dim">
            nothing settled in any of your {live.length} running project
            {live.length === 1 ? "" : "s"}
          </span>
          <div className="spacer" />
          <button className="linkish" onClick={() => setOpen(!open)}>
            {open ? "hide" : "show each"}
          </button>
        </div>
        {open && (
          <div className="bhushed">
            {live.map((b) => (
              <div key={b.project_id} className="bhrow">
                <Link className="co" to={`/projects/${b.project_id}`}>
                  {b.name}
                </Link>
                <span className="dim">{b.headline}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="bpanel">
      <div className="bpanel-head">
        <span className="bttl">Since you were last here</span>
        <span className="dim">
          {news.length} project{news.length === 1 ? "" : "s"} moved
          {silent.length > 0 && `, ${silent.length} quiet`}
        </span>
        <div className="spacer" />
        {/* What the panel drew, not what the endpoint sent: marking a paused
            project read would consume a window the chairman was never shown. */}
        <button className="linkish" disabled={busy} onClick={() => markRead(live)}>
          {busy ? "marking…" : "Mark all read"}
        </button>
      </div>
      {news.map((brief) => (
        <BriefingBlock
          key={brief.project_id}
          brief={brief}
          onSeen={(b) => markRead([b])}
        />
      ))}
      {silent.length > 0 && (
        <div className="bhushed">
          {silent.map((b) => (
            <div key={b.project_id} className="bhrow">
              <Link className="co" to={`/projects/${b.project_id}`}>
                {b.name}
              </Link>
              <span className="dim">{b.headline}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** The same panel inside one project, where the project's name is the page. */
export function ProjectBriefing({ projectId }: { projectId: string }) {
  const { data, reload } = usePoll(
    () => get<Briefing>(`/observe/projects/${projectId}/briefing`),
    30000,
  );
  if (!data) return null;
  return (
    <BriefingBlock
      brief={data}
      showName={false}
      onSeen={async (brief) => {
        await post(`/admin/projects/${projectId}/briefing/seen`, { until: brief.until });
        reload();
      }}
    />
  );
}
