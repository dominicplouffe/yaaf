// What each Settings section's state is, said once, for the rail, the phone's
// index and the Overview to read alike. Every note is a state ("3 ready ·
// 1 blocked", "Brave · $0.005 a query"), never a description of the section:
// the rail answers "is anything wrong here?" without opening anything.

import { names, SettingsData, founderOf, liveProjects } from "./data";
import { providerState, ProviderReadiness, STATE_ORDER } from "../readiness";
import { splitLead } from "../settingsCopy";

export type SectionKey =
  | "overview" | "providers" | "models" | "prices" | "search" | "founder" | "slack" | "people" | "account";

export type NoteTone = "good" | "warn" | "critical" | "hollow" | "";

export interface RailNote { text: string; tone: NoteTone; count?: number }

export const SECTIONS: ({ group: string; admin: boolean } | { key: SectionKey; label: string; admin: boolean })[] = [
  { key: "overview", label: "Overview", admin: true },
  { group: "Install", admin: true },
  { key: "providers", label: "Providers", admin: true },
  { key: "models", label: "Models", admin: true },
  { key: "prices", label: "Model prices", admin: true },
  { key: "search", label: "Web search", admin: true },
  { key: "founder", label: "Founder", admin: true },
  { key: "slack", label: "Slack", admin: true },
  { key: "people", label: "People", admin: true },
  { group: "You", admin: false },
  { key: "account", label: "Account", admin: false },
];

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** Providers in the order they are read: what stops seats first, the
    default first within a state. */
export function byHealth(rows: ProviderReadiness[]): ProviderReadiness[] {
  return [...rows].sort((a, b) => STATE_ORDER[providerState(a)] - STATE_ORDER[providerState(b)]
    || Number(b.is_default) - Number(a.is_default) || a.label.localeCompare(b.label));
}

export function lineupModels(rows: ProviderReadiness[]): { model: string; priced: boolean; uses: string[] }[] {
  const out = new Map<string, { model: string; priced: boolean; uses: string[] }>();
  for (const p of rows) {
    for (const t of p.tiers) {
      if (!t.model) continue;
      const found = out.get(t.model) ?? { model: t.model, priced: t.priced || t.states_cost, uses: [] };
      found.uses.push(`${t.label} · ${p.label}`);
      out.set(t.model, found);
    }
  }
  return [...out.values()];
}

export function testTone(test: { ok: boolean } | null | undefined): NoteTone {
  return !test ? "hollow" : test.ok ? "good" : "critical";
}

export function railNote(key: SectionKey, d: SettingsData, email: string): RailNote {
  if (key === "account") {
    const live = (d.tokens ?? []).filter((t) => !t.revoked_at && new Date(t.expires_at).getTime() > Date.now());
    const soon = live.filter((t) => new Date(t.expires_at).getTime() - Date.now() < 3 * 86_400_000);
    if (d.tokens === null) return { text: email, tone: "" };
    if (soon.length) return { text: `${plural(soon.length, "token")} expiring soon`, tone: "warn" };
    return { text: live.length ? `${plural(live.length, "token")} · none expiring` : email, tone: "" };
  }
  if (key === "overview") {
    const needs = needsOf(d);
    const count = needs.filter((n) => n.tone !== "neutral").length;
    if (!d.readiness) return { text: "…", tone: "" };
    return count ? { text: `${count} need${count === 1 ? "s" : ""} you`, tone: "", count }
      : { text: "Nothing needs you", tone: "good" };
  }
  if (key === "people") {
    if (!d.users) return { text: "…", tone: "" };
    const active = d.users.filter((u) => u.active);
    const expired = active.filter((u) => !u.has_password && u.invite?.expired).length;
    const waiting = active.filter((u) => !u.has_password).length;
    if (expired) return { text: `${plural(expired, "invite link")} ran out`, tone: "warn" };
    if (waiting) return { text: `${waiting} haven’t signed in`, tone: "warn" };
    return { text: plural(active.length, "person", "people"), tone: "" };
  }
  if (key === "providers") {
    if (!d.readiness) return { text: "…", tone: "" };
    const rows = d.readiness.providers;
    if (!rows.length) return { text: "None connected", tone: "critical" };
    const count = (s: string) => rows.filter((p) => providerState(p) === s).length;
    const parts: string[] = [];
    const ready = count("ready");
    if (ready) parts.push(`${ready} ready`);
    for (const [s, word] of [["blocked", "blocked"], ["failing", "failing"], ["attention", "to look at"],
                             ["untested", "untested"]] as const) {
      if (count(s)) parts.push(`${count(s)} ${word}`);
    }
    const tone: NoteTone = count("blocked") || count("failing") ? "critical"
      : count("attention") ? "warn" : count("untested") ? "hollow" : "good";
    return { text: parts.slice(0, 2).join(" · "), tone };
  }
  if (key === "prices") {
    if (!d.readiness) return { text: "…", tone: "" };
    const used = lineupModels(d.readiness.providers);
    const missing = used.filter((m) => !m.priced).length;
    if (!used.length) return { text: "No lineup to price yet", tone: "" };
    return missing ? { text: `${plural(missing, "model")} in use unpriced`, tone: "critical" }
      : { text: `All ${used.length} in use priced`, tone: "good" };
  }
  if (key === "models") {
    if (!d.models || !d.readiness) return { text: "…", tone: "" };
    const label = d.models.lineups.find((r) => r.provider === d.models!.default_provider)?.label
      ?? d.models.default_provider;
    const cls = d.models.app_model?.startsWith("@") ? d.models.app_model.slice(1) : d.models.app_model;
    const word = cls ? cls.charAt(0).toUpperCase() + cls.slice(1) : "Shipped class";
    return { text: `${word} on ${label}`, tone: d.readiness.install.default_connected
      || !d.readiness.providers.length ? "" : "warn" };
  }
  if (key === "search") {
    const configured = d.search?.configured;
    if (!d.search) return { text: "…", tone: "" };
    if (configured) {
      return { text: `${configured.label} · $${configured.price_usd} a query`, tone: testTone(d.search.last_test) };
    }
    const without = (d.readiness?.providers ?? []).filter((p) => p.search === "none").length;
    return without ? { text: `Off · ${without} can’t search`, tone: "warn" } : { text: "Off", tone: "" };
  }
  if (key === "founder") {
    const f = founderOf(d.settings);
    if (!f) return { text: "…", tone: "" };
    if (!f.configured) return { text: "Not set", tone: "" };
    const tone: NoteTone = f.evidence?.state === "refused" ? "critical"
      : f.evidence?.state === "answered" ? "good" : "hollow";
    return { text: `${f.model}${f.source?.name ? ` · ${f.source.name}` : ""}`, tone };
  }
  // slack
  const s = d.settings?.slack;
  if (!s) return { text: "…", tone: "" };
  if (!s.bot_token_configured) return { text: "Off", tone: "" };
  const mode = s.app_token_configured && s.chairman_slack_user ? "chat in" : "mirrors";
  return {
    text: !s.last_test ? `Saved · not tested` : s.last_test.ok ? `Connected · ${mode}` : "Test failed",
    tone: testTone(s.last_test),
  };
}

// ── what needs you ──────────────────────────────────────────────────────────

export interface Need {
  key: string;
  tone: "critical" | "warn" | "neutral";
  icon: "alert" | "user" | "key" | "plug" | "clock" | "info" | "search" | "message";
  title: string;
  body?: string;
  facts?: string[];
  /** The provider whose issues (and their one-press fixes) belong under it. */
  provider?: ProviderReadiness;
  go: { label: string; section: SectionKey; params?: Record<string, string> };
  /** A Test the Overview can press in place. */
  test?: { credentialId: string };
}

const RANK = { critical: 0, warn: 1, neutral: 2 } as const;

/** Everything on the install that needs its administrator, ranked by what it
    stops: a seat yaaf would refuse or a key the provider turned away first,
    then what is worth a look, then what simply has not been confirmed yet. */
export function needsOf(d: SettingsData): Need[] {
  const out: Need[] = [];
  const providers = d.readiness?.providers ?? [];
  if (d.readiness && !providers.length) {
    out.push({ key: "connect", tone: "critical", icon: "plug", title: "No provider is connected yet",
      body: "Projects need one to run. Paste a key and yaaf tests it on the spot.",
      go: { label: "Connect a provider", section: "providers", params: { connect: "1" } } });
  }
  for (const p of byHealth(providers)) {
    const state = providerState(p);
    const pays = liveProjects(d.creds, p.provider);
    const facts = pays.length ? [`Pays ${names(pays, 2)}`] : ["No live project pays it", "Nothing is stopped today"];
    const open = { label: `Open ${p.label}`, section: "providers" as SectionKey, params: { provider: p.provider } };
    if (state === "blocked") {
      out.push({ key: `p-${p.provider}`, tone: "critical", icon: "alert",
        title: `${p.label} can’t run some seats`, provider: p, facts, go: open });
    } else if (state === "failing") {
      out.push({ key: `p-${p.provider}`, tone: "critical", icon: "key",
        title: `${p.label} refused its key`,
        body: p.evidence?.detail ? splitLead(p.evidence.detail)[0] : undefined,
        facts: pays.length ? [`Runs on ${names(pays, 2)} fail until it is rotated`] : facts,
        go: { ...open, label: "Rotate the key" } });
    } else if (state === "attention") {
      out.push({ key: `p-${p.provider}`, tone: "warn", icon: "info", title: `${p.label} needs a look`,
        provider: p, facts: pays.length ? [`Pays ${names(pays, 2)}`] : undefined, go: open });
    } else if (state === "untested") {
      const key = p.credentials.find((c) => c.mode === "api_key");
      out.push({ key: `p-${p.provider}`, tone: "neutral", icon: "key", title: `${p.label} has never answered`,
        body: "It is set up, but no Test, model list or run has come back on its key since it was saved.",
        facts: pays.length ? [`Pays ${names(pays, 2)}`] : undefined, go: open,
        test: key ? { credentialId: key.id } : undefined });
    }
  }
  if (d.readiness && providers.length && !d.readiness.install.default_connected) {
    const label = d.models?.lineups.find((r) => r.provider === d.readiness!.default_provider)?.label
      ?? d.readiness.default_provider;
    out.push({ key: "default", tone: "warn", icon: "info", title: `The default provider, ${label}, has no key here`,
      body: "A project that pays nobody yet, and the Founder before it has a key, resolve their classes on it.",
      go: { label: "Change the default", section: "models" } });
  }
  const founder = founderOf(d.settings);
  if (founder && !founder.configured && providers.length) {
    out.push({ key: "founder", tone: "neutral", icon: "message", title: "The Founder can’t draft yet",
      body: "It drafts a new project with you. Give it one of your keys.",
      go: { label: "Set up the Founder", section: "founder" } });
  } else if (founder?.configured && founder.evidence?.state === "refused") {
    out.push({ key: "founder", tone: "critical", icon: "key", title: "The Founder’s key was refused",
      body: founder.evidence.detail ? splitLead(founder.evidence.detail)[0] : undefined,
      go: { label: "Open Founder", section: "founder" } });
  }
  const slack = d.settings?.slack;
  if (slack?.bot_token_configured && slack.last_test && !slack.last_test.ok) {
    out.push({ key: "slack", tone: "warn", icon: "message", title: "Slack’s last test failed",
      body: slack.last_test.detail, go: { label: "Open Slack", section: "slack" } });
  } else if (slack?.bot_token_configured && !slack.last_test) {
    out.push({ key: "slack", tone: "neutral", icon: "message", title: "Slack is saved, not tested",
      go: { label: "Open Slack", section: "slack" } });
  }
  if (d.search?.configured && d.search.last_test && !d.search.last_test.ok) {
    out.push({ key: "search", tone: "warn", icon: "search", title: "Web search’s last test failed",
      body: d.search.last_test.detail, go: { label: "Open Web search", section: "search" } });
  }
  const active = (d.users ?? []).filter((u) => u.active && !u.has_password);
  const expired = active.filter((u) => u.invite?.expired);
  if (expired.length) {
    out.push({ key: "invites-expired", tone: "warn", icon: "user",
      title: `${plural(expired.length, "invite link")} ran out`,
      body: `${names(expired.map((u) => u.display_name))} can’t sign in until they get a new one.`,
      go: { label: "Open People", section: "people" } });
  }
  const waiting = active.filter((u) => !u.invite?.expired);
  if (waiting.length) {
    out.push({ key: "invites", tone: "warn", icon: "user",
      title: `${waiting.length === 1 ? "1 person hasn’t" : `${waiting.length} people haven’t`} set a password`,
      body: `${names(waiting.map((u) => u.display_name + (u.is_admin ? " (admin)" : "")))}. A link works once, for 72 hours. yaaf sends no email.`,
      go: { label: "Open People", section: "people" } });
  }
  const soon = (d.tokens ?? []).filter((t) => !t.revoked_at
    && new Date(t.expires_at).getTime() > Date.now()
    && new Date(t.expires_at).getTime() - Date.now() < 3 * 86_400_000);
  if (soon.length) {
    out.push({ key: "tokens", tone: "warn", icon: "clock",
      title: soon.length === 1 ? `Your token “${soon[0].name}” expires within 3 days`
        : `${soon.length} of your tokens expire within 3 days`,
      body: names(soon.map((t) => t.name)), go: { label: "Open Account", section: "account" } });
  }
  return out.sort((a, b) => RANK[a.tone] - RANK[b.tone]);
}
