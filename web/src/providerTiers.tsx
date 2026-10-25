// What each class runs on, on one provider — with the evidence beside each
// choice (is it priced, does the key list it, what did its last check say, how
// much does it read at once) and the door to change it.
//
// The change control offers what the key can actually call when the provider
// will say (one free call to its own model list), because a Groq lineup typed
// from memory is a lineup that 404s at 3am. When it will not say — no network,
// a key without list access — it falls back to a name typed by hand, with the
// catalog's models as suggestions.

import { useEffect, useMemo, useState } from "react";
import { get, put } from "./api";
import { fmtWindow } from "./llmCredentials";
import { CheckChip, CheckRow } from "./providerCheck";
import { forgetModels, useModels } from "./providers";
import type { ReadinessTier } from "./readiness";
import { fmtAgo } from "./ui";

export interface DiscoveredModel { id: string; name: string; listed: boolean; in_lineup: boolean }
export interface Discovery { models: DiscoveredModel[] | null; from: string | null; error: string | null; busy: boolean }
export const NO_DISCOVERY: Discovery = { models: null, from: null, error: null, busy: false };

/** Ask the provider which models answer to this key. Free; the server also
    remembers the list, which is what "the key lists it" is read against. */
export async function discoverModels(credentialId: string): Promise<DiscoveredModel[]> {
  const found = await get<{ models: DiscoveredModel[] }>(
    `/admin/settings/llm-credentials/${credentialId}/models`);
  return found.models;
}

export interface LineupSaved { unrunnable?: string[] }

const perM = (n: number) => `$${n.toLocaleString(undefined, { maximumFractionDigits: 3 })}`;

/** One class's model, chosen from what the key lists or typed by name. */
export function TierEditor({ provider, providerLabel, tier, discovery, onDiscover, onSaved, onCancel }: {
  provider: string; providerLabel: string;
  tier: Pick<ReadinessTier, "class" | "label" | "model" | "source">;
  discovery: Discovery;
  /** Ask the key for its list; absent where nothing can be asked (no API key). */
  onDiscover?: () => void;
  onSaved: (said: LineupSaved) => void;
  onCancel: () => void;
}) {
  const catalog = useModels();
  const [value, setValue] = useState(tier.model ?? "");
  const [byName, setByName] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // Asked once, on opening, where nobody has asked yet.
  useEffect(() => {
    if (!discovery.models && !discovery.busy && !discovery.error && onDiscover) onDiscover();
  }, []);
  const suggestions = useMemo(() =>
    (catalog?.models ?? []).filter((m) => m.provider === provider).map((m) => m.id), [catalog, provider]);
  const listed = discovery.models;
  const useList = !!listed?.length && !byName;
  const options = useMemo(() => {
    if (!listed) return [];
    // The catalog's own models first: those are the ones yaaf knows how to label and price.
    return [...listed].sort((a, b) => Number(b.listed) - Number(a.listed) || a.id.localeCompare(b.id));
  }, [listed]);
  const inList = !!listed?.some((m) => m.id === value);

  async function save(model: string | null) {
    setBusy(true); setError("");
    try {
      const said = await put<LineupSaved>("/admin/settings/models/lineup", { provider, class: tier.class, model });
      forgetModels();
      onSaved(said);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }

  const id = `tier-${provider}-${tier.class}`;
  return <div className="tier-editor">
    <label className="field" htmlFor={id}>{tier.label} model on {providerLabel}</label>
    {discovery.busy && <p className="help" role="status">Asking {providerLabel} which models this key can call…</p>}
    {useList ? <select id={id} value={inList ? value : ""} disabled={busy}
        onChange={(e) => setValue(e.target.value)}>
        <option value="">Choose one of the {listed!.length} models the key lists…</option>
        {options.map((m) => <option key={m.id} value={m.id}>
          {m.id}{m.in_lineup ? " · in the lineup" : ""}{m.listed ? "" : " · not in yaaf's catalog"}</option>)}
      </select>
      : <>
        <input id={id} list={`${id}-list`} value={value} disabled={busy} maxLength={128}
          placeholder={`a ${providerLabel} model, e.g. ${suggestions[0] ?? `${provider}/model-name`}`}
          onChange={(e) => setValue(e.target.value)} />
        <datalist id={`${id}-list`}>{suggestions.map((o) => <option key={o} value={o} />)}</datalist>
      </>}
    <p className="help">
      {useList ? <>From {discovery.from ? <b>{discovery.from}</b> : "the key"}&apos;s own list.{" "}
          <button type="button" className="linkish" onClick={() => setByName(true)}>Type a name instead</button></>
        : discovery.error ? <>Could not read the key&apos;s list ({discovery.error}) — type the model&apos;s name as the provider spells it.{" "}
          {onDiscover && <button type="button" className="linkish" onClick={onDiscover}>Ask again</button>}</>
        : listed && byName ? <button type="button" className="linkish" onClick={() => setByName(false)}>
            Pick from the key&apos;s list</button>
        : !onDiscover ? "Type the model's name as the provider spells it." : null}
      {" "}Every seat naming this class on a project {providerLabel} pays moves with it on its next run.
    </p>
    {error && <p className="error" role="alert">{error}</p>}
    <div className="actions" style={{ marginTop: 8 }}>
      <button type="button" className="primary small" disabled={busy || !value.trim() || value.trim() === tier.model}
        onClick={() => save(value.trim())}>{busy ? "Saving…" : "Save"}</button>
      {tier.source === "install" && <button type="button" className="small" disabled={busy}
        onClick={() => save(null)}>Back to shipped</button>}
      <button type="button" className="small" disabled={busy} onClick={onCancel}>Cancel</button>
    </div>
  </div>;
}

/** The three classes on one provider, a row each, with the evidence. */
export function TierTable({ provider, providerLabel, tiers, discovery, onDiscover, editing, setEditing,
                            onSaved, scoreboard, viewing, setViewing, servedAt }: {
  provider: string; providerLabel: string;
  tiers: ReadinessTier[];
  discovery: Discovery;
  onDiscover?: () => void;
  editing: string | null;
  setEditing: (cls: string | null) => void;
  onSaved: (said: LineupSaved) => void;
  scoreboard?: Record<string, CheckRow>;
  viewing?: string | null;
  setViewing?: (model: string | null) => void;
  /** When the key's list was last read, for the "listed" column's header. */
  servedAt?: string | null;
}) {
  return <div className="tiers" role="table" aria-label={`${providerLabel} models by class`}>
    <div className="tier-row tier-headrow" role="row">
      <span role="columnheader">Class</span><span role="columnheader">Model</span>
      <span role="columnheader">Price per M tokens</span>
      <span role="columnheader" title={servedAt ? `List read ${fmtAgo(servedAt)}` : undefined}>Key lists it</span>
      <span role="columnheader">Last check</span><span role="columnheader">Context</span>
    </div>
    {tiers.map((t) => {
      const check = t.model ? scoreboard?.[t.model] : undefined;
      return <div key={t.class} className={`tier-row${editing === t.class ? " editing" : ""}`} role="row" id={`tier-${provider}-${t.class}`}>
        <span role="cell" className="tier-class"><b>{t.label}</b></span>
        <span role="cell" data-label="Model" className="tier-model">
          {t.model ? <code>{t.model}</code> : <span className="critical-ink">none set — seats refused</span>}
          <span className="tier-sub">
            {t.source && <span className="help">{t.source === "install" ? "this install's choice" : "shipped with yaaf"} · </span>}
            <button type="button" className="linkish" aria-expanded={editing === t.class}
              aria-label={`${t.model ? "Change" : "Choose"} the ${t.label} model`}
              onClick={() => setEditing(editing === t.class ? null : t.class)}>
              {t.model ? "Change" : "Choose a model"}</button>
          </span>
        </span>
        <span role="cell" data-label="Price">
          {!t.model ? "—" : t.price ? <>{perM(t.price.input)} in · {perM(t.price.output)} out</>
            : t.states_cost ? <span title="The provider states each call's cost">stated per call</span>
            : <span className="chip critical">no price</span>}
        </span>
        <span role="cell" data-label="Key lists it">
          {t.served === true ? <span className="good">✓ listed</span>
            : t.served === false ? <span className="chip warn">not listed</span>
            : <span className="muted" title="Press Models on a credential to read the key's list">not read yet</span>}
        </span>
        <span role="cell" data-label="Last check">
          {t.check ? <button type="button" className="tier-check" disabled={!check || !setViewing}
              aria-expanded={viewing === t.model}
              title={t.check.summary}
              onClick={() => setViewing?.(viewing === t.model ? null : t.model)}>
              <CheckChip status={t.check.status} />
              {t.check.at && <span className="help">{fmtAgo(t.check.at)}</span>}
            </button>
            : <span className="muted">not checked</span>}
        </span>
        <span role="cell" data-label="Context">{t.context_window ? fmtWindow(t.context_window) : <span className="muted">—</span>}</span>
        {editing === t.class && <div className="tier-edit-wrap">
          <TierEditor provider={provider} providerLabel={providerLabel} tier={t} discovery={discovery}
            onDiscover={onDiscover} onCancel={() => setEditing(null)}
            onSaved={(said) => { setEditing(null); onSaved(said); }} />
        </div>}
      </div>;
    })}
  </div>;
}
