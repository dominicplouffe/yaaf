import { useEffect, useMemo, useRef, useState } from "react";
import { plainStatus } from "../ui/kit";
import { Link, useSearchParams } from "react-router-dom";
import { DeskProject, DeskView, get } from "../api";
import { usePoll } from "../hooks";
import { AsOf, Skeleton, clip, fmtAgo, fmtUnit } from "../ui";
import { needsYouCount, WARN_VERDICTS } from "../desk";

export function statusBadge(status: string, unpaid = false) {
  // A project that cannot pay for a run is not running, whatever its row says:
  // its first run dies on the missing key. Say so where the status is read.
  if (unpaid && (status === "running" || status === "draft")) {
    return <span className="badge warn" title="No billing credential: agents cannot run">
      {plainStatus(status)} · no key</span>;
  }
  const cls =
    status === "running"
      ? "good"
      : status === "paused"
        ? "warn"
        : status === "stopped"
          ? "critical"
          : "accent";
  return <span className={`badge ${cls}`}>{plainStatus(status)}</span>;
}

/** A project nobody can act on again. Terminal projects are the archive: they
    are kept, they are readable, and they are not what this page opens on. */
function isTerminal(p: DeskProject): boolean {
  return p.status === "stopped" || p.status === "completed";
}

/** Only things with a consequence if ignored, and only on a project that is
    running: an approval that auto-denies, a bet past its kill date, a frozen
    thread, a switch the CEO parked on the chairman. The optional spot-check is
    deliberately not counted — a card that shouts "1 waiting on you" when the
    honest answer is "nothing is" teaches the reader to stop reading the number.

    This page does not decide any of that: the Inbox does, and this asks it (see
    `needsYouCount`). It used to count for itself and got two things wrong that
    the chip's own link exposed — a paused project counted as one decision
    because it was paused, and its parked approvals counted too, so a portfolio
    with seven projects held read "12 waiting on you" and sent the chairman to
    a desk with nothing on it. Pausing is a state, not a request; a held
    project's approvals are held with it. Both are zero here now, as are
    drafts' and terminals'. */
function needsYou(p: DeskProject): number {
  return needsYouCount(p);
}

// ── the scopes ─────────────────────────────────────────────────────────────────
//
// The old page rendered every project it had, in five always-open sections, as
// one full-size card each. That is fine at four projects and unreadable at
// forty: the ones that ended — which by definition can never need anything —
// took the same room as the ones running, and pushed the live portfolio off
// the first screen. So the page now opens on the working set and keeps the
// archive one press away, which is the only thing about it that changed: the
// record is not hidden, it is just no longer the default reading.

type ScopeKey = "attention" | "active" | "running" | "paused" | "draft" | "archived" | "all";

const SCOPES: {
  key: ScopeKey;
  label: string;
  match: (p: DeskProject) => boolean;
  /** Kept on the bar even at zero — these are the page's two anchors, and a
      filter row that reshuffles as counts change cannot be learned. */
  pinned?: boolean;
  empty: string;
}[] = [
  {
    key: "attention",
    label: "Needs you",
    match: (p) => needsYou(p) > 0,
    empty: "Nothing is waiting on you. Every live project is running itself.",
  },
  {
    key: "active",
    label: "Active",
    match: (p) => !isTerminal(p),
    pinned: true,
    empty: "No live projects. Start one, or look through the archive.",
  },
  { key: "running", label: "Running", match: (p) => p.status === "running",
    empty: "Nothing is running." },
  { key: "paused", label: "Paused", match: (p) => p.status === "paused",
    empty: "Nothing is paused." },
  { key: "draft", label: "Drafts", match: (p) => p.status === "draft",
    empty: "No drafts — every project here has been started at least once." },
  { key: "archived", label: "Archived", match: isTerminal,
    empty: "Nothing has finished yet." },
  { key: "all", label: "All", match: () => true, pinned: true,
    empty: "No projects yet." },
];

const SORTS: { key: string; label: string; cmp: (a: DeskProject, b: DeskProject) => number }[] = [
  {
    key: "attention",
    label: "Needs you first",
    cmp: (a, b) => needsYou(b) - needsYou(a) || byRecent(a, b),
  },
  { key: "recent", label: "Recently active", cmp: byRecent },
  { key: "name", label: "Name", cmp: (a, b) => a.name.localeCompare(b.name) },
  {
    key: "spend",
    label: "Budget used",
    cmp: (a, b) => budgetFrac(b) - budgetFrac(a),
  },
];

function byRecent(a: DeskProject, b: DeskProject): number {
  const at = a.last_run_at ? new Date(a.last_run_at).getTime() : 0;
  const bt = b.last_run_at ? new Date(b.last_run_at).getTime() : 0;
  return bt - at;
}

function budgetFrac(p: DeskProject): number {
  return p.budget.cap > 0 ? p.budget.spent / p.budget.cap : 0;
}

/** The status groups, in the order a chairman reads them: what is live, what is
    held, what never left the hangar, then the record. An unknown status lands
    in "Other" rather than being filed under a label that misdescribes it —
    a stranger out of place beats a stranger actively lied about.

    `open` is whether the group is expanded before anyone touches it. Only the
    ones that can still do something are: a held project and a finished one are
    a count and a heading until you ask for them, which is the whole difference
    between a page you scroll past and a page you read. Nothing is hidden — the
    heading always carries the true count, and one press opens it. */
const GROUPS: { key: string; title: string; note?: string; open: boolean }[] = [
  { key: "running", title: "Running", open: true },
  { key: "paused", title: "Paused", note: "held — nothing new starts until you resume", open: false },
  // Every project is born a draft, so a portfolio can legitimately be nothing
  // else — and a first-run chairman who lands on one shut drawer has been shown
  // nothing at all. Drafts are also short and actionable, which is the opposite
  // of the three below.
  { key: "draft", title: "Drafts", note: "never started", open: true },
  { key: "completed", title: "Completed", note: "delivered, and the platform proved it", open: false },
  { key: "stopped", title: "Stopped", note: "terminal — the record stays readable", open: false },
  { key: "other", title: "Other", open: true },
];

const VIEW_KEY = "yaaf_projects_view";

/** What a closed group is still holding, so collapsing never buries a decision. */
function needsIn(items: DeskProject[]): number {
  return items.reduce((n, p) => n + needsYou(p), 0);
}

/** The portfolio directory: what projects exist and how each is doing — the
    Inbox answers "what needs me now?"; this page answers "what do I own?".
    Scope, search and sort live in the URL so a particular reading of the
    portfolio is a link you can send; the card/compact choice is a preference
    about your own eyes, so it lives in this browser. */
export default function Projects() {
  const { data, error, at } = usePoll(() => get<DeskView>("/observe/desk"), 10000);
  const [params, setParams] = useSearchParams();
  const [view, setView] = useState<"cards" | "compact">(
    () => (localStorage.getItem(VIEW_KEY) === "compact" ? "compact" : "cards"),
  );
  // Which groups the reader has opened or closed by hand. Anything absent falls
  // back to the rule below — so a scope change re-decides, and an explicit
  // press always wins over it.
  const [toggled, setToggled] = useState<Record<string, boolean>>({});
  const searchRef = useRef<HTMLInputElement>(null);

  const scope = (SCOPES.find((s) => s.key === params.get("scope"))?.key ?? "active") as ScopeKey;
  const sortKey = SORTS.find((s) => s.key === params.get("sort"))?.key ?? "attention";
  const query = params.get("q") ?? "";

  function setParam(key: string, value: string, fallback: string) {
    const next = new URLSearchParams(params);
    if (value === fallback) next.delete(key);
    else next.set(key, value);
    // A filter is a view of the moment, not a place — replacing keeps Back
    // pointing at the page you came from rather than at your own keystrokes.
    setParams(next, { replace: true });
  }

  function chooseView(v: "cards" | "compact") {
    setView(v);
    localStorage.setItem(VIEW_KEY, v);
  }

  // "/" is the search box everywhere else on the web; it should be here too.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      const el = document.activeElement;
      if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) return;
      e.preventDefault();
      searchRef.current?.focus();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const projects = useMemo(() => data?.projects ?? [], [data]);

  const matchesQuery = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return () => true;
    return (p: DeskProject) =>
      [p.name, p.slug, p.goal, p.status, p.status_reason ?? ""]
        .join(" ")
        .toLowerCase()
        .includes(q);
  }, [query]);

  // Counts on the scope pills are read off the searched set, not the whole
  // portfolio: a pill that says "Archived 12" and then shows three is a pill
  // that lied about the button you were deciding whether to press.
  const searched = useMemo(() => projects.filter(matchesQuery), [projects, matchesQuery]);
  const active = SCOPES.find((s) => s.key === scope)!;
  const sorter = SORTS.find((s) => s.key === sortKey)!;

  const visible = useMemo(
    () => searched.filter(active.match).sort(sorter.cmp),
    [searched, active, sorter],
  );

  // A group opens by default when it is live work; when the reader asked for
  // this narrowly and a closed drawer would be the answer to their own press
  // (choose "Archived" and get two shut headings — the first version did
  // exactly that, because "archived" spans two groups and neither was alone on
  // the page); or when a search is running, since a match you cannot see is a
  // search that failed.
  const searching = query.trim().length > 0;
  const narrow = scope !== "active" && scope !== "all";
  // A drawer you shut while reading the live portfolio should not still be shut
  // when you go looking through the archive — the rule above gets to decide
  // again every time the reading changes. Keyed on whether a search is running
  // rather than on the text: resetting per keystroke would undo a collapse the
  // reader made mid-search, every time they typed the next letter.
  useEffect(() => setToggled({}), [scope, searching]);
  const filled = GROUPS.map((g) => ({
    ...g,
    items: visible.filter(
      (p) => (GROUPS.some((x) => x.key === p.status) ? p.status : "other") === g.key,
    ),
  })).filter((g) => g.items.length > 0);
  // …and a group that is the whole page opens whatever its own default says:
  // one shut drawer and nothing else is a page that answered no question.
  const groups = filled.map((g) => ({
    ...g,
    open: toggled[g.key] ?? (g.open || narrow || searching || filled.length === 1),
  }));

  const live = projects.filter((p) => !isTerminal(p));
  const waiting = live.reduce((n, p) => n + needsYou(p), 0);
  const shape = [
    { label: "running", n: live.filter((p) => p.status === "running").length },
    { label: "paused", n: live.filter((p) => p.status === "paused").length },
    { label: "draft", n: live.filter((p) => p.status === "draft").length },
    { label: "archived", n: projects.length - live.length },
  ].filter((s) => s.n > 0);

  if (error && !data) return <div className="error">{error}</div>;

  return (
    <>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <h1>Projects</h1>
        <div className="spacer" />
        <Link to="/projects/new"><button className="small primary">+ New project</button></Link>
      </div>
      <p className="subtitle" style={{ marginBottom: 12 }}>
        Every project has a CEO, teams, a constitution — and you as chairman.
      </p>

      {!data ? (
        <Skeleton rows={4} />
      ) : projects.length === 0 ? (
        <div className="card">
          <p style={{ margin: "0 0 10px" }}>
            No projects yet. You can describe what you want and have yaaf draft
            the whole thing, or fill the form in yourself — the next screen
            lays out both.
          </p>
          <Link to="/projects/new"><button className="primary">+ New project</button></Link>
        </div>
      ) : (
        <>
          {/* What the portfolio is, in one line, before any filtering — so the
              numbers on this strip never change as you narrow the list below.
              Empty buckets are left out, and a portfolio the strip has nothing
              to say about (one draft, and nothing waiting) does not get a strip
              reading "0 running 0 paused 0 archived". */}
          {(shape.length > 1 || waiting > 0) && (
            <div className="psum">
              {shape.map((s) => (
                <span key={s.label} className={s.label === "archived" ? "arch" : undefined}>
                  <b>{s.n}</b> {s.label}
                </span>
              ))}
              {waiting > 0 && (
                <Link to="/inbox"><span className="chip warn">
                  {waiting} {waiting === 1 ? "decision" : "decisions"} waiting on you
                </span></Link>
              )}
            </div>
          )}

          <div className="hire-tools">
            <div className="hire-search">
              <input
                ref={searchRef}
                type="search"
                value={query}
                onChange={(e) => setParam("q", e.target.value, "")}
                placeholder="Search projects — name, goal, slug  ( / )"
                aria-label="Search projects"
              />
            </div>
            <div className="viewtog" role="group" aria-label="Density">
              <button
                className={view === "cards" ? "on" : ""}
                aria-pressed={view === "cards"}
                onClick={() => chooseView("cards")}
              >
                Cards
              </button>
              <button
                className={view === "compact" ? "on" : ""}
                aria-pressed={view === "compact"}
                onClick={() => chooseView("compact")}
              >
                Compact
              </button>
            </div>
            <label className="sortpick">
              Sort
              <select
                value={sortKey}
                onChange={(e) => setParam("sort", e.target.value, "attention")}
                aria-label="Sort projects"
              >
                {SORTS.map((s) => (
                  <option key={s.key} value={s.key}>{s.label}</option>
                ))}
              </select>
            </label>
          </div>

          <div className="pills" role="group" aria-label="Which projects to show">
            {SCOPES.map((s) => {
              const n = searched.filter(s.match).length;
              if (n === 0 && !s.pinned && s.key !== scope) return null;
              return (
                <button
                  key={s.key}
                  className={scope === s.key ? "active" : ""}
                  aria-pressed={scope === s.key}
                  onClick={() => setParam("scope", s.key, "active")}
                >
                  {s.label} {n}
                </button>
              );
            })}
          </div>

          {visible.length === 0 && (
            <div className="card help">
              {query.trim() && searched.length === 0
                ? `Nothing matches “${query.trim()}”.`
                : active.empty}{" "}
              {(query.trim() || scope !== "all") && (
                <button
                  className="linkish"
                  onClick={() => setParams(new URLSearchParams({ scope: "all" }), { replace: true })}
                >
                  Show every project
                </button>
              )}
            </div>
          )}

          {groups.map((g) => (
            <div key={g.key}>
              <h2 className="zone">
                <button
                  className="zonetog"
                  aria-expanded={g.open}
                  onClick={() => setToggled({ ...toggled, [g.key]: !g.open })}
                >
                  <span className={`caret${g.open ? " open" : ""}`} aria-hidden="true">▸</span>
                  {g.title} <span className="cnt">{g.items.length}</span>
                  {g.note && <span className="note">{g.note}</span>}
                  {!g.open && needsIn(g.items) > 0 && (
                    <span className="chip warn">{needsIn(g.items)} waiting on you</span>
                  )}
                </button>
              </h2>
              {g.open &&
                (view === "compact" ? (
                  <div className="wbox">
                    {g.items.map((p) => <ProjectRow key={p.id} p={p} />)}
                  </div>
                ) : (
                  <div className="list-grid wide">
                    {g.items.map((p) => <ProjectCard key={p.id} p={p} />)}
                  </div>
                ))}
            </div>
          ))}
        </>
      )}

      <AsOf at={at} error={error} />
    </>
  );
}

/** The numbers a chairman compares, computed once for both shapes so a card and
    its compact row can never disagree about the same project. */
function vitals(p: DeskProject) {
  const frac = budgetFrac(p);
  return {
    needs: needsYou(p),
    terminal: isTerminal(p),
    frac,
    meterCls: frac >= 1 ? "over" : frac >= p.budget.warn_ratio ? "warn" : "",
    judgment:
      p.ceo_judgment.conversion === null ? null : Math.round(p.ceo_judgment.conversion * 100),
  };
}

function BudgetBit({ p }: { p: DeskProject }) {
  const { frac, meterCls } = vitals(p);
  return (
    <span>
      <span className={`mini-meter ${meterCls}`}>
        <i style={{ width: `${Math.min(frac, 1) * 100}%` }} />
      </span>
      <span className="mono">
        {fmtUnit(p.budget.spent, p.budget.unit)}/{fmtUnit(p.budget.cap, p.budget.unit)}
      </span>
    </span>
  );
}

function WaitingChip({ p }: { p: DeskProject }) {
  const { needs, terminal } = vitals(p);
  if (needs === 0 || terminal) return null;
  return (
    <Link to={`/inbox?project=${p.id}`}><span className="chip warn">{needs} waiting on you</span></Link>
  );
}

/** New boardroom messages — news, not a demand, so accent rather than warn. */
function UnreadChip({ p }: { p: DeskProject }) {
  const n = p.boardroom_unread ?? 0;
  if (n === 0) return null;
  return (
    <Link to={`/projects/${p.id}/chat`}>
      <span className="chip accent">{n > 99 ? "99+" : n} new in chat</span>
    </Link>
  );
}

/** Goal text is clipped to its opening — some are memo-length, and a card that
    scrolls is a card nobody compares against its neighbour. */
function ProjectCard({ p }: { p: DeskProject }) {
  const { terminal, judgment } = vitals(p);

  return (
    <div className={`card pcard${terminal ? " done" : ""}`}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <Link to={`/projects/${p.id}`}>
          <strong style={{ fontSize: 15 }}>{p.name}</strong>
        </Link>
        {statusBadge(p.status, p.unpaid)}
        <WaitingChip p={p} />
        <UnreadChip p={p} />
        <div className="spacer" />
        <span className="mono help">{p.slug}</span>
      </div>

      {/* The sentence every surface says about it (its pause or stop reason
          included), then what it is for. */}
      {p.status_line ? (
        <p className={`pcard-status${WARN_VERDICTS.includes(p.status_line.verdict) ? " warn" : ""}`}>
          {p.status_line.sentence}
        </p>
      ) : p.status_reason && p.status !== "running" && (
        <p className="help" style={{ margin: "6px 0 0" }}>
          {p.status}: {clip(p.status_reason, 120)}
        </p>
      )}
      <p style={{ margin: "6px 0 0", fontSize: 13, color: "var(--ink-2)", lineHeight: 1.5 }}>
        {clip(p.goal, 220)}
      </p>

      <div className="pcard-vitals">
        <span>
          <b>{judgment === null ? "—" : `${judgment}%`}</b> CEO judgment
        </span>
        <span>
          <b>{p.evidence_7d}</b> evidence · 7d
        </span>
        <span>
          <b>{p.open_tasks}</b> of {p.queue_ceiling} jobs open
        </span>
        <BudgetBit p={p} />
        <span className="help">active {fmtAgo(p.last_run_at)}</span>
      </div>

      <div style={{ display: "flex", gap: 16, marginTop: 10, fontSize: 12.5 }}>
        <Link to={`/projects/${p.id}`}>Overview →</Link>
        <Link to={`/projects/${p.id}/chat`}>Chat →</Link>
        <Link to={`/projects/${p.id}/details/tasks`}>Tasks →</Link>
        <Link to={`/projects/${p.id}/details/settings`}>Settings →</Link>
      </div>
    </div>
  );
}

/** The same project on one line. Forty of these scan; forty cards do not — and
    the archive is read by scanning for a name, not by comparing vitals. */
function ProjectRow({ p }: { p: DeskProject }) {
  const { terminal } = vitals(p);
  return (
    <div className={`wrow prow${terminal ? " done" : ""}`}>
      {statusBadge(p.status, p.unpaid)}
      <div className="grow">
        <Link to={`/projects/${p.id}`}><strong>{p.name}</strong></Link>{" "}
        <span className="mono help">{p.slug}</span>
        <div className={`sb${p.status_line && WARN_VERDICTS.includes(p.status_line.verdict) ? " warn" : ""}`}>
          {p.status_line?.sentence
            ?? (p.status_reason && p.status !== "running"
              ? `${p.status}: ${clip(p.status_reason, 160)}`
              : clip(p.goal, 160))}
        </div>
      </div>
      <WaitingChip p={p} />
      <UnreadChip p={p} />
      {!terminal && <BudgetBit p={p} />}
      <span className="help" style={{ whiteSpace: "nowrap" }}>active {fmtAgo(p.last_run_at)}</span>
      <Link to={`/projects/${p.id}`} className="rowgo" aria-label={`Open ${p.name}`}>→</Link>
    </div>
  );
}
