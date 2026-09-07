import { useState } from "react";
import { Link } from "react-router-dom";
import { Briefing, BriefingLine, get, post } from "./api";
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

/** The three questions, in the order a chairman asks them. The names come from
    the composer; these are what he reads. */
const HEADINGS: Record<BriefingLine["group"], string> = {
  attention: "Worth your eye",
  moved: "What moved",
  delegated: "The CEO handled this itself",
};

/** One group of lines under its heading.

    The panel used to print every line at one weight in the order the composer
    queried them, so a stall sat between "20 deliverables published" and "the
    CEO exercised 26 decisions" and read like a third tally. Thirteen equal
    sentences is not a briefing, it is a paragraph — the reader ends up doing
    the triage the panel exists to do.

    The delegated group folds away, because the autonomy dial already says the
    chairman does not want it. Five rules, each reading `chairman` or `ceo`;
    every line names the rule that governs it, and one still reading `ceo` is
    an authority he handed over on purpose. Printing it back at him is arguing
    with the setting. */
function Group({
  group,
  lines,
  to,
}: {
  group: BriefingLine["group"];
  lines: BriefingLine[];
  to: string;
}) {
  const foldable = group === "delegated";
  const [open, setOpen] = useState(!foldable);
  if (lines.length === 0) return null;
  return (
    <div className={`bgroup bg-${group}`}>
      <div className="bghead">
        <span className="bglbl">{HEADINGS[group]}</span>
        {foldable && (
          <button className="linkish bgfold" onClick={() => setOpen(!open)}>
            {open ? "hide" : `${lines.length} thing${lines.length === 1 ? "" : "s"}`}
          </button>
        )}
      </div>
      {open && (
        <ul className="blines">
          {lines.map((line, i) => (
            <li key={`${line.kind}-${i}`} className={`bl ${line.tone}`}>
              <span className="bm" aria-hidden="true">
                {MARK[line.tone]}
              </span>
              <span className="bt">
                {line.text}
                {line.note && <span className="bnote">{line.note}</span>}
              </span>
              {line.where && (
                <Link className="bgo" to={`${to}/${line.where}`}>
                  look →
                </Link>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

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
        (["attention", "moved", "delegated"] as const).map((group) => (
          <Group
            key={group}
            group={group}
            to={to}
            lines={brief.lines.filter((line) => line.group === group)}
          />
        ))
      ) : (
        <Pulse brief={brief} />
      )}
      <Money brief={brief} />
      <Waiting brief={brief} />
      <NextUp brief={brief} />
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
