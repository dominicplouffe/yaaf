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
import { useReadiness } from "./readiness";
import { fmtAgo } from "./ui";
import { EmptyState } from "./ui/kit";
import { holdDecision } from "./undo";
import { DefRow, Group, SectionHead, Toggle } from "./settings/kit";

interface LineupCellInfo {
  model: string | null; shipped: string | null; source: "install" | "shipped" | "auto" | null;
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
        : cell.source === "shipped" ? "shipped with yaaf"
        : cell.source === "auto" ? "picked by yaaf from the key's list — by price, newest" : "not set"}
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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [last, setLast] = useState<UpkeepRun | null>(upkeep?.last ?? null);
  const [on, setOn] = useState(!!upkeep?.enabled);
  useEffect(() => { setLast(upkeep?.last ?? null); }, [upkeep?.last]);
  useEffect(() => { setOn(!!upkeep?.enabled); }, [upkeep?.enabled]);
  // A switch shows its new state at once and is held for a few seconds with
  // Undo before it is sent (undo.tsx), like a decision.
  function toggle(enabled: boolean) {
    setOn(enabled); setError("");
    holdDecision({
      key: "settings:upkeep", method: "PUT", path: "/admin/settings/models/upkeep", body: { enabled },
      label: enabled ? "Models and prices will be kept current." : "Models and prices will no longer be kept current.",
      after: (said) => onSaved(said as Saved),
      onUndo: () => setOn(!enabled),
    });
  }
  async function run() {
    setBusy(true); setError("");
    try {
      setLast(await post<UpkeepRun>("/admin/settings/models/upkeep/run"));
      forgetModels();
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const keys = Object.values(last?.credentials ?? {});
  const failed = keys.filter((k) => k.error);
  const prices = last && !("error" in last.prices) ? last.prices : null;
  return <div className="set-card">
    <div className="set-row" style={{ gridTemplateColumns: "minmax(0, 1fr) auto auto" }}>
      <Toggle id="upkeep" checked={on} onChange={toggle} disabled={busy}
        label="Re-read model lists and feed prices daily"
        sub="Prices you typed, and the vendor-checked ones yaaf ships, are never touched. Reading a key’s list is free." />
      <div className="set-row-val" style={{ textAlign: "right" }}>{last
        ? <>Last ran <b>{fmtAgo(last.at)}</b> · read {keys.length - failed.length} of {keys.length} key{keys.length === 1 ? "" : "s"}
            <br />{"error" in last.prices ? <span className="warn">Prices were not refreshed: {last.prices.error}</span>
              : prices && prices.changed.length ? <span title={prices.changed.join(", ")}>{prices.changed.length} price{prices.changed.length === 1 ? "" : "s"} changed of {prices.checked} checked</span>
              : prices ? `No price changed of ${prices.checked} checked` : null}</>
        : "It has not run yet."}</div>
      <div className="set-row-act"><button type="button" className="small" disabled={busy} onClick={run}>
        {busy ? "Running…" : "Run now"}</button></div>
    </div>
    {failed.length > 0 && <ul className="help" style={{ margin: 0, padding: "0 18px 12px 36px" }}>
      {failed.map((k) => <li key={k.name}><b>{k.name}</b>: {k.error}</li>)}</ul>}
    {error && <p className="error" role="alert" style={{ padding: "0 18px 12px" }}>{error}</p>}
  </div>;
}

function CompareModels({ rows, labelOf }: { rows: CheckRow[] | null; labelOf: (p: string) => string }) {
  if (rows === null) return <div className="card muted">Loading…</div>;
  if (!rows.length) return <EmptyState icon="chart" title="No model has been checked yet.">
      Run a check from a provider&apos;s card under <Link to="/settings?section=providers">Providers</Link>;
      the graded results line up here side by side.
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

export function ModelsSection({ onChanged }: { onChanged?: () => void } = {}) {
  const [data, setData] = useState<ModelsSettings | null>(null);
  const [error, setError] = useState("");
  const [stranded, setStranded] = useState<string[]>([]);
  const [nonce, setNonce] = useState(0);
  const [scoreboard, setScoreboard] = useState<CheckRow[] | null>(null);
  const catalog = useModels(nonce);
  const registry = useProviders();
  const { data: readiness } = useReadiness(nonce);
  const [editing, setEditing] = useState<"provider" | "model" | "harness" | null>(null);
  const [draft, setDraft] = useState("");
  const [harnesses, setHarnesses] = useState<{ name: string; label: string }[]>([]);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    get<ModelsSettings>("/admin/settings/models").then(setData).catch((e) => setError(e.message));
    get<CheckRow[]>("/admin/settings/provider-checks").then(setScoreboard).catch(() => setScoreboard([]));
    get<{ name: string; label: string }[]>("/admin/harnesses")
      .then(setHarnesses).catch(() => setHarnesses([]));
  }, []);
  const saved = useCallback((said: Saved) => {
    setData(said); setStranded(said.unrunnable ?? []); setNonce((n) => n + 1); onChanged?.();
  }, [onChanged]);
  const edit = (what: "provider" | "model" | "harness", value: string) => {
    setEditing(what); setDraft(value); setError("");
  };
  // One way to save a default: Change, then Save. The provider used to save the
  // moment its dropdown moved, beside a card that needed a press, and a failed
  // harness save was dropped without a word.
  async function save() {
    setBusy(true); setError("");
    try {
      if (editing === "provider") {
        saved(await put<Saved>("/admin/settings/models/default-provider", { provider: draft }));
        forgetModels();
      } else if (editing === "model") {
        saved(await put<Saved>("/admin/settings/models/app-model", { model: draft || null }));
      } else if (editing === "harness") {
        saved(await put<Saved>("/admin/settings/models/app-harness", { harness: draft || null }));
      }
      setEditing(null);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  if (!data) return error ? <p className="error">{error}</p> : <div className="card muted">Loading…</div>;
  const connected = new Set((readiness?.providers ?? []).map((p) => p.provider));
  const labelOf = (name: string) => data.lineups.find((r) => r.provider === name)?.label ?? name;
  const mine = data.lineups.filter((r) => connected.has(r.provider));
  const others = data.lineups.filter((r) => !connected.has(r.provider));
  const cls = data.app_model?.startsWith("@") ? data.app_model.slice(1) : null;
  const resolved = cls ? data.lineups.find((r) => r.provider === data.default_provider)?.classes[cls]?.model : null;
  const harnessLabel = harnesses.find((h) => h.name === data.app_harness)?.label;
  const buttons = <>
    <button type="button" className="small" disabled={busy} onClick={() => setEditing(null)}>Cancel</button>
    <button type="button" className="small primary" disabled={busy} onClick={save}>{busy ? "Saving…" : "Save"}</button>
  </>;
  return <>
    <SectionHead title="Models"
      lede={<>The defaults every project starts from. Each provider&apos;s Frontier, Balanced and Fast are set
        on its card under <Link to="/settings?section=providers">Providers</Link>.</>} />
    {error && <p className="error" role="alert">{error}</p>}
    {stranded.length > 0 && <div className="notice-box" role="alert">
      <b>That change leaves seats unable to run:</b>
      <ul>{stranded.map((line) => <li key={line}>{line}</li>)}</ul></div>}

    <Group title="Defaults">
      <div className="set-card">
        <DefRow label="Default provider" editing={editing === "provider"}
          onEdit={() => edit("provider", data.default_provider)}
          value={<><b>{labelOf(data.default_provider)}</b> · where a project that pays nobody yet runs, and the
            Founder before it has a key{readiness && !readiness.install.default_connected && readiness.providers.length > 0
              && <span className="warn"> · it has no key here: pick a connected one</span>}</>}
          editor={<select aria-label="Default provider" value={draft} onChange={(e) => setDraft(e.target.value)} disabled={busy}>
            {mine.length > 0 && <optgroup label="Connected">
              {mine.map((r) => <option key={r.provider} value={r.provider}>{r.label}</option>)}</optgroup>}
            <optgroup label={mine.length ? "No key here" : "Every provider"}>
              {others.map((r) => <option key={r.provider} value={r.provider}>{r.label}</option>)}</optgroup>
          </select>}
          actions={buttons} />
        <DefRow label="Default model" editing={editing === "model"}
          onEdit={() => edit("model", data.app_model ?? "")}
          value={<><b>{cls ? cls.charAt(0).toUpperCase() + cls.slice(1) : data.app_model || "The shipped class"}</b>
            {resolved && <> → <code>{resolved}</code></>} · for a seat whose role names no model</>}
          editor={<ModelSelect value={draft} onChange={setDraft} info={catalog} registry={registry}
            providersShown={(readiness?.providers ?? []).map((p) => p.provider)}
            empty="The shipped class (the install's provider resolves it)" disabled={busy} />}
          actions={buttons} />
        <DefRow label="Default harness" editing={editing === "harness"}
          onEdit={() => edit("harness", data.app_harness ?? "")}
          value={<><b>{harnessLabel ?? "The paying key’s own"}</b> · what drives a seat whose role names none</>}
          editor={<select aria-label="Default harness" value={draft} onChange={(e) => setDraft(e.target.value)} disabled={busy}>
            <option value="">The paying key&apos;s own</option>
            {harnesses.map((h) => <option key={h.name} value={h.name}>{h.label}</option>)}
          </select>}
          actions={buttons} />
      </div>
    </Group>

    <Group title="Staying current">
      <UpkeepCard upkeep={data.upkeep} onSaved={saved} />
    </Group>

    <Group title="Compare models" count="graded checks, side by side">
      <CompareModels rows={scoreboard} labelOf={labelOf} />
    </Group>

    <details className="card other-providers" style={{ marginTop: "var(--s6)" }}>
      <summary><b>Lineups for the {others.length} providers you haven&apos;t connected</b></summary>
      <p className="help">Ready for the day you connect one. Connecting is under{" "}
        <Link to="/settings?section=providers&connect=1">Providers → Connect a provider</Link>,
        which also offers each class&apos;s model from the key&apos;s own list.</p>
      {others.map((row) => <OtherLineup key={row.provider} row={row}
        catalog={(catalog?.models ?? []).filter((m) => m.provider === row.provider).map((m) => m.id)}
        onSaved={saved} />)}
    </details>
  </>;
}
