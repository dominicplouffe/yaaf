// Connect a provider: choose who sells the tokens, paste the key, watch it
// answer, clear whatever would still refuse a seat, and go.
//
// The old way in was a credential form that opened on Anthropic with a
// provider dropdown halfway down it, and everything after the save was left to
// be discovered — a price missing here, a lineup gap there, a search backend
// the browsing roles needed — one refusal at a time, from a project that had
// already been created. This walks the same ground in the order it matters:
// the provider first (because it decides what the form even asks for), a real
// call on the key the moment it is saved (because "saved" is not "works"),
// then the same readiness list the provider's card shows, with its one-press
// fixes, while the person who can fix it is still looking at it.

import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, get, post, put } from "./api";
import { CredentialForm, SavedCredential } from "./credentialForm";
import type { Credential, SmokeResult } from "./llmCredentials";
import { fmtUsd } from "./providerCheck";
import { forgetModels, ProviderInfo, useProviders } from "./providers";
import { discoverModels, Discovery, NO_DISCOVERY, TierTable } from "./providerTiers";
import { IssueList, providerStatus, Readiness, StatusBadge, useReadiness } from "./readiness";
import { Icon } from "./ui/Icon";

/** How the grid is grouped. Only providers the registry has are drawn; one it
    has that is not named here lands under More (or Local), so a provider added
    on the server is never missing from the grid. */
const GROUPS: [string, string[]][] = [
  ["Popular", ["anthropic", "openai", "google", "deepseek", "mistral", "xai"]],
  ["Clouds", ["azure_openai", "bedrock", "vertex"]],
  ["Fast inference", ["groq", "together", "fireworks", "cerebras", "nvidia", "huggingface"]],
  ["Routers", ["openrouter"]],
  ["More", ["moonshot", "qwen", "zai", "cohere"]],
  ["Local", ["ollama", "lmstudio", "custom"]],
];

/** What each one is, in a few words — the one thing the registry does not say. */
const BLURB: Record<string, string> = {
  anthropic: "Claude: Opus, Sonnet and Haiku",
  openai: "GPT models",
  google: "Gemini, from Google AI Studio",
  deepseek: "DeepSeek chat and reasoner, low prices",
  mistral: "Mistral Large, Medium and Small",
  xai: "Grok models",
  azure_openai: "OpenAI models in your Azure tenant",
  bedrock: "Claude, Llama and more in your AWS account",
  vertex: "Gemini and more in your Google Cloud project",
  groq: "Open models on very fast hardware",
  together: "Open models, hosted",
  fireworks: "Open models, hosted and fast",
  cerebras: "Open models at very high speed",
  nvidia: "Open models on NVIDIA NIM",
  huggingface: "Open models through Inference Providers",
  openrouter: "One key, hundreds of models",
  moonshot: "Kimi models",
  qwen: "Qwen models from Alibaba Cloud",
  zai: "GLM models",
  cohere: "Command models",
  ollama: "Models on a machine of your own",
  lmstudio: "Models on a machine of your own",
  custom: "Any OpenAI-compatible server",
};

type Step = "choose" | "key" | "test" | "ready" | "done";
const STEPS: [Step, string][] = [["choose", "Provider"], ["key", "Key"], ["test", "Test"], ["ready", "Ready"], ["done", "Done"]];

function tileFacts(p: ProviderInfo, platformSearch: boolean): string[] {
  const out: string[] = [];
  out.push(p.search ? "searches on its own" : platformSearch ? "searches through your backend" : "no web search of its own");
  if (p.local) out.push("no key needed");
  if (p.reports_cost) out.push("states its own costs");
  if (p.kinds.some((k) => k.mode === "subscription")) out.push("key or subscription");
  return out;
}

export function ConnectProvider({ initial, readiness, onChanged, onClose }: {
  /** A provider to start on, skipping the grid. */
  initial?: string | null;
  readiness: Readiness | null;
  onChanged: () => void;
  /** Closed; with the provider just connected, so the page can show its card. */
  onClose: (provider?: string) => void;
}) {
  const registry = useProviders();
  const [step, setStep] = useState<Step>(initial ? "key" : "choose");
  const [provider, setProvider] = useState<string | null>(initial ?? null);
  const [saved, setSaved] = useState<SavedCredential | null>(null);
  const [names, setNames] = useState<string[]>([]);
  const seller = registry.find((p) => p.name === provider);

  useEffect(() => {
    get<{ name: string }[]>("/admin/settings/llm-credentials?include_hidden=true")
      .then((rows) => setNames(rows.map((r) => r.name.toLowerCase()))).catch(() => {});
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(saved?.provider); };
    window.addEventListener("keydown", onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { window.removeEventListener("keydown", onKey); document.body.style.overflow = previous; };
  }, [onClose, saved?.provider]);

  // Named after the provider, and never a name the install already holds:
  // names are unique, and a refusal on the first field is a poor welcome.
  const suggested = useMemo(() => {
    if (!seller) return "";
    const base = seller.label;
    if (!names.includes(base.toLowerCase())) return base;
    let n = 2;
    while (names.includes(`${base} ${n}`.toLowerCase())) n += 1;
    return `${base} ${n}`;
  }, [seller, names]);

  const at = STEPS.findIndex(([s]) => s === step);
  return <div className="modal-back" onClick={() => onClose(saved?.provider)}>
    <div className="modal connect" role="dialog" aria-modal="true" aria-label="Connect a provider"
      onClick={(e) => e.stopPropagation()}>
      <div className="connect-top">
        <h3>{step === "choose" || !seller ? "Connect a provider" : `Connect ${seller.label}`}</h3>
        <button type="button" className="icon small" aria-label="Close" title="Close"
          onClick={() => onClose(saved?.provider)}><Icon name="x" size={15} /></button>
      </div>
      <ol className="connect-steps" aria-label="Steps">
        {STEPS.map(([s, label], i) => <li key={s} className={i < at ? "done" : i === at ? "on" : ""}
          aria-current={i === at ? "step" : undefined}>
          <span className="n">{i < at ? "✓" : i + 1}</span><span className="l">{label}</span></li>)}
      </ol>

      {step === "choose" && <ChooseProvider registry={registry} readiness={readiness}
        onPick={(name) => { setProvider(name); setStep("key"); }} />}

      {step === "key" && seller && <>
        <p className="help connect-lede">{BLURB[seller.name] ? `${BLURB[seller.name]}. ` : ""}
          {seller.local ? "It runs on your own network and takes no key — give its address."
            : "Paste a key; it is encrypted at rest and never shown again."}</p>
        <CredentialForm key={seller.name} provider={seller.name} initialName={suggested} title=""
          submitLabel="Save and test" cancelLabel="Back"
          onCancel={() => { setStep("choose"); setProvider(null); }}
          onSaved={(said) => { setSaved(said); onChanged(); setStep("test"); }} />
      </>}

      {step === "test" && seller && saved && <TestStep seller={seller} saved={saved}
        onReplace={(said) => { setSaved({ ...saved, ...said, founder: saved.founder }); onChanged(); }}
        onNext={() => setStep("ready")} />}

      {step === "ready" && seller && saved && <ReadyStep seller={seller} saved={saved}
        onChanged={onChanged} onNext={() => setStep("done")} />}

      {step === "done" && seller && saved && <DoneStep seller={seller} saved={saved}
        onClose={() => onClose(seller.name)} />}
    </div>
  </div>;
}

// ── (a) who sells the tokens ────────────────────────────────────────────────────

function ChooseProvider({ registry, readiness, onPick }: {
  registry: ProviderInfo[]; readiness: Readiness | null; onPick: (name: string) => void;
}) {
  const [term, setTerm] = useState("");
  const platformSearch = !!readiness?.install.web_search;
  const connected = new Set((readiness?.providers ?? []).map((p) => p.provider));
  const groups = useMemo(() => {
    const placed = new Set(GROUPS.flatMap(([, names]) => names));
    const out = GROUPS.map(([title, names]) => [title, names
      .map((n) => registry.find((p) => p.name === n)).filter(Boolean) as ProviderInfo[]] as const);
    const rest = registry.filter((p) => !placed.has(p.name));
    return out.map(([title, rows]) => [title, [...rows,
      ...rest.filter((p) => (title === "Local" ? p.local : title === "More" && !p.local))]] as const);
  }, [registry]);
  const q = term.trim().toLowerCase();
  const match = (p: ProviderInfo) => !q || p.label.toLowerCase().includes(q) || p.name.includes(q)
    || (BLURB[p.name] ?? "").toLowerCase().includes(q);
  const any = groups.some(([, rows]) => rows.some(match));
  return <div className="connect-choose">
    <input type="search" autoFocus value={term} onChange={(e) => setTerm(e.target.value)}
      aria-label="Search providers" placeholder="Search — Anthropic, Groq, Ollama…" />
    {registry.length === 0 && <p role="status">Loading providers…</p>}
    {groups.map(([title, rows]) => {
      const shown = rows.filter(match);
      if (!shown.length) return null;
      return <section key={title} className="pgroup" aria-label={title}>
        <h4>{title}</h4>
        <div className="ptiles">
          {shown.map((p) => <button key={p.name} type="button" className="ptile" onClick={() => onPick(p.name)}>
            <span className="pt-name">{p.label}{connected.has(p.name) && <span className="chip ok">connected</span>}</span>
            <span className="pt-blurb">{BLURB[p.name] ?? (p.local ? "A server on your own network" : "An OpenAI-compatible API")}</span>
            <span className="pt-facts">{tileFacts(p, platformSearch).join(" · ")}</span>
          </button>)}
        </div>
      </section>;
    })}
    {registry.length > 0 && !any && <p className="help">No provider matches “{term}”. A server that speaks
      the OpenAI API can be connected as <button type="button" className="linkish"
        onClick={() => onPick("custom")}>a custom endpoint</button>.</p>}
  </div>;
}

// ── (c) one real call on the key ────────────────────────────────────────────────

function failureAdvice(seller: ProviderInfo, said: SmokeResult): string {
  if (said.kind === "auth") {
    return `${seller.label} refused the key. Check that you copied all of it and that it is active in `
      + `the ${seller.label} console, then replace it below.`;
  }
  if (said.kind === "context" || said.kind === "fatal") {
    return `${seller.label} answered, but refused this request. If the message names the model, choose `
      + "another for the Fast class in the next step and test again from the provider's card.";
  }
  if (seller.local || seller.credential_base_url) {
    return "Check the address: it must be reachable from this server (on the sandbox network, by its "
      + "name there — not localhost), and the server must be running.";
  }
  return `That is usually the network between this server and ${seller.label} — a firewall or proxy `
    + `in the way — or ${seller.label} having a bad minute. The key is saved either way; test it again `
    + "here, or later from its card.";
}

function TestStep({ seller, saved, onReplace, onNext }: {
  seller: ProviderInfo; saved: SavedCredential;
  onReplace: (said: SavedCredential) => void; onNext: () => void;
}) {
  const callable = saved.mode === "api_key" && !!seller.protocol;
  const [said, setSaid] = useState<SmokeResult | null>(null);
  const [cannot, setCannot] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [replacing, setReplacing] = useState(false);
  const [tries, setTries] = useState(0);

  useEffect(() => {
    if (!callable) return;
    let alive = true;
    setBusy(true); setSaid(null); setCannot(null);
    post<SmokeResult>(`/admin/settings/llm-credentials/${saved.id}/smoke`, {})
      .then((r) => { if (alive) setSaid(r); })
      .catch((e) => { if (alive) setCannot((e as Error).message); })
      .finally(() => { if (alive) setBusy(false); });
    return () => { alive = false; };
  }, [saved.id, tries]);

  const asCredential: Credential = { id: saved.id, name: saved.name, mode: saved.mode,
    provider: saved.provider, projects: [] };
  return <div className="connect-body">
    <p className="ok-line">✓ Saved <b>{saved.name}</b>.{saved.founder ? " The Founder drafts on it too." : ""}</p>
    {saved.warning && <p className="help warn">{saved.warning}</p>}
    {!callable && <div className="smoke neutral">
      <p><b>Nothing to call from here.</b> A {seller.kinds.find((k) => k.mode === saved.mode)?.label ?? "login"} pays
        only its own command-line tool, which runs only inside a project&apos;s sandbox — its first
        run is the test.</p>
    </div>}
    {busy && <div className="smoke working" role="status">
      <span className="working-dot" aria-hidden="true" /> Calling {seller.label} with one tiny request…
    </div>}
    {said?.ok === true && <div className="smoke s-good" role="status">
      <p><b>✓ {seller.label} answered</b> on <code>{said.served || said.model}</code>
        {said.latency_ms != null && ` in ${said.latency_ms} ms`}.</p>
      <p className="help">{said.priced_usd != null
        ? `That call cost about ${fmtUsd(said.priced_usd)}.`
        : "It has no price on this install yet, so the call's cost is unknown — the next step fixes that."}
        {said.called_tool === false && " It answered without calling the test tool; seats depend on tool "
          + "calls, and a full check will say how far that goes."}</p>
    </div>}
    {said?.ok === null && <div className="smoke neutral"><p>{said.detail}</p></div>}
    {said?.ok === false && <div className="smoke s-bad" role="alert">
      <p><b>✕ The test call did not go through.</b></p>
      <p className="mono smoke-detail">{said.detail}</p>
      <p>{failureAdvice(seller, said)}</p>
    </div>}
    {cannot && <div className="smoke neutral" role="status">
      <p><b>Nothing to call yet.</b> {cannot}.</p>
      <p className="help">Choose its models in the next step, then test it from the provider&apos;s card.</p>
    </div>}
    {replacing && <CredentialForm editing={asCredential} title={`Replace the key for “${saved.name}”`}
      submitLabel="Save and test again" onCancel={() => setReplacing(false)}
      onSaved={(next) => { setReplacing(false); onReplace(next); setTries((t) => t + 1); }} />}
    <div className="actions">
      <button type="button" className="primary" disabled={busy} onClick={onNext}>
        {said?.ok === false ? "Continue anyway" : "Continue"}</button>
      {callable && !busy && <button type="button" onClick={() => setTries((t) => t + 1)}>Test again</button>}
      {said?.ok === false && !replacing && <button type="button" onClick={() => setReplacing(true)}>Replace the key</button>}
    </div>
  </div>;
}

// ── (d) what would still refuse a seat ──────────────────────────────────────────

function ReadyStep({ seller, saved, onChanged, onNext }: {
  seller: ProviderInfo; saved: SavedCredential; onChanged: () => void; onNext: () => void;
}) {
  const [nonce, setNonce] = useState(0);
  const { data } = useReadiness(nonce);
  const [editing, setEditing] = useState<string | null>(null);
  const [discovery, setDiscovery] = useState<Discovery>(NO_DISCOVERY);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const registry = useProviders();
  const p = data?.providers.find((row) => row.provider === seller.name);
  const changed = () => { setNonce((n) => n + 1); onChanged(); };
  const canList = saved.mode === "api_key" && !!seller.protocol;

  async function discover() {
    setDiscovery((d) => ({ ...d, busy: true, error: null }));
    try {
      setDiscovery({ models: await discoverModels(saved.id), from: saved.name, error: null, busy: false });
    } catch (e) { setDiscovery({ models: null, from: null, error: (e as Error).message, busy: false }); }
  }
  async function act(fn: () => Promise<unknown>, done: string) {
    setBusy(true); setNote(null);
    try { await fn(); setNote({ ok: true, text: done }); changed(); }
    catch (e) { setNote({ ok: false, text: (e as Error).message }); } finally { setBusy(false); }
  }

  if (!p) return <div className="connect-body"><p role="status">Reading what {seller.label} still needs…</p></div>;
  const status = providerStatus(p);
  const defaultLabel = registry.find((r) => r.name === data?.default_provider)?.label ?? data?.default_provider;
  return <div className="connect-body">
    <div className="ri-head">
      <StatusBadge p={p} />
      <span>{status.tone === "good" ? `${seller.label} is ready: every class has a priced model.`
        : p.ready ? `${seller.label} can run seats. One thing is worth a look.`
        : `A few things would still refuse seats on ${seller.label}. Each has its fix beside it:`}</span>
    </div>
    <IssueList provider={p.provider} issues={p.issues} onChanged={changed}
      onSetLineup={(cls) => setEditing(cls)} />
    <TierTable provider={p.provider} providerLabel={p.label} tiers={p.tiers} discovery={discovery}
      onDiscover={canList ? discover : undefined} editing={editing} setEditing={setEditing}
      onSaved={() => changed()} />

    {data && (!data.install.default_connected && !p.is_default || !data.install.founder) && <div className="connect-offers">
      {!data.install.default_connected && !p.is_default && <div className="offer">
        <span className="grow">The default provider{defaultLabel ? `, ${defaultLabel},` : ""} has no
          key here. Projects that pay nobody yet resolve their classes on the default.</span>
        <button type="button" className="small" disabled={busy} onClick={() => act(async () => {
          await put("/admin/settings/models/default-provider", { provider: seller.name });
          forgetModels();
        }, `${seller.label} is now the default provider.`)}>Make {seller.label} the default</button>
      </div>}
      {!data.install.founder && <div className="offer">
        <span className="grow">The Founder — the assistant that drafts a project from a paragraph — has no key.</span>
        <button type="button" className="small" disabled={busy} onClick={() => act(
          () => put("/admin/founder/credential/llm", { credential_id: saved.id }),
          `The Founder now drafts on ${saved.name}.`)}>Use this key for the Founder</button>
      </div>}
    </div>}
    {note && <p className={note.ok ? "ok-line" : "error"} role={note.ok ? "status" : "alert"}>{note.ok ? "✓ " : ""}{note.text}</p>}
    <div className="actions">
      <button type="button" className="primary" onClick={onNext}>{p.ready ? "Continue" : "Continue — I'll finish later"}</button>
    </div>
  </div>;
}

// ── (e) go ──────────────────────────────────────────────────────────────────────

function DoneStep({ seller, saved, onClose }: { seller: ProviderInfo; saved: SavedCredential; onClose: () => void }) {
  const callable = saved.mode === "api_key" && !!seller.protocol;
  const { data } = useReadiness();
  const p = data?.providers.find((row) => row.provider === seller.name);
  const [estimate, setEstimate] = useState<{ usd: number | null; steps: number; models: string[] } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!callable) return;
    post<{ estimate_usd: number | null; steps: number; models: string[] }>(
      `/admin/settings/llm-credentials/${saved.id}/check`, { dry_run: true })
      .then((r) => setEstimate({ usd: r.estimate_usd, steps: r.steps, models: r.models }))
      .catch(() => {});
  }, [saved.id]);
  async function runCheck() {
    setBusy(true); setError("");
    try {
      await post(`/admin/settings/llm-credentials/${saved.id}/check`, {});
      onClose();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) onClose();
      else setError((e as Error).message);
    } finally { setBusy(false); }
  }
  return <div className="connect-body">
    <div className="done-mark"><Icon name="check" size={22} strokeWidth={2.2} /></div>
    <p className="done-line"><b>{seller.label} is connected{p?.ready ? " and ready" : ""}.</b>{" "}
      {p && !p.ready ? "It still refuses some seats; its card in Providers lists what is left, with the fixes."
        : "Projects that pay with this key run on it from their first run."}</p>
    {callable && <p className="help">A full check runs short probes and five small agent tasks on each
      class&apos;s model{estimate ? ` (${estimate.models.join(", ")})` : ""} and grades them, so you know
      whether a model can drive a seat — not only answer a ping. It takes a few minutes and runs while
      you carry on; the results land on {seller.label}&apos;s card.</p>}
    {error && <p className="error" role="alert">{error}</p>}
    <div className="actions">
      {callable && <button type="button" className="primary" disabled={busy} onClick={runCheck}>
        {busy ? "Starting…" : estimate?.usd != null ? `Run a full check (≈${fmtUsd(estimate.usd)})` : "Run a full check"}</button>}
      <Link className={`btnlink${callable ? "" : " primary"}`} to="/projects/new">Start a project</Link>
      <button type="button" onClick={onClose}>Close</button>
    </div>
    {callable && estimate && estimate.usd == null && <p className="help">No cost estimate: a model here has no price yet.</p>}
  </div>;
}
