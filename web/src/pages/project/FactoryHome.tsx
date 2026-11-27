import { ReactNode } from "react";
import { Link } from "react-router-dom";
import { get } from "../../api";
import { usePoll } from "../../hooks";

/* A Code Factory's home, below the decisions. Shaped after what tools for
   supervising coding agents converged on: a review queue sorted by what each
   change waits on (review is the bottleneck once agents write the code), each
   seat's live state, delivery numbers in speed/quality pairs, and cost per
   merged change against the budget. Every figure comes from the platform's
   own rows (yaaf/server/factory_view.py); every summary says how many changes
   it rests on, and is marked early below five. */

type Waiting = "you" | "fix" | "review" | "tests" | "checks" | "ready" | "merging";
type Stat = { value: number | null; n: number; early: boolean };
export type FactoryView = {
  phase: string | null;
  stage?: "spec" | "build" | "first_release" | "running" | "kept";
  progress?: { accepted: number; proposed: number; met: number; working: number;
    merged_week: number; promises_kept: number | null; promises: number };
  in_flight?: { id: string; number: number | null; title: string; author: string;
    requirement: { key: string; name: string } | null; waiting_on: Waiting;
    added: number; removed: number; gates_green: number; gates_total: number;
    returns: number; opened_at: string }[];
  waiting_counts?: Record<Waiting, number>;
  crew?: { id: string; name: string; lead: boolean; role: string | null;
    state: "working" | "idle"; since: string | null; on: string | null;
    runs_today: number; tokens_today: number }[];
  cost?: { unit: "usd" | "tokens"; estimate: boolean; spent_usd: number; tokens: number;
    /** What a provider actually billed, and the subscription's runs at list price. */
    billed_usd?: number; subscription_usd?: number;
    cap: number; per_merged_change_usd: number | null; merged: number;
    top_seat: { name: string; usd: number } | null };
  numbers?: {
    time_to_merge_hours: Stat; sent_back: { count: number; n: number; early: boolean };
    first_review_minutes: Stat; change_lines: Stat & { limit: number };
    deploys: { count: number; early: boolean };
    rolled_back: { count: number; n: number; early: boolean };
    recover_hours: Stat; early_below: number;
  };
};

export function useFactory(projectId: string, on: boolean) {
  return usePoll(() => (on ? get<FactoryView>(`/observe/projects/${projectId}/factory`)
    : Promise.resolve(null)), 15000, [projectId, on]).data;
}

const STAGES: [NonNullable<FactoryView["stage"]>, string][] = [
  ["spec", "Spec"], ["build", "Building"], ["first_release", "First release"],
  ["running", "Running"], ["kept", "Kept weekly"],
];

/** Where the project is in its life, as one line: the phases a factory moves
    through once each. Not the steps a change goes through — those are the
    "Changes in flight" below. */
export function FactoryStage({ view }: { view: FactoryView | null }) {
  if (!view?.stage) return null;
  const at = STAGES.findIndex(([k]) => k === view.stage);
  return (
    <nav className="fh-stage" aria-label="Where the project is">
      {STAGES.flatMap(([key, label], i) => [
        ...(i ? [<i key={`${key}-sep`} aria-hidden="true">›</i>] : []),
        <span key={key} className={i < at ? "done" : i === at ? "now" : ""}
          aria-current={i === at ? "step" : undefined}>
          {i < at && <span aria-hidden="true">✓ </span>}{label}
        </span>,
      ])}
    </nav>
  );
}

const WAIT: Record<Waiting, [string, string]> = {
  you: ["You", "you"],
  fix: ["Needs a fix", "fix"],
  review: ["Waiting for review", "review"],
  tests: ["Needs hidden tests", "tests"],
  checks: ["Checks running", "checks"],
  ready: ["Ready to merge", "ready"],
  merging: ["Merging", "ready"],
};

function ago(iso: string): string {
  const min = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (min < 60) return `${min} min`;
  const h = Math.round(min / 60);
  return h < 48 ? `${h} h` : `${Math.round(h / 24)} d`;
}

function tokens(n: number): string {
  return n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}k` : `${n}`;
}

export function FactoryInFlight({ view, base }: { view: FactoryView | null; base: string }) {
  const rows = view?.in_flight;
  if (!rows || !view.waiting_counts) return null;
  const counts = view.waiting_counts;
  const shown: Waiting[] = ["you", "fix", "review", "tests", "checks", "ready"];
  return (
    <section className="card fh-card" aria-labelledby="fh-flight">
      <div className="fh-head">
        <h2 id="fh-flight">Changes in flight</h2>
        <Link to={`${base}/details/changes`}>All changes</Link>
      </div>
      <div className="fh-counts">
        {shown.map((k) => (
          <div key={k} className={`fh-count w-${WAIT[k][1]}`}>
            <span>{WAIT[k][0]}</span>
            <b className="mono">{k === "ready" ? counts.ready + counts.merging : counts[k]}</b>
          </div>
        ))}
      </div>
      {rows.length === 0 ? (
        <p className="help">No change is open. When a seat finishes a piece of work it opens one
          here, and the platform merges it once every check is green.</p>
      ) : (
        <div className="fh-scroll">
          <table className="fh-table">
            <caption>People first, then machines; oldest first within each.</caption>
            <thead><tr>
              <th scope="col">Change</th><th scope="col">Waiting on</th>
              <th scope="col" className="num">Size</th><th scope="col">Checks</th>
              <th scope="col" className="num">Open for</th>
            </tr></thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.id}>
                  <td>
                    <Link className="fh-title" to={`${base}/details/changes`}>
                      #{c.number ?? "?"} {c.title}</Link>
                    <span className="help">{c.author}
                      {c.requirement && <> · {c.requirement.key} {c.requirement.name}</>}
                      {c.returns > 0 && <> · sent back {c.returns === 1 ? "once" : `${c.returns} times`}</>}
                    </span>
                  </td>
                  <td><span className={`fh-pill w-${WAIT[c.waiting_on][1]}`}>{WAIT[c.waiting_on][0]}</span></td>
                  <td className="num mono">+{c.added} −{c.removed}</td>
                  <td className="help">{c.gates_total ? `${c.gates_green} of ${c.gates_total} green` : "not checked yet"}</td>
                  <td className="num mono">{ago(c.opened_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function FactoryCrew({ view, base }: { view: FactoryView | null; base: string }) {
  const crew = view?.crew;
  if (!crew?.length) return null;
  const busy = crew.filter((s) => s.state === "working").length;
  return (
    <section className="card fh-card" aria-labelledby="fh-crew">
      <div className="fh-head">
        <h2 id="fh-crew">The crew <span className="help">{busy} working now</span></h2>
        <Link to={`${base}/details/activity/runs`}>Every run</Link>
      </div>
      <div className="fh-scroll">
        <table className="fh-table">
          <thead><tr>
            <th scope="col">Seat</th><th scope="col">Now</th><th scope="col">On</th>
            <th scope="col" className="num">Runs, 24 h</th><th scope="col" className="num">Tokens, 24 h</th>
          </tr></thead>
          <tbody>
            {crew.map((s) => (
              <tr key={s.id}>
                <td><b>{s.name}</b> <span className="help">· {s.lead ? "leads" : s.role ?? "seat"}</span></td>
                <td><span className={`fh-dot ${s.state}`}>{s.state === "working"
                  ? `Working${s.since ? ` · ${ago(s.since)}` : ""}` : "Idle"}</span></td>
                <td className="help">{s.on ?? "—"}</td>
                <td className="num mono">{s.runs_today}</td>
                <td className="num mono">{tokens(s.tokens_today)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function FactoryCost({ view, base }: { view: FactoryView | null; base: string }) {
  const c = view?.cost;
  if (!c) return null;
  const usd = (n: number) => `${c.estimate ? "~" : ""}$${n < 10 ? n.toFixed(2) : Math.round(n)}`;
  const used = c.unit === "usd" ? c.spent_usd : c.tokens;
  const share = c.cap > 0 ? Math.min(1, used / c.cap) : 0;
  const money = (n: number) => `$${n < 10 ? n.toFixed(2) : Math.round(n)}`;
  const billed = c.billed_usd ?? 0;
  const onPlan = c.subscription_usd ?? 0;
  // A factory that codes on a subscription and talks on an API key (or the
  // other way round) has two kinds of dollar in one total: say which is which.
  const split = c.unit === "usd" ? onPlan > 0 : billed > 0;
  return (
    <section className="card fh-card" aria-labelledby="fh-cost">
      <div className="fh-head">
        <h2 id="fh-cost">Cost</h2>
        <Link to={`${base}/details/spend`}>Spend</Link>
      </div>
      <div className="fh-big">
        <span className="help">This month, against the {c.unit === "usd" ? `$${c.cap}` : `${tokens(c.cap)}-token`} cap</span>
        <b className="mono">{c.unit === "usd" ? usd(c.spent_usd) : `${tokens(c.tokens)} tokens`}</b>
      </div>
      <div className="fh-bar" role="img" aria-label={`${Math.round(share * 100)}% of the monthly cap used`}>
        <span style={{ width: `${share * 100}%` }} />
      </div>
      <dl className="fh-dl">
        {split && <div><dt>Billed</dt><dd className="mono">{money(billed)}</dd></div>}
        {split && c.unit === "usd" && <div><dt>On subscription</dt>
          <dd className="mono">~{money(onPlan)} at list price</dd></div>}
        <div><dt>Per merged change</dt><dd className="mono">{c.per_merged_change_usd == null
          ? "no merges yet" : `${usd(c.per_merged_change_usd)} · ${c.merged} merged`}</dd></div>
        {c.unit === "tokens" && <div><dt>At list price</dt><dd className="mono">{usd(c.spent_usd)}</dd></div>}
        {c.top_seat && <div><dt>Biggest seat</dt><dd>{c.top_seat.name}, {usd(c.top_seat.usd)}</dd></div>}
      </dl>
      <p className="help">{c.estimate
        ? "Paid by a subscription, so dollars are estimates from tokens at list price. "
        : ""}{split && c.unit === "usd"
        ? "Billed is what a provider charged. Subscription runs cost nothing extra and count against the cap at list price. "
        : ""}{split && c.unit === "tokens"
        ? "Billed is what an API key was charged, on top of the subscription. "
        : ""}Counts every run, including ones that produced nothing.</p>
    </section>
  );
}

function Num({ label, value, note, early }: { label: string; value: ReactNode; note: string; early?: boolean }) {
  return (
    <div className="fh-num">
      <span>{label}</span>
      <b className="mono">{value}</b>
      <span className={early ? "fh-early" : "help"}>{note}</span>
    </div>
  );
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** Each speed number beside the quality number that keeps it honest, and each
    saying how many changes it rests on. */
export function FactoryNumbers({ view }: { view: FactoryView | null }) {
  const n = view?.numbers;
  if (!n) return null;
  const based = (k: number, one: string, many: string, early: boolean) =>
    `${early ? "Early · " : ""}${plural(k, one, many)}`;
  const shippedYet = n.deploys.count > 0;
  return (
    <section className="card fh-card" aria-labelledby="fh-nums">
      <div className="fh-head"><h2 id="fh-nums">How the factory is doing</h2></div>
      <p className="help">Last 30 days. Each speed number sits beside the quality number that
        keeps it honest. Below {n.early_below}, a number is marked early.</p>
      <div className="fh-pairs">
        <div className="fh-pair">
          <h3>Getting changes in</h3>
          <Num label="Time to merge" value={n.time_to_merge_hours.value == null ? "—" : `${n.time_to_merge_hours.value} h`}
            note={based(n.time_to_merge_hours.n, "merged change", "merged changes", n.time_to_merge_hours.early)}
            early={n.time_to_merge_hours.early && n.time_to_merge_hours.n > 0} />
          <Num label="Sent back by review" value={n.sent_back.n ? `${n.sent_back.count} of ${n.sent_back.n}` : "—"}
            note={based(n.sent_back.n, "review", "reviews", n.sent_back.early)}
            early={n.sent_back.early && n.sent_back.n > 0} />
        </div>
        <div className="fh-pair">
          <h3>Review</h3>
          <Num label="Wait for a first review" value={n.first_review_minutes.value == null ? "—" : `${n.first_review_minutes.value} min`}
            note={based(n.first_review_minutes.n, "change", "changes", n.first_review_minutes.early)}
            early={n.first_review_minutes.early && n.first_review_minutes.n > 0} />
          <Num label="Typical change size" value={n.change_lines.value == null ? "—" : `${n.change_lines.value} lines`}
            note={`Limit ${n.change_lines.limit}`} />
        </div>
        <div className="fh-pair">
          <h3>Shipping</h3>
          <Num label="Deploys" value={n.deploys.count} note={shippedYet ? "Last 30 days" : "Counted from the first release"} />
          <Num label="Rolled back · recovery" value={shippedYet ? `${n.rolled_back.count} of ${n.rolled_back.n}${n.recover_hours.value != null ? ` · ${n.recover_hours.value} h` : ""}` : "—"}
            note={shippedYet ? based(n.rolled_back.n, "deploy", "deploys", n.rolled_back.early) : "Counted from the first release"}
            early={shippedYet && n.rolled_back.early} />
        </div>
      </div>
    </section>
  );
}
