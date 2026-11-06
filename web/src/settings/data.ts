// What the Settings page reads above its sections: one load, shared by the
// rail, the Overview and the phone's index, so the three can never disagree
// about whether something needs you. Each section still reads its own detail;
// when one changes something it calls `reload` and the rail follows.

import { useCallback, useEffect, useState } from "react";
import { get, InstallSpend, PersonalToken, SearchSettings, UserRow } from "../api";
import type { Credential } from "../llmCredentials";
import { DEFAULT_PROVIDER } from "../providers";
import { Evidence, Readiness, READINESS } from "../readiness";
import type { LastTest } from "./kit";
import { monthToDateUrl } from "./spend";

export interface PlatformSettings {
  configured: boolean;
  billing_mode: string;
  /** Which provider the platform credential pays; the Founder runs on it. */
  provider: string;
  /** The model the Founder runs on: its reference, resolved on that provider. */
  model: string;
  /** As set — a class (`@frontier`) until somebody names a model. */
  model_ref?: string;
  note: string;
  /** Its own key, or one of the LLM credentials, borrowed — named, never shown. */
  source?: { source: "own" | "llm_credential" | null; credential_id?: string; name?: string };
  /** Whether that key has been seen to work (yaaf.core.key_evidence). */
  evidence?: Evidence;
}

export interface SlackSettings {
  bot_token_configured: boolean;
  signing_secret_configured: boolean;
  app_token_configured: boolean;
  chairman_slack_user: string;
  channel_prefix: string;
  channel_visibility: string;
  auto_create_channels: boolean;
  /** The last "Test connection", forgotten whenever a token changes. */
  last_test?: LastTest | null;
}

export interface AppSettings {
  platform?: PlatformSettings;
  /** The same section, under the name it had when it could pay nobody else. */
  anthropic: PlatformSettings;
  slack: SlackSettings;
  llm_credentials: { total: number; hidden: number; in_use: number; offerable: number };
}

export interface ModelsSummary {
  default_provider: string;
  app_model: string;
  lineups: { provider: string; label: string }[];
}

export interface SettingsData {
  admin: boolean;
  settings: AppSettings | null;
  readiness: Readiness | null;
  users: UserRow[] | null;
  usersError: string | null;
  models: ModelsSummary | null;
  search: SearchSettings | null;
  tokens: PersonalToken[] | null;
  creds: Credential[] | null;
  /** This month so far, across every project — the Spend entry's state. */
  spend: InstallSpend | null;
  error: string | null;
  reload: () => void;
  reloadUsers: () => void;
}

export const founderOf = (s: AppSettings | null) => s ? (s.platform ?? s.anthropic) : null;

export function useSettingsData(admin: boolean): SettingsData {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [users, setUsers] = useState<UserRow[] | null>(null);
  const [usersError, setUsersError] = useState<string | null>(null);
  const [models, setModels] = useState<ModelsSummary | null>(null);
  const [search, setSearch] = useState<SearchSettings | null>(null);
  const [tokens, setTokens] = useState<PersonalToken[] | null>(null);
  const [creds, setCreds] = useState<Credential[] | null>(null);
  const [spend, setSpend] = useState<InstallSpend | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    get<PersonalToken[]>("/auth/tokens").then(setTokens).catch(() => setTokens([]));
    if (!admin) return;
    get<AppSettings>("/admin/settings").then((s) => { setSettings(s); setError(null); })
      .catch((e) => setError((e as Error).message));
    get<Readiness>(READINESS).then(setReadiness).catch(() => {});
    get<ModelsSummary>("/admin/settings/models").then(setModels).catch(() => {});
    get<SearchSettings>("/admin/settings/web-search").then(setSearch).catch(() => {});
    get<Credential[]>("/admin/settings/llm-credentials?include_hidden=true").then(setCreds)
      .catch(() => {});
    get<InstallSpend>(monthToDateUrl()).then(setSpend).catch(() => {});
  }, [admin]);

  const reloadUsers = useCallback(() => {
    if (!admin) return;
    get<UserRow[]>("/admin/users")
      .then((rows) => { setUsers(rows); setUsersError(null); })
      .catch((e) => setUsersError((e as Error).message));
  }, [admin]);

  useEffect(reload, [reload]);
  useEffect(reloadUsers, [reloadUsers]);

  return { admin, settings, readiness, users, usersError, models, search, tokens, creds, spend, error,
    reload, reloadUsers };
}

/** "Ines Laurent, Sam Okafor and Priya Raman". */
export function names(list: string[], most = 3): string {
  const shown = list.slice(0, most);
  const rest = list.length - shown.length;
  if (rest > 0) return `${shown.join(", ")} and ${rest} more`;
  return shown.length <= 1 ? shown.join("") : `${shown.slice(0, -1).join(", ")} and ${shown[shown.length - 1]}`;
}

/** How many live projects a provider's keys pay. */
export function liveProjects(creds: Credential[] | null, provider: string): string[] {
  const out = new Set<string>();
  for (const c of creds ?? []) {
    if ((c.provider ?? DEFAULT_PROVIDER) !== provider || c.hidden_at) continue;
    for (const p of c.projects) if (p.live) out.add(p.name);
  }
  return [...out].sort();
}
