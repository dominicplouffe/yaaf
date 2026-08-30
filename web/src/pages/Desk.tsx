import { Link } from "react-router-dom";
import { DeskProject, DeskView, get } from "../api";
import { DeskBriefingPanel } from "../briefing";
import { usePoll } from "../hooks";
import {
  clip,
  DecideButtons,
  fmtAgo,
  fmtSoon,
  fmtUnit,
  fmtWhenShort,
  hoursSince,
  paceDate,
} from "../ui";

/** The chairman's desk: one question, asked once — what breaks if I close this
    tab?

    Everything here is ranked by *time to consequence*, which the platform
    already knows: approvals auto-deny, bets auto-block their tasks, budgets
    auto-pause. Three tiers, and the soonest deadline is the top row whoever
    owns it — the page never sorts by which project was created first.

      1  stopped      it has stopped, and only the chairman restarts it
      2  on a clock   it resolves without him, at a time we can name
      3  drifting     nothing breaks; the claim just gets weaker

    Tier is time-to-consequence, and it orders the list. It does not decide the
    count: `yours` does, and the two are orthogonal on purpose. A row is `yours`
    when the chairman holds the only lever — an approval that auto-denies, a bet
    only the chairman extends, a thread only the chairman unfreezes, scope only
    the chairman accepts, a date only the chairman signs, a verdict nobody on
    the project may pass. The count and the sidebar badge are exactly those
    rows, because the number is a promise: above zero means a decision is
    waiting.

    Everything else on a clock still gets a row and still sorts by when it lands
    — a drought, a kill date, a budget on pace — but it is not counted, and it
    does not say "if you do nothing", because there is nothing to do: the CEO
    resolves all three. Counting them cost what padding a count always costs. A
    chairman read "1 thing needs you" over a drought row at 8h of 12, went
    looking for the approval it implied, found none, and had to ask the CEO what
    had been missed — and the honest answer was nothing. Project → Overview has
    drawn this line since it shipped ("Needs you" holds decisions; "Watch" holds
    drought, kill dates and budget); this page now draws it in the same place.

    Tier 3 is real work — it is the difference between "done means verified"
    being true and being a slogan — but nothing expires and no agent is blocked,
    so it is never `yours` either.

    Only a project that is *running* appears here at all. The server drops the
    terminal ones (`?live=true`) because a stopped or completed project cannot
    need anything and every number on it is frozen; the page then drops draft
    and paused ones too. Neither is working, and the desk is the working set —
    a draft has never started and a pause sits there until someone resumes it,
    so neither can break while the tab is closed. They keep their tally in the
    "nothing is running" card below, which is a number and not a row, and their
    rows live on Projects.

    The autonomy dial decides what is even eligible for a row. Where the
    constitution says `ceo`, the CEO accepts its own plan, signs the platform's
    projected date and judges its own observables — each on the record and
    disclosed — so asking the chairman as well would be asking for a decision
    he deliberately gave away. The server does that filtering (`awaiting_you`
    is computed per switch), and an autonomous project simply has no such rows.

    What the dial never moves: spend, legal, tool_request and assist approvals
    are undelegable at every setting — money over the threshold, legal
    ambiguity, a credential the CEO does not hold, and a step only a human can
    take. Nor does it move the spot-check, which exists precisely *because* a
    CEO judges its own closes. Authority is configuration; integrity is
    structure, and the desk draws the line in the same place the server does. */

type Tier = 1 | 2 | 3;

export interface Item {
  key: string;
  tier: Tier;
  /** Epoch ms of the consequence — the sort key inside a tier. Tier 1 has
      already happened (0); tier 3 never does (+∞). */
  at: number;
  /** Whether `at` is a real future deadline, as opposed to a sort position.
      Two tier-2 rows have no deadline at all: an active drought is happening
      now and ends when evidence lands rather than on a clock, and an unsigned
      date waits indefinitely. Only a clocked row may be quoted as "the soonest
      resolves itself in X" — the headline names a deadline, so it has to come
      from something that has one. */
  clocked?: boolean;
  /** Whether the chairman holds the only lever on this row. The count, the
      badge and the "if you do nothing" label are all exactly this and never the
      tier — see the header. A row that is not `yours` is on the page so nothing
      happens silently, not because anything is being asked. */
  yours?: boolean;
  project: DeskProject;
  kind: string;
  what: React.ReactNode;
  consequence: React.ReactNode;
  actions?: React.ReactNode;
}

const NEVER = Number.MAX_SAFE_INTEGER;
// A floor under the "spent, proved nothing" row, so it can't fire on a few
// cents of overnight cache traffic. Arbitrary, and deliberately so: there is
// no principled number here, only a threshold below which the row is noise.
const SPEND_FLOOR_USD = 1;

/** What belongs on the desk: a project doing work right now. Exported and
    used by the sidebar badge too — a second copy of this test would drift, and
    a badge counting rows the page does not show is worse than no badge. */
export const isLive = (p: DeskProject) => p.status === "running";

/** The one definition of "a decision is waiting on the chairman". Exported
    because the sidebar badge has to count the same rows this page counts: a
    badge that disagrees with the list under it is worse than no badge, and the
    drought taught us that a badge nobody can act on is worse than both. */
export const needsYou = (i: Item) => i.yours === true;

/** One desk row. Extracted so the three lists below cannot drift apart in how
    they draw the same thing — the label that distinguishes an ask from a
    notice lives here, once. */
function DeskRow({ it }: { it: Item }) {
  return (
    <div className={`item t${it.tier}`}>
      <div className="stripe" />
      <div className="ib">
        <div className="irow1">
          <Link className="co" to={`/projects/${it.project.id}`}>
            {it.project.name}
          </Link>
          <span className={`kind k${it.tier}`}>{it.kind}</span>
        </div>
        <div className="what">{it.what}</div>
        <div className="cons">
          {/* "If you do nothing" is a question only worth asking where doing
              something is an option. On every other row it is the whole
              misreading: it casts a status line as an ultimatum and the reader
              goes hunting for the button. */}
          <span className="lbl">
            {needsYou(it) ? "If you do nothing" : "What happens next"}
          </span>
          {it.consequence}
        </div>
        {it.actions && <div className="acts">{it.actions}</div>}
      </div>
    </div>
  );
}

const latestRun = (ps: DeskProject[]): string | null =>
  ps.map((p) => p.last_run_at).filter((v): v is string => v !== null).sort().at(-1) ?? null;

export function buildItems(projects: DeskProject[], reload: () => void): Item[] {
  const items: Item[] = [];
  const now = Date.now();

  for (const p of projects.filter(isLive)) {
    const to = `/projects/${p.id}`;

    // A paused project is never a row — it is not on the desk at all (see
    // isLive above). Pausing is a state, not a request: it sits there until
    // someone resumes it, and a row that says so every day is reporting a fact
    // the chairman can already see on Projects.
    //
    // This holds however the pause happened, the platform's own included. A
    // budget-exhausted pause still emails him the moment it trips
    // (enforce_budget -> chairman_notify), so nothing is lost by keeping it out
    // of the list; what is gained is a count that means something. Parking four
    // projects used to pin the badge at four forever, and a badge that is
    // permanently non-zero is one the chairman stops reading — which costs him
    // the approval that auto-denies tomorrow.
    //
    // ── tier 1: stopped, and only the chairman restarts it ──────────────
    for (const bet of p.expired_bets) {
      items.push({
        key: bet.id, tier: 1, at: 0, yours: true, project: p, kind: "bet expired",
        what: (
          <>
            “{clip(bet.statement, 120)}” hit its kill date {fmtWhenShort(bet.kill_date)}
            {(bet.blocked_tasks ?? 0) > 0 && (
              <>, and <b>{bet.blocked_tasks} task(s) are blocked</b> behind it</>
            )}.
          </>
        ),
        consequence: (
          <span className="tick">the CEO pivots — you hold the one extension</span>
        ),
        actions: <Link className="act pri" to={`${to}/work`}>Decide</Link>,
      });
    }
    if (p.frozen_threads > 0) {
      items.push({
        key: `${p.id}-frozen`, tier: 1, at: 0, yours: true, project: p,
        kind: "thread frozen",
        what: (
          <>
            The pair breaker froze <b>{p.frozen_threads} thread(s)</b> — two agents were
            talking in a loop.
          </>
        ),
        consequence: <span className="tick hot">those agents stay stuck until you unfreeze</span>,
        actions: <Link className="act pri" to={`${to}/records/messages`}>Review the thread</Link>,
      });
    }

    // The dial's own three. Each list is server-side empty where the CEO
    // holds that switch, so an autonomous project reaches none of this.
    const w = p.awaiting_you;
    for (const v of w.verdicts) {
      items.push({
        key: `${p.id}-verdict-${v.key}`, tier: 1, at: 0, yours: true, project: p,
        kind: "your verdict",
        what: (
          <>
            <b>{v.key}</b> has no work left open and its acceptance test is an observable
            no script can run: “{clip(v.statement, 130)}”
          </>
        ),
        consequence: (
          <span className="tick hot">
            it stays frozen — the CEO cannot mark it met, and neither can anyone there
          </span>
        ),
        actions: <Link className="act pri" to={`${to}/plan`}>Judge it</Link>,
      });
    }
    if (w.plan > 0) {
      items.push({
        key: `${p.id}-plan`, tier: 1, at: 0, yours: true, project: p, kind: "accept scope",
        what: (
          <>
            The CEO proposed <b>{w.plan} requirement{w.plan === 1 ? "" : "s"}</b> and is
            waiting on you.
          </>
        ),
        consequence: (
          <span className="tick hot">
            proposed scope is not scope — no work can be built against it
          </span>
        ),
        actions: <Link className="act pri" to={`${to}/plan`}>Review the plan</Link>,
      });
    }
    if (w.commitment) {
      items.push({
        key: `${p.id}-commitment`, tier: 2, at: NEVER - 1, yours: true, project: p,
        kind: "sign the date",
        what: (
          <>
            The CEO proposes delivering <b>{w.commitment.scope_count} requirement
            {w.commitment.scope_count === 1 ? "" : "s"}</b> by{" "}
            <b>{fmtWhenShort(w.commitment.deliver_by)}</b>.
          </>
        ),
        consequence: (
          <span className="tick cold">
            the project keeps working, but it is promising nothing until you sign
          </span>
        ),
        actions: <Link className="act pri" to={`${to}/plan`}>Sign or move it</Link>,
      });
    }

    // ── tier 2: it resolves without you, at a time we can name ──────────
    for (const a of p.pending_approvals) {
      items.push({
        key: a.id, tier: 2, at: new Date(a.expires_at).getTime(), clocked: true,
        yours: true, project: p, kind: a.kind.replaceAll("_", " "),
        what: (
          <>
            {a.title}
            {a.amount_usd !== null && (
              <> — <b className="mono">${a.amount_usd.toLocaleString()}</b></>
            )}
          </>
        ),
        consequence: (
          <>
            <span className="tick hot">auto-denied {fmtSoon(a.expires_at)}</span>
            <span className="dim">· silence is a decision here</span>
          </>
        ),
        actions: (
          <>
            <DecideButtons approvalId={a.id} onDone={reload} />
            <Link className="act lnk" to={`${to}/records/decisions`}>read the request →</Link>
          </>
        ),
      });
    }
    for (const bet of p.kill_dates_soon) {
      items.push({
        key: bet.id, tier: 2, at: new Date(bet.kill_date).getTime(), clocked: true,
        project: p, kind: "kill date",
        what: <>“{clip(bet.statement, 120)}”</>,
        consequence: (
          <>
            <span className="tick">expires {fmtSoon(bet.kill_date)}</span>
            <span className="dim">· then its tasks block and the CEO re-plans</span>
          </>
        ),
        actions: <Link className="act lnk" to={`${to}/work`}>see the bet →</Link>,
      });
    }

    // A drought is the platform refusing new internal work until proof lands.
    // Active is happening now, so it sorts to the head of the clock tier;
    // approaching is dated from the last countable evidence.
    const d = p.drought;
    const droughtShown = d.active || (d.applies && d.last_countable_at !== null
      && hoursSince(d.last_countable_at) >= d.window_hours * 0.5);
    if (d.active) {
      items.push({
        key: `${p.id}-drought`, tier: 2, at: now, project: p, kind: "drought",
        what: (
          <>
            No verified external evidence in <b>{d.window_hours}h</b>.
          </>
        ),
        consequence: (
          <span className="tick hot">
            happening now — new internal work is being refused until proof lands
          </span>
        ),
        actions: <Link className="act pri" to={`${to}/work`}>See the proof</Link>,
      });
    } else if (droughtShown && d.last_countable_at) {
      const startsAt = new Date(d.last_countable_at).getTime() + d.window_hours * 3_600_000;
      items.push({
        key: `${p.id}-drought`, tier: 2, at: startsAt, clocked: true, project: p,
        kind: "drought",
        what: (
          <>
            <b>{Math.floor(hoursSince(d.last_countable_at))}h</b> without verified external
            evidence, of {d.window_hours}h.
          </>
        ),
        consequence: (
          <span className="tick">
            the platform starts refusing internal work {fmtSoon(new Date(startsAt).toISOString())}
          </span>
        ),
        actions: <Link className="act lnk" to={`${to}/work`}>see the proof →</Link>,
      });
    }

    // Budget. A cap already reached pauses the project, which is the tier-1
    // row above; a projected landing is a real clock; a high-but-static
    // fraction is worth knowing and has no clock at all.
    const b = p.budget;
    const frac = b.cap > 0 ? b.spent / b.cap : 0;
    // paceDate is spent-over-elapsed-days against the cap: arithmetic, not
    // currency. Gating it on USD left token-billed projects with no dated
    // warning at all before enforce_budget paused them, and told them their
    // spending had stopped while runs were burning tokens.
    const pace = paceDate(b.spent, b.cap);
    if (frac < 1 && pace) {
      items.push({
        key: `${p.id}-budget`, tier: 2, at: pace.getTime(), clocked: true, project: p,
        kind: "budget",
        what: (
          <>
            {fmtUnit(b.spent, b.unit)} of {fmtUnit(b.cap, b.unit)} spent — at this pace the cap
            lands <b>{fmtWhenShort(pace.toISOString())}</b>.
          </>
        ),
        consequence: (
          <span className="tick">the platform pauses the project at 100%</span>
        ),
        actions: <Link className="act lnk" to={`${to}/settings`}>raise or steer →</Link>,
      });
    } else if (frac < 1 && frac >= b.warn_ratio) {
      items.push({
        key: `${p.id}-budget`, tier: 3, at: NEVER, project: p, kind: "budget",
        what: (
          <>
            {Math.round(frac * 100)}% of the monthly cap used — {fmtUnit(b.spent, b.unit)} of{" "}
            {fmtUnit(b.cap, b.unit)}.
          </>
        ),
        consequence: (
          <span className="tick cold">
            nothing yet — at this rate the cap holds to the end of the month
          </span>
        ),
        actions: <Link className="act lnk" to={`${to}/settings`}>see the spend →</Link>,
      });
    }

    // ── tier 3: nothing breaks; the claim gets weaker ───────────────────
    if (p.audit.unaudited >= 3) {
      items.push({
        key: `${p.id}-audit`, tier: 3, at: NEVER, project: p, kind: "spot-check",
        what: (
          <>
            <b>{p.audit.unaudited} finished tasks</b> nobody has checked. The agents marked
            these done and wrote their own evidence for it.
          </>
        ),
        consequence: (
          <span className="tick cold">
            nothing breaks — but nothing independent says the work is real
          </span>
        ),
        actions: (
          <Link className="act" to={`${to}/work?audit=1`}>Check 5 at random</Link>
        ),
      });
    }
    // Money moved and nothing outside the project confirmed anything. Distinct
    // from the drought, which counts hours rather than dollars — so it is
    // suppressed while a drought row is already speaking for this project.
    // USD only: a zero here in token billing means "not priced in dollars",
    // not "spent nothing".
    if (b.unit === "usd" && !droughtShown && p.spend_24h >= SPEND_FLOOR_USD
        && p.evidence_24h === 0 && p.status === "running") {
      items.push({
        key: `${p.id}-spend-noproof`, tier: 3, at: NEVER, project: p, kind: "no proof",
        what: (
          <>
            <b>${p.spend_24h.toLocaleString(undefined, { maximumFractionDigits: 2 })}</b> spent
            in the last 24h, and nothing verified by anyone outside the project.
          </>
        ),
        consequence: (
          <span className="tick cold">
            nothing breaks — the work may be real, but only the agents say so
          </span>
        ),
        actions: <Link className="act lnk" to={`${to}/work`}>see what it bought →</Link>,
      });
    }
  }

  return items.sort((x, y) => x.tier - y.tier || x.at - y.at);
}

export default function Desk() {
  const { data, error, reload } = usePoll(
    () => get<DeskView>("/observe/desk?live=true"), 10000);

  if (error) return <div className="error">{error}</div>;
  if (!data) return <p className="subtitle">Loading…</p>;

  // `all` is every non-terminal project the server sent; `live` is the desk.
  // The difference is only ever spent on one thing — saying how many projects
  // are parked when none is working, so an empty desk can't read as "all
  // clear" or as "you have no projects".
  const all = data.projects;
  const live = all.filter(isLive);
  const idle = all.length - live.length;
  const items = buildItems(live, reload);
  // Waiting on the chairman, and on a clock the CEO runs out itself. Both are
  // shown and both sort by consequence; only the first is ever counted.
  const mine = items.filter(needsYou);
  const watching = items.filter((i) => i.tier <= 2 && !needsYou(i));
  // Tier 3 is neither: nothing is on a clock and nothing is asking. It gets
  // its own heading rather than trailing the clocked rows unlabelled.
  const drifting = items.filter((i) => i.tier === 3 && !needsYou(i));
  const soonest = items.find((i) => i.tier === 2 && i.clocked);
  const running = live.length;
  const lastRun = latestRun(live);

  return (
    <>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <h1>Your desk</h1>
        <div className="spacer" />
        <span className="asof">
          as of{" "}
          {new Date(data.as_of).toLocaleTimeString(undefined, {
            hour: "2-digit", minute: "2-digit",
          })}
        </span>
      </div>
      {mine.length > 0 ? (
        <p className="subtitle">
          <b>
            {mine.length} thing{mine.length === 1 ? "" : "s"} need
            {mine.length === 1 ? "s" : ""} you.
          </b>
          {soonest && (
            <> The soonest resolves itself{" "}
              <span className="tick">{fmtSoon(new Date(soonest.at).toISOString())}</span>.
            </>
          )}
        </p>
      ) : watching.length > 0 && (
        /* Rows, but none of them a decision. Said out loud, because the rows
           below are the same shape as the ones that are — and silence here
           reads as "work it out from the list", which is what sent a chairman
           looking for an approval behind a drought counter. */
        <p className="subtitle">
          <b>Nothing needs a decision from you.</b>{" "}
          {watching.length === 1 ? "One thing is" : `${watching.length} things are`} on a
          clock below; the CEO resolves {watching.length === 1 ? "it" : "them"}, and
          {watching.length === 1 ? " it is" : " they are"} listed so nothing happens
          silently.
        </p>
      )}

      {/* What happened, above what to do about it. The desk's rows answer
          "what breaks if I close this tab"; a chairman arriving after three
          days asks the earlier question first, and used to have to reconstruct
          it from four tables in Records. */}
      <DeskBriefingPanel />

      {running === 0 && idle === 0 && (
        <EmptyDesk finished={data.finished} />
      )}

      {/* Two lists, not one sorted list.

          Both halves were already computed and the row already switched its
          own label between "If you do nothing" and "What happens next" — but
          they were then interleaved by tier, so an evidence-drought notice sat
          between two real approvals and read as a third. It cost this
          exchange, twice in one morning:

            CHAIRMAN: have I been ask to approve or acknowledge something?
                      I'm wondering what?
            CEO:      No — everything I've filed has already been approved.
            CHAIRMAN: why do I see this then? [the drought banner]
            CHAIRMAN: but it's showing up as if I have to approve it in my Desk

          A label the reader has to notice is not a distinction; a heading they
          cannot skip past is. */}
      {mine.length > 0 && (
        <div className="list">
          <h2 className="listhead">Needs you</h2>
          {mine.map((it) => (
            <DeskRow it={it} key={it.key} />
          ))}
        </div>
      )}

      {watching.length > 0 && (
        <div className="list">
          <h2 className="listhead calm">
            Running its own clock
            <span className="sub">
              — nothing here is yours to approve; it resolves without you
            </span>
          </h2>
          {watching.map((it) => (
            <DeskRow it={it} key={it.key} />
          ))}
        </div>
      )}

      {drifting.length > 0 && (
        <div className="list">
          <h2 className="listhead calm">
            Drifting
            <span className="sub">— nothing breaks; the claim just gets weaker</span>
          </h2>
          {drifting.map((it) => (
            <DeskRow it={it} key={it.key} />
          ))}
        </div>
      )}

      {running > 0 && mine.length === 0 && (
        <div className="quiet ok">
          <div className="mark">Nothing needs you.</div>
          <div className="sub">
            {watching.length > 0 ? (
              <>
                No decision is blocked and nothing has stopped. What is on a clock above
                runs out without you — there is nothing there to approve or acknowledge.
                The proof it is actually working:
              </>
            ) : (
              <>
                No decision is blocked, no clock is running out, and nothing has stopped.
                The proof it is actually working:
              </>
            )}
          </div>
          <div className="proof">
            <div className="pf">
              <div className="pv">{running}</div>
              <div className="pl">project{running === 1 ? "" : "s"} working</div>
            </div>
            <div className="pf">
              <div className="pv">{fmtAgo(lastRun)}</div>
              <div className="pl">last run</div>
            </div>
            <div className="pf">
              <div className="pv">{soonest ? fmtSoon(new Date(soonest.at).toISOString()) : "none"}</div>
              <div className="pl">next clock</div>
            </div>
          </div>
        </div>
      )}

      {running === 0 && idle > 0 && (
        <div className="quiet bad">
          <div className="mark">Nothing is running.</div>
          <div className="sub">
            You have {idle} project{idle === 1 ? "" : "s"} that could be
            working and {idle === 1 ? "it is not" : "none is"}. No agent is doing
            anything right now — which is <b>not</b> the same as all clear.
          </div>
          <div className="proof">
            <div className="pf">
              <div className="pv">{all.filter((p) => p.status === "paused").length}</div>
              <div className="pl">paused</div>
            </div>
            <div className="pf">
              <div className="pv">{all.filter((p) => p.status === "draft").length}</div>
              <div className="pl">never started</div>
            </div>
            <div className="pf">
              <div className="pv">{fmtAgo(latestRun(all))}</div>
              <div className="pl">last run</div>
            </div>
          </div>
          <div className="acts" style={{ justifyContent: "center" }}>
            <Link className="act lnk" to="/projects">see them on Projects →</Link>
          </div>
        </div>
      )}

      {running > 0 && (
        <ProjectStrip
          live={live}
          elsewhere={idle + data.finished}
          needing={new Set(mine.map((i) => i.project.id))}
        />
      )}
    </>
  );
}

/** No live projects at all. Two different situations wearing one card would be
    the old bug: a chairman whose four projects all delivered is not a new
    user, and must not be shown the new-user screen. */
function EmptyDesk({ finished }: { finished: number }) {
  if (finished > 0) {
    return (
      <div className="quiet ok">
        <div className="mark">Every project has finished.</div>
        <div className="sub">
          All {finished} of your projects were completed or stopped. Nothing is running
          because there is nothing left to run — for a Deliver project that is the end
          state, not a fault.
        </div>
        <div className="acts" style={{ justifyContent: "center" }}>
          <Link className="act pri" to="/projects/new">Start another project</Link>
          <Link className="act lnk" to="/projects">review what they delivered →</Link>
        </div>
      </div>
    );
  }
  return (
    <div className="quiet">
      <div className="mark">You don’t chair a project yet.</div>
      <div className="sub">
        A project is a CEO agent, a team, a goal and a budget. Once one is running, this
        page collects every decision it can’t make without you — and stays empty the rest
        of the time.
      </div>
      <div className="acts" style={{ justifyContent: "center" }}>
        <Link className="act pri" to="/projects/new">Create your first project</Link>
      </div>
    </div>
  );
}

/** Navigation, not a metrics table — the Projects page is that, and this used
    to be a second copy of it. Name, state, and whether it is asking for
    something. */
function ProjectStrip(
  { live, elsewhere, needing }:
  { live: DeskProject[]; elsewhere: number; needing: Set<string> },
) {
  // Every row here is running, so the header needs one number and the dot is
  // never anything but green. The old version counted every non-terminal
  // project, and three parked ones beside a working one read as
  // "Live projects · 4" — a count of projects that were not working.
  return (
    <div className="roster">
      <div className="sh">
        Working · {live.length} project{live.length === 1 ? "" : "s"}
      </div>
      {live.map((p) => {
        // Read off the list above rather than recounted here. A second
        // definition drifted immediately: this one predated the dial rows, so
        // a project showing "accept scope" in the list read "12m ago" in the
        // roster — the exact contradiction the rest of this page exists to
        // stop.
        const needsYou = needing.has(p.id);
        return (
          <div className="crow" key={p.id}>
            <span className="dot run" />
            <Link className="nm" to={`/projects/${p.id}`}>{p.name}</Link>
            <span className="gl">{clip(p.goal, 70)}</span>
            <span className={`rt ${needsYou ? "need" : ""}`}>
              {needsYou ? "needs you" : fmtAgo(p.last_run_at)}
            </span>
          </div>
        );
      })}
      {elsewhere > 0 && (
        <div className="fin">
          {elsewhere} project{elsewhere === 1 ? " is" : "s are"} not running — parked,
          never started or finished — and not shown here →{" "}
          <Link to="/projects">Projects</Link>
        </div>
      )}
    </div>
  );
}


