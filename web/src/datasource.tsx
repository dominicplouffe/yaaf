import { ReactNode } from "react";
import { SecretInput } from "./ui";

/** One data source form, used by every screen that offers one. A source has
    grown fields since the wizard first shipped — how it authenticates, how its
    results reach an agent — and a chairman who meets a shorter version of this
    form while creating a project, and a longer one in Settings afterwards, is
    being told the platform has two different ideas of what a data source is.
    It has one, and it lives here. */

export type SourceAuth = "none" | "bearer" | "oauth";
export type SourceAccess = "inline" | "bulk";

/** Everything the form collects, in the shape the two save paths agree on.
    Held as one object so a new field appears on both screens at once. */
export interface DataSourceDraft {
  name: string;
  url: string;
  auth: SourceAuth;
  bearer: string;
  clientId: string;
  clientSecret: string;
  scope: string;
  access: SourceAccess;
  roles: string[];
}

export const BLANK_DATA_SOURCE: DataSourceDraft = {
  name: "",
  url: "",
  auth: "none",
  bearer: "",
  clientId: "",
  clientSecret: "",
  scope: "",
  access: "inline",
  roles: [],
};

export function envVarName(name: string): string {
  return name.replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, "").toUpperCase();
}

/** Live-normalize a data source name to the API's slug rule (^[a-z0-9-]{2,32}$):
    the name becomes an env var, a tool prefix and secret names, so "SEMRush"
    must land as "semrush" here rather than bounce off the server as a regex.
    Trailing hyphens survive typing ("my-" on the way to "my-source"); leading
    ones never; the save handler strips the trailing ones. */
export function slugifyName(raw: string): string {
  return raw.toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/-+/g, "-")
    .replace(/^-+/, "").slice(0, 32);
}

/** The same rule the server applies to a data source's address: a full
    http(s) address, not a bare host and not a token pasted into the wrong box. */
export function isFullUrl(value: string): boolean {
  try {
    const parsed = new URL(value.trim());
    return (parsed.protocol === "http:" || parsed.protocol === "https:") && !!parsed.hostname;
  } catch {
    return false;
  }
}

/** The name as it will be saved — what the trailing-hyphen tolerance above
    costs is one strip at the door. */
export function savedName(draft: DataSourceDraft): string {
  return draft.name.replace(/-+$/, "");
}

/** A source is savable once it has a name the server accepts, an address, and
    somebody to give the tools to. Ungranted is not a draft state worth keeping:
    a source nobody receives is a secret stored for no one. */
export function sourceReady(draft: DataSourceDraft): boolean {
  return savedName(draft).length >= 2 && !!draft.url.trim() && draft.roles.length > 0;
}

/** The POST body for /admin/projects/{id}/mcp_servers. `bearer` is the one
    field a caller may need to override: on the add path a blank box means "no
    token", while on the edit path it means "keep the stored one", and only the
    caller knows which it is. */
export function dataSourceBody(draft: DataSourceDraft) {
  const oauth = draft.auth === "oauth";
  return {
    name: savedName(draft),
    url: draft.url.trim(),
    bearer: draft.auth === "bearer" ? draft.bearer.trim() || undefined : undefined,
    roles: draft.roles,
    access: draft.access,
    auth: draft.auth,
    client_id: oauth ? draft.clientId.trim() || undefined : undefined,
    client_secret: oauth ? draft.clientSecret.trim() || undefined : undefined,
    scope: oauth ? draft.scope.trim() || undefined : undefined,
  };
}

export type RoleOption = { slug: string; teams: string[] };

/** One checkbox per distinct role in the org, labelled with the team(s) running
    it — grants are per role, so sibling teams share a box. */
export function RoleTicks({ options, selected, onChange, emptyHint }: {
  options: RoleOption[];
  selected: string[];
  onChange: (slugs: string[]) => void;
  emptyHint?: string;
}) {
  if (options.length === 0)
    return (
      <p className="help">
        {emptyHint ?? "No teams yet — add a team on the Overview tab first."}
      </p>
    );
  return (
    <div className="pill-row">
      {options.map((option) => (
        <label key={option.slug} style={{ display: "flex", gap: 5, alignItems: "center" }}>
          <input
            type="checkbox"
            style={{ width: "auto" }}
            checked={selected.includes(option.slug)}
            onChange={(e) =>
              onChange(
                e.target.checked
                  ? [...selected, option.slug]
                  : selected.filter((slug) => slug !== option.slug),
              )
            }
          />
          {option.teams.join(", ")} ({option.slug})
        </label>
      ))}
    </div>
  );
}

/** How a source's results reach an agent — the choice that decides whether a
    data pull costs tokens or CPU. Offered wherever a source is written, because
    it is the one setting worth changing after the fact. */
export function AccessPicker({ value, onChange }: {
  value: SourceAccess;
  onChange: (next: SourceAccess) => void;
}) {
  return (
    <>
      <label className="field">How agents read it</label>
      <select value={value} onChange={(e) => onChange(e.target.value as SourceAccess)}>
        <option value="inline">inline — mounted as tools, results go into the model</option>
        <option value="bulk">bulk — results go to a file, via data_fetch</option>
      </select>
      <div className="help" style={{ marginTop: 4 }}>
        {value === "inline"
          ? "Every result is copied into the agent's context and billed by the token. Right for small lookups; ruinous for exports."
          : "The tools are not mounted at all, so nothing can pull rows into context by accident. Agents call data_fetch, which writes the full result to a workspace file they then process with code."}
      </div>
    </>
  );
}

/** How a source authenticates. "oauth" trades the pasted credential for a
    browser sign-in: Connect opens the provider's consent screen, and from then
    on the platform stores and refreshes the tokens itself. */
export function AuthPicker({ value, onChange, oauthNote }: {
  value: SourceAuth;
  onChange: (next: SourceAuth) => void;
  oauthNote?: ReactNode;
}) {
  return (
    <>
      <label className="field">How it authenticates</label>
      <select value={value} onChange={(e) => onChange(e.target.value as SourceAuth)}>
        <option value="none">none — the server is open</option>
        <option value="bearer">bearer token — paste a credential</option>
        <option value="oauth">OAuth — sign in through the provider</option>
      </select>
      {value === "oauth" && (
        <div className="help" style={{ marginTop: 4 }}>
          {oauthNote ?? (
            <>
              For hosted servers like Notion, Linear or Atlassian: saving opens the
              provider's sign-in in a new tab; finish it there and the source connects.
              Tokens are stored encrypted and refreshed automatically.
            </>
          )}
        </div>
      )}
    </>
  );
}

/** Manual OAuth client fields — needed only by providers without dynamic
    client registration, so they hide behind a disclosure. */
export function OAuthClientFields({
  clientId, clientSecret, scope, onClientId, onClientSecret, onScope,
}: {
  clientId: string;
  clientSecret: string;
  scope: string;
  onClientId: (v: string) => void;
  onClientSecret: (v: string) => void;
  onScope: (v: string) => void;
}) {
  return (
    <details style={{ marginTop: 6 }}>
      <summary className="help" style={{ cursor: "pointer" }}>
        Advanced — leave blank; most servers register yaaf automatically
      </summary>
      <div className="row" style={{ marginTop: 6 }}>
        <div>
          <label className="field">Client ID (optional)</label>
          <input
            value={clientId}
            onChange={(e) => onClientId(e.target.value)}
            placeholder="from the provider's developer console"
          />
        </div>
        <div>
          <label className="field">Client secret (optional)</label>
          <SecretInput
            value={clientSecret}
            onChange={onClientSecret}
            placeholder="only if the provider issued one"
          />
        </div>
        <div>
          <label className="field">Scope (optional)</label>
          <input
            value={scope}
            onChange={(e) => onScope(e.target.value)}
            placeholder="blank = the server's default"
          />
        </div>
      </div>
    </details>
  );
}

/** What a data source is, said once. Both screens open with it so the
    explanation a chairman reads while creating a project is the explanation
    they meet again in Settings. */
export function DataSourceIntro({ children }: { children?: ReactNode }) {
  return (
    <>
      <p className="subtitle">
        A data source is an MCP server — a URL that offers tools. An agent's sandbox is a locked
        box whose only way in is an environment variable you granted, so the address travels as
        one: saving <code>ppc</code> stores its URL encrypted and hands the granted roles{" "}
        <code>$MCP_PPC_URL</code>. That variable <em>is</em> the delivery mechanism, not a side
        effect — which is why everything about a source is edited here and nowhere else.
      </p>
      <p className="help">
        One save does all of it: validates the URL, stores it and any token encrypted, grants
        them to the roles you tick, and calls the server to confirm it answers. {children}
      </p>
    </>
  );
}

/** The fields themselves. The name box is hidden when editing (a source's name
    is its identity and its env var), and the bearer box takes its label from
    the caller for the same reason: blank means "none" here and "keep the
    stored one" there. */
export function DataSourceFields({
  value,
  onChange,
  roleOptions,
  showName = true,
  bearerLabel = "Bearer token",
  bearerPlaceholder = "paste the credential",
  bearerDisabled = false,
  belowBearer,
  rolesLabel = "Which roles get this source (nobody, until you tick)",
  rolesEmptyHint,
  oauthNote,
}: {
  value: DataSourceDraft;
  onChange: (next: DataSourceDraft) => void;
  roleOptions: RoleOption[];
  showName?: boolean;
  bearerLabel?: ReactNode;
  bearerPlaceholder?: string;
  bearerDisabled?: boolean;
  belowBearer?: ReactNode;
  rolesLabel?: string;
  rolesEmptyHint?: string;
  oauthNote?: ReactNode;
}) {
  const set = (patch: Partial<DataSourceDraft>) => onChange({ ...value, ...patch });
  const name = savedName(value);
  return (
    <>
      <div className="row">
        {showName && (
          <div>
            <label className="field">Name (lowercase slug)</label>
            <input
              value={value.name}
              onChange={(e) => set({ name: slugifyName(e.target.value) })}
              placeholder="figma, analytics, crm…"
            />
            {!!name && (
              <div className="help" style={{ marginTop: 4 }}>
                granted roles will get <code>${envVarName(`mcp-${name}-url`)}</code> and
                call its tools as <code>mcp__{name}__*</code>
              </div>
            )}
          </div>
        )}
        <div>
          <label className="field">URL — the server's full address</label>
          <input
            value={value.url}
            onChange={(e) => set({ url: e.target.value })}
            placeholder="https://host:3845/mcp"
          />
          {!!value.url.trim() && !isFullUrl(value.url) && (
            <div className="error" style={{ marginTop: 4 }}>
              Not a full address — like https://host:3845/mcp. localhost will not
              work: sandboxes are containers, use the host&apos;s LAN address.
            </div>
          )}
        </div>
        <div>
          <AuthPicker
            value={value.auth}
            onChange={(auth) => set({ auth })}
            oauthNote={oauthNote}
          />
        </div>
      </div>
      {value.auth === "bearer" && (
        <div style={{ maxWidth: 420 }}>
          <label className="field">{bearerLabel}</label>
          <SecretInput
            value={value.bearer}
            disabled={bearerDisabled}
            onChange={(bearer) => set({ bearer })}
            placeholder={bearerPlaceholder}
          />
          {belowBearer}
        </div>
      )}
      {value.auth === "oauth" && (
        <OAuthClientFields
          clientId={value.clientId}
          clientSecret={value.clientSecret}
          scope={value.scope}
          onClientId={(clientId) => set({ clientId })}
          onClientSecret={(clientSecret) => set({ clientSecret })}
          onScope={(scope) => set({ scope })}
        />
      )}
      <AccessPicker value={value.access} onChange={(access) => set({ access })} />
      <label className="field">{rolesLabel}</label>
      <RoleTicks
        options={roleOptions}
        selected={value.roles}
        onChange={(roles) => set({ roles })}
        emptyHint={rolesEmptyHint}
      />
    </>
  );
}
