import { Link } from "react-router-dom";
import { DeskProject, DeskView, get } from "../api";
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
    owns it — the page never sorts by which company was created first.

      1  stopped      it has stopped, and only the chairman restarts it
      2  on a clock   it resolves without him, at a time we can name
      3  drifting     nothing breaks; the claim just gets weaker

    Only tiers 1 and 2 are counted anywhere, because the count is a promise: a
    number above zero means something has a consequence attached. Tier 3 is
    real work — it is the difference between "done means verified" being true
    and being a slogan — but nothing expires and no agent is blocked, so
    padding the count with it would spend the one number a chairman has to be
    able to trust.

    Companies that are over do not appear at all (`?live=true`): a stopped or
    completed company cannot need anything, and every number on it is frozen.

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

export function buildItems(projects: DeskProject[], reload: () => void): Item[] {
  const items: Item[] = [];
  const now = Date.now();

  for (const p of projects) {
    const to = `/projects/${p.id}`;

    // ── tier 1: stopped, and only the chairman restarts it ──────────────
    if (p.status === "paused") {
      items.push({
        // "paused", never "stopped": stopped is a real and different status in
        // this system — terminal, and off the desk entirely. A badge that
        // borrowed its word would describe a resumable company as a dead one.
        key: `${p.id}-paused`, tier: 1, at: 0, project: p, kind: "paused",
        what: (
          <>
            <b>Nothing is running here.</b>{" "}
            {p.status_reason ?? "The platform paused this company."}
          </>
        ),
        consequence: <span className="tick hot">it stays paused — indefinitely</span>,
        actions: <Link className="act pri" to={to}>Review &amp; resume</Link>,
      });
    }
    for (const bet of p.expired_bets) {
      items.push({
        key: bet.id, tier: 1, at: 0, project: p, kind: "bet expired",
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
        key: `${p.id}-frozen`, tier: 1, at: 0, project: p, kind: "thread frozen",
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
        key: `${p.id}-verdict-${v.key}`, tier: 1, at: 0, project: p, kind: "your verdict",
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
        key: `${p.id}-plan`, tier: 1, at: 0, project: p, kind: "accept scope",
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
        key: `${p.id}-commitment`, tier: 2, at: NEVER - 1, project: p, kind: "sign the date",
        what: (
          <>
            The CEO proposes delivering <b>{w.commitment.scope_count} requirement
            {w.commitment.scope_count === 1 ? "" : "s"}</b> by{" "}
            <b>{fmtWhenShort(w.commitment.deliver_by)}</b>.
          </>
        ),
        consequence: (
          <span className="tick cold">
            the company keeps working, but it is promising nothing until you sign
          </span>
        ),
        actions: <Link className="act pri" to={`${to}/plan`}>Sign or move it</Link>,
      });
    }

    // ── tier 2: it resolves without you, at a time we can name ──────────
    for (const a of p.pending_approvals) {
      items.push({
        key: a.id, tier: 2, at: new Date(a.expires_at).getTime(), project: p,
        kind: a.kind.replaceAll("_", " "),
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
        key: bet.id, tier: 2, at: new Date(bet.kill_date).getTime(), project: p,
        kind: "kill date",
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
        key: `${p.id}-drought`, tier: 2, at: startsAt, project: p, kind: "drought",
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
    const pace = b.unit === "usd" ? paceDate(b.spent, b.cap) : null;
    if (frac < 1 && pace) {
      items.push({
        key: `${p.id}-budget`, tier: 2, at: pace.getTime(), project: p, kind: "budget",
        what: (
          <>
            {fmtUnit(b.spent, b.unit)} of {fmtUnit(b.cap, b.unit)} spent — at this pace the cap
            lands <b>{fmtWhenShort(pace.toISOString())}</b>.
          </>
        ),
        consequence: (
          <span className="tick">the platform pauses the company at 100%</span>
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
            nothing yet — spending has stopped, so there is no date to give you
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
    // Money moved and nothing outside the company confirmed anything. Distinct
    // from the drought, which counts hours rather than dollars — so it is
    // suppressed while a drought row is already speaking for this company.
    // USD only: a zero here in token billing means "not priced in dollars",
    // not "spent nothing".
    if (b.unit === "usd" && !droughtShown && p.spend_24h >= SPEND_FLOOR_USD
        && p.evidence_24h === 0 && p.status === "running") {
      items.push({
        key: `${p.id}-spend-noproof`, tier: 3, at: NEVER, project: p, kind: "no proof",
        what: (
          <>
            <b>${p.spend_24h.toLocaleString(undefined, { maximumFractionDigits: 2 })}</b> spent
            in the last 24h, and nothing verified by anyone outside the company.
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

  const live = data.projects;
  const items = buildItems(live, reload);
  const needing = items.filter((i) => i.tier <= 2);
  // The headline names a deadline, so it may only come from an item that has
  // one: an unsigned commitment sits in tier 2 without a clock.
  const soonest = items.find((i) => i.tier === 2 && i.at < NEVER - 1);
  const running = live.filter((p) => p.status === "running").length;
  const lastRun = live
    .map((p) => p.last_run_at)
    .filter((v): v is string => v !== null)
    .sort()
    .at(-1) ?? null;

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
      {needing.length > 0 && (
        <p className="subtitle">
          <b>
            {needing.length} thing{needing.length === 1 ? "" : "s"} need
            {needing.length === 1 ? "s" : ""} you.
          </b>
          {soonest && (
            <> The soonest resolves itself{" "}
              <span className="tick">{fmtSoon(new Date(soonest.at).toISOString())}</span>.
            </>
          )}
        </p>
      )}

      {live.length === 0 && (
        <EmptyDesk finished={data.finished} />
      )}

      {items.length > 0 && (
        <div className="list">
          {items.map((it) => (
            <div className={`item t${it.tier}`} key={it.key}>
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
                  <span className="lbl">If you do nothing</span>
                  {it.consequence}
                </div>
                {it.actions && <div className="acts">{it.actions}</div>}
              </div>
            </div>
          ))}
        </div>
      )}

      {live.length > 0 && needing.length === 0 && running > 0 && (
        <div className="quiet ok">
          <div className="mark">Nothing needs you.</div>
          <div className="sub">
            No decision is blocked, no clock is running out, and nothing has stopped. The
            proof it is actually working:
          </div>
          <div className="proof">
            <div className="pf">
              <div className="pv">{running}</div>
              <div className="pl">compan{running === 1 ? "y" : "ies"} working</div>
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

      {live.length > 0 && running === 0 && (
        <div className="quiet bad">
          <div className="mark">Nothing is running.</div>
          <div className="sub">
            You have {live.length} compan{live.length === 1 ? "y" : "ies"} that could be
            working and {live.length === 1 ? "it is not" : "none is"}. No agent is doing
            anything right now — which is <b>not</b> the same as all clear.
          </div>
          <div className="proof">
            <div className="pf">
              <div className="pv">{live.filter((p) => p.status === "paused").length}</div>
              <div className="pl">paused</div>
            </div>
            <div className="pf">
              <div className="pv">{live.filter((p) => p.status === "draft").length}</div>
              <div className="pl">never started</div>
            </div>
            <div className="pf">
              <div className="pv">{fmtAgo(lastRun)}</div>
              <div className="pl">last run</div>
            </div>
          </div>
        </div>
      )}

      {live.length > 0 && <CompanyStrip live={live} finished={data.finished} />}
    </>
  );
}

/** No live companies at all. Two different situations wearing one card would be
    the old bug: a chairman whose four companies all delivered is not a new
    user, and must not be shown the new-user screen. */
function EmptyDesk({ finished }: { finished: number }) {
  if (finished > 0) {
    return (
      <div className="quiet ok">
        <div className="mark">Every company has finished.</div>
        <div className="sub">
          All {finished} of your companies were completed or stopped. Nothing is running
          because there is nothing left to run — for a Deliver company that is the end
          state, not a fault.
        </div>
        <div className="acts" style={{ justifyContent: "center" }}>
          <Link className="act pri" to="/projects/new">Start another company</Link>
          <Link className="act lnk" to="/projects">review what they delivered →</Link>
        </div>
      </div>
    );
  }
  return (
    <div className="quiet">
      <div className="mark">You don’t chair a company yet.</div>
      <div className="sub">
        A company is a CEO agent, a team, a goal and a budget. Once one is running, this
        page collects every decision it can’t make without you — and stays empty the rest
        of the time.
      </div>
      <div className="acts" style={{ justifyContent: "center" }}>
        <Link className="act pri" to="/projects/new">Create your first company</Link>
      </div>
    </div>
  );
}

/** Navigation, not a metrics table — the Projects page is that, and this used
    to be a second copy of it. Name, state, and whether it is asking for
    something. */
function CompanyStrip({ live, finished }: { live: DeskProject[]; finished: number }) {
  return (
    <div className="roster">
      <div className="sh">Live companies · {live.length}</div>
      {live.map((p) => {
        const needsYou =
          p.pending_approvals.length + p.expired_bets.length + (p.status === "paused" ? 1 : 0);
        const dot = p.status === "running" ? "run" : p.status === "paused" ? "pau" : "stp";
        return (
          <div className="crow" key={p.id}>
            <span className={`dot ${dot}`} />
            <Link className="nm" to={`/projects/${p.id}`}>{p.name}</Link>
            <span className="gl">
              {p.status === "running" ? clip(p.goal, 70) : `${p.status} — ${clip(p.status_reason ?? p.goal, 60)}`}
            </span>
            <span className={`rt ${needsYou > 0 ? "need" : ""}`}>
              {needsYou > 0 ? "needs you" : fmtAgo(p.last_run_at)}
            </span>
          </div>
        );
      })}
      {finished > 0 && (
        <div className="fin">
          {finished} finished compan{finished === 1 ? "y is" : "ies are"} not shown here →{" "}
          <Link to="/projects">Projects</Link>
        </div>
      )}
    </div>
  );
}


