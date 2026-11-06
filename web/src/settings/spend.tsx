// Settings → Spend: what each provider, model and project cost the install,
// over time, in tokens and in dollars.
//
// Every other money screen is one project's: its budget, its meter, its card.
// The person who runs the box asks across them — which provider is costing me,
// which model, which project, and is it going up — and before this the only
// answer was a CLI report that opened the database directly and printed one
// project after another.
//
// The filters live in the URL, like the section does, so "weekly, by model,
// DeepSeek only" is a link somebody can be sent. They sit in one row above
// everything they scope, and the tiles, the chart and the tables all re-read
// the same answer, so no two numbers on the page can disagree.
//
// Dollars are only ever what a provider billed. A subscription's usage is
// tokens on a flat plan, and the page says so rather than pricing it.

import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { get, InstallSpend, SpendEvery, SpendGroupBy } from "../api";
import { usePoll } from "../hooks";
import { fmtUnit } from "../ui";
import { Column, DataTable, Seg } from "../ui/kit";
import { SectionHead } from "./kit";

type Measure = "usd" | "tokens";
type Group = InstallSpend["groups"][number];

const EVERY: { key: SpendEvery; label: string }[] = [
  { key: "day", label: "Daily" }, { key: "week", label: "Weekly" }, { key: "month", label: "Monthly" },
];
const BY: { key: SpendGroupBy; label: string }[] = [
  { key: "provider", label: "Provider" }, { key: "model", label: "Model" }, { key: "project", label: "Project" },
];
/** The design's five categorical slots, validated in both themes. A sixth
    series is never a generated hue: it folds into Other. */
const SLOTS = ["var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)"];
const OTHER = "__other__";
const OTHER_FILL = "color-mix(in srgb, var(--muted) 62%, var(--surface))";

const browserZone = () => {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; } catch { return "UTC"; }
};

/** A calendar date from the server, on the reader's calendar — never through
    `new Date("2026-11-06")`, which is midnight UTC and the day before here. */
function day(iso: string): Date {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function period(iso: string, every: SpendEvery, short = false): string {
  const at = day(iso);
  if (every === "month") {
    return at.toLocaleDateString(undefined, short ? { month: "short" } : { month: "long", year: "numeric" });
  }
  const md = at.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return every === "week" && !short ? `Week of ${md}` : md;
}

const valueOf = (m: { usd: number; tokens: number }, measure: Measure) => (measure === "usd" ? m.usd : m.tokens);
const show = (n: number, measure: Measure) => fmtUnit(measure === "usd" ? Math.round(n * 100) / 100 : n, measure);

/** Ticks on clean numbers: 1, 2 or 5 times a power of ten. */
function ticks(max: number): number[] {
  if (max <= 0) return [0];
  const rough = max / 4;
  const pow = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 5, 10].map((f) => f * pow).find((s) => s >= rough) ?? 10 * pow;
  const out: number[] = [];
  for (let v = 0; v <= max + step * 0.001; v += step) out.push(v);
  if (out[out.length - 1] < max) out.push(out[out.length - 1] + step);
  return out;
}

/** Colour follows the entity, never its rank: a series keeps the slot it was
    first given for as long as the page is open, so narrowing a filter never
    repaints the survivors. */
function useSlots(by: SpendGroupBy, keys: string[]): Map<string, number> {
  const memory = useRef(new Map<string, Map<string, number>>());
  return useMemo(() => {
    const seen = memory.current.get(by) ?? new Map<string, number>();
    memory.current.set(by, seen);
    const out = new Map<string, number>();
    const used = new Set<number>();
    for (const k of keys) {
      const s = seen.get(k);
      if (s !== undefined && !used.has(s)) { out.set(k, s); used.add(s); }
    }
    for (const k of keys) {
      if (out.has(k)) continue;
      let s = 0;
      while (used.has(s)) s++;
      out.set(k, s); used.add(s); seen.set(k, s);
    }
    return out;
  }, [by, keys.join("\u0000")]);
}

export function SpendSection() {
  const [params, setParams] = useSearchParams();
  const every = (EVERY.find((e) => e.key === params.get("every"))?.key ?? "day") as SpendEvery;
  const by = (BY.find((b) => b.key === params.get("by"))?.key ?? "provider") as SpendGroupBy;
  const chosen = {
    project: params.getAll("project"), provider: params.getAll("provider"), model: params.getAll("model"),
  };
  const paid = params.get("paid") === "usd" || params.get("paid") === "tokens" ? params.get("paid")! : "";
  const kind = params.get("kind") === "all" ? "all" : "model_usage";
  const since = params.get("since") ?? "";
  const until = params.get("until") ?? "";

  const set = (changes: Record<string, string | string[] | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) {
      next.delete(k);
      for (const one of v === null ? [] : Array.isArray(v) ? v : [v]) if (one) next.append(k, one);
    }
    setParams(next, { replace: true });
  };

  const query = new URLSearchParams({ every, group_by: by, tz: browserZone(), kind });
  if (paid) query.set("paid_in", paid);
  if (since) query.set("since", since);
  if (until) query.set("until", until);
  for (const k of ["project", "provider", "model"] as const) for (const v of chosen[k]) query.append(k, v);
  const url = `/observe/spend?${query}`;
  const { data: answer, error, at } = usePoll(
    () => get<InstallSpend>(url).then((d) => ({ url, d })), 60000, [url]);
  const data = answer?.d ?? null;
  // A refetch holds the last answer, dimmed, rather than flashing to empty.
  const stale = !!answer && answer.url !== url;

  // Dollars unless nothing was billed in dollars, until the reader says.
  const asked = params.get("measure");
  const measure: Measure = asked === "usd" || asked === "tokens" ? asked
    : data && data.totals.usd === 0 && data.totals.tokens > 0 ? "tokens" : "usd";

  const ranked = useMemo(() => [...(data?.groups ?? [])]
    .filter((g) => valueOf(g, measure) > 0)
    .sort((a, b) => valueOf(b, measure) - valueOf(a, measure)), [data, measure]);
  const named = ranked.slice(0, ranked.length > SLOTS.length ? SLOTS.length : ranked.length);
  const folded = ranked.slice(named.length);
  const slots = useSlots(by, named.map((g) => g.key));
  const fill = (key: string) => (key === OTHER ? OTHER_FILL : SLOTS[slots.get(key) ?? 0]);
  const series = [...named.map((g) => ({ key: g.key, label: g.label })),
    ...(folded.length ? [{ key: OTHER, label: folded.length === 1 ? folded[0].label : `${folded.length} others` }] : [])];
  const foldedKeys = new Set(folded.map((g) => g.key));

  return <>
    <SectionHead title="Spend"
      lede={<>What each provider, model and project cost, across every project, in tokens and in the dollars
        a provider billed. Each project’s budget and meter stay on its own Spend page.</>}>
      {data && <span>{data.since === data.until ? period(data.since, "day")
        : `${period(data.since, "day")} – ${period(data.until, "day")}`} · {data.tz}
        {at && ` · as of ${at.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}`}</span>}
    </SectionHead>

    <div className="sp-filters" role="group" aria-label="What to show">
      <Seg value={every} onPick={(v) => set({ every: v, since: null, until: null })} options={EVERY} />
      <label className="sp-date"><span>From</span>
        <input type="date" value={since || data?.since || ""} max={until || data?.until}
          onChange={(e) => set({ since: e.target.value || null })} /></label>
      <label className="sp-date"><span>To</span>
        <input type="date" value={until || data?.until || ""} min={since || data?.since}
          onChange={(e) => set({ until: e.target.value || null })} /></label>
      {(since || until) && <button type="button" className="linkish small"
        onClick={() => set({ since: null, until: null })}>Reset dates</button>}
    </div>
    <div className="sp-filters" role="group" aria-label="Filters">
      <span className="sp-k">Group by</span>
      <Seg value={by} onPick={(v) => set({ by: v })} options={BY} />
      <Pick label="Project" chosen={chosen.project} onChange={(v) => set({ project: v })}
        options={(data?.options.projects ?? []).map((p) => ({ key: p.slug, label: p.name,
          hint: p.spent ? undefined : "nothing in this window" }))} />
      <Pick label="Provider" chosen={chosen.provider} onChange={(v) => set({ provider: v })}
        options={(data?.options.providers ?? []).map((p) => ({ key: p.key, label: p.label }))} />
      <Pick label="Model" chosen={chosen.model} onChange={(v) => set({ model: v })}
        options={(data?.options.models ?? []).map((m) => ({ key: m, label: m }))} mono />
      <label className="sp-select"><span>Paid by</span>
        <select value={paid} onChange={(e) => set({ paid: e.target.value || null })}>
          <option value="">API keys and plans</option>
          <option value="usd">API keys (billed in dollars)</option>
          <option value="tokens">Subscriptions (tokens)</option>
        </select></label>
      <label className="sp-select"><span>Counting</span>
        <select value={kind} onChange={(e) => set({ kind: e.target.value === "all" ? "all" : null })}>
          <option value="model_usage">Model usage</option>
          <option value="all">Everything the budget counts</option>
        </select></label>
    </div>

    {error && <div className="error" role="alert">{error}</div>}
    {!data ? <div className="card muted">Loading…</div> : <div className={stale ? "sp-stale" : undefined}>
      <div className="kpi-row sp-kpis">
        <div><span>Billed in dollars</span><b>{show(data.totals.usd, "usd")}</b>
          <em>by API keys{kind === "all" ? ", plus purchases and infra" : ""}</em></div>
        <div><span>Tokens</span><b>{show(data.totals.tokens, "tokens")}</b>
          <em>{fmtUnit(data.totals.tokens_out, "tokens")} of them output</em></div>
        <div><span>On subscriptions</span><b>{show(data.totals.plan_tokens, "tokens")}</b>
          <em>{data.totals.tokens ? `${Math.round((data.totals.plan_tokens / data.totals.tokens) * 100)}% of tokens · ` : ""}a flat fee, not priced</em></div>
        <div><span>Runs</span><b>{data.totals.runs.toLocaleString()}</b>
          <em>{data.options.projects.filter((p) => p.spent).length} of {data.options.projects.length} projects spent</em></div>
      </div>
      <p className="help sp-note">{data.notes.dollars}</p>

      <div className="card sp-card">
        <div className="charthead">
          <div className="ct">
            <h2>{measure === "usd" ? "Dollars billed" : "Tokens used"} per {every}, by {by}</h2>
            <span className="cm">{measure === "usd" ? "US dollars, as providers billed them" : "tokens, cache included"}
              {" · "}{data.series.length} {every}{data.series.length === 1 ? "" : "s"}</span>
          </div>
          <Seg value={measure} onPick={(v) => set({ measure: v })}
            options={[{ key: "usd", label: "Dollars" }, { key: "tokens", label: "Tokens" }]} />
        </div>
        {ranked.length === 0
          ? <p className="help sp-empty">Nothing {measure === "usd" ? "was billed in dollars" : "was used"} in this
            window{chosen.project.length + chosen.provider.length + chosen.model.length || paid ? " with these filters" : ""}.
            {measure === "usd" && data.totals.tokens > 0 && <> Everything here ran on a subscription —{" "}
              <button type="button" className="linkish inline" onClick={() => set({ measure: "tokens" })}>show tokens</button>.</>}</p>
          : <>
            {series.length > 1 && <div className="chartlegend sp-legend" aria-label="Legend">
              {series.map((s) => <span key={s.key}><i className="sw" style={{ background: fill(s.key) }} />{s.label}</span>)}
            </div>}
            <Chart data={data} measure={measure} series={series} folded={foldedKeys} fill={fill} />
            <ChartNumbers data={data} measure={measure} series={series} folded={foldedKeys} />
          </>}
      </div>

      <h3 className="set-group-h sp-h">By {by}</h3>
      <GroupTable data={data} measure={measure} fill={(k) => (foldedKeys.has(k) ? OTHER_FILL : slots.has(k) ? fill(k) : "transparent")} />

      <h3 className="set-group-h sp-h">Every project, provider and model</h3>
      <DetailTable data={data} />
      <p className="help sp-note">{data.notes.tokens}</p>
    </div>}
  </>;
}

/** A checklist under a summary: several of a kind, or all of them. */
function Pick({ label, options, chosen, onChange, mono }: {
  label: string;
  options: { key: string; label: string; hint?: string }[];
  chosen: string[];
  onChange: (keys: string[]) => void;
  mono?: boolean;
}) {
  // A choice the window no longer holds stays listed, so it can be unchosen.
  const all: { key: string; label: string; hint?: string }[] = [
    ...options, ...chosen.filter((c) => !options.some((o) => o.key === c)).map((c) => ({ key: c, label: c }))];
  const nameOf = (k: string) => all.find((o) => o.key === k)?.label ?? k;
  const summary = !chosen.length ? "All" : chosen.length === 1 ? nameOf(chosen[0]) : `${chosen.length} chosen`;
  return (
    <details className={`sp-pick${chosen.length ? " on" : ""}`}>
      <summary><span className="sp-k">{label}</span> <b>{summary}</b></summary>
      <div className="sp-pick-menu" role="group" aria-label={label}>
        <button type="button" className="linkish small" disabled={!chosen.length} onClick={() => onChange([])}>
          All {label.toLowerCase()}s</button>
        {all.length === 0 && <span className="help">Nothing in this window.</span>}
        {all.map((o) => <label key={o.key} className={mono ? "mono" : undefined}>
          <input type="checkbox" checked={chosen.includes(o.key)}
            onChange={(e) => onChange(e.target.checked ? [...chosen, o.key] : chosen.filter((c) => c !== o.key))} />
          <span>{o.label}{o.hint && <em> · {o.hint}</em>}</span>
        </label>)}
      </div>
    </details>
  );
}

interface Series { key: string; label: string }

/** The element's width, kept current: the axis spaces its labels by it, and
    the readout stays inside it. */
function useWidth<T extends HTMLElement>(): [React.RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(() => setWidth(el.clientWidth));
    observer.observe(el);
    setWidth(el.clientWidth);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

/** Where the readout goes: beside the column it describes, on whichever side
    has room, and never past the plot's edge. */
const TIP = 220;
function tipLeft(center: number, plot: number): number {
  const width = Math.min(TIP, plot);
  if (center + 12 + width <= plot) return center + 12;
  if (center - 12 - width >= 0) return center - 12 - width;
  return Math.max(0, Math.min(center - width / 2, plot - width));
}

function cellsOf(bucket: InstallSpend["series"][number], measure: Measure, series: Series[], folded: Set<string>) {
  const out = new Map<string, number>(series.map((s) => [s.key, 0]));
  for (const [key, v] of Object.entries(bucket.by)) {
    const target = folded.has(key) ? OTHER : key;
    if (out.has(target)) out.set(target, (out.get(target) ?? 0) + valueOf(v, measure));
  }
  return out;
}

/** Stacked columns on one axis. The column's whole band is its hover and focus
    target, and the readout lists every series in it — the pointer never has to
    land on a two-pixel segment to get a number. */
function Chart({ data, measure, series, folded, fill }: {
  data: InstallSpend; measure: Measure; series: Series[]; folded: Set<string>; fill: (key: string) => string;
}) {
  const [hover, setHover] = useState<{ i: number; center: number } | null>(null);
  const [plotRef, plotWidth] = useWidth<HTMLDivElement>();
  const cols = data.series.map((b) => ({ bucket: b, cells: cellsOf(b, measure, series, folded) }));
  const totalOf = (cells: Map<string, number>) => [...cells.values()].reduce((a, v) => a + v, 0);
  const max = Math.max(...cols.map((c) => totalOf(c.cells)), 0);
  const marks = ticks(max);
  const top = marks[marks.length - 1] || 1;
  // One label per ~64px, so a phone gets fewer than a desk and none collide.
  const labelEvery = Math.max(1, Math.ceil(cols.length / Math.max(1, Math.floor((plotWidth || 640) / 64))));
  const shown = hover ? cols[hover.i] : null;

  return (
    <div className="sp-chart" onMouseLeave={() => setHover(null)}>
      <div className="sp-y" aria-hidden="true">
        {marks.map((m) => <span key={m} style={{ bottom: `${(m / top) * 100}%` }}>{show(m, measure)}</span>)}
      </div>
      <div className="sp-plot" ref={plotRef}>
        {marks.map((m) => <i key={m} className="sp-grid" style={{ bottom: `${(m / top) * 100}%` }} />)}
        <div className="sp-cols">
          {cols.map(({ bucket, cells }, i) => {
            const total = totalOf(cells);
            const name = period(bucket.start, data.every);
            const parts = series.filter((s) => (cells.get(s.key) ?? 0) > 0);
            return (
              <button type="button" key={bucket.start} className={`sp-col${hover?.i === i ? " on" : ""}`}
                aria-label={`${name}: ${show(total, measure)}${parts.length ? ` — ${parts.map((s) =>
                  `${s.label} ${show(cells.get(s.key) ?? 0, measure)}`).join(", ")}` : ""}`}
                onMouseEnter={(e) => setHover({ i, center: e.currentTarget.offsetLeft + e.currentTarget.offsetWidth / 2 })}
                onFocus={(e) => setHover({ i, center: e.currentTarget.offsetLeft + e.currentTarget.offsetWidth / 2 })}
                onBlur={() => setHover(null)}>
                <span className="sp-stack" style={{ height: `${(total / top) * 100}%` }}>
                  {series.map((s) => {
                    const v = cells.get(s.key) ?? 0;
                    return v > 0 ? <i key={s.key} style={{ flexGrow: v, background: fill(s.key) }} /> : null;
                  })}
                </span>
              </button>
            );
          })}
        </div>
        {shown && hover && <div className="sp-tip" role="status"
          style={{ left: tipLeft(hover.center, plotWidth), width: Math.min(TIP, plotWidth) }}>
          <div className="sp-tip-h">{period(shown.bucket.start, data.every)}</div>
          <div className="sp-tip-row total"><b>{show(totalOf(shown.cells), measure)}</b><span>in all</span></div>
          {series.filter((s) => (shown.cells.get(s.key) ?? 0) > 0).map((s) => (
            <div className="sp-tip-row" key={s.key}><i style={{ background: fill(s.key) }} />
              <b>{show(shown.cells.get(s.key) ?? 0, measure)}</b><span>{s.label}</span></div>))}
          {measure === "usd" && shown.bucket.plan_tokens > 0 && <div className="sp-tip-foot">
            plus {fmtUnit(shown.bucket.plan_tokens, "tokens")} tokens on subscriptions</div>}
        </div>}
      </div>
      <div className="sp-x" aria-hidden="true">
        {cols.map(({ bucket }, i) => <span key={bucket.start}>
          {i % labelEvery === 0 ? period(bucket.start, data.every, true) : ""}</span>)}
      </div>
    </div>
  );
}

/** The chart as a table: every value it draws, readable without a pointer. */
function ChartNumbers({ data, measure, series, folded }: {
  data: InstallSpend; measure: Measure; series: Series[]; folded: Set<string>;
}) {
  type Row = { start: string; cells: Map<string, number>; total: number };
  const rows: Row[] = data.series.map((b) => {
    const cells = cellsOf(b, measure, series, folded);
    return { start: b.start, cells, total: [...cells.values()].reduce((a, v) => a + v, 0) };
  });
  const columns: Column<Row>[] = [
    { key: "period", head: data.every === "day" ? "Day" : data.every === "week" ? "Week of" : "Month",
      cell: (r) => period(r.start, data.every), value: (r) => r.start },
    ...series.map((s): Column<Row> => ({ key: s.key, head: s.label, num: true,
      cell: (r) => show(r.cells.get(s.key) ?? 0, measure), value: (r) => r.cells.get(s.key) ?? 0 })),
    { key: "total", head: "Total", num: true, cell: (r) => <b>{show(r.total, measure)}</b>, value: (r) => r.total },
  ];
  return (
    <details className="sp-numbers">
      <summary>The numbers behind the chart</summary>
      <div className="sp-scroll">
        <DataTable rows={rows} columns={columns} rowKey={(r) => r.start}
          filename={`spend-per-${data.every}-by-${data.group_by}-${measure}.csv`} />
      </div>
    </details>
  );
}

function GroupTable({ data, measure, fill }: { data: InstallSpend; measure: Measure; fill: (key: string) => string }) {
  const total = valueOf(data.totals, measure) || 1;
  const tok = (n: number) => fmtUnit(n, "tokens");
  const columns: Column<Group>[] = [
    { key: "label", head: BY.find((b) => b.key === data.group_by)!.label,
      cell: (g) => <span className="sp-name"><i className="sw" style={{ background: fill(g.key) }} />
        {data.group_by === "project"
          ? <Link to={`/projects/${g.key}/details/spend`}>{g.label}</Link>
          : <span className={data.group_by === "model" ? "mono" : undefined}>{g.label}</span>}</span>,
      value: (g) => g.label },
    { key: "runs", head: "Runs", num: true, cell: (g) => g.runs.toLocaleString(), value: (g) => g.runs },
    { key: "in", head: "Input", num: true, cell: (g) => tok(g.tokens_in), value: (g) => g.tokens_in },
    { key: "cw", head: "Cache write", num: true, cell: (g) => tok(g.cache_creation), value: (g) => g.cache_creation },
    { key: "cr", head: "Cache read", num: true, cell: (g) => tok(g.cache_read), value: (g) => g.cache_read },
    { key: "out", head: "Output", num: true, cell: (g) => tok(g.tokens_out), value: (g) => g.tokens_out },
    { key: "tokens", head: "Tokens", num: true, cell: (g) => tok(g.tokens), value: (g) => g.tokens },
    { key: "plan", head: "On a plan", num: true, cell: (g) => (g.plan_tokens ? tok(g.plan_tokens) : "—"),
      value: (g) => g.plan_tokens },
    { key: "usd", head: "Billed", num: true, cell: (g) => <b>{show(g.usd, "usd")}</b>, value: (g) => g.usd },
    { key: "share", head: "Share", num: true,
      cell: (g) => `${Math.round((valueOf(g, measure) / total) * 100)}%`, value: (g) => valueOf(g, measure) / total },
  ];
  return <div className="card sp-table"><div className="sp-scroll">
    <DataTable rows={[...data.groups].sort((a, b) => valueOf(b, measure) - valueOf(a, measure))}
      columns={columns} rowKey={(g) => g.key}
      filename={`spend-by-${data.group_by}-${data.since}-to-${data.until}.csv`}
      empty={<p className="help">Nothing in this window.</p>} />
  </div></div>;
}

function DetailTable({ data }: { data: InstallSpend }) {
  type Row = InstallSpend["rows"][number];
  const tok = (n: number) => fmtUnit(n, "tokens");
  const columns: Column<Row>[] = [
    { key: "project", head: "Project", cell: (r) => <Link to={`/projects/${r.project_id}/details/spend`}>{r.project}</Link>,
      value: (r) => r.project },
    { key: "provider", head: "Provider", cell: (r) => r.provider_label, value: (r) => r.provider_label },
    { key: "model", head: "Model", cell: (r) => <span className="mono">{r.model}</span>, value: (r) => r.model },
    { key: "runs", head: "Runs", num: true, cell: (r) => r.runs.toLocaleString(), value: (r) => r.runs },
    { key: "tokens", head: "Tokens", num: true, cell: (r) => tok(r.tokens), value: (r) => r.tokens },
    { key: "plan", head: "On a plan", num: true, cell: (r) => (r.plan_tokens ? tok(r.plan_tokens) : "—"),
      value: (r) => r.plan_tokens },
    { key: "usd", head: "Billed", num: true, cell: (r) => <b>{show(r.usd, "usd")}</b>, value: (r) => r.usd },
  ];
  if (!data.rows.length) return <p className="help">Nothing in this window.</p>;
  return <div className="card sp-table"><div className="sp-scroll">
    <DataTable rows={data.rows} columns={columns} rowKey={(r) => `${r.project_id}/${r.provider}/${r.model}`}
      filename={`spend-detail-${data.since}-to-${data.until}.csv`} />
  </div></div>;
}

/** For the rail: this month's bill, as a state rather than a description. */
export function spendNote(d: InstallSpend | null): { text: string; tone: "" } {
  if (!d) return { text: "…", tone: "" };
  const month = day(d.until).toLocaleDateString(undefined, { month: "long" });
  if (d.totals.usd > 0) return { text: `${fmtUnit(Math.round(d.totals.usd * 100) / 100, "usd")} billed in ${month}`, tone: "" };
  if (d.totals.tokens > 0) return { text: `${fmtUnit(d.totals.tokens, "tokens")} tokens in ${month}`, tone: "" };
  return { text: `Nothing spent in ${month}`, tone: "" };
}

export function monthToDateUrl(): string {
  const now = new Date();
  const first = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-01`;
  return `/observe/spend?every=month&since=${first}&tz=${encodeURIComponent(browserZone())}`;
}

