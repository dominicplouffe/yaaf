import { useEffect, useMemo, useState } from "react";
import { CrewSummary, OrgView, RoleSummary, get } from "./api";
import { envVarName } from "./datasource";
import { modelLabel, parseRef } from "./providers";
import { GrantOption, TeamSwitches } from "./grants";
import { clip, downloadCsv } from "./ui";

/** Everything the chairman meets when hiring, in one place.

    There were two role pickers — the wizard's and the one on a live project's
    Overview — and both were a bare <select> showing a name and a model id.
    Everything else the API already sends about a role (what it does, what it
    can do, what key it needs, who runs it) was loaded and thrown away. This is
    the datasource.tsx move applied to hiring: one component, so the two
    screens cannot drift again, and so a field added here appears on both. */

// ── the taxonomy, mirrored from ROLE_CATEGORIES in the server ────────────────
// Order is the order of the aisles, and it is deliberate: who decides, who
// finds out, who builds, who writes it up, who checks it, who sells it.
export const CATEGORIES: { id: string; name: string; line: string }[] = [
  { id: "leadership", name: "Leadership & Direction", line: "Decides what happens next" },
  { id: "research", name: "Research & Analysis", line: "Finds out what's true" },
  { id: "build", name: "Build & Engineering", line: "Makes it work" },
  { id: "content", name: "Design & Content", line: "Makes it worth reading, using, learning" },
  { id: "challenge", name: "Challenge & Control", line: "Tries to prove it wrong before the world does" },
  { id: "growth", name: "Growth & Customers", line: "Finds buyers, and keeps them" },
];

export const DOMAINS: { id: string; label: string; short: string }[] = [
  { id: "general", label: "Any project", short: "Any" },
  { id: "markets", label: "Markets & investing", short: "Markets" },
  { id: "data", label: "Data & analytics", short: "Data" },
  { id: "games", label: "Mobile games", short: "Games" },
  { id: "education", label: "Education", short: "Learning" },
];

const CATEGORY_BY_ID = new Map(CATEGORIES.map((c) => [c.id, c]));
const DOMAIN_BY_ID = new Map(DOMAINS.map((d) => [d.id, d]));

export function categoryName(id: string | null): string {
  return CATEGORY_BY_ID.get(id ?? "")?.name ?? "Uncategorised";
}
export function domainLabel(id: string): string {
  return DOMAIN_BY_ID.get(id)?.label ?? id;
}
export function domainShort(id: string): string {
  return DOMAIN_BY_ID.get(id)?.short ?? id;
}

// ── derived, so nothing new has to be stored ─────────────────────────────────

/** What a hire costs in time and money, in the chairman's terms.

    A practice manager does not need to know what a frontier model is; they need
    to know this one thinks longer and costs about four times as much. The model
    and effort stay one click away, in the detail pane, for anyone who wants them.
    A role names a class whatever the vendor; a role pinned to a model is read
    by the family words vendors use for their largest and smallest. */
export function depthOf(model: string | null, effort: string | null):
  { band: string; dollars: string; rank: number } {
  const { cls } = parseRef(model);
  const name = (model ?? "").toLowerCase();
  const deep = cls === "frontier" || (cls === null && /opus|astra|fable|-pro\b|large/.test(name));
  const light = cls === "fast"
    || (cls === null && /haiku|flash|luna|mini|small|nano|lite/.test(name));
  const hard = effort === "xhigh" || effort === "max";
  if (deep) return { band: "Deepest", dollars: "$$$$", rank: 4 };
  if (light) return { band: "Light", dollars: "$", rank: 1 };
  if (hard) return { band: "Thorough", dollars: "$$$", rank: 3 };
  return { band: "Standard", dollars: "$$", rank: 2 };
}

/** A very rough monthly figure, only ever shown with a "≈". It exists so the
    tray can answer "what am I signing up for?" against the cap the chairman
    set two steps earlier — not to be an estimate anyone should rely on. */
const MONTHLY_BY_RANK: Record<number, number> = { 1: 8, 2: 26, 3: 52, 4: 140 };
export function roughMonthly(roles: RoleSummary[]): number {
  return roles.reduce(
    (sum, r) => sum + (MONTHLY_BY_RANK[depthOf(r.model, r.effort).rank] ?? 26), 0);
}

const TOOL_WORDS: Record<string, string> = {
  files: "Files",
  bash: "Code",
  data_processing: "Data",
  web: "Web",
};

/** tool_permissions as words. `{"bash": true}` is not a sentence. */
export function canDo(tools: string[]): string[] {
  return tools
    .filter((t) => t !== "egress_unrestricted")
    .map((t) => TOOL_WORDS[t] ?? t);
}

const SECRET_WORDS: Record<string, string> = {
  github: "a GitHub token",
  "market-data": "a market-data key",
  wordpress: "a WordPress login",
  "news-api": "a news API key",
};

/** What a role cannot work without. The API has always sent this; the picker
    never showed it, so the first sign of a missing key was a run that failed
    for a role the chairman had already hired. */
export function needsOf(scopes: string[]): { scope: string; words: string }[] {
  return (scopes ?? []).map((s) => ({ scope: s, words: SECRET_WORDS[s] ?? `a ${s} key` }));
}

// ── who's missing: rules over the roster, never a model call ─────────────────

export interface Gap {
  key: string;
  sentence: string;
  because: string;
  suggest: string[];
}

/** Deterministic, free, explainable, and available before a billing key is
    even attached — which is the whole argument against asking a model. Each
    rule reads the roster's categories, domains and secret scopes and says
    something about *this* project rather than about role theory. */
export function gapsFor(roster: RoleSummary[]): Gap[] {
  const cats = new Set(roster.map((r) => r.category ?? ""));
  const slugs = new Set(roster.map((r) => r.slug));
  const domains = new Set(roster.flatMap((r) => r.domains ?? []));
  const scopes = new Set(roster.flatMap((r) => r.secret_scopes ?? []));
  const producing = roster.filter(
    (r) => r.category === "research" || r.category === "build").length;
  const gaps: Gap[] = [];

  if (cats.has("research") && !cats.has("challenge")) {
    gaps.push({
      key: "unchecked",
      sentence: "Nobody here checks the numbers before you see them.",
      because: "This project produces findings and nothing re-derives them — "
        + "and the CEO cannot mark its own homework.",
      suggest: domains.has("data") ? ["data-skeptic", "adversary"]
        : domains.has("markets") ? ["risk-manager", "compliance-officer"]
          : ["adversary"],
    });
  }
  if (producing >= 2 && !cats.has("content")) {
    gaps.push({
      key: "unwritten",
      sentence: "People here produce, and nobody writes it up.",
      because: "Findings that never reach a readable form are findings you "
        + "will end up writing up yourself.",
      suggest: domains.has("markets") ? ["finance-writer", "writer"]
        : domains.has("data") ? ["exec-communicator", "writer"] : ["writer"],
    });
  }
  if (scopes.has("market-data") && !slugs.has("market-data-engineer")) {
    gaps.push({
      key: "unowned-data",
      sentence: "Your analysts read market data nobody owns.",
      because: "Corporate actions, point-in-time correctness and a pipeline "
        + "that reruns without you are somebody's job or they are yours.",
      suggest: ["market-data-engineer"],
    });
  }
  if (cats.has("build") && !cats.has("challenge")) {
    gaps.push({
      key: "unbroken",
      sentence: "Nothing here tries to break the build.",
      because: "One agent checking its own work has the same blind spots in "
        + "both seats.",
      suggest: ["adversary"],
    });
  }
  if (cats.has("growth") && !slugs.has("support")) {
    gaps.push({
      key: "unanswered",
      sentence: "You'll get customers, and nobody answers them.",
      because: "What users say when something breaks is the cheapest research "
        + "this project can get.",
      suggest: ["support"],
    });
  }
  if (domains.has("markets") && !slugs.has("compliance-officer")) {
    gaps.push({
      key: "unreviewed",
      sentence: "Nothing you publish is checked for the lines that must not be crossed.",
      because: "Advice, claims, licensing and market conduct are not things to "
        + "discover you got wrong afterwards.",
      suggest: ["compliance-officer"],
    });
  }
  return gaps;
}

// ── small shared pieces ──────────────────────────────────────────────────────

/** The wizard styles this shape inline in three places already (project shape,
    authority, pace). One component, so the CEO picker and the crew cards look
    like the choices the chairman has already been making. */
export function PickCard({ on, title, blurb, example, onClick, disabled }: {
  on: boolean;
  title: string;
  blurb?: string;
  example?: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-pressed={on}
      className={`pickcard${on ? " on" : ""}`}
    >
      <span className="pt">{on ? "◉" : "○"} {title}</span>
      {blurb && <span className="pb">{blurb}</span>}
      {example && <span className="pe">{example}</span>}
    </button>
  );
}

/** Which CEO runs the project. Until now this was never asked: the form took
    whichever CEO role the database returned first, so an install carrying both
    `ceo` and `cio` could hand a markets desk the generic CEO — and the CEO is
    the only agent the chairman ever speaks to. */
export function CeoPicker({ roles, value, onChange }: {
  roles: RoleSummary[];
  value: string;
  onChange: (slug: string) => void;
}) {
  const ceos = roles.filter((r) => r.is_ceo_role);
  if (ceos.length === 0) {
    return (
      <p className="help">
        This install's library has no CEO role. Load one with{" "}
        <code>yaaf seed-roles roles/ --only-missing</code>, or create one on the
        Roles page — every project runs exactly one.
      </p>
    );
  }
  return (
    <div className="pickrow">
      {ceos.map((role) => (
        <PickCard
          key={role.slug}
          on={value === role.slug}
          title={role.name}
          blurb={clip(role.description, 130)}
          example={role.domains?.includes("markets")
            ? "pick this for markets work"
            : "the default — any kind of project"}
          onClick={() => onChange(role.slug)}
        />
      ))}
    </div>
  );
}

// ── the hiring desk ──────────────────────────────────────────────────────────

export interface HireSelection {
  slug: string;
  /** Set when the pick came from a crew, so the team row can prefill. */
  team?: { name: string; employees: string[]; lead?: string | null;
           schedules?: Record<string, string> };
}

export function useRoleLibrary() {
  const [roles, setRoles] = useState<RoleSummary[]>([]);
  const [crews, setCrews] = useState<CrewSummary[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    Promise.all([
      get<RoleSummary[]>("/admin/roles"),
      // A library with no crews is normal on an install that has not run
      // seed-crews yet, and must not take the role list down with it.
      get<CrewSummary[]>("/admin/crews").catch(() => [] as CrewSummary[]),
    ])
      .then(([r, c]) => { setRoles(r); setCrews(c); setLoaded(true); })
      .catch((e: Error) => { setError(e.message); setLoaded(true); });
  }, []);
  return { roles, crews, loaded, error };
}

/** Roles grouped into aisles, uncategorised last and never hidden. */
function groupRoles(rows: RoleSummary[]) {
  const groups = CATEGORIES
    .map((c) => ({ ...c, rows: rows.filter((r) => r.category === c.id) }))
    .filter((g) => g.rows.length);
  const loose = rows.filter((r) => !CATEGORY_BY_ID.has(r.category ?? ""));
  if (loose.length) {
    groups.push({
      id: "", name: "Uncategorised", rows: loose,
      line: "Roles written here, or from before the categories",
    });
  }
  return groups;
}

export function HirePanel({
  roles, crews, loaded, error, selected, onToggle, onApplyCrew,
  onClose, alreadyOn, gaps, title, goal,
}: {
  roles: RoleSummary[];
  crews: CrewSummary[];
  loaded: boolean;
  error?: string | null;
  selected: string[];
  onToggle: (slug: string) => void;
  onApplyCrew?: (crew: CrewSummary) => void;
  onClose: () => void;
  /** Slugs already staffed on this project — selectable again, but dimmed. */
  alreadyOn?: Record<string, string>;
  gaps?: Gap[];
  title?: string;
  goal?: string;
}) {
  const [q, setQ] = useState("");
  const [domain, setDomain] = useState("all");
  const [view, setView] = useState<"table" | "cards">("table");
  const [az, setAz] = useState(false);
  const [detail, setDetail] = useState<string | null>(null);

  const workers = useMemo(() => roles.filter((r) => !r.is_ceo_role), [roles]);
  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return workers.filter((r) => {
      if (domain !== "all" && !(r.domains ?? []).includes(domain)) return false;
      if (!needle) return true;
      return `${r.name} ${r.slug} ${r.description} ${r.category ?? ""}`
        .toLowerCase().includes(needle);
    });
  }, [workers, q, domain]);

  const hiddenByDomain = domain === "all" ? 0
    : workers.filter((r) => (r.domains ?? []).includes("general")
      && !(r.domains ?? []).includes(domain)).length;

  const groups = az
    ? [{ id: "az", name: "Everything, A–Z", line: "sorted by name",
         rows: [...shown].sort((a, b) => a.name.localeCompare(b.name)) }]
    : groupRoles(shown);

  const detailRole = roles.find((r) => r.slug === detail)
    ?? groups[0]?.rows[0] ?? null;
  const picked = selected.map((s) => roles.find((r) => r.slug === s))
    .filter(Boolean) as RoleSummary[];

  function exportLibrary() {
    downloadCsv(
      "role-library.csv",
      ["slug", "name", "category", "domains", "depth", "cost", "model", "effort",
        "can_do", "needs", "teams_running_it", "description"],
      workers.map((r) => {
        const d = depthOf(r.model, r.effort);
        return [
          r.slug, r.name, r.category ?? "uncategorised", (r.domains ?? []).join(" "),
          d.band, d.dollars, r.model ?? "", r.effort ?? "",
          canDo(r.tools).join(" "), (r.secret_scopes ?? []).join(" "),
          String(r.usage?.length ?? 0), r.description,
        ];
      }),
    );
  }

  return (
    <div className="modal-back" onClick={(e) => {
      if (e.target === e.currentTarget) onClose();
    }}>
      <div className="modal hire" role="dialog" aria-modal="true" aria-label="Hire from the role library">
        <div className="hire-head">
          <div style={{ flex: 1, minWidth: 220 }}>
            <strong style={{ fontSize: 17 }}>{title ?? "Who's on this team?"}</strong>
            <p className="help" style={{ margin: "3px 0 0" }}>
              Hire from the role library. You can change any of it afterwards.
            </p>
          </div>
          <button type="button" onClick={onClose}>Done</button>
        </div>

        {goal?.trim() && (
          <div className="hire-goal"><strong>Your goal — </strong>{clip(goal, 260)}</div>
        )}

        {error && <div className="error">{error}</div>}

        {!loaded && <div className="skel-rows" aria-label="Loading roles">
          <div className="skel" /><div className="skel" /><div className="skel" />
        </div>}

        {loaded && workers.length === 0 && !error && (
          <div className="notice-box">
            <strong>This install has no roles yet.</strong> Load the thirty that
            ship with your checkout — <code>yaaf seed-roles roles/</code> — then
            reload this page.
          </div>
        )}

        {gaps && gaps.length > 0 && (
          <>
            <h2 className="zone">Who's missing</h2>
            {gaps.map((gap) => (
              <div className="card gap" key={gap.key}>
                <strong>{gap.sentence}</strong>
                <p className="help" style={{ margin: "5px 0 9px" }}>{gap.because}</p>
                <div className="pill-row">
                  {gap.suggest
                    .map((s) => roles.find((r) => r.slug === s))
                    .filter(Boolean)
                    .map((r) => (
                      <button type="button" className="small" key={r!.slug}
                        onClick={() => onToggle(r!.slug)}>
                        {selected.includes(r!.slug) ? "✓ " : "+ "}{r!.name}{" "}
                        <span className="chip">{depthOf(r!.model, r!.effort).dollars}</span>
                      </button>
                    ))}
                </div>
              </div>
            ))}
          </>
        )}

        {onApplyCrew && crews.length > 0 && (
          <>
            <h2 className="zone">Start from a crew</h2>
            <div className="crew-rail">
              {crews.map((crew) => (
                <button type="button" className="crew" key={crew.slug}
                  onClick={() => onApplyCrew(crew)}
                  disabled={crew.missing_role_slugs.length > 0}
                  title={crew.missing_role_slugs.length
                    ? `This install's library has no ${crew.missing_role_slugs.join(", ")}`
                    : undefined}>
                  <span className="cn">{crew.name}</span>
                  <span className="co">{crew.outcome}</span>
                  <span className="cr">
                    {crew.teams.map((t, i) => (
                      <span className="chip" key={i}>
                        {t.role_name ?? t.role_slug}
                        {t.employees.length > 1 ? ` ×${t.employees.length}` : ""}
                      </span>
                    ))}
                  </span>
                  <span className="cf">
                    <span>{crew.teams.length} team{crew.teams.length === 1 ? "" : "s"}</span>
                    <span>{crew.missing_role_slugs.length
                      ? "needs roles you don't have"
                      : `≈ $${roughMonthly(crew.teams.flatMap((t) => {
                        const r = roles.find((x) => x.slug === t.role_slug);
                        // One agent per employee, so two analysts cost twice.
                        return r ? Array(Math.max(1, t.employees.length)).fill(r) : [];
                      }) as RoleSummary[])}/mo`}</span>
                  </span>
                </button>
              ))}
            </div>
          </>
        )}

        <h2 className="zone">{onApplyCrew ? "Or pick roles yourself" : "The role library"}</h2>
        <div className="hire-tools">
          <div className="hire-search">
            <input type="search" value={q} onChange={(e) => setQ(e.target.value)}
              placeholder="Search roles — try 'check', 'deck', 'filings'"
              aria-label="Search roles" />
          </div>
          <div className="pills" role="group" aria-label="What a role works on">
            <button type="button" className={domain === "all" ? "active" : ""}
              onClick={() => setDomain("all")}>All {workers.length}</button>
            {DOMAINS.map((d) => {
              const n = workers.filter((r) => (r.domains ?? []).includes(d.id)).length;
              if (!n) return null;
              return (
                <button type="button" key={d.id} className={domain === d.id ? "active" : ""}
                  onClick={() => setDomain(d.id)}>{d.label} {n}</button>
              );
            })}
          </div>
          <div className="viewtog" role="group" aria-label="View">
            <button type="button" className={view === "table" ? "on" : ""}
              onClick={() => setView("table")}>Table</button>
            <button type="button" className={view === "cards" ? "on" : ""}
              onClick={() => setView("cards")}>Cards</button>
          </div>
        </div>

        <div className="hire-split">
          <div>
            {view === "table" && (
              <div className="rhead">
                <span /><span>Role</span><span>What they do</span>
                <span>Works on</span><span>Depth</span><span>Needs</span>
              </div>
            )}
            <div className={`hire-list${view === "table" ? "" : " cards"}`}>
              {shown.length === 0 ? (
                <div style={{ padding: "20px 16px" }}>
                  <div>No role matches <strong>{q}</strong>.</div>
                  <p className="help" style={{ marginTop: 6 }}>
                    Try a plainer word — <em>check</em>, <em>write</em>,{" "}
                    <em>build</em> — or write your own role on the Roles page.
                  </p>
                </div>
              ) : groups.map((group) => (
                <div key={group.id || group.name}>
                  <div className="cat-head">
                    <span className="cname">{group.name}</span>
                    <span className="cline">{group.line}</span>
                    <span className="ccount">{group.rows.length}</span>
                  </div>
                  {view === "cards" ? (
                    <div className="rcards">
                      {group.rows.map((r) => (
                        <button type="button" key={r.slug}
                          className={`rcard${selected.includes(r.slug) ? " on" : ""}`}
                          onClick={() => onToggle(r.slug)}>
                          <span className="rc-top">
                            <strong>{r.name}</strong>
                            <span className="chip">{depthOf(r.model, r.effort).dollars}</span>
                          </span>
                          <span className="rd">{clip(r.description, 120)}</span>
                          <span className="pill-row">
                            {(r.domains ?? []).map((d) => (
                              <span className="chip" key={d}>{domainShort(d)}</span>
                            ))}
                            {needsOf(r.secret_scopes ?? []).map((n) => (
                              <span className="chip warn" key={n.scope}>{n.scope}</span>
                            ))}
                          </span>
                        </button>
                      ))}
                    </div>
                  ) : group.rows.map((r) => {
                    const depth = depthOf(r.model, r.effort);
                    const on = alreadyOn?.[r.slug];
                    return (
                      <div key={r.slug}
                        className={`rrow${selected.includes(r.slug) ? " on" : ""}`
                          + (on ? " already" : "")}
                        onClick={() => setDetail(r.slug)}>
                        <input type="checkbox" checked={selected.includes(r.slug)}
                          onChange={() => onToggle(r.slug)}
                          onClick={(e) => e.stopPropagation()}
                          aria-label={`Hire ${r.name}`} />
                        <span>
                          <button type="button" className="rn"
                            onClick={(e) => { e.stopPropagation(); setDetail(r.slug); }}>
                            {r.name}
                          </button>
                          <span className="rs">{r.slug}</span>
                          {on && <span className="rs on-project">on this project · {on}</span>}
                        </span>
                        <span className="rd">{clip(r.description, 132)}</span>
                        <span className="rw">
                          {(r.domains ?? []).map((d) => (
                            <span className="chip" key={d} title={domainLabel(d)}>
                              {domainShort(d)}
                            </span>
                          ))}
                        </span>
                        <span className="dep">
                          <b>{depth.band}</b><span className="rm">{depth.dollars}</span>
                        </span>
                        <span className="rneeds">
                          {needsOf(r.secret_scopes ?? []).length === 0
                            ? <span className="rm dash">—</span>
                            : needsOf(r.secret_scopes ?? []).map((n) => (
                              <span className="chip warn" key={n.scope}
                                title={`Needs ${n.words}`}>{n.scope}</span>
                            ))}
                        </span>
                      </div>
                    );
                  })}
                </div>
              ))}
              {hiddenByDomain > 0 && shown.length > 0 && (
                <button type="button" className="hire-more"
                  onClick={() => setDomain("all")}>
                  {hiddenByDomain} more role{hiddenByDomain === 1 ? "" : "s"} work on
                  any project — show them
                </button>
              )}
            </div>

            <div className="tray">
              <div className="picked">
                {picked.length === 0
                  ? <span className="help">
                      Nobody hired yet{onApplyCrew ? " — pick a crew above, or tick roles." : "."}
                    </span>
                  : picked.map((r) => (
                    <span className="tchip" key={r.slug}>
                      {r.name}
                      <button type="button" onClick={() => onToggle(r.slug)}
                        aria-label={`Remove ${r.name}`}>×</button>
                    </span>
                  ))}
              </div>
              {picked.length > 0 && (
                <span className="est">≈ ${roughMonthly(picked)}/mo</span>
              )}
              <button type="button" className="primary" onClick={onClose}
                disabled={picked.length === 0}>
                {picked.length === 0 ? "Done" : `Hire ${picked.length} →`}
              </button>
            </div>

            <div className="hire-foot">
              <button type="button" className="linkish" onClick={() => setAz((v) => !v)}>
                {az ? "Back to categories" : "Show everything, A–Z"}
              </button>
              <button type="button" className="linkish" onClick={exportLibrary}>
                Export the library (CSV)
              </button>
              <span className="help">Nothing fits? Write your own on the Roles page.</span>
            </div>
          </div>

          {detailRole && (
            <aside className="hire-detail">
              <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
                <strong style={{ fontSize: 15 }}>{detailRole.name}</strong>
                {detailRole.is_ceo_role && <span className="badge accent">CEO</span>}
              </div>
              <div className="rs">{detailRole.slug}</div>
              <div className="dl">What they do</div>
              <div className="dv">{detailRole.description || "No description yet."}</div>
              <div className="dl">Depth</div>
              <div className="dv">
                {depthOf(detailRole.model, detailRole.effort).band}{" "}
                <span className="chip">{depthOf(detailRole.model, detailRole.effort).dollars}</span>
                <div className="help" style={{ marginTop: 3 }}>
                  {detailRole.model ? modelLabel(detailRole.model) : "default model"}
                  {" · "}{detailRole.effort ?? "default"} effort
                </div>
              </div>
              <div className="dl">Can do</div>
              <div className="pill-row">
                {canDo(detailRole.tools).map((t) => <span className="chip" key={t}>{t}</span>)}
              </div>
              <div className="dl">Needs</div>
              <div className="pill-row">
                {needsOf(detailRole.secret_scopes ?? []).length === 0
                  ? <span className="help">Nothing — works out of the box</span>
                  : needsOf(detailRole.secret_scopes ?? []).map((n) => (
                    <span className="chip warn" key={n.scope}>{n.words}</span>
                  ))}
              </div>
              <div className="dl">Works on</div>
              <div className="pill-row">
                {(detailRole.domains ?? []).length === 0
                  ? <span className="help">Not filed yet</span>
                  : (detailRole.domains ?? []).map((d) => (
                    <span className="chip" key={d}>{domainLabel(d)}</span>
                  ))}
              </div>
              <div className="dl">In use</div>
              <div className="help">
                {detailRole.usage?.length
                  ? `Running ${detailRole.usage.length} team${detailRole.usage.length === 1 ? "" : "s"}`
                  + ` in ${new Set(detailRole.usage.map((u) => u.project_name)).size} of your projects`
                  : "Not on any team yet"}
              </div>
              <div className="actions">
                <button type="button" className="small"
                  onClick={() => onToggle(detailRole.slug)}>
                  {selected.includes(detailRole.slug) ? "Remove" : "Hire"}
                </button>
              </div>
            </aside>
          )}
        </div>
      </div>
    </div>
  );
}


// ── who receives a credential ────────────────────────────────────────────────

/** One grantable role, with the people a grant to it would actually reach. */
export interface GrantRole {
  slug: string;
  /** Which teams run it. More than one is why this is grouped by role. */
  teams: string[];
  people: string[];
  /** The agent that filed the need is one of these. */
  asked: boolean;
  isCeo: boolean;
}

/** The roster, as grant targets.
 *
 *  Grouped by ROLE, not by team, because that is what a grant is: scoping a
 *  secret to `engineer` reaches every engineer on the project. Drawing a row
 *  per team would let a chairman tick "Build" and quietly also hand it to
 *  "Platform", which is the same lie in a friendlier font.
 */
export function grantRoles(org: OrgView | null, askedByAgentId?: string | null): GrantRole[] {
  const by = new Map<string, GrantRole>();
  const add = (slug: string | null, team: string, agents: { id: string; name: string }[],
               isCeo: boolean) => {
    if (!slug) return;
    const row = by.get(slug) ?? { slug, teams: [], people: [], asked: false, isCeo };
    row.teams.push(team);
    row.people.push(...agents.map((a) => a.name));
    row.asked = row.asked || agents.some((a) => a.id === askedByAgentId);
    by.set(slug, row);
  };
  for (const team of org?.teams ?? []) add(team.role_slug, team.name, team.agents, false);
  if (org?.ceo) add(org.ceo.role_slug, "CEO", [{ id: org.ceo.id, name: org.ceo.name }], true);
  // Whoever asked first: the chairman is confirming a proposal, so put the
  // thing being proposed where he looks first.
  return [...by.values()].sort((a, b) =>
    Number(b.asked) - Number(a.asked) || a.slug.localeCompare(b.slug));
}

/** Who gets this credential — as people, not slugs.
 *
 *  This replaced a free-text box labelled "Role slugs, comma-separated", into
 *  which the chairman reasonably typed the names of the two people he could
 *  see on the screen. The field was asking him to translate what he knows
 *  (who should have this) into an identifier he had no way to look up, and
 *  then storing a grant that reached nobody when he got it wrong.
 *
 *  Same argument as the hiring pickers above: one component, so the two
 *  screens that answer "who receives this" cannot drift apart again.
 */
export function RoleGrants({
  org, selected, onChange, askedByAgentId, secretName, disabled,
}: {
  org: OrgView | null;
  selected: string[];
  onChange: (slugs: string[]) => void;
  askedByAgentId?: string | null;
  /** Shown in the consequence line, as the variable it will arrive as. */
  secretName?: string;
  disabled?: boolean;
}) {
  if (!org) return <p className="help">Loading the roster…</p>;
  return (
    <TeamSwitches
      options={grantOptions(org, askedByAgentId)}
      selected={selected}
      onChange={onChange}
      disabled={disabled}
      what={secretName ? <code>${envVarName(secretName)}</code> : "it"}
      emptyHint={<>This project has no teams yet, so there is nobody to give a credential to.
        Hire someone first — a credential granted to no one reaches no one.</>}
    />
  );
}

/** The roster as the shared picker draws it: a role per row, named by the
    team(s) running it, with the people under it. */
export function grantOptions(org: OrgView | null, askedByAgentId?: string | null): GrantOption[] {
  return grantRoles(org, askedByAgentId).map((role) => ({
    slug: role.slug,
    name: role.teams.join(" + "),
    people: role.people,
    hue: role.teams[0],
    tags: [
      ...(role.asked ? [{ text: "asked for this", tone: "accent" as const }] : []),
      ...(role.isCeo ? [{ text: "plans, does not build" }] : []),
      // Sibling teams share a role, and so share a grant. Said, not hidden.
      ...(role.teams.length > 1 ? [{ text: `one role, ${role.teams.length} teams` }] : []),
    ],
  }));
}
