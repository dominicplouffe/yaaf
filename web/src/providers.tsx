// Who sells the tokens, and which model a seat runs on — as every screen needs it.
//
// The server holds the provider registry and the model catalog (yaaf.runtimes);
// this is their one reading on the client, so the credential form, the role
// editor, New Project, the Founder's key and Settings → Models cannot each grow
// their own idea of which providers exist or what "@balanced" means. Nothing
// here names a provider: a new one on the server is a new row on every screen.

import { useEffect, useMemo, useState } from "react";
import { get } from "./api";
import { SecretInput } from "./ui";

/** One way to pay a provider (yaaf.runtimes.providers.CredentialKind). */
export interface ProviderKind {
  mode: string;
  meter: string;
  bills: string;
  label: string;
  placeholder: string;
  help: string;
  /** A value that is a file — a login, a service account — pasted whole. */
  multiline: boolean;
  default_harness: string;
}

/** A provider as the registry describes it (GET /admin/providers). */
export interface ProviderInfo {
  name: string;
  label: string;
  /** The wire yaaf's loop and the Founder speak to it; null for a CLI-only one. */
  protocol: string | null;
  default_harness: string;
  kinds: ProviderKind[];
  credential_base_url: boolean;
  /** How the address is spelled, blanks in braces: {region}, {resource}… */
  base_url_template: string | null;
  console_url: string | null;
  lineup: Record<string, string>;
  search: boolean;
  reports_cost: boolean;
  plan_meter: boolean;
  /** A server on the chairman's own network, which takes no key. */
  local: boolean;
}

/** What a credential row written before there was a second provider pays —
    the server's DEFAULT_PROVIDER, for the rows an old server sends unlabelled. */
export const DEFAULT_PROVIDER = "anthropic";

export interface ClassInfo { name: string; ref: string; label: string; blurb: string }
export interface CatalogModel {
  id: string; provider: string; label: string; tier: number;
  efforts: string[] | null;
  /** Whether this install can meter it in dollars: a price, or a provider
      that states each call's cost. */
  priced: boolean;
}
/** GET /admin/models: everything a model picker offers. */
export interface ModelsInfo {
  default_provider: string;
  classes: ClassInfo[];
  /** As this install resolves them: the shipped lineup under its own. */
  lineups: Record<string, Record<string, string>>;
  models: CatalogModel[];
  efforts: string[];
}

let providerCache: Promise<ProviderInfo[]> | null = null;
/** The provider registry, fetched once per page load: it changes with a release,
    not with anything a person does on a screen. */
export function providers(): Promise<ProviderInfo[]> {
  providerCache ??= get<ProviderInfo[]>("/admin/providers").catch(() => {
    providerCache = null;
    return [] as ProviderInfo[];
  });
  return providerCache;
}

export function useProviders(): ProviderInfo[] {
  const [rows, setRows] = useState<ProviderInfo[]>([]);
  useEffect(() => { providers().then(setRows); }, []);
  return rows;
}

let modelsCache: Promise<ModelsInfo | null> | null = null;
/** The catalog, the classes and the lineups. Unlike the registry the lineups
    are the install's to change, so the screen that changes them drops this. */
export function modelsInfo(): Promise<ModelsInfo | null> {
  modelsCache ??= get<ModelsInfo>("/admin/models").catch(() => {
    modelsCache = null;
    return null;
  });
  return modelsCache;
}

export function forgetModels(): void { modelsCache = null; }

export function useModels(nonce = 0): ModelsInfo | null {
  const [info, setInfo] = useState<ModelsInfo | null>(null);
  useEffect(() => {
    if (nonce) forgetModels();
    modelsInfo().then(setInfo);
  }, [nonce]);
  return info;
}

// ── references: a model, or a class of one ─────────────────────────────────────
//
// Mirrors yaaf.runtimes.catalog: `@balanced` is the project's own provider's
// balanced model, `openai/@fast` is OpenAI's fast model, anything else is a
// model by name (`gpt-6.1-sol`, `openrouter/qwen/qwen3-coder`).

export const CLASS_ORDER = ["frontier", "balanced", "fast"] as const;
const CLASS_LABEL: Record<string, string> = { frontier: "Frontier", balanced: "Balanced", fast: "Fast" };

export function parseRef(ref: string | null | undefined): { provider: string | null; cls: string | null } {
  const value = ref ?? "";
  const slash = value.indexOf("/");
  if (slash > 0 && value.slice(slash + 1).startsWith("@")) {
    return { provider: value.slice(0, slash), cls: value.slice(slash + 2) };
  }
  if (value.startsWith("@")) return { provider: null, cls: value.slice(1) };
  return { provider: null, cls: null };
}

export const isClass = (ref: string | null | undefined) => parseRef(ref).cls !== null;
export const classRef = (cls: string, provider?: string | null) =>
  provider ? `${provider}/@${cls}` : `@${cls}`;

const SPELLED: Record<string, string> = { gpt: "GPT", deepseek: "DeepSeek", glm: "GLM" };

/** A model as a person says it — the server's catalog.label, here so a table
    of seats does not have to ask: "claude-sonnet-5-5" is "Claude Sonnet 5.5",
    "gpt-5.6-luna" is "GPT 5.6 Luna", "@balanced" is "Balanced". */
export function modelLabel(ref: string | null | undefined,
                           registry: ProviderInfo[] = []): string {
  if (!ref) return "the default model";
  const { provider, cls } = parseRef(ref);
  if (cls !== null) {
    const name = CLASS_LABEL[cls] ?? cls;
    const seller = provider ? registry.find((p) => p.name === provider)?.label ?? provider : null;
    return seller ? `${name} (${seller})` : name;
  }
  const words: string[] = [];
  for (const part of (ref.split("/").pop() ?? ref).split("-")) {
    const last = words[words.length - 1];
    if (/^\d+$/.test(part) && last && /^[\d.]+$/.test(last)) {
      words[words.length - 1] = `${last}.${part}`;
    } else {
      words.push(SPELLED[part] ?? (/^\d/.test(part) ? part : part.charAt(0).toUpperCase() + part.slice(1)));
    }
  }
  return words.join(" ");
}

/** The provider a model reference names, where it names one without the
    catalog's help: a provider prefix, or a catalog row. */
export function providerOf(ref: string, info: ModelsInfo | null,
                           registry: ProviderInfo[], fallback: string): string {
  const { provider, cls } = parseRef(ref);
  if (cls !== null) return provider ?? fallback;
  const row = info?.models.find((m) => m.id === ref);
  if (row) return row.provider;
  const prefix = ref.split("/")[0];
  return ref.includes("/") && registry.some((p) => p.name === prefix) ? prefix : fallback;
}

/** What a reference runs on when `primary` pays the project, or null when the
    class has no model there (R8 — the server refuses such a seat). */
export function resolveRef(ref: string, primary: string, info: ModelsInfo | null): string | null {
  const { provider, cls } = parseRef(ref);
  if (cls === null) return ref;
  return info?.lineups[provider ?? primary]?.[cls] ?? null;
}

export function isPriced(model: string | null, info: ModelsInfo | null,
                         registry: ProviderInfo[]): boolean | null {
  if (!model || !info) return null;
  const row = info.models.find((m) => m.id === model);
  if (row) return row.priced;
  const prefix = model.split("/")[0];
  // A provider that states each call's cost needs no price; an unlisted
  // model's price is the install's, which only the prices table knows.
  return registry.find((p) => p.name === prefix)?.reports_cost ? true : null;
}

// ── a model picker ──────────────────────────────────────────────────────────────

/** One picker for a model reference, wherever one is chosen: the classes first
    (each shown with what it runs on for `primary`), then the catalog's models
    grouped by provider, then any model by name. `providersShown` limits the
    pinned models and the provider-qualified classes to the providers a
    project pays. */
export function ModelSelect({ value, onChange, info, registry, primary, providersShown,
                              classes = true, qualified = false, empty, disabled, id,
                              unpaidShown }: {
  value: string; onChange: (ref: string) => void;
  info: ModelsInfo | null; registry: ProviderInfo[];
  /** Which provider a bare class resolves on, for the hint beside it. */
  primary?: string | null;
  providersShown?: string[];
  classes?: boolean;
  /** Offer `<provider>/@<class>` for every provider shown, too. */
  qualified?: boolean;
  /** A first, empty option ("each role's own model"). */
  empty?: string;
  /** Providers listed after the paid ones, marked "no credential here" —
      offered so picking one opens the door to pay it (SeatEditor's door). */
  unpaidShown?: string[];
  disabled?: boolean; id?: string;
}) {
  const shownProviders = providersShown ?? registry.map((p) => p.name);
  const grouped = useMemo(() => {
    const out = new Map<string, CatalogModel[]>();
    for (const m of info?.models ?? []) {
      if (!shownProviders.includes(m.provider)) continue;
      out.set(m.provider, [...(out.get(m.provider) ?? []), m]);
    }
    for (const list of out.values()) list.sort((a, b) => b.tier - a.tier);
    return out;
  }, [info, shownProviders]);
  const unpaidGroups = useMemo(() => {
    const out = new Map<string, CatalogModel[]>();
    for (const m of info?.models ?? []) {
      if (!(unpaidShown ?? []).includes(m.provider)) continue;
      out.set(m.provider, [...(out.get(m.provider) ?? []), m]);
    }
    for (const list of out.values()) list.sort((a, b) => b.tier - a.tier);
    return out;
  }, [info, unpaidShown]);
  const known = value === "" || isClass(value) || (info?.models ?? []).some((m) => m.id === value);
  const [other, setOther] = useState(!known);
  useEffect(() => { if (!known) setOther(true); }, [known]);
  const labelOf = (name: string) => registry.find((p) => p.name === name)?.label ?? name;
  const hint = (cls: string, provider: string | null | undefined) => {
    if (!provider) return "";
    const model = info?.lineups[provider]?.[cls];
    return model ? ` → ${model}` : ` → none set on ${labelOf(provider)}`;
  };
  return <span className="model-select">
    <select id={id} disabled={disabled} value={other ? "__other" : value}
      onChange={(e) => {
        if (e.target.value === "__other") { setOther(true); return; }
        setOther(false); onChange(e.target.value);
      }}>
      {empty !== undefined && <option value="">{empty}</option>}
      {classes && <optgroup label="A class — runs on the project's own provider">
        {(info?.classes ?? CLASS_ORDER.map((c) => ({ name: c, ref: `@${c}`, label: CLASS_LABEL[c], blurb: "" })))
          .map((c) => <option key={c.ref} value={c.ref}>
            {c.label}{c.blurb ? ` — ${c.blurb}` : ""}{hint(c.name, primary)}
          </option>)}
      </optgroup>}
      {classes && qualified && shownProviders.map((name) =>
        <optgroup key={`cls-${name}`} label={`${labelOf(name)} — by class`}>
          {CLASS_ORDER.map((c) => <option key={c} value={classRef(c, name)}>
            {labelOf(name)} · {CLASS_LABEL[c]}{hint(c, name)}
          </option>)}
        </optgroup>)}
      {[...grouped.entries()].map(([name, models]) =>
        <optgroup key={name} label={`${labelOf(name)} — a model of its own`}>
          {models.map((m) => <option key={m.id} value={m.id}>
            {m.id}{m.priced ? "" : " · no price yet"}
          </option>)}
        </optgroup>)}
      {[...unpaidGroups.entries()].map(([name, models]) =>
        <optgroup key={`unpaid-${name}`} label={`${labelOf(name)} — no credential on this project`}>
          {models.map((m) => <option key={m.id} value={m.id}>{m.id}</option>)}
        </optgroup>)}
      <option value="__other">Another model, by name…</option>
    </select>
    {other && <input value={value} disabled={disabled} maxLength={128}
      aria-label="Model name" placeholder="provider/model — e.g. openrouter/qwen/qwen3-coder"
      onChange={(e) => onChange(e.target.value.trim())} />}
  </span>;
}

// ── an address the credential carries ──────────────────────────────────────────

/** The blanks a provider's address template asks for, in order. */
export function templateFields(template: string | null): string[] {
  return [...new Set([...(template ?? "").matchAll(/\{(\w+)\}/g)].map((m) => m[1]))];
}

const FIELD_LABEL: Record<string, string> = {
  base_url: "Base URL", region: "Region", resource: "Resource name", project: "Project ID",
};
const FIELD_HINT: Record<string, string> = {
  region: "e.g. us-central1, eu-west-1 — or global",
  resource: "the name in https://<resource>.openai.azure.com",
  project: "the Google Cloud project the service account belongs to",
};

/** The address, composed from the template's blanks — or "" until every blank
    is filled, so a half-typed address is never sent. Vertex's global region
    has no regional host: it is aiplatform.googleapis.com itself. */
export function composeUrl(template: string, values: Record<string, string>): string {
  const fields = templateFields(template);
  if (fields.some((f) => !(values[f] ?? "").trim())) return "";
  let out = template;
  if ((values.region ?? "").trim() === "global") {
    out = out.replace("{region}-aiplatform.googleapis.com", "aiplatform.googleapis.com");
  }
  for (const f of fields) out = out.split(`{${f}}`).join(values[f].trim());
  return out.replace(/\/+$/, "");
}

/** The project a pasted Google service account belongs to, if it is one. */
export function serviceAccountProject(pasted: string): string | null {
  try {
    const parsed = JSON.parse(pasted);
    return typeof parsed?.project_id === "string" ? parsed.project_id : null;
  } catch { return null; }
}

/** Inputs for a provider whose address is the credential's own. `secret` is
    the value being pasted, read only to prefill what it already says (a
    service account's project). An existing address is shown as itself and
    edited whole, because a template cannot be read back out of a URL. */
export function BaseUrlFields({ provider, value, onChange, secret = "", disabled }: {
  provider: ProviderInfo; value: string; onChange: (url: string) => void;
  secret?: string; disabled?: boolean;
}) {
  const template = provider.base_url_template ?? "{base_url}";
  const fields = templateFields(template);
  const [parts, setParts] = useState<Record<string, string>>({});
  const [raw, setRaw] = useState(!!value && fields.length > 1);
  const project = serviceAccountProject(secret);
  useEffect(() => {
    if (project && fields.includes("project") && !parts.project) {
      setParts((prev) => ({ ...prev, project }));
    }
  }, [project]);
  useEffect(() => {
    if (!raw && fields.length > 1) onChange(composeUrl(template, parts));
  }, [parts, raw, template]);
  if (raw || fields.length === 1) {
    return <label className="field">{fields.length === 1 ? FIELD_LABEL[fields[0]] ?? "Address" : "Address"}
      <input required disabled={disabled} value={value} onChange={(e) => onChange(e.target.value)}
        placeholder={provider.local ? "http://ollama:11434/v1" : template.replace("{base_url}", "https://…")} />
      <span className="help">{provider.local
        ? "The server's address on the sandbox network — its name there, not localhost. Price its models under Settings → Model prices; zero is a price."
        : fields.length > 1 ? <>Spelled {template}.{" "}
            <button type="button" className="linkish" onClick={() => { setRaw(false); onChange(""); }}>
              Fill in the blanks instead</button></>
        : "The server's OpenAI-compatible address."}</span>
    </label>;
  }
  return <div className="base-url-fields">
    {fields.map((f) => <label key={f} className="field">{FIELD_LABEL[f] ?? f}
      <input required disabled={disabled} value={parts[f] ?? ""} placeholder={FIELD_HINT[f] ?? ""}
        onChange={(e) => setParts({ ...parts, [f]: e.target.value })} />
    </label>)}
    <p className="help">Address: <code>{value || template}</code>{" "}
      <button type="button" className="linkish" onClick={() => setRaw(true)}>Type it instead</button></p>
  </div>;
}

/** Where a key for this provider comes from, in its own words, with the link. */
export function KeyHelp({ provider, kind }: { provider: ProviderInfo | undefined; kind: ProviderKind | undefined }) {
  if (!provider || !kind) return null;
  return <p className="help">{kind.help}{" "}
    {provider.console_url && <a href={provider.console_url} target="_blank" rel="noreferrer">
      Open the {provider.label} console ↗</a>}
  </p>;
}

/** A credential's value: a textarea where it is a file (a login, a service
    account), a masked input where it is a key. */
export function KeyInput({ kind, value, onChange, placeholder, id, disabled }: {
  kind: ProviderKind | undefined; value: string; onChange: (v: string) => void;
  placeholder?: string; id?: string; disabled?: boolean;
}) {
  if (kind?.multiline) {
    return <textarea id={id} className="mono" rows={6} value={value} disabled={disabled}
      spellCheck={false} autoComplete="off" placeholder={placeholder ?? "Paste the whole file"}
      onChange={(e) => onChange(e.target.value)} />;
  }
  return <SecretInput id={id} value={value} onChange={onChange} disabled={disabled}
    placeholder={placeholder ?? (kind?.placeholder || "Paste the key")} />;
}
