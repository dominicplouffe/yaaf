// Settings → Founder: what the assistant that drafts new projects pays with.
//
// It was two cards that each said the state in different words, three
// primary-sized "Use …" buttons (one for a key whose provider was blocked),
// two save buttons and a Remove whose meaning changed with where the key came
// from. Now it is one choice — one of your keys, or a key of its own — the
// model, and one Save. A key the Founder could not run on is still listed,
// greyed, with the reason beside it rather than offered as a button.

import { useEffect, useMemo, useState } from "react";
import { del, post, put } from "../api";
import type { Credential } from "../llmCredentials";
import {
  BaseUrlFields, DEFAULT_PROVIDER, KeyHelp, KeyInput, ModelSelect, ProviderInfo, useModels, useProviders,
} from "../providers";
import { providerState, Readiness, StatusBadge } from "../readiness";
import { ConfirmModal } from "../ui";
import { Why } from "../settingsCopy";
import { PlatformSettings } from "./data";
import { EvidenceSeal, Flash, Group, SaveBar, SectionHead } from "./kit";

const PLATFORM = "/admin/settings/platform-credential";

/** Kinds the Founder can be paid with. It runs in the control plane's own
    process: on a provider's API with a key, or — for a login only the Claude
    Code CLI can hold — on that CLI, which the server drives for it. */
const founderKinds = (seller: ProviderInfo | undefined) =>
  (seller?.kinds ?? []).filter((k) => k.mode === "api_key" || k.default_harness === "claude_agent_sdk");

export function FounderSection({ founder, creds, readiness, onChanged }: {
  founder: PlatformSettings;
  creds: Credential[] | null;
  readiness: Readiness | null;
  onChanged: () => void;
}) {
  const sellers = useProviders().filter((p) => p.protocol);
  const catalog = useModels();
  const borrowed = founder.source?.source === "llm_credential" ? founder.source.credential_id ?? null : null;
  const offered = (creds ?? []).filter((c) => !c.hidden_at);
  const [how, setHow] = useState<"borrow" | "own">(borrowed || (!founder.configured && offered.length) ? "borrow" : "own");
  const [pick, setPick] = useState<string | null>(borrowed);
  const [provider, setProvider] = useState(founder.provider || DEFAULT_PROVIDER);
  const [mode, setMode] = useState(founder.billing_mode || "api_key");
  const [baseUrl, setBaseUrl] = useState("");
  const [value, setValue] = useState("");
  const saved = founder.model_ref ?? founder.model;
  const [model, setModel] = useState(saved);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [turningOff, setTurningOff] = useState(false);

  useEffect(() => {
    setPick(borrowed);
    setModel(founder.model_ref ?? founder.model);
    setProvider(founder.provider || DEFAULT_PROVIDER);
    setMode(founder.billing_mode || "api_key");
  }, [borrowed, founder.model, founder.model_ref, founder.provider, founder.billing_mode]);

  const chosen = offered.find((c) => c.id === pick);
  const payingProvider = how === "borrow" ? chosen?.provider ?? DEFAULT_PROVIDER : provider;
  const seller = sellers.find((p) => p.name === provider);
  const kinds = founderKinds(seller);
  const kind = kinds.find((k) => k.mode === mode) ?? kinds[0];

  /** Why a key cannot pay the Founder, or null. */
  const unusable = useMemo(() => (c: Credential): string | null => {
    const p = readiness?.providers.find((r) => r.provider === (c.provider ?? DEFAULT_PROVIDER));
    if (p && providerState(p) === "blocked") return "Blocked: its classes have no model";
    if (p && providerState(p) === "failing") return "Its provider refused the key";
    const s = sellers.find((r) => r.name === (c.provider ?? DEFAULT_PROVIDER));
    if (s && !founderKinds(s).some((k) => k.mode === c.mode)) return "The Founder can’t run on this kind of key";
    return null;
  }, [readiness, sellers]);

  const keyTyped = how === "own" && (value.trim().length >= 8 || (!!seller?.local && !founder.configured));
  const switching = how === "borrow" ? !!pick && pick !== borrowed : keyTyped;
  const modelMoved = model.trim().length >= 2 && model.trim() !== saved;
  const dirty = switching || modelMoved;

  async function save() {
    setBusy(true); setNote(null);
    try {
      if (how === "borrow" && pick && pick !== borrowed) {
        await put("/admin/founder/credential/llm", { credential_id: pick });
      } else if (how === "own" && keyTyped) {
        await put(PLATFORM, {
          value: value.trim(), mode: kind?.mode ?? mode, provider,
          ...(baseUrl.trim() ? { base_url: baseUrl.trim() } : {}),
          // The model travels with a key of another provider: the two are one
          // decision, and the server refuses them apart.
          ...(modelMoved || provider !== founder.provider ? { model: model.trim() } : {}),
        });
        setValue("");
      }
      if (modelMoved && !(how === "own" && keyTyped)) {
        await put(`${PLATFORM}/model`, { model: model.trim() });
      }
      setNote({ ok: true, text: "Saved. The Founder drafts on this from its next message." });
      onChanged();
    } catch (e) {
      setNote({ ok: false, text: (e as Error).message });
    } finally { setBusy(false); }
  }

  async function test() {
    setBusy(true); setNote(null);
    try {
      const r = await post<{ ok: boolean; detail: string }>(`${PLATFORM}/test`);
      setNote({ ok: r.ok, text: r.detail });
      onChanged();
    } catch (e) { setNote({ ok: false, text: (e as Error).message }); } finally { setBusy(false); }
  }

  const payer = founder.source?.name ?? `a ${providerLabel(sellers, founder.provider)} key of its own`;
  const ev = founder.evidence;
  return <>
    <SectionHead title="Founder"
      lede="The assistant that drafts a new project with you: the goal, the teams, the budget and the date. It costs nothing until you talk to it."
      actions={founder.configured && <button type="button" disabled={busy} onClick={test}>Test</button>}>
      {founder.configured ? <>
        <span className={`badge ${ev?.state === "refused" ? "critical critical-fill" : ev?.state === "answered" ? "good" : "hollow"}`}>
          {ev?.state === "refused" ? "Failing" : ev?.state === "answered" ? "Ready" : "Untested"}</span>
        <span>Drafting on <b>{founder.model}</b>, paid by <b>{payer}</b></span>
        <EvidenceSeal evidence={ev} />
      </> : <>
        <span className="badge plain">Off</span>
        <span>The Founder can’t draft. Everything else works: projects pay with their own keys.</span>
      </>}
    </SectionHead>
    <Flash note={note} />

    <Group title="Pays with">
      <div className="choice">
        <div className={`choice-card${how === "borrow" ? " on" : ""}`}>
          <input type="radio" id="founder-borrow" name="founder-how" checked={how === "borrow"}
            disabled={!offered.length} onChange={() => setHow("borrow")} />
          <label className="t" htmlFor="founder-borrow">One of your keys</label>
          <span className="d">{offered.length ? "No second key to manage. Rotating that key rotates this too."
            : "Connect a provider first; its key will be offered here."}</span>
          {how === "borrow" && offered.length > 0 && <div className="body">
            <div className="keypick" role="radiogroup" aria-label="The key the Founder pays with">
              {offered.map((c) => {
                const off = unusable(c);
                const p = readiness?.providers.find((r) => r.provider === (c.provider ?? DEFAULT_PROVIDER));
                return <label key={c.id} className={`${pick === c.id ? "on" : ""}${off ? " off" : ""}`}
                  title={off ?? undefined}>
                  <input type="radio" name="founder-key" checked={pick === c.id} disabled={!!off}
                    onChange={() => setPick(c.id)} />
                  <span><b>{c.name}</b>{off && <span className="help"> · {off}</span>}</span>
                  {p ? <StatusBadge p={p} /> : <span />}
                </label>;
              })}
            </div>
          </div>}
        </div>
        <div className={`choice-card${how === "own" ? " on" : ""}`}>
          <input type="radio" id="founder-own" name="founder-how" checked={how === "own"}
            onChange={() => setHow("own")} />
          <label className="t" htmlFor="founder-own">Its own key</label>
          <span className="d">A key only the Founder uses: an API key, or a Claude subscription run through Claude Code.</span>
          {how === "own" && <div className="body">
            <div className="row">
              <div><label className="field" htmlFor="founder-provider">Provider</label>
                <select id="founder-provider" value={provider} disabled={busy}
                  onChange={(e) => { setProvider(e.target.value); setMode("api_key"); setBaseUrl(""); }}>
                  {sellers.length === 0 && <option value={provider}>Loading providers…</option>}
                  {sellers.map((p) => <option key={p.name} value={p.name}>{p.label}</option>)}
                </select></div>
              {kinds.length > 1 && <div><label className="field" htmlFor="founder-kind">Key type</label>
                <select id="founder-kind" value={mode} disabled={busy} onChange={(e) => setMode(e.target.value)}>
                  {kinds.map((k) => <option key={k.mode} value={k.mode}>{k.bills.split(",")[0]}</option>)}
                </select></div>}
            </div>
            {seller?.credential_base_url && <BaseUrlFields provider={seller} value={baseUrl}
              onChange={setBaseUrl} secret={value} disabled={busy} />}
            <label className="field">{kind?.label ?? "Key"}
              {founder.configured && founder.source?.source === "own" && <span className="help"> · leave empty to keep the saved one</span>}</label>
            <KeyInput kind={kind} value={value} onChange={setValue} disabled={busy}
              placeholder={seller?.local ? "Leave empty: it takes no key" : undefined} />
            <KeyHelp provider={seller} kind={kind} />
            <Why>Encrypted at rest and never shown again. It is never injected into a sandbox and never
              reaches a project’s agents: every project pays its own way. Pasting a key where another
              kind belongs is refused here rather than failing on your first message.</Why>
          </div>}
        </div>
      </div>
    </Group>

    <Group title="Model">
      <div className="hrow" style={{ alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <ModelSelect value={model} onChange={setModel} info={catalog} registry={sellers}
          primary={payingProvider} providersShown={[payingProvider]} disabled={busy} />
        {founder.model_ref && founder.model_ref !== founder.model && <span className="help">
          {founder.model_ref} resolves to <code>{founder.model}</code>. It follows the class when the lineup changes.</span>}
      </div>
    </Group>

    <SaveBar dirty={dirty} busy={busy} onSave={save}
      danger={founder.configured && <button type="button" className="quiet-danger" disabled={busy}
        onClick={() => setTurningOff(true)}>Turn the Founder off…</button>} />

    {turningOff && <ConfirmModal title="Turn the Founder off?" confirmLabel="Turn it off" danger busy={busy}
      onCancel={() => setTurningOff(false)}
      onConfirm={async () => {
        setBusy(true);
        try { await del(PLATFORM); setNote({ ok: true, text: "The Founder is off." }); onChanged(); }
        catch (e) { setNote({ ok: false, text: (e as Error).message }); }
        finally { setBusy(false); setTurningOff(false); }
      }}>
      <p>It stops being offered for new projects. No project is affected: none of them ever used it.
        {founder.source?.source === "llm_credential" && <> <b>{founder.source.name}</b> stays as it is; only the
        Founder stops using it.</>}</p>
    </ConfirmModal>}
  </>;
}

function providerLabel(sellers: ProviderInfo[], name: string) {
  return sellers.find((p) => p.name === name)?.label ?? name;
}

