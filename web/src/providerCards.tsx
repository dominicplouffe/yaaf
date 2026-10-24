// Settings → Providers: one card per provider this install pays, each saying
// whether it can run seats and, where it cannot, the press that fixes it.
//
// It replaced a flat list of credentials and a Models page of twenty-three
// collapsed accordions, most of them for providers nobody here uses. Neither
// answered the question an administrator opens Settings with — "can my
// projects run, and if not, why?" — without opening every row. A card per
// connected provider answers it at a glance: the keys, what each class runs
// on with the evidence beside it (priced, listed by the key, checked), whether
// it can search, how many runs it may carry, and the list of what would be
// refused, each with its fix. The flat list is still here, under the cards,
// for what it was good at: bulk hiding and the hidden shelf.

import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { del, get, post, put } from "./api";
import { ConnectProvider } from "./connectProvider";
import { CredentialForm } from "./credentialForm";
import {
  Credential, LlmCredentials, PlanMeterCompact, Probe, ProbeLine, smokeLine, SmokeResult,
} from "./llmCredentials";
import { CheckConfirm, CheckResult, CheckRow, useCredentialChecks } from "./providerCheck";
import { forgetModels, ProviderInfo, useProviders } from "./providers";
import { discoverModels, Discovery, LineupSaved, NO_DISCOVERY, TierTable } from "./providerTiers";
import { FIX_LINK, IssueList, ProviderReadiness, StatusBadge, useReadiness } from "./readiness";
import { ConfirmModal, fmtAgo } from "./ui";
import { EmptyState } from "./ui/kit";
import { Icon } from "./ui/Icon";

const CRED = "/admin/settings/llm-credentials";

/** What a credential can be asked: a key on an API can be called and listed;
    a login pays only its CLI, which runs only in a sandbox. */
const callable = (mode: string, seller: ProviderInfo | undefined) => mode === "api_key" && !!seller?.protocol;

export function ProvidersSection({ onChanged }: { onChanged?: () => void }) {
  const [nonce, setNonce] = useState(0);
  const { data, error } = useReadiness(nonce);
  const registry = useProviders();
  const [creds, setCreds] = useState<Credential[]>([]);
  const [scoreboard, setScoreboard] = useState<Record<string, CheckRow>>({});
  const [searchLabel, setSearchLabel] = useState<string | null>(null);
  const [params, setParams] = useSearchParams();
  const connecting = params.get("connect");
  const focus = params.get("provider");
  const [stranded, setStranded] = useState<string[]>([]);

  const refresh = useCallback(() => {
    setNonce((n) => n + 1);
    onChanged?.();
  }, [onChanged]);
  useEffect(() => {
    get<Credential[]>(`${CRED}?include_hidden=true`).then(setCreds).catch(() => {});
    get<CheckRow[]>("/admin/settings/provider-checks")
      .then((rows) => setScoreboard(Object.fromEntries(rows.map((r) => [r.model, r])))).catch(() => {});
    get<{ configured: { label: string } | null }>("/admin/settings/web-search")
      .then((s) => setSearchLabel(s.configured?.label ?? null)).catch(() => {});
  }, [nonce]);

  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value === null) next.delete(key); else next.set(key, value);
    setParams(next, { replace: true });
  };
  const openConnect = () => setParam("connect", "1");
  const lineupSaved = (said: LineupSaved) => { setStranded(said.unrunnable ?? []); refresh(); };
  const hidden = creds.filter((c) => c.hidden_at);
  const providers = data?.providers ?? [];
  const otherDefault = data && !data.install.default_connected && providers.length > 0;
  const defaultLabel = registry.find((p) => p.name === data?.default_provider)?.label ?? data?.default_provider;

  return <>
    <div className="llm-header">
      <div>
        <h2>Providers</h2>
        <p className="help">Who sells the tokens your projects run on. A role names a class —
          Frontier, Balanced or Fast — and the provider a project pays says whose model that is.
          Each card below says whether that provider can run seats and, where it cannot, what
          fixes it. Keys are encrypted at rest and never shown back.</p>
      </div>
      <button type="button" className="primary" onClick={openConnect}>Connect a provider</button>
    </div>

    {error && <p className="error" role="alert">{error}</p>}
    {stranded.length > 0 && <div className="notice-box" role="alert">
      <b>That change leaves seats unable to run:</b>
      <ul>{stranded.map((line) => <li key={line}>{line}</li>)}</ul></div>}
    {otherDefault && <div className="strip" style={{ marginBottom: 14 }}>
      <div className="grow"><b>The default provider, {defaultLabel}, has no key here.</b>
        <div className="sb">A project that pays nobody yet, and the Founder before it has a key,
          resolve their classes on the default. Make one of yours the default from its card.</div></div>
    </div>}

    {!data && !error && <div className="card muted">Loading…</div>}
    {data && providers.length === 0 && <EmptyState icon="plus" title="No provider is connected yet."
        foot={<button type="button" className="primary" onClick={openConnect}>Connect a provider</button>}>
        Projects need one to run: pick who sells the tokens — Anthropic, OpenAI, DeepSeek, a cloud,
        or a model server on your own network — paste its key, and yaaf tests it and tells you
        what else, if anything, stands between it and a running project.
      </EmptyState>}

    {providers.map((p) => <ProviderCard key={p.provider} p={p} creds={creds} registry={registry}
      searchLabel={searchLabel} scoreboard={scoreboard} focused={focus === p.provider}
      onChanged={refresh} onLineupSaved={lineupSaved} />)}

    {hidden.length > 0 && <p className="help">{hidden.length} hidden credential{hidden.length === 1 ? " is" : "s are"} not
      on these cards. They are on the hidden shelf below, one press from coming back.</p>}

    {creds.length > 0 && <details className="all-creds" open={params.has("show") || undefined}>
      <summary><b>LLM credentials</b> — every key in one list, hidden ones included: rename,
        rotate, hide in bulk, restore</summary>
      <LlmCredentials title="Every credential" onAdd={openConnect} onChanged={refresh} nonce={nonce} />
    </details>}

    {connecting !== null && <ConnectProvider initial={connecting !== "1" ? connecting : null}
      readiness={data}
      onChanged={refresh}
      onClose={(provider) => {
        const next = new URLSearchParams(params);
        next.delete("connect");
        if (provider) next.set("provider", provider);
        setParams(next, { replace: true });
        refresh();
      }} />}
  </>;
}

// ── one provider ────────────────────────────────────────────────────────────────

function ProviderCard({ p, creds, registry, searchLabel, scoreboard, focused, onChanged, onLineupSaved }: {
  p: ProviderReadiness; creds: Credential[]; registry: ProviderInfo[];
  searchLabel: string | null; scoreboard: Record<string, CheckRow>; focused: boolean;
  onChanged: () => void; onLineupSaved: (said: LineupSaved) => void;
}) {
  const seller = registry.find((r) => r.name === p.provider);
  const [discovery, setDiscovery] = useState<Discovery>(NO_DISCOVERY);
  const [editing, setEditing] = useState<string | null>(null);
  const [viewing, setViewing] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<{ id: string; name: string; models: string[] | null } | null>(null);
  const [checkNonce, setCheckNonce] = useState<Record<string, number>>({});
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const ref = useRef<HTMLElement>(null);
  const first = p.credentials.find((c) => callable(c.mode, seller));

  useEffect(() => {
    if (!focused) return;
    ref.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    ref.current?.classList.add("flash");
    const t = setTimeout(() => ref.current?.classList.remove("flash"), 1600);
    return () => clearTimeout(t);
  }, [focused]);

  const discover = useCallback(async (credential = first) => {
    if (!credential) return;
    setDiscovery((d) => ({ ...d, busy: true, error: null }));
    try {
      const models = await discoverModels(credential.id);
      setDiscovery({ models, from: credential.name, error: null, busy: false });
      onChanged();  // "the key lists it" is read against the list just remembered
      return models;
    } catch (e) {
      setDiscovery({ models: null, from: null, error: (e as Error).message, busy: false });
      throw e;
    }
  }, [first?.id, onChanged]);

  async function makeDefault() {
    setNote(null);
    try {
      await put("/admin/settings/models/default-provider", { provider: p.provider });
      forgetModels();
      onChanged();
    } catch (e) { setNote({ ok: false, text: (e as Error).message }); }
  }

  const startCheck = (models: string[] | null) => {
    if (first) setConfirm({ id: first.id, name: first.name, models });
  };
  const setLineup = (cls: string) => {
    setEditing(cls);
    setTimeout(() => document.getElementById(`tier-${p.provider}-${cls}`)
      ?.scrollIntoView({ behavior: "smooth", block: "center" }), 50);
  };

  return <section ref={ref} className="card provider-card" aria-label={p.label} id={`provider-${p.provider}`}>
    <div className="pc-head">
      <h3>{p.label}</h3>
      <StatusBadge p={p} />
      {p.is_default ? <span className="chip accent" title="Bare classes resolve here for a project that pays nobody yet">Default</span>
        : <button type="button" className="small" onClick={makeDefault}>Make default</button>}
      {p.local && <span className="chip">on your network</span>}
    </div>
    {note && <p className={note.ok ? "help" : "error"}>{note.text}</p>}

    <div className="pc-creds">
      {p.credentials.map((c) => <CredentialRow key={c.id} c={c}
        full={creds.find((row) => row.id === c.id)} seller={seller} provider={p}
        checkNonce={checkNonce[c.id] ?? 0}
        onRunCheck={() => setConfirm({ id: c.id, name: c.name, models: null })}
        onDiscover={() => discover(c).catch(() => {})}
        onChanged={onChanged} />)}
    </div>

    <TierTable provider={p.provider} providerLabel={p.label} tiers={p.tiers}
      discovery={discovery} onDiscover={first ? () => { discover().catch(() => {}); } : undefined}
      editing={editing} setEditing={setEditing} onSaved={onLineupSaved}
      scoreboard={scoreboard} viewing={viewing} setViewing={setViewing} servedAt={p.served?.at} />
    {viewing && scoreboard[viewing] && <CheckResult row={scoreboard[viewing]} onDismiss={() => setViewing(null)} />}

    <div className="pc-facts">
      <SearchLine p={p} searchLabel={searchLabel} />
      <CeilingEdit provider={p.provider} label={p.label} ceiling={p.ceiling} onSaved={onChanged} />
      {p.served && <span className="help">The key listed {p.served.count} models {fmtAgo(p.served.at)}.</span>}
    </div>

    {p.issues.length > 0 && <div className="pc-issues">
      <h4>{p.ready ? "Worth a look" : "Before seats can run here"}</h4>
      <IssueList provider={p.provider} issues={p.issues} onChanged={onChanged}
        onSetLineup={setLineup} onCheck={first ? (models) => startCheck(models) : undefined} />
    </div>}

    {confirm && <CheckConfirm credential={confirm} models={confirm.models}
      onCancel={() => setConfirm(null)}
      onStarted={(why) => {
        setCheckNonce((prev) => ({ ...prev, [confirm.id]: Date.now() }));
        setConfirm(null);
        if (why) setNote({ ok: true, text: why });
      }} />}
  </section>;
}

function SearchLine({ p, searchLabel }: { p: ProviderReadiness; searchLabel: string | null }) {
  const ok = <span className="good"><Icon name="check" size={13} strokeWidth={2.2} /></span>;
  if (p.search === "own") return <span className="pc-search">{ok} Searches the web on its own API.</span>;
  if (p.search === "platform") return <span className="pc-search">{ok} Searches through{" "}
    {searchLabel ?? "this install's search backend"}.</span>;
  return <span className="pc-search"><span className="warn"><Icon name="alert" size={13} strokeWidth={2.2} /></span>{" "}
    Cannot search the web — roles that browse are refused here.{" "}
    <Link to={FIX_LINK.search}>Set up web search</Link></span>;
}

function CeilingEdit({ provider, label, ceiling, onSaved }: {
  provider: string; label: string; ceiling: number | null; onSaved: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(ceiling ? String(ceiling) : "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { setValue(ceiling ? String(ceiling) : ""); }, [ceiling]);
  async function save() {
    setBusy(true); setError("");
    try {
      await put("/admin/settings/models/ceiling", { provider, limit: value.trim() ? Number(value) : null });
      setOpen(false); onSaved();
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const id = `ceiling-${provider}`;
  if (!open) return <span className="pc-ceiling">
    Runs at once, across every project: <b>{ceiling ?? "no ceiling"}</b>{" "}
    <button type="button" className="linkish" onClick={() => setOpen(true)}
      aria-label={`Change how many runs ${label} may carry at once`}>change</button>
  </span>;
  return <span className="pc-ceiling editing">
    <label htmlFor={id}>Runs on {label} at once</label>
    <input id={id} type="number" min={1} max={1000} value={value} disabled={busy} placeholder="no ceiling"
      onChange={(e) => setValue(e.target.value)} />
    <button type="button" className="small primary" disabled={busy || value === (ceiling ? String(ceiling) : "")}
      onClick={save}>Save</button>
    <button type="button" className="small" disabled={busy} onClick={() => { setOpen(false); setValue(ceiling ? String(ceiling) : ""); }}>Cancel</button>
    <span className="help">Blank lifts it. Over the ceiling, runs wait their turn rather than fail on the provider&apos;s rate limit.</span>
    {error && <span className="error" role="alert">{error}</span>}
  </span>;
}

// ── one credential on a provider's card ─────────────────────────────────────────

function CredentialRow({ c, full, seller, provider, checkNonce, onRunCheck, onDiscover, onChanged }: {
  c: ProviderReadiness["credentials"][number];
  full: Credential | undefined;
  seller: ProviderInfo | undefined;
  provider: ProviderReadiness;
  checkNonce: number;
  onRunCheck: () => void;
  onDiscover: () => void;
  onChanged: () => void;
}) {
  const [probe, setProbe] = useState<Probe | null>(null);
  const [probing, setProbing] = useState<"smoke" | "models" | null>(null);
  const [editing, setEditing] = useState(false);
  const [asking, setAsking] = useState<"hide" | "delete" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const canCall = callable(c.mode, seller);
  const checks = useCredentialChecks(canCall ? c.id : null);
  // Results stay on screen from the moment they were running here until
  // dismissed; a check finished last week is the tier table's to show.
  const [watched, setWatched] = useState<Set<string>>(new Set());
  const wasRunning = useRef(false);
  useEffect(() => { if (checkNonce) checks.reload(); }, [checkNonce]);
  useEffect(() => {
    const running = checks.rows.filter((r) => r.status === "running").map((r) => r.id);
    if (running.length) setWatched((prev) => new Set([...prev, ...running]));
    if (wasRunning.current && !checks.running) onChanged();
    wasRunning.current = checks.running;
  }, [checks.rows]);
  const live = (full?.projects ?? []).filter((x) => x.live);

  async function smoke() {
    setProbing("smoke"); setProbe(null);
    try { setProbe(smokeLine(await post<SmokeResult>(`${CRED}/${c.id}/smoke`, {}))); }
    catch (e) { setProbe({ ok: false, text: `✕ ${(e as Error).message}` }); }
    finally { setProbing(null); }
  }
  async function models() {
    setProbing("models"); setProbe(null);
    try {
      const found = await discoverModels(c.id);
      const listed = found.filter((m) => m.listed).length;
      setProbe({ ok: true, text: `✓ ${found.length} models answer to this key${listed ? ` (${listed} in the catalog)` : ""} — offered when you change a class below`,
        models: found.map((m) => m.id) });
      onDiscover();
    } catch (e) { setProbe({ ok: false, text: `✕ ${(e as Error).message}` }); }
    finally { setProbing(null); }
  }
  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await fn(); setAsking(null); onChanged(); }
    catch (e) { setError((e as Error).message); setAsking(null); } finally { setBusy(false); }
  }

  return <div className="pc-cred">
    <div className="pc-cred-line">
      <span className="pc-cred-name"><b>{c.name}</b> <span className="chip">{c.kind}</span>{" "}
        <span className="help">{usageLine(live)
          || (full?.projects.length ? "only ended projects used it" : "no project uses it yet")}</span></span>
      <span className="pc-cred-acts">
        {canCall && <button type="button" className="small" disabled={probing !== null || busy}
          title="One real, trivial call on this key — a few hundred tokens" onClick={smoke}>
          {probing === "smoke" ? "Calling…" : "Test"}</button>}
        {seller?.protocol && <button type="button" className="small" disabled={probing !== null || busy}
          title="Ask the provider which models answer to this key — free" onClick={models}>
          {probing === "models" ? "Asking…" : "Models"}</button>}
        {canCall && <button type="button" className="small" disabled={checks.running || busy}
          title="Probes and small agent tasks on each class's model, graded — spends this key's money; the cost is shown first"
          onClick={onRunCheck}>{checks.running ? "Checking…" : "Run check"}</button>}
        <button type="button" className="small" disabled={busy || !full} onClick={() => setEditing((v) => !v)}
          aria-expanded={editing}>Edit</button>
        <button type="button" className="small" disabled={busy || !!full?.in_use}
          title={full?.in_use ? "A live project pays with this — reassign or stop it first" : `Hide ${c.name}`}
          onClick={() => setAsking("hide")}>Hide</button>
        {full && full.projects.length === 0 && <button type="button" className="small danger" disabled={busy}
          onClick={() => setAsking("delete")}>Delete</button>}
      </span>
    </div>
    {c.mode === "subscription" && seller?.plan_meter && <div className="pc-meter">
      <PlanMeterCompact credentialId={c.id} mode={c.mode} auto={!!full?.in_use} meters />
    </div>}
    {error && <p className="error" role="alert">{error}</p>}
    {probe && <ProbeLine probe={probe} onDismiss={() => setProbe(null)} />}
    {editing && full && <CredentialForm editing={full} title={`Rename or rotate “${c.name}”`}
      onCancel={() => setEditing(false)}
      onSaved={() => { setEditing(false); onChanged(); }} />}
    {checks.rows.filter((r) => watched.has(r.id)).map((r) => <CheckResult key={r.id} row={r}
      onDismiss={() => setWatched((prev) => { const next = new Set(prev); next.delete(r.id); return next; })} />)}

    {asking === "hide" && <ConfirmModal title={`Hide “${c.name}”?`} confirmLabel="Hide" busy={busy}
      onCancel={() => setAsking(null)}
      onConfirm={() => act(async () => {
        const said = await post<{ refused: { reason: string }[] }>(`${CRED}/hide`, { ids: [c.id] });
        if (said.refused?.length) throw new Error(said.refused[0].reason);
      })}>
      <p>It leaves this card, the project picker, the Founder&apos;s list and every MCP spec.
        Nothing is deleted: projects that ended keep naming what paid for them, and it comes back
        from the hidden shelf under <b>LLM credentials</b> in one press.
        {provider.credentials.length === 1 && ` It is ${provider.label}'s only key here, so the card goes with it.`}</p>
    </ConfirmModal>}
    {asking === "delete" && <ConfirmModal title={`Delete “${c.name}”?`} confirmLabel="Delete credential"
      danger busy={busy} onCancel={() => setAsking(null)}
      onConfirm={() => act(() => del(`${CRED}/${c.id}`))}>
      <p>No project has ever been assigned to this credential, so nothing loses its record when
        it goes. The stored value is destroyed and cannot be recovered.</p>
    </ConfirmModal>}
  </div>;
}


/** "pays A, B · fallback for C": what a key does for each live project. */
function usageLine(live: { name: string; as?: string }[]): string {
  const list = (names: string[]) => names.slice(0, 2).join(", ")
    + (names.length > 2 ? ` and ${names.length - 2} more` : "");
  const pays = live.filter((p) => p.as !== "fallback").map((p) => p.name);
  const backs = live.filter((p) => p.as === "fallback").map((p) => p.name);
  return [pays.length ? `pays ${list(pays)}` : "", backs.length ? `fallback for ${list(backs)}` : ""]
    .filter(Boolean).join(" · ");
}
