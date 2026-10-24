// The one form that adds or edits an LLM credential.
//
// Three screens open it — the full credentials list, a provider's card (to
// rename or rotate a key in place) and the connect flow — and each used to be
// the place a rule could be forgotten: a base URL a custom endpoint needs, a
// login pasted whole, the type that may not change once projects are metered
// by it, the offer to let the Founder draft on the first key. So the rules live
// here once, and the screens decide only what happens after a save.

import { useEffect, useState } from "react";
import { get, patch, post, put } from "./api";
import type { Credential } from "./llmCredentials";
import { BaseUrlFields, DEFAULT_PROVIDER, KeyHelp, KeyInput, useProviders } from "./providers";

const endpoint = "/admin/settings/llm-credentials";

export interface SavedCredential {
  id: string;
  name: string;
  provider: string;
  mode: string;
  created: boolean;
  /** A subscription's value was replaced: its meter should check itself. */
  rotated: boolean;
  /** Set when the Founder now drafts on it. */
  founder: boolean;
  /** Saved, but something beside the save did not go through. */
  warning?: string;
}

export function CredentialForm({ editing, provider: fixedProvider, initialName = "", title,
                                 submitLabel = "Save credential", onSaved, onCancel, cancelLabel = "Cancel" }: {
  /** The credential being edited; absent to add one. */
  editing?: Credential | null;
  /** Add a credential for this provider, chosen already — no provider picker. */
  provider?: string;
  initialName?: string;
  title?: string;
  submitLabel?: string;
  cancelLabel?: string;
  onSaved: (saved: SavedCredential) => void;
  onCancel: () => void;
}) {
  const registry = useProviders();
  const isNew = !editing;
  const [name, setName] = useState(editing?.name ?? initialName);
  const [provider, setProvider] = useState(editing?.provider ?? fixedProvider ?? "");
  const [mode, setMode] = useState(editing?.mode ?? "api_key");
  const [baseUrl, setBaseUrl] = useState(editing?.base_url ?? "");
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // While the Founder has no key, a new credential offers to be the one it
  // drafts on — ticked, because the alternative was pasting this same key
  // again under a second name on the next screen.
  const [founderKeyless, setFounderKeyless] = useState(false);
  const [alsoDraft, setAlsoDraft] = useState(true);
  useEffect(() => {
    if (!isNew) return;
    get<{ credential_configured: boolean }>("/admin/founder/status")
      .then((st) => setFounderKeyless(!st.credential_configured)).catch(() => {});
  }, [isNew]);
  useEffect(() => { if (fixedProvider) setProvider(fixedProvider); }, [fixedProvider]);
  useEffect(() => { if (isNew && initialName && !name) setName(initialName); }, [initialName]);

  const seller = registry.find((p) => p.name === (provider || DEFAULT_PROVIDER));
  const kind = seller?.kinds.find((k) => k.mode === mode) ?? seller?.kinds[0];
  const live = (editing?.projects ?? []).filter((p) => p.live);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      if (isNew) {
        const created = await post<{ id: string; name?: string }>(endpoint, {
          name: name.trim(), mode: kind?.mode ?? mode, value: value.trim(), provider,
          ...(seller?.credential_base_url ? { base_url: baseUrl.trim() } : {}) });
        let founder = false;
        let warning: string | undefined;
        if (founderKeyless && alsoDraft && created?.id) {
          // A refusal here (a provider the Founder cannot run on) must not read
          // as the credential failing to save — it saved.
          await put("/admin/founder/credential/llm", { credential_id: created.id })
            .then(() => { founder = true; })
            .catch((err) => { warning = `Saved, but the Founder cannot draft on it: ${(err as Error).message}`; });
        }
        setValue("");
        onSaved({ id: created.id, name: name.trim(), provider, mode: kind?.mode ?? mode,
                  created: true, rotated: false, founder, warning });
      } else if (editing) {
        await patch(`${endpoint}/${editing.id}`, {
          name: name.trim(), ...(value.trim() ? { value: value.trim() } : {}),
          ...(seller?.credential_base_url && baseUrl.trim() ? { base_url: baseUrl.trim() } : {}) });
        const rotated = editing.mode === "subscription" && !!value.trim();
        setValue("");
        onSaved({ id: editing.id, name: name.trim(), provider: editing.provider ?? DEFAULT_PROVIDER,
                  mode: editing.mode, created: false, rotated, founder: false });
      }
    } catch (err) {
      setError((err as Error).message);
    } finally { setBusy(false); }
  }

  const valueId = `cred-value-${editing?.id ?? "new"}`;
  return <form onSubmit={save} className="card cred-form">
    {title !== "" && <h3>{title ?? (isNew ? "Add credential" : `Edit “${editing!.name}”`)}</h3>}
    {error && <p className="error" role="alert">{error}</p>}
    <fieldset disabled={busy} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
      <label className="field">Name<input autoFocus required maxLength={120} value={name}
        onChange={(e) => setName(e.target.value)} placeholder="e.g. Production — engineering" /></label>
      {!fixedProvider && <label className="field">Provider<select value={provider} required
        disabled={!isNew} onChange={(e) => { setProvider(e.target.value); setMode("api_key"); setBaseUrl(""); }}>
        {isNew && <option value="">Choose a provider…</option>}
        {registry.length === 0 && provider && <option value={provider}>Loading providers…</option>}
        {registry.map((p) => <option key={p.name} value={p.name}>{p.label}</option>)}
      </select></label>}
      {(seller?.kinds.length ?? 0) > 1 && <label className="field">Type
        <select value={mode} disabled={!isNew} onChange={(e) => setMode(e.target.value)}>
          {seller!.kinds.map((k) => <option key={k.mode} value={k.mode}>{k.bills.split(",")[0]}</option>)}
        </select></label>}
      {kind && (provider || !isNew) && <p className="help">{kind.bills}.</p>}
      {seller?.credential_base_url && (provider || !isNew) && <BaseUrlFields provider={seller}
        value={baseUrl} onChange={setBaseUrl} secret={value} />}
      {!isNew && <p className="help">The type is fixed: it decides how every project on this
        credential is metered, and changing it under them would rewrite what their budgets mean.</p>}
      {(provider || !isNew) && <>
        <label className="field" htmlFor={valueId}>
          {isNew ? kind?.label ?? "Credential value"
            : `Replace ${(kind?.label ?? "credential value").toLowerCase()} (optional)`}</label>
        <KeyInput id={valueId} kind={kind} value={value} onChange={setValue}
          placeholder={isNew ? (seller?.local ? "Leave blank — it takes no key"
            : kind?.placeholder || undefined) : "Leave blank to keep current value"} />
        <KeyHelp provider={seller} kind={kind} />
      </>}
      {live.length > 0 && <p className="notice-box">Replacing this value changes what{" "}
        {live.map((p) => p.name).join(", ")} pays with on the next run.</p>}
      {isNew && founderKeyless && provider &&
        <label className="check-line">
          <input type="checkbox" checked={alsoDraft} onChange={(e) => setAlsoDraft(e.target.checked)} />
          Also use it to draft projects — the Founder has no key yet
        </label>}
      <div className="actions">
        <button className="primary" disabled={!name.trim() || (isNew && !provider)
          || (isNew && !seller?.local && value.trim().length < 8)
          || (isNew && !!seller?.credential_base_url && !baseUrl.trim())}>
          {busy ? "Saving…" : submitLabel}</button>
        <button type="button" onClick={onCancel}>{cancelLabel}</button>
      </div>
    </fieldset>
  </form>;
}
