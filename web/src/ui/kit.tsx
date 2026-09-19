/** The shapes this product repeats.
 *
 * Before this file there was no Button, Card, Badge, Table or EmptyState — 441
 * inline style objects across 25 files each re-decided a padding nothing could
 * agree on. Everything here lives at module scope and takes props, which is
 * the rule `tests/selftest_invariants/test_web_hygiene.py` enforces: a
 * component declared inside a render body is a new type on every render, and
 * React throws its DOM away rather than updating it. */

import { ReactNode, useEffect, useState } from "react";
import { Icon, IconName } from "./Icon";
import { downloadCsv } from "../ui";

/* ── people ──────────────────────────────────────────────────────────────── */

/** Two letters, from however many words the name has. */
export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

/** A team keeps its colour everywhere it appears, so the same desk is the same
    hue on the roster, in the spend chart and beside a file it wrote. Derived
    from the name rather than stored, because a colour nobody chose is one
    nobody has to maintain — and it is drawn from the five reserved chart slots,
    never from the status palette, so green never stops meaning "verified". */
export function teamHue(key: string | null | undefined): string {
  if (!key) return "";
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) >>> 0;
  return ` t${(h % 5) + 1}`;
}

export function Avatar({
  name,
  team,
  size,
  title,
}: {
  name: string;
  team?: string | null;
  size?: "sm" | "lg";
  title?: string;
}) {
  return (
    <span
      className={`avatar${size ? ` ${size}` : ""}${teamHue(team ?? name)}`}
      title={title ?? name}
      aria-hidden={title === undefined ? undefined : true}
    >
      {initials(name)}
    </span>
  );
}

/** A name, a job, a face. The product used to render a person as the string
    "CEO" and a task's owner as a bare surname in a column called "holder". */
export function Person({
  name,
  role,
  team,
  size,
}: {
  name: string;
  role?: string | null;
  team?: string | null;
  size?: "sm" | "lg";
}) {
  return (
    <span className="person">
      <Avatar name={name} team={team} size={size} />
      <span className="pn">
        <b>{name}</b>
        {role ? <span>{role}</span> : null}
      </span>
    </span>
  );
}

export interface Teammate {
  name: string;
  role?: string | null;
  team?: string | null;
  working?: boolean;
}

/** Who is awake right now. A team working while you are not looking is the
    thing you bought; it belonged in the frame, not at the foot of a page. */
export function TeamStrip({ people, note }: { people: Teammate[]; note?: ReactNode }) {
  if (!people.length) return null;
  return (
    <div className="teamstrip">
      {people.map((p) => (
        <span className="teamchip" key={`${p.team ?? ""}:${p.name}`}>
          <Avatar name={p.name} team={p.team} size="sm" />
          <b>{p.name}</b>
          {p.role ? <span>{p.role}</span> : null}
          <i className={p.working ? "on" : ""} title={p.working ? "working now" : "idle"} />
        </span>
      ))}
      {note ? <span style={{ fontSize: "var(--t-micro)", color: "var(--muted)" }}>{note}</span> : null}
    </div>
  );
}

/* ── controls ────────────────────────────────────────────────────────────── */

export type Tone = "good" | "warn" | "serious" | "critical" | "accent";

export function Btn({
  children,
  onClick,
  kind = "secondary",
  icon,
  small,
  disabled,
  busy,
  title,
  type,
}: {
  children?: ReactNode;
  onClick?: () => void;
  kind?: "primary" | "secondary" | "danger";
  icon?: IconName;
  small?: boolean;
  disabled?: boolean;
  busy?: boolean;
  title?: string;
  type?: "button" | "submit";
}) {
  const cls = [kind === "primary" ? "primary" : kind === "danger" ? "danger" : "", small ? "small" : ""]
    .filter(Boolean)
    .join(" ");
  return (
    <button className={cls} onClick={onClick} disabled={disabled || busy} title={title} type={type}>
      {icon ? <Icon name={icon} size={small ? 13 : 15} /> : null}
      {busy ? "Working…" : children}
    </button>
  );
}

/** An icon with no word needs a name a screen reader can read, and a tooltip
    for everyone else. The old pause and stop buttons had neither. */
export function IconBtn({
  icon,
  label,
  onClick,
  small,
  disabled,
}: {
  icon: IconName;
  label: string;
  onClick?: () => void;
  small?: boolean;
  disabled?: boolean;
}) {
  return (
    <button className={`icon${small ? " small" : ""}`} onClick={onClick} aria-label={label} title={label} disabled={disabled}>
      <Icon name={icon} size={small ? 15 : 16} />
    </button>
  );
}

export function Badge({ tone, children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`badge${tone ? ` ${tone}` : ""}`}>{children}</span>;
}

export function Seg<T extends string>({
  value,
  onPick,
  options,
}: {
  value: T;
  onPick: (v: T) => void;
  options: { key: T; label: string; count?: number | string }[];
}) {
  return (
    <div className="seg" role="tablist">
      {options.map((o) => (
        <button
          key={o.key}
          role="tab"
          aria-selected={o.key === value}
          className={o.key === value ? "on" : ""}
          onClick={() => onPick(o.key)}
        >
          {o.label}
          {o.count !== undefined ? <span className="n">{o.count}</span> : null}
        </button>
      ))}
    </div>
  );
}

/* ── words the reader is allowed not to know ─────────────────────────────── */

/** The product invented vocabulary — a bet, a run, a drought, a requirement.
    Where the word earns its keep, it explains itself in place. A glossary is
    where a definition goes to be not read. */
export function Term({ children, means }: { children: ReactNode; means: string }) {
  return (
    <abbr className="term" title={means} style={{ textDecoration: "none" }}>
      {children}
    </abbr>
  );
}

/* ── structure ───────────────────────────────────────────────────────────── */

export function PageHead({
  crumbs,
  title,
  badge,
  lede,
  actions,
}: {
  crumbs?: ReactNode;
  title: ReactNode;
  badge?: ReactNode;
  lede?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="pagehead">
      <div className="ph">
        {crumbs ? <div className="crumbs">{crumbs}</div> : null}
        <div className="t">
          <h1>{title}</h1>
          {badge}
        </div>
        {lede ? <p className="lede">{lede}</p> : null}
      </div>
      {actions ? <div className="pacts">{actions}</div> : null}
    </div>
  );
}

export interface Vital {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: Tone;
}

/** The same four facts, in the same place, on every tab of a project. */
export function VitalsBar({ vitals }: { vitals: Vital[] }) {
  return (
    <div className="vitalsbar">
      {vitals.map((v) => (
        <div key={v.label}>
          <span className="l">{v.label}</span>
          <span className={`v${v.tone ? ` ${v.tone}` : ""}`}>{v.value}</span>
          {v.sub ? <span className="s">{v.sub}</span> : null}
        </div>
      ))}
    </div>
  );
}

/** One sentence, in the biggest type on the page, answering the question the
    page exists for. Everything under it is the evidence. */
export function Verdict({
  tone,
  icon,
  headline,
  children,
  actions,
}: {
  tone?: "warn" | "serious" | "critical";
  icon?: IconName;
  headline: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
}) {
  const color = tone === "critical" ? "var(--critical)" : tone === "serious" ? "var(--serious)" : tone === "warn" ? "var(--warning)" : "var(--good)";
  return (
    <div className={`verdict${tone ? ` ${tone}` : ""}`}>
      <span style={{ color }}>
        <Icon name={icon ?? (tone ? "alert" : "check")} size={24} strokeWidth={1.8} />
      </span>
      <div className="vbody">
        <h2>{headline}</h2>
        {children}
        {actions ? <div className="vacts">{actions}</div> : null}
      </div>
    </div>
  );
}

/** What happened, what happens if you do nothing, and the verb — in that
    order, every time, on every row that wants a decision. */
export function NeedRow({
  who,
  title,
  detail,
  consequence,
  consequenceTone,
  actions,
}: {
  who?: { name: string; team?: string | null };
  title: ReactNode;
  detail?: ReactNode;
  consequence?: ReactNode;
  consequenceTone?: "warn" | "serious" | "critical";
  actions?: ReactNode;
}) {
  return (
    <div className="needrow">
      {who ? <Avatar name={who.name} team={who.team} /> : null}
      <div className="nbody">
        <span className="nt">{title}</span>
        {detail ? <span className="nd">{detail}</span> : null}
        {consequence ? (
          <span className={`consequence${consequenceTone ? ` ${consequenceTone}` : ""}`}>
            <Icon name="clock" size={13} strokeWidth={2} />
            {consequence}
          </span>
        ) : null}
      </div>
      {actions ? <div className="nacts">{actions}</div> : null}
    </div>
  );
}

/** Proves it is quiet rather than asserting it: what is running, when
    something last happened, when the next thing will. */
export function EmptyState({
  icon,
  good,
  title,
  children,
  foot,
}: {
  icon?: IconName;
  good?: boolean;
  title: ReactNode;
  children?: ReactNode;
  foot?: ReactNode;
}) {
  return (
    <div className="emptystate">
      <span className={`em-ico${good ? " good" : ""}`}>
        <Icon name={icon ?? (good ? "check" : "inbox")} size={22} strokeWidth={2} />
      </span>
      <h3>{title}</h3>
      {children ? <p>{children}</p> : null}
      {foot ? <span className="em-foot">{foot}</span> : null}
    </div>
  );
}

/* ── charts ──────────────────────────────────────────────────────────────── */

/** A chart states its own question, window, unit and moment. A number a reader
    cannot reconcile against their own records is a number they will not
    believe, and this reader checks. */
export function ChartHead({
  question,
  meta,
  legend,
  onExport,
}: {
  question: ReactNode;
  meta: ReactNode;
  legend?: ReactNode;
  onExport?: () => void;
}) {
  return (
    <div className="charthead">
      <div className="ct">
        <h2>{question}</h2>
        <span className="cm">{meta}</span>
      </div>
      {legend ? <div className="chartlegend">{legend}</div> : null}
      {onExport ? <Btn small icon="download" onClick={onExport}>Export</Btn> : null}
    </div>
  );
}

export interface Bar {
  label: string;
  value: number;
  display: string;
  team?: string | null;
}

const SLOT = ["var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)"];

/** Horizontal bars with a stated scale, a share of the total and a direct
    label on every row — the label is what lets the chart survive a reader who
    cannot separate two of the hues. */
export function BarChart({ bars, total, unit }: { bars: Bar[]; total: string; unit?: string }) {
  const max = Math.max(...bars.map((b) => b.value), 1);
  const sum = bars.reduce((a, b) => a + b.value, 0) || 1;
  return (
    <>
      <div className="barchart">
        {bars.map((b, i) => (
          <div className="barrow" key={b.label}>
            <span className="bl">
              <i style={{ background: SLOT[i % SLOT.length] }} />
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{b.label}</span>
            </span>
            <span className="bt">
              <i style={{ width: `${Math.max((b.value / max) * 100, 1)}%`, background: SLOT[i % SLOT.length] }} />
            </span>
            <span className="bv">
              <b>{b.display}</b>
              <span>{Math.round((b.value / sum) * 100)}%</span>
            </span>
          </div>
        ))}
      </div>
      <div className="baraxis">
        <span>0</span>
        <span className="mid">{unit}</span>
        <span className="tot">{total} in total</span>
      </div>
    </>
  );
}

/* ── tables ──────────────────────────────────────────────────────────────── */

export interface Column<T> {
  key: string;
  head: string;
  num?: boolean;
  /** What the cell shows. */
  cell: (row: T) => ReactNode;
  /** What the CSV says, and what sorting compares. Falls back to the cell when
      the cell is already a plain string or number. */
  value?: (row: T) => string | number | null;
}

/** Sortable, and it exports. Export used to be built one table at a time,
    which is why seven of the app's twenty-two tables had it and the ones a
    reader most wants out — the deliverables, the plan, the review — did not.
    Building it into the table is what makes that true by construction. */
export function DataTable<T>({
  rows,
  columns,
  filename,
  empty,
  rowKey,
}: {
  rows: T[];
  columns: Column<T>[];
  filename?: string;
  empty?: ReactNode;
  rowKey: (row: T, i: number) => string;
}) {
  const [sort, setSort] = useState<{ key: string; desc: boolean } | null>(null);

  const valueOf = (c: Column<T>, r: T): string | number | null => {
    if (c.value) return c.value(r);
    const v = c.cell(r);
    return typeof v === "string" || typeof v === "number" ? v : null;
  };

  const shown = sort
    ? [...rows].sort((a, b) => {
        const c = columns.find((x) => x.key === sort.key)!;
        const av = valueOf(c, a);
        const bv = valueOf(c, b);
        if (av === bv) return 0;
        if (av === null) return 1;
        if (bv === null) return -1;
        const d = typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv));
        return sort.desc ? -d : d;
      })
    : rows;

  if (!rows.length && empty) return <>{empty}</>;

  return (
    <>
      {filename ? (
        <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: "var(--s2)" }}>
          <Btn
            small
            icon="download"
            onClick={() =>
              downloadCsv(
                filename,
                columns.map((c) => c.head),
                rows.map((r) => columns.map((c) => valueOf(c, r))),
              )
            }
          >
            Export
          </Btn>
        </div>
      ) : null}
      <table>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} className={c.num ? "num" : undefined}>
                <button
                  className="linkish"
                  style={{ font: "inherit", color: "inherit", minHeight: 0, padding: 0, background: "none", border: 0 }}
                  onClick={() => setSort((s) => (s && s.key === c.key ? { key: c.key, desc: !s.desc } : { key: c.key, desc: false }))}
                >
                  {c.head}
                  {sort && sort.key === c.key ? (sort.desc ? " ↓" : " ↑") : ""}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map((r, i) => (
            <tr key={rowKey(r, i)}>
              {columns.map((c) => (
                <td key={c.key} className={c.num ? "num" : undefined}>
                  {c.cell(r)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

/* ── theme ───────────────────────────────────────────────────────────────── */

export type Theme = "light" | "dark" | "auto";
const THEME_KEY = "yaaf_theme";

export function readTheme(): Theme {
  try {
    const v = localStorage.getItem(THEME_KEY);
    return v === "light" || v === "dark" ? v : "auto";
  } catch {
    return "auto";
  }
}

export function applyTheme(t: Theme) {
  const root = document.documentElement;
  if (t === "auto") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", t);
  try {
    if (t === "auto") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, t);
  } catch {
    /* a browser that refuses storage still gets the theme for this session */
  }
}

const THEMES: { key: Theme; label: string; icon: IconName }[] = [
  { key: "light", label: "Light", icon: "sun" },
  { key: "dark", label: "Dark", icon: "moon" },
  { key: "auto", label: "Auto", icon: "monitor" },
];

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(readTheme);
  useEffect(() => applyTheme(theme), [theme]);
  return (
    <div className="themetog" role="group" aria-label="Colour theme">
      {THEMES.map((t) => (
        <button
          key={t.key}
          className={theme === t.key ? "on" : ""}
          aria-pressed={theme === t.key}
          title={t.key === "auto" ? "Follow the system" : t.label}
          onClick={() => setTheme(t.key)}
        >
          <Icon name={t.icon} size={13} />
          {t.label}
        </button>
      ))}
    </div>
  );
}
