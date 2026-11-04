// Settings → Providers: who sells the tokens your projects run on, in the
// order of what needs you.
//
// It was four full-size cards in a fixed order, so the one blocked provider
// sat fourth, 1,785px down, below a healthy card in green; every key carried
// six equal buttons, the paid one looking like the free one; and the same keys
// were listed again under a disclosure with different verbs. Now:
//
// - Providers are read by health: blocked, failing, needs a look, untested,
//   ready. One that needs you opens as a card with its fixes on top; one that
//   needs nothing is a single row that opens on a press.
// - Green is the server's to give, once a key has answered (key_evidence), and
//   the seal beside it says when and through what.
// - A key shows Test and Rotate. Everything else is in ⋯, and what spends
//   money says how much before the confirmation does.
// - "All keys" is a view of this section, not a second list under a fold: bulk
//   hide, the hidden shelf, search by name, project or digest.

import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { del, get, post, put } from "./api";
import { ConnectProvider } from "./connectProvider";
import { CredentialForm } from "./credentialForm";
import {
  Credential, LlmCredentials, PlanMeterCompact, Probe, ProbeLine, smokeLine, SmokeResult,
} from "./llmCredentials";
import { CheckConfirm, CheckResult, CheckRow, fmtUsd, useCredentialChecks } from "./providerCheck";
import { forgetModels, ProviderInfo, useProviders } from "./providers";
import { discoverModels, Discovery, LineupSaved, NO_DISCOVERY, TierTable } from "./providerTiers";
import {
  FIX_LINK, IssueList, ProviderReadiness, providerState, StatusBadge, useReadiness,
} from "./readiness";
import { ConfirmModal, fmtAgo, MenuItem, RowMenu } from "./ui";
import { EmptyState, Seg } from "./ui/kit";
import { Icon } from "./ui/Icon";
import { EvidenceSeal, Group, SectionHead } from "./settings/kit";
import { byHealth } from "./settings/state";
import { Link } from "react-router-dom";

const CRED = "/admin/settings/llm-credentials";

/** What a credential can be asked: a key on an API can be called and listed;
    a login pays only its CLI, which runs only in a sandbox. */
const callable = (mode: string, seller: ProviderInfo | undefined) => mode === "api_key" && !!seller?.protocol;
const needsYou = (p: ProviderReadiness) => ["blocked", "failing", "attention"].includes(providerState(p));
const perM = (n: number) => `$${n.toLocaleString(undefined, { maximumFractionDigits: 3 })}`;

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
  const view = params.get("view") === "keys" || params.has("show") ? "keys" : "providers";
  const [stranded, setStranded] = useState<string[]>([]);
  const [open, setOpen] = useState<Set<string>>(new Set());

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
  // A provider somebody linked to opens, wherever it sits.
  useEffect(() => { if (focus) setOpen((prev) => new Set([...prev, focus])); }, [focus]);

  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value === null) next.delete(key); else next.set(key, value);
    setParams(next, { replace: true });
  };
  const setView = (v: "providers" | "keys") => {
    const next = new URLSearchParams(params);
    next.delete("show");
    if (v === "keys") next.set("view", "keys"); else next.delete("view");
    setParams(next, { replace: true });
  };
  const openConnect = () => setParam("connect", "1");
  const lineupSaved = (said: LineupSaved) => { setStranded(said.unrunnable ?? []); refresh(); };
  const toggle = (name: string) => setOpen((prev) => {
    const next = new Set(prev);
    if (next.has(name)) next.delete(name); else next.add(name);
    return next;
  });
  const hidden = creds.filter((c) => c.hidden_at);
  const providers = byHealth(data?.providers ?? []);
  const urgent = providers.filter(needsYou);
  const calm = providers.filter((p) => !needsYou(p));
  const otherDefault = data && !data.install.default_connected && providers.length > 0;
  const defaultLabel = registry.find((p) => p.name === data?.default_provider)?.label ?? data?.default_provider;
  const card = (p: ProviderReadiness, inset = false) => <ProviderCard key={p.provider} p={p} creds={creds}
    registry={registry} searchLabel={searchLabel} scoreboard={scoreboard} focused={focus === p.provider}
    inset={inset} onCollapse={inset ? () => toggle(p.provider) : undefined}
    onChanged={refresh} onLineupSaved={lineupSaved} />;

  return <>
    <SectionHead title="Providers" lede="Who sells the tokens your projects run on."
      actions={<>
        {creds.length > 0 && <Seg value={view} onPick={setView} options={[
          { key: "providers", label: "By provider" },
          { key: "keys", label: "All keys", count: creds.length }]} />}
        <button type="button" className="primary" onClick={openConnect}>
          <Icon name="plus" size={15} />Connect a provider</button>
      </>} />

    {error && <p className="error" role="alert">{error}</p>}
    {stranded.length > 0 && <div className="notice-box" role="alert">
      <b>That change leaves seats unable to run:</b>
      <ul>{stranded.map((line) => <li key={line}>{line}</li>)}</ul></div>}
    {otherDefault && <div className="strip" style={{ marginBottom: 14 }}>
      <div className="grow"><b>The default provider, {defaultLabel}, has no key here.</b>
        <div className="sb">A project that pays nobody yet resolves its classes on it. Make one of yours
          the default from its ⋯ menu, or under Models.</div></div>
    </div>}

    {view === "keys" ? <LlmCredentials title="All keys" onAdd={openConnect} onChanged={refresh} nonce={nonce} />
      : <>
      {!data && !error && <div className="card muted">Loading…</div>}
      {data && providers.length === 0 && <EmptyState icon="plus" title="No provider is connected yet."
          foot={<button type="button" className="primary" onClick={openConnect}>Connect a provider</button>}>
          Projects need one to run. Paste a key — Anthropic, OpenAI, DeepSeek, a cloud, or a model
          server on your network — and yaaf tests it.
        </EmptyState>}

      {urgent.length > 0 && <Group title="Needs you" chip={urgent.length}>
        {urgent.map((p) => card(p))}
      </Group>}

      {calm.length > 0 && <Group title={urgent.length ? "Connected" : "Connected providers"} count={calm.length}>
        <div className="set-card prov-rows">
          {calm.map((p) => open.has(p.provider) ? <div key={p.provider} className="prov-open">{card(p, true)}</div>
            : <ProviderRow key={p.provider} p={p} creds={creds} searchLabel={searchLabel}
                onOpen={() => toggle(p.provider)} onChanged={refresh} />)}
        </div>
      </Group>}

      {hidden.length > 0 && <p className="help" style={{ marginTop: 12 }}>
        {hidden.length} hidden key{hidden.length === 1 ? "" : "s"}, {hidden.map((c) => c.name).join(", ")}.{" "}
        <button type="button" className="linkish" onClick={() => {
          const next = new URLSearchParams(params); next.set("view", "keys"); next.set("show", "hidden");
          setParams(next, { replace: true });
        }}>Show hidden keys</button></p>}
    </>}

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

// ── a provider that needs nothing, on one line ──────────────────────────────────

function ProviderRow({ p, creds, searchLabel, onOpen, onChanged }: {
  p: ProviderReadiness; creds: Credential[]; searchLabel: string | null;
  onOpen: () => void; onChanged: () => void;
}) {
  const [testing, setTesting] = useState(false);
  const [said, setSaid] = useState<string | null>(null);
  const priced = p.tiers.filter((t) => t.price);
  const lo = Math.min(...priced.map((t) => Math.min(t.price!.input, t.price!.output)));
  const hi = Math.max(...priced.map((t) => Math.max(t.price!.input, t.price!.output)));
  const live = new Set(creds.filter((c) => (c.provider ?? "") === p.provider && !c.hidden_at)
    .flatMap((c) => c.projects.filter((x) => x.live).map((x) => x.name)));
  const keyName = p.credentials.map((c) => c.name).join(", ");
  const facts = [
    priced.length ? `${perM(lo)}–${perM(hi)} per M` : p.states_cost ? "stated per call" : null,
    live.size ? `pays ${live.size} project${live.size === 1 ? "" : "s"}` : "no project pays it yet",
    `key ${keyName}`,
    p.ceiling ? `up to ${p.ceiling} runs at once` : null,
    p.search === "own" ? "searches itself" : p.search === "platform" ? `searches through ${searchLabel ?? "your backend"}` : null,
  ].filter(Boolean);
  const untested = providerState(p) === "untested";
  const key = p.credentials.find((c) => c.mode === "api_key");

  async function test() {
    if (!key) return;
    setTesting(true); setSaid(null);
    try { setSaid(smokeLine(await post<SmokeResult>(`${CRED}/${key.id}/smoke`, {})).text); }
    catch (e) { setSaid(`✕ ${(e as Error).message}`); }
    finally { setTesting(false); onChanged(); }
  }

  return <div className="prov-row" id={`provider-${p.provider}`}>
    <button type="button" className="prov-hit" aria-expanded={false} onClick={onOpen}
      aria-label={`${p.label}: open`}>
      <span className="nm"><b>{p.label}</b><span><StatusBadge p={p} />
        {p.is_default && <span className="chip accent">Default</span>}
        {p.local && <span className="chip">Your network</span>}</span></span>
      <span className="line"><span className="mono">{p.tiers.map((t) => t.model ?? "—").join(" · ")}</span>
        {facts.join(" · ")}</span>
    </button>
    <span className="ev">
      {untested ? <>{said ? <span className="help">{said}</span> : <EvidenceSeal evidence={p.evidence} />}
        {key && <button type="button" className="small" disabled={testing} onClick={test}
          title="One real, trivial call on the key — a few hundred tokens">{testing ? "Calling…" : "Test"}</button>}</>
        : <EvidenceSeal evidence={p.evidence} />}
    </span>
    <button type="button" className="icon small prov-chev" aria-label={`Open ${p.label}`} onClick={onOpen}>
      <Icon name="down" size={15} /></button>
  </div>;
}

// ── one provider, open ──────────────────────────────────────────────────────────

function ProviderCard({ p, creds, registry, searchLabel, scoreboard, focused, inset, onCollapse,
                        onChanged, onLineupSaved }: {
  p: ProviderReadiness; creds: Credential[]; registry: ProviderInfo[];
  searchLabel: string | null; scoreboard: Record<string, CheckRow>; focused: boolean;
  inset?: boolean; onCollapse?: () => void;
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
  const state = providerState(p);

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
      onChanged();  // a key the provider turned away is Failing now, not Untested
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
  const refused = p.credentials.filter((c) => c.evidence?.state === "refused");

  return <section ref={ref} className={`prov-card ${state}${inset ? " inset" : ""}`} aria-label={p.label}
    id={`provider-${p.provider}`}>
    <div className="prov-card-h">
      <h3>{p.label}</h3>
      <StatusBadge p={p} />
      {p.is_default && <span className="chip accent" title="Bare classes resolve here for a project that pays nobody yet">Default</span>}
      {p.local && <span className="chip">Your network</span>}
      <EvidenceSeal evidence={p.evidence} long />
      <span className="acts">
        {!p.is_default && <RowMenu label={`More for ${p.label}`} items={[
          { label: "Make it the default", icon: "target", onClick: makeDefault },
        ]} />}
        {onCollapse && <button type="button" className="icon small" aria-label={`Close ${p.label}`}
          onClick={onCollapse}><Icon name="down" size={15} /></button>}
      </span>
    </div>
    <div className="prov-card-b">
      {note && <p className={note.ok ? "help" : "error"}>{note.text}</p>}
      {refused.map((c) => <div key={c.id} className="sg-rec bad" role="alert">
        <span className="crit-ink"><Icon name="key" size={16} /></span>
        <div><b className="crit-ink">{p.label} refused {c.name} {c.evidence?.at ? fmtAgo(c.evidence.at) : ""}.</b>{" "}
          {c.evidence?.detail ? `${c.evidence.detail}. ` : ""}Check it was copied whole, or make a new key and
          rotate it below; then press Test.</div>
      </div>)}
      {/* Each issue says itself ("blocks seats", "worth a look") on its chip; a
          heading over them would say it twice. */}
      {p.issues.length > 0 && <IssueList provider={p.provider} issues={p.issues} onChanged={onChanged}
        onSetLineup={setLineup} onCheck={first ? (models) => startCheck(models) : undefined} />}

      <div>
        <TierTable provider={p.provider} providerLabel={p.label} tiers={p.tiers}
          discovery={discovery} onDiscover={first ? () => { discover().catch(() => {}); } : undefined}
          editing={editing} setEditing={setEditing} onSaved={onLineupSaved}
          scoreboard={scoreboard} viewing={viewing} setViewing={setViewing} servedAt={p.served?.at} />
        {viewing && scoreboard[viewing] && <CheckResult row={scoreboard[viewing]} onDismiss={() => setViewing(null)} />}
      </div>

      <div className="sg-facts">
        <SearchFact p={p} searchLabel={searchLabel} />
        <CeilingEdit provider={p.provider} label={p.label} ceiling={p.ceiling} onSaved={onChanged} />
        {p.served ? <span className="sg-fact">Model list read {fmtAgo(p.served.at)} · {p.served.count} models</span>
          : first && <span className="sg-fact">Model list not read yet ·{" "}
            <button type="button" className="linkish" onClick={() => { discover().catch(() => {}); }}>
              Read it, free</button></span>}
      </div>

      <div>
        {p.credentials.length > 1 && <p className="prov-sub" style={{ marginBottom: 8 }}>Keys · {p.credentials.length}</p>}
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {p.credentials.map((c) => <CredentialRow key={c.id} c={c}
            full={creds.find((row) => row.id === c.id)} seller={seller} provider={p}
            checkNonce={checkNonce[c.id] ?? 0}
            onRunCheck={() => setConfirm({ id: c.id, name: c.name, models: null })}
            onDiscover={() => discover(c).catch(() => {})}
            onChanged={onChanged} />)}
        </div>
      </div>
    </div>

    {confirm && <CheckConfirm credential={confirm} models={confirm.models}
      onCancel={() => setConfirm(null)}
      onStarted={(why) => {
        setCheckNonce((prev) => ({ ...prev, [confirm.id]: Date.now() }));
        setConfirm(null);
        if (why) setNote({ ok: true, text: why });
      }} />}
  </section>;
}

function SearchFact({ p, searchLabel }: { p: ProviderReadiness; searchLabel: string | null }) {
  if (p.search === "own") return <span className="sg-fact">Searches the web itself</span>;
  if (p.search === "platform") return <span className="sg-fact">Searches through {searchLabel ?? "your backend"}</span>;
  return <span className="sg-fact warn">Can’t search: roles that browse are refused ·{" "}
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
  if (!open) return <span className="sg-fact">
    {ceiling ? <>Up to <b>{ceiling}</b> runs at once</> : "No limit on runs at once"} ·{" "}
    <button type="button" className="linkish" onClick={() => setOpen(true)}
      aria-label={`Change how many runs ${label} may carry at once`}>Change</button>
  </span>;
  return <span className="pc-ceiling editing">
    <label htmlFor={id}>Runs on {label} at once, across every project</label>
    <input id={id} type="number" min={1} max={1000} value={value} disabled={busy} placeholder="no limit"
      onChange={(e) => setValue(e.target.value)} />
    <button type="button" className="small primary" disabled={busy || value === (ceiling ? String(ceiling) : "")}
      onClick={save}>Save</button>
    <button type="button" className="small" disabled={busy} onClick={() => { setOpen(false); setValue(ceiling ? String(ceiling) : ""); }}>Cancel</button>
    <span className="help">Empty lifts it. Over the limit, runs wait their turn instead of failing on the provider&apos;s rate limit.</span>
    {error && <span className="error" role="alert">{error}</span>}
  </span>;
}

// ── one key on a provider's card ────────────────────────────────────────────────

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
  const [editing, setEditing] = useState<"rotate" | "rename" | null>(null);
  const [asking, setAsking] = useState<"hide" | "delete" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [estimate, setEstimate] = useState<number | null | undefined>(undefined);
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
  // What a graded check would cost, asked once (a dry run spends nothing), so
  // the menu can say it before the confirmation does.
  useEffect(() => {
    if (!canCall) return;
    let alive = true;
    post<{ estimate_usd: number | null }>(`${CRED}/${c.id}/check`, { tasks: true, dry_run: true })
      .then((plan) => { if (alive) setEstimate(plan.estimate_usd); })
      .catch(() => { if (alive) setEstimate(null); });
    return () => { alive = false; };
  }, [c.id, canCall]);
  const live = (full?.projects ?? []).filter((x) => x.live);

  async function smoke() {
    setProbing("smoke"); setProbe(null);
    try { setProbe(smokeLine(await post<SmokeResult>(`${CRED}/${c.id}/smoke`, {}))); }
    catch (e) { setProbe({ ok: false, text: `✕ ${(e as Error).message}` }); }
    finally { setProbing(null); onChanged(); }
  }
  async function models() {
    setProbing("models"); setProbe(null);
    try {
      const found = await discoverModels(c.id);
      const chat = found.filter((m) => m.chat);
      const priced = chat.filter((m) => m.price).length;
      setProbe({ ok: true, text: `✓ ${chat.length} models answer to this key${chat.length ? ` (${priced} priced)` : ""}; offered when you change a class above`,
        models: chat.map((m) => m.id) });
      onDiscover();
    } catch (e) { setProbe({ ok: false, text: `✕ ${(e as Error).message}` }); onChanged(); }
    finally { setProbing(null); }
  }
  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await fn(); setAsking(null); onChanged(); }
    catch (e) { setError((e as Error).message); setAsking(null); } finally { setBusy(false); }
  }

  const items: MenuItem[] = [];
  if (seller?.protocol) items.push({ label: probing === "models" ? "Reading…" : "Read its model list", icon: "list",
    hint: "Free", note: "Asks the provider which models answer to this key", disabled: probing !== null, onClick: models });
  if (canCall) items.push({ label: checks.running ? "Checking…" : "Run a graded check…", icon: "chart",
    hint: estimate === undefined ? "…" : estimate === null ? "cost first" : `≈ ${fmtUsd(estimate)}`,
    note: "Probes and five small tasks per class, graded. Spends this key’s money; you confirm first.",
    disabled: checks.running || busy, onClick: onRunCheck });
  items.push({ label: "Rename", icon: "edit", disabled: busy || !full, onClick: () => setEditing("rename") });
  items.push({ label: "Hide", icon: "eye", disabled: busy || !!full?.in_use,
    note: full?.in_use ? "A live project pays with it. Reassign or stop it first." : undefined,
    onClick: () => setAsking("hide") });
  if (full && full.projects.length === 0) items.push({ label: "Delete…", icon: "trash", danger: true,
    disabled: busy, onClick: () => setAsking("delete") });

  return <div className="prov-key">
    <div>
      <div className="kn"><b>{c.name}</b><span className="chip">{c.kind}</span>
        <EvidenceSeal evidence={c.evidence} /></div>
      <div className="ku">{usageLine(live)
        || (full?.projects.length ? "Only ended projects used it" : "No project uses it yet")}</div>
    </div>
    <div className="acts">
      {canCall && <button type="button" className="small" disabled={probing !== null || busy}
        title="One real, trivial call on this key — a few hundred tokens" onClick={smoke}>
        {probing === "smoke" ? "Calling…" : "Test"}</button>}
      <button type="button" className="small" disabled={busy || !full} aria-expanded={editing === "rotate"}
        onClick={() => setEditing(editing === "rotate" ? null : "rotate")}>Rotate key</button>
      <RowMenu label={`More for ${c.name}`} items={items} />
    </div>
    {c.mode === "subscription" && seller?.plan_meter && <div className="full">
      <PlanMeterCompact credentialId={c.id} mode={c.mode} auto={!!full?.in_use} meters />
    </div>}
    {error && <p className="error full" role="alert">{error}</p>}
    {probe && <div className="full"><ProbeLine probe={probe} onDismiss={() => setProbe(null)} /></div>}
    {editing && full && <div className="full"><CredentialForm editing={full}
      title={editing === "rotate" ? `Rotate “${c.name}”` : `Rename “${c.name}”`}
      onCancel={() => setEditing(null)}
      onSaved={() => { setEditing(null); onChanged(); }} /></div>}
    {checks.rows.filter((r) => watched.has(r.id)).map((r) => <div key={r.id} className="full"><CheckResult row={r}
      onDismiss={() => setWatched((prev) => { const next = new Set(prev); next.delete(r.id); return next; })} /></div>)}

    {asking === "hide" && <ConfirmModal title={`Hide “${c.name}”?`} confirmLabel="Hide" busy={busy}
      onCancel={() => setAsking(null)}
      onConfirm={() => act(async () => {
        const said = await post<{ refused: { reason: string }[] }>(`${CRED}/hide`, { ids: [c.id] });
        if (said.refused?.length) throw new Error(said.refused[0].reason);
      })}>
      <p>It leaves this page, the project picker, the Founder&apos;s list and every MCP spec.
        Nothing is deleted: projects that ended keep naming what paid for them, and it comes back
        from <b>All keys → Hidden</b> in one press.
        {provider.credentials.length === 1 && ` It is ${provider.label}'s only key here, so the provider goes with it.`}</p>
    </ConfirmModal>}
    {asking === "delete" && <ConfirmModal title={`Delete “${c.name}”?`} confirmLabel="Delete key"
      danger busy={busy} onCancel={() => setAsking(null)}
      onConfirm={() => act(() => del(`${CRED}/${c.id}`))}>
      <p>No project has ever been assigned to this key, so nothing loses its record when it goes.
        The stored value is destroyed and cannot be recovered.</p>
    </ConfirmModal>}
  </div>;
}


/** "pays A, B · fallback for C": what a key does for each live project. */
function usageLine(live: { name: string; as?: string }[]): string {
  const list = (names: string[]) => names.slice(0, 2).join(" and ")
    + (names.length > 2 ? ` and ${names.length - 2} more` : "");
  const pays = live.filter((p) => p.as !== "fallback").map((p) => p.name);
  const backs = live.filter((p) => p.as === "fallback").map((p) => p.name);
  return [pays.length ? `Pays ${list(pays)}` : "", backs.length ? `fallback for ${list(backs)}` : ""]
    .filter(Boolean).join(" · ");
}
