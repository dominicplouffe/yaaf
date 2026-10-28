// Settings → Models: the install's choices about models that are not any one
// provider's — where bare classes resolve, whether lists and prices are kept
// current, and the evidence a lineup is picked from.
//
// Each connected provider's lineup is edited on its own card under Providers,
// next to its keys and its readiness, because that is where the consequence of
// a change is visible. What stays here is the comparison across providers (the
// check scoreboard), the daily upkeep, and — folded away under one disclosure —
// the lineups of the providers this install holds no key for, which matter
// only to whoever is about to connect one.

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { get, post, put } from "./api";
import { CheckChip, CheckRow, fmtUsd } from "./providerCheck";
import { forgetModels, ModelSelect, useModels, useProviders } from "./providers";
import { FIX_LINK, useReadiness } from "./readiness";
import { fmtAgo } from "./ui";
import { EmptyState } from "./ui/kit";

interface LineupCellInfo {
  model: string | null; shipped: string | null; source: "install" | "shipped" | null;
  served?: boolean | null; check?: { status: string; summary: string; at: string | null } | null;
}
interface LineupRow { provider: string; label: string; classes: Record<string, LineupCellInfo>;
  served?: { at: string; count: number } | null }
interface UpkeepRun {
  at: string;
  credentials: Record<string, { name: string; models?: number; error?: string }>;
  prices: { changed: string[]; checked: number } | { error: string };
}
interface ModelsSettings {
  default_provider: string; lineups: LineupRow[]; ceilings: Record<string, number>;
  app_model: string; app_harness: string | null;
  upkeep?: { enabled: boolean; last: UpkeepRun | null };
}
type Saved = ModelsSettings & { unrunnable?: string[] };

const CLASS_NAMES: [string, string][] = [["frontier", "Frontier"], ["balanced", "Balanced"], ["fast", "Fast"]];

/** What the last check and the key's list say about one lineup entry. */
function Evidence({ cell }: { cell: LineupCellInfo }) {
  if (!cell.model) return null;
  return <span className="evidence">
    {cell.served === true && <span className="good" title="The key's own model list has it">✓ listed</span>}
    {cell.served === false && <span className="chip warn" title="The key's own model list does not have it">not listed</span>}
    {cell.check && <span title={cell.check.summary}><CheckChip status={cell.check.status} /></span>}
  </span>;
}

/** One class on one provider: what it names, and the door to change it. */
function LineupCell({ provider, cls, label, cell, options, onSaved }: {
  provider: string; cls: string; label: string; cell: LineupCellInfo;
  options: string[]; onSaved: (said: Saved) => void;
}) {
  const [value, setValue] = useState(cell.model ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { setValue(cell.model ?? ""); }, [cell.model]);
  async function save(model: string | null) {
    setBusy(true); setError("");
    try {
      onSaved(await put<Saved>("/admin/settings/models/lineup", { provider, class: cls, model }));
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const listId = `lineup-${provider}-${cls}`;
  return <div className="lineup-cell">
    <label className="field">{label}
      <input list={listId} value={value} disabled={busy} maxLength={128}
        placeholder={`none set — a ${label} seat on this provider is refused`}
        onChange={(e) => setValue(e.target.value)} />
      <datalist id={listId}>{options.map((o) => <option key={o} value={o} />)}</datalist>
    </label>
    <span className="help">
      {cell.source === "install" ? `this install's${cell.shipped ? ` (shipped: ${cell.shipped})` : ""}`
        : cell.source === "shipped" ? "shipped with yaaf" : "not set"}
    </span>{" "}<Evidence cell={cell} />{" "}
    <button type="button" className="small" disabled={busy || !value.trim() || value.trim() === cell.model}
      onClick={() => save(value.trim())}>Save</button>{" "}
    {cell.source === "install" && <button type="button" className="small" disabled={busy}
      onClick={() => save(null)}>{cell.shipped ? "Back to shipped" : "Clear"}</button>}
    {error && <p className="error" role="alert">{error}</p>}
  </div>;
}

function OtherLineup({ row, catalog, onSaved }: { row: LineupRow; catalog: string[]; onSaved: (said: Saved) => void }) {
  const gaps = CLASS_NAMES.filter(([c]) => !row.classes[c]?.model).length;
  return <details className="other-lineup">
    <summary><strong>{row.label}</strong>{" "}
      <span className="help">{gaps === 3 ? "no lineup — every class would be chosen when you connect it"
        : gaps ? `${gaps} class${gaps === 1 ? "" : "es"} unset`
        : CLASS_NAMES.map(([c]) => row.classes[c]?.model).join(" · ")}</span>
    </summary>
    <div className="row" style={{ flexWrap: "wrap", gap: 12 }}>
      {CLASS_NAMES.map(([cls, label]) => <LineupCell key={cls} provider={row.provider} cls={cls}
        label={label} cell={row.classes[cls] ?? { model: null, shipped: null, source: null }}
        options={catalog} onSaved={onSaved} />)}
    </div>
  </details>;
}

function UpkeepCard({ upkeep, onSaved }: { upkeep: ModelsSettings["upkeep"]; onSaved: (said: Saved) => void }) {
  const [busy, setBusy] = useState<"toggle" | "run" | null>(null);
  const [error, setError] = useState("");
  const [last, setLast] = useState<UpkeepRun | null>(upkeep?.last ?? null);
  useEffect(() => { setLast(upkeep?.last ?? null); }, [upkeep?.last]);
  async function toggle(enabled: boolean) {
    setBusy("toggle"); setError("");
    try { onSaved(await put<Saved>("/admin/settings/models/upkeep", { enabled })); }
    catch (e) { setError((e as Error).message); } finally { setBusy(null); }
  }
  async function run() {
    setBusy("run"); setError("");
    try {
      setLast(await post<UpkeepRun>("/admin/settings/models/upkeep/run"));
      forgetModels();
    } catch (e) { setError((e as Error).message); } finally { setBusy(null); }
  }
  const keys = Object.values(last?.credentials ?? {});
  const failed = keys.filter((k) => k.error);
  return <div className="card">
    <label className="check-line strong">
      <input type="checkbox" checked={!!upkeep?.enabled} disabled={busy !== null}
        onChange={(e) => toggle(e.target.checked)} />
      Keep models and prices current
    </label>
    <p className="help">Once a day: re-read each key&apos;s model list, so a seat on a model the
      provider retired is named on its project&apos;s health checklist before it fails, and refresh
      prices that came from the price feed. Prices you typed, and the vendor-checked ones shipped
      with yaaf, are never touched.</p>
    {last ? <div className="upkeep-last">
      <p><b>Last ran {fmtAgo(last.at)}.</b>{" "}
        {keys.length === 0 ? "No key to read." : `Read ${keys.length - failed.length} of ${keys.length} key${keys.length === 1 ? "" : "s"}.`}{" "}
        {"error" in last.prices ? <span className="warn">Prices were not refreshed: {last.prices.error}</span>
          : last.prices.changed.length ? `Prices: ${last.prices.changed.length} changed (${last.prices.changed.slice(0, 4).join(", ")}${last.prices.changed.length > 4 ? "…" : ""}) of ${last.prices.checked} checked.`
          : `Prices: none changed of ${last.prices.checked} checked.`}</p>
      {failed.length > 0 && <ul className="help">{failed.map((k) => <li key={k.name}><b>{k.name}</b>: {k.error}</li>)}</ul>}
    </div> : <p className="help">It has not run yet.</p>}
    {error && <p className="error" role="alert">{error}</p>}
    <div className="actions" style={{ marginTop: 8 }}>
      <button type="button" className="small" disabled={busy !== null} onClick={run}>
        {busy === "run" ? "Running…" : "Run now"}</button>
      <span className="help">Runs whether or not it is switched on. Reading a key&apos;s list is free.</span>
    </div>
  </div>;
}

function CompareModels({ rows, labelOf }: { rows: CheckRow[] | null; labelOf: (p: string) => string }) {
  if (rows === null) return <div className="card muted">Loading…</div>;
  if (!rows.length) return <EmptyState icon="chart" title="No model has been checked yet.">
      Run a check from a provider&apos;s card under <Link to="/settings?section=providers">Providers</Link>.
      It grades each class&apos;s model on short probes and five small agent tasks, and the results line
      up here side by side — the evidence a lineup is picked from.
    </EmptyState>;
  const probes = (r: CheckRow) => {
    const graded = r.outcomes.filter((o) => o.kind === "probe" && o.ok !== null);
    return `${graded.filter((o) => o.ok).length}/${graded.length}`;
  };
  const tasks = (r: CheckRow) => {
    const graded = r.outcomes.filter((o) => o.kind === "task" && o.ok !== null);
    return graded.length ? `${graded.filter((o) => o.ok).length}/${graded.length}` : "—";
  };
  return <div className="card compare">
    <div className="table-scroll"><table className="compare-table">
      <thead><tr><th>Model</th><th>Provider</th><th>Result</th><th className="num">Probes</th>
        <th className="num">Tasks</th><th className="num">Calls / task</th><th className="num">Cost / task</th>
        <th className="num">Seconds / task</th><th>When</th></tr></thead>
      <tbody>{rows.map((r) => <tr key={r.id}>
        <td><code>{r.model}</code></td>
        <td>{labelOf(r.provider ?? "")}</td>
        <td><CheckChip status={r.status} /></td>
        <td className="num">{probes(r)}</td>
        <td className="num">{tasks(r)}</td>
        <td className="num">{r.task_calls ?? "—"}</td>
        <td className="num">{fmtUsd(r.task_cost_usd)}</td>
        <td className="num">{r.task_seconds ?? "—"}</td>
        <td>{r.finished_at ? fmtAgo(r.finished_at) : "—"}</td>
      </tr>)}</tbody>
    </table></div>
    <p className="help">Fewer calls and seconds per task is a model that gets to the point; the
      tasks are the same five on every model, so the columns compare.</p>
  </div>;
}

export function ModelsSection() {
  const [data, setData] = useState<ModelsSettings | null>(null);
  const [error, setError] = useState("");
  const [stranded, setStranded] = useState<string[]>([]);
  const [nonce, setNonce] = useState(0);
  const [scoreboard, setScoreboard] = useState<CheckRow[] | null>(null);
  const catalog = useModels(nonce);
  const registry = useProviders();
  const { data: readiness } = useReadiness(nonce);
  const [appModel, setAppModel] = useState("");
  const [appHarness, setAppHarness] = useState("");
  const [harnesses, setHarnesses] = useState<{ name: string; label: string }[]>([]);
  const [appBusy, setAppBusy] = useState(false);
  useEffect(() => {
    get<ModelsSettings>("/admin/settings/models").then((said) => {
      setData(said); setAppModel(said.app_model ?? ""); setAppHarness(said.app_harness ?? "");
    }).catch((e) => setError(e.message));
    get<CheckRow[]>("/admin/settings/provider-checks").then(setScoreboard).catch(() => setScoreboard([]));
    get<{ name: string; label: string }[]>("/admin/harnesses")
      .then(setHarnesses).catch(() => setHarnesses([]));
  }, []);
  async function saveApp() {
    setAppBusy(true); setError("");
    try {
      saved(await put<Saved>("/admin/settings/models/app-model", { model: appModel || null }));
      await put<Saved>("/admin/settings/models/app-harness", { harness: appHarness || null })
        .catch(() => undefined);
    } catch (e) { setError((e as Error).message); } finally { setAppBusy(false); }
  }
  const saved = useCallback((said: Saved) => {
    setData(said); setStranded(said.unrunnable ?? []); setNonce((n) => n + 1);
  }, []);
  async function setDefault(provider: string) {
    setError("");
    try { saved(await put<Saved>("/admin/settings/models/default-provider", { provider })); forgetModels(); }
    catch (e) { setError((e as Error).message); }
  }
  if (!data) return error ? <p className="error">{error}</p> : <div className="card muted">Loading…</div>;
  const connected = new Set((readiness?.providers ?? []).map((p) => p.provider));
  const labelOf = (name: string) => data.lineups.find((r) => r.provider === name)?.label ?? name;
  const mine = data.lineups.filter((r) => connected.has(r.provider));
  const others = data.lineups.filter((r) => !connected.has(r.provider));
  return <>
    <h2 style={{ marginTop: 0 }}>Models</h2>
    <p className="help">A role names a class of model — <b>Frontier</b>, <b>Balanced</b> or{" "}
      <b>Fast</b> — and the provider a project pays says whose model that is. A provider with
      nothing set for a class refuses that class&apos;s seats rather than quietly running another
      vendor&apos;s model. Each connected provider&apos;s lineup is changed on its card under{" "}
      <Link to="/settings?section=providers">Providers</Link>.</p>
    {error && <p className="error" role="alert">{error}</p>}
    {stranded.length > 0 && <div className="notice-box" role="alert">
      <b>That change leaves seats unable to run:</b>
      <ul>{stranded.map((line) => <li key={line}>{line}</li>)}</ul></div>}

    <div className="card">
      <h3 style={{ marginTop: 0 }}>The app&apos;s model</h3>
      <p className="help">What a seat runs on when neither it nor its role names a model — the
        role&apos;s charter is the effort, and the app&apos;s model is the default beneath it.
        Empty goes back to the shipped class, which resolves on the install&apos;s provider.</p>
      <div className="hrow" style={{ alignItems: "end", gap: 12 }}>
        <label className="field" style={{ maxWidth: 460, flex: 1 }}>Default model
          <ModelSelect value={appModel} onChange={setAppModel} info={catalog} registry={registry}
            providersShown={(readiness?.providers ?? []).map((p) => p.provider)}
            empty="The shipped class (the install's provider resolves it)"
            disabled={appBusy} />
        </label>
        <label className="field" style={{ maxWidth: 280 }}>Default harness
          <select value={appHarness} disabled={appBusy} onChange={(e) => setAppHarness(e.target.value)}>
            <option value="">The paying credential&apos;s</option>
            {harnesses.map((h) => <option key={h.name} value={h.name}>{h.label}</option>)}
          </select>
        </label>
        <button className="primary" disabled={appBusy} onClick={saveApp}
          style={{ minHeight: 34 }}>Save</button>
      </div>
    </div>

    <div className="card">
      <label className="field" style={{ maxWidth: 360, marginTop: 0 }}>Default provider
        <select value={data.default_provider} onChange={(e) => setDefault(e.target.value)}>
          {mine.length > 0 && <optgroup label="Connected">
            {mine.map((r) => <option key={r.provider} value={r.provider}>{r.label}</option>)}</optgroup>}
          <optgroup label={mine.length ? "No key here" : "Every provider"}>
            {others.map((r) => <option key={r.provider} value={r.provider}>{r.label}</option>)}</optgroup>
        </select></label>
      <p className="help">Where a project that pays nobody yet resolves its classes, and what the
        Founder&apos;s Frontier model means before it has a key.
        {readiness && !readiness.install.default_connected && readiness.providers.length > 0 &&
          <span className="warn"> The default has no key on this install — pick a connected one.</span>}</p>
    </div>

    <h3>Staying current</h3>
    <UpkeepCard upkeep={data.upkeep} onSaved={saved} />

    <h3>Compare models</h3>
    <CompareModels rows={scoreboard} labelOf={labelOf} />

    {mine.length > 0 && <>
      <h3>Your providers&apos; lineups</h3>
      <div className="card">
        <div className="table-scroll"><table className="lineup-table">
          <thead><tr><th>Provider</th>{CLASS_NAMES.map(([c, l]) => <th key={c}>{l}</th>)}<th /></tr></thead>
          <tbody>{mine.map((r) => <tr key={r.provider}>
            <td><b>{r.label}</b>{data.default_provider === r.provider && <> <span className="chip accent">Default</span></>}</td>
            {CLASS_NAMES.map(([c]) => {
              const cell = r.classes[c];
              return <td key={c}>{cell?.model ? <code>{cell.model}</code> : <span className="critical-ink">none set</span>}
                {cell && <div><Evidence cell={cell} /></div>}</td>;
            })}
            <td><Link to={FIX_LINK.provider(r.provider)}>Change on its card</Link></td>
          </tr>)}</tbody>
        </table></div>
      </div>
    </>}

    <details className="card other-providers">
      <summary><b>Other providers</b> — {others.length} this install holds no key for</summary>
      <p className="help">Their lineups, ready for the day you connect one. Connecting a provider
        is under <Link to="/settings?section=providers&connect=1">Providers → Connect a provider</Link>,
        which also offers each class&apos;s model from the key&apos;s own list.</p>
      {others.map((row) => <OtherLineup key={row.provider} row={row}
        catalog={(catalog?.models ?? []).filter((m) => m.provider === row.provider).map((m) => m.id)}
        onSaved={saved} />)}
    </details>
  </>;
}
