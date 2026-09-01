import { useState } from "react";
import { Link } from "react-router-dom";
import { Briefing, BriefingLine, DeskBriefing, get, post } from "./api";
import { fmtWhen, usePoll } from "./hooks";
import { fmtAgo, fmtSoon } from "./ui";

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
    answers it better. This panel carries the count and hands off.

    And one thing it must do, which it did not: **lead with a verdict.** The
    panel used to open with a concatenation of counts and then print thirteen
    bullets in the order the server's builders happened to run, so a healthy
    project and a stalling one looked identical and the alarming line sat ninth.
    The desk now draws the summary only — verdict, what moved, what it cost, the
    two or three exceptions — and the operating detail lives on the project's own
    page and in the daily mail, where the reader has asked for it. */

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

/** One clause, composed server-side. The share of the cap used to be arithmetic
    done here, which meant the panel and the email could differ about the same
    month; and `73,738,051` used to be printed in full, which is nine glyphs the
    eye slides off. Both are now the server's problem, once. */
function Money({ brief }: { brief: Briefing }) {
  return <div className="bmoney">{brief.money.text}</div>;
}

/** The question a chairman asks first, and the one this panel never answered.

    The chip colour is the app's ordinary clock vocabulary, and it is the ONLY
    coloured element in a briefing block — see the rule above `.bpanel` in
    styles.css. Nothing here is a judgement the panel formed: `label` and
    `reason` are the platform's own status, rendered. */
function Verdict({ brief }: { brief: Briefing }) {
  const { verdict } = brief;
  const cls = verdict.severity === "flat" ? "" : ` ${verdict.severity}`;
  return (
    <div className="bverdict">
      <span className={`chip${cls}`}>{verdict.label}</span>
      <span className="bvr">{verdict.reason}</span>
    </div>
  );
}

/** How far along, with its denominator. */
function Moved({ brief }: { brief: Briefing }) {
  return <div className="bmoved">{brief.movement.text}</div>;
}

/** The exceptions, in the order the server gave them and never re-sorted: the
    desk, the mail and the MCP view must lead with the same line. */
function Exceptions({ brief, to }: { brief: Briefing; to: string }) {
  if (brief.exceptions.length === 0) return null;
  return (
    <ul className="blines">
      {brief.exceptions.map((exc) => (
        <li key={exc.kind} className="bl bad">
          <span className="bm" aria-hidden="true">!</span>
          <span className="bt">
            {exc.text}
            {exc.cause && <span className="dim"> — {exc.cause}</span>}
          </span>
          {exc.where && (
            <Link className="bgo" to={`${to}/${exc.where}`}>look →</Link>
          )}
        </li>
      ))}
    </ul>
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

/** When this project moves next.

    Sits below the waiting line on purpose: what needs you now outranks what
    happens later. But a quiet window with nothing here at all is the exact
    state that produced "why are we idle?" — so the absence gets a sentence
    rather than a blank. */
function NextUp({ brief }: { brief: Briefing }) {
  const n = brief.next_up;
  if (!n) return null;
  return (
    <div className="bnext">
      <span className="lbl">Next</span>
      {n.at ? (
        <>
          {n.label} <span className="tick">{fmtSoon(n.at)}</span>
        </>
      ) : (
        <span className="tick">{n.why}</span>
      )}
    </div>
  );
}

/** One project's briefing. `onSeen` is absent on a read-only rendering.

    Two flags rather than two components, deliberately. The head carries the
    mark-read button, and that button stamps the edge the panel DREW rather than
    the moment of the click — a subtle, load-bearing rule with a test on it.
    Forking this into a summary component and a detail component is how that
    rule gets copied once and then copied wrong. */
export function BriefingBlock({
  brief,
  onSeen,
  showName = true,
  summary = true,
  detail = true,
}: {
  brief: Briefing;
  onSeen?: (brief: Briefing) => void;
  showName?: boolean;
  /** The board's four questions. What the desk draws. */
  summary?: boolean;
  /** The operating record. What the project's own page draws. */
  detail?: boolean;
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
      {summary && (
        <>
          <Verdict brief={brief} />
          <Moved brief={brief} />
          <Exceptions brief={brief} to={to} />
        </>
      )}
      {detail && (
        <>
          {/* The headline restates the verdict, so it only earns its place on
              the page that is NOT showing one. */}
          {!summary && <div className="bline">{brief.headline}</div>}
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
        </>
      )}
      <Money brief={brief} />
      <Waiting brief={brief} />
      <NextUp brief={brief} />
    </div>
  );
}

/** The portfolio in one line. This used to read "5 projects moved", which is a
    count of activity and tells a chairman nothing about whether any of them is
    in trouble. Worst first, same order as the blocks below it. */
function census(live: Briefing[]): string {
  const counts = new Map<string, number>();
  for (const b of live) {
    counts.set(b.verdict.label, (counts.get(b.verdict.label) ?? 0) + 1);
  }
  return [...counts].map(([label, n]) => `${n} ${label}`).join(" · ");
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
  // `hushable`, not `quiet`. A stall that began before the window logs nothing
  // inside it, so a stalling project is `quiet === true` — and collapsing it to
  // a one-liner is the exact failure this panel was rebuilt to stop.
  const news = live.filter((b) => !b.hushable);
  const silent = live.filter((b) => b.hushable);

  // Every project quiet is not nothing to say — it is the proof-of-life
  // sentence — but it does not deserve the top of the desk, so it collapses to
  // one line above the rows that do have consequences attached.
  if (news.length === 0) {
    return (
      <div className="bpanel hushed">
        <div className="bpanel-head">
          <span className="bttl">Since you were last here</span>
          <span className="dim">{census(live)}</span>
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
                <span className="dim">{b.verdict.reason}</span>
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
        <span className="dim">{census(live)}</span>
        <div className="spacer" />
        {/* What the panel drew, not what the endpoint sent: marking a paused
            project read would consume a window the chairman was never shown. */}
        <button className="linkish" disabled={busy} onClick={() => markRead(live)}>
          {busy ? "marking…" : "Mark all read"}
        </button>
      </div>
      {/* Summary only, and in the order the server sent them — which is now
          worst verdict first, rather than by the age of the project. */}
      {news.map((brief) => (
        <BriefingBlock
          key={brief.project_id}
          brief={brief}
          detail={false}
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
              <span className="dim">{b.verdict.reason}</span>
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
      summary={false}
      onSeen={async (brief) => {
        await post(`/admin/projects/${projectId}/briefing/seen`, { until: brief.until });
        reload();
      }}
    />
  );
}
