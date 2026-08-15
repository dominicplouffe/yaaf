import { useEffect, useState } from "react";
import { get, post, RoleSummary } from "../api";

interface RoleDetail {
  slug: string;
  name: string;
  is_ceo_role: boolean;
  version: number;
  description: string;
  system_prompt: string;
  model: string;
  effort: string;
  // Booleans for the sandbox tools plus egress_allow: string[] — unknown keys
  // are preserved verbatim on save (the proxy generator reads this dict too).
  tool_permissions: Record<string, unknown>;
  secret_scopes: string[];
}

function egressLines(perms: Record<string, unknown>): string {
  const list = perms["egress_allow"];
  return Array.isArray(list) ? list.join("\n") : "";
}

interface McpSpec {
  name: string;
  url_env: string;
  bearer_env?: string;
  transport?: string;
}

// One server per line: "name URL_ENV_VAR [bearer TOKEN_ENV_VAR] [sse]".
// Friendlier than raw JSON, loses nothing we support.
function mcpLines(perms: Record<string, unknown>): string {
  const list = perms["mcp_servers"];
  if (!Array.isArray(list)) return "";
  return (list as McpSpec[])
    .map((s) =>
      [s.name, s.url_env, s.bearer_env ? `bearer ${s.bearer_env}` : "",
       s.transport === "sse" ? "sse" : ""].filter(Boolean).join(" "))
    .join("\n");
}

function parseMcpLines(text: string): McpSpec[] {
  const specs: McpSpec[] = [];
  for (const line of text.split("\n")) {
    const words = line.trim().split(/\s+/).filter(Boolean);
    if (words.length < 2) continue;
    const spec: McpSpec = { name: words[0], url_env: words[1] };
    for (let i = 2; i < words.length; i++) {
      if (words[i] === "bearer" && words[i + 1]) spec.bearer_env = words[++i];
      else if (words[i] === "sse") spec.transport = "sse";
    }
    specs.push(spec);
  }
  return specs;
}

const EMPTY: RoleDetail = {
  slug: "",
  name: "",
  is_ceo_role: false,
  version: 0,
  description: "",
  system_prompt: "",
  model: "claude-sonnet-5",
  effort: "high",
  tool_permissions: { files: true, bash: false, web: true },
  secret_scopes: [],
};

export default function Roles() {
  const [roles, setRoles] = useState<RoleSummary[]>([]);
  const [editing, setEditing] = useState<RoleDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  // Raw textarea text for the egress list — kept separate so blank lines
  // survive typing; the parsed domains live in tool_permissions.egress_allow.
  const [egressText, setEgressText] = useState("");
  const [mcpText, setMcpText] = useState("");
  const [portsText, setPortsText] = useState("");

  function refresh() {
    get<RoleSummary[]>("/admin/roles").then(setRoles).catch((e: Error) => setError(e.message));
  }
  useEffect(refresh, []);

  async function openRole(slug: string) {
    setSaved(null);
    try {
      const detail = await get<RoleDetail>(`/admin/roles/${slug}`);
      setEditing(detail);
      setEgressText(egressLines(detail.tool_permissions));
      setMcpText(mcpLines(detail.tool_permissions));
      const ports = detail.tool_permissions["egress_ports"];
      setPortsText(Array.isArray(ports) ? ports.join(", ") : "");
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!editing) return;
    setError(null);
    try {
      const result = await post<{ version: number }>("/admin/roles", {
        slug: editing.slug,
        name: editing.name,
        is_ceo_role: editing.is_ceo_role,
        description: editing.description,
        system_prompt: editing.system_prompt,
        model: editing.model,
        effort: editing.effort,
        tool_permissions: editing.tool_permissions,
        secret_scopes: editing.secret_scopes,
      });
      setSaved(
        `Saved as version ${result.version}. Existing teams keep their pinned version; new teams get this one.`,
      );
      refresh();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  const perms = editing?.tool_permissions ?? {};

  return (
    <>
      <h1>Role library</h1>
      <p className="subtitle">
        Versioned role templates. Saving creates a new version — teams pin the version that existed
        when they were created.
      </p>
      {error && <div className="error">{error}</div>}
      <div className="row" style={{ alignItems: "flex-start" }}>
        <div style={{ maxWidth: 320 }}>
          <table>
            <thead>
              <tr>
                <th>Role</th>
                <th>Model</th>
                <th className="num">v</th>
              </tr>
            </thead>
            <tbody>
              {roles.map((role) => (
                <tr key={role.slug} style={{ cursor: "pointer" }} onClick={() => openRole(role.slug)}>
                  <td>
                    {role.name}
                    {role.is_ceo_role && (
                      <span className="badge accent" style={{ marginLeft: 6 }}>
                        CEO
                      </span>
                    )}
                  </td>
                  <td className="mono">{role.model ?? "—"}</td>
                  <td className="num">{role.latest_version}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="actions">
            <button onClick={() => { setSaved(null); setEditing({ ...EMPTY }); setEgressText(""); setMcpText(""); setPortsText(""); }}>+ New role</button>
          </div>
        </div>

        {editing && (
          <form className="card" onSubmit={save} style={{ flex: 2 }}>
            <div className="row">
              <div>
                <label className="field">Slug</label>
                <input
                  value={editing.slug}
                  disabled={editing.version > 0}
                  pattern="[a-z0-9-]{2,64}"
                  onChange={(e) => setEditing({ ...editing, slug: e.target.value })}
                  required
                />
              </div>
              <div>
                <label className="field">Name</label>
                <input
                  value={editing.name}
                  onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                  required
                />
              </div>
            </div>
            <div className="row">
              <div>
                <label className="field">Model</label>
                <select
                  value={editing.model}
                  onChange={(e) => setEditing({ ...editing, model: e.target.value })}
                >
                  <option value="claude-opus-5">claude-opus-5 (most capable)</option>
                  <option value="claude-sonnet-5">claude-sonnet-5 (balanced)</option>
                  <option value="claude-haiku-4-5">claude-haiku-4-5 (cheap/fast)</option>
                </select>
              </div>
              <div>
                <label className="field">Effort</label>
                <select
                  value={editing.effort}
                  onChange={(e) => setEditing({ ...editing, effort: e.target.value })}
                >
                  {["low", "medium", "high", "xhigh", "max"].map((level) => (
                    <option key={level}>{level}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="field">Kind</label>
                <select
                  value={editing.is_ceo_role ? "ceo" : "worker"}
                  onChange={(e) => setEditing({ ...editing, is_ceo_role: e.target.value === "ceo" })}
                >
                  <option value="worker">worker role</option>
                  <option value="ceo">CEO role</option>
                </select>
              </div>
            </div>
            <label className="field">Description (one line, shown in pickers)</label>
            <input
              value={editing.description}
              onChange={(e) => setEditing({ ...editing, description: e.target.value })}
              required
            />
            <label className="field">System prompt — the role's craft and discipline</label>
            <textarea
              style={{ minHeight: 220 }}
              value={editing.system_prompt}
              onChange={(e) => setEditing({ ...editing, system_prompt: e.target.value })}
              required
            />
            <label className="field">Sandbox tool permissions</label>
            <div className="pill-row">
              {(["files", "bash", "web"] as const).map((perm) => (
                <label key={perm} style={{ display: "flex", gap: 5, alignItems: "center" }}>
                  <input
                    type="checkbox"
                    style={{ width: "auto" }}
                    checked={!!perms[perm]}
                    onChange={(e) =>
                      setEditing({
                        ...editing,
                        tool_permissions: { ...perms, [perm]: e.target.checked },
                      })
                    }
                  />
                  {perm}
                </label>
              ))}
            </div>
            <label className="field">
              Egress allowlist — one domain per line; blank = role uses the global allowlist
            </label>
            <textarea
              className="mono"
              style={{ minHeight: 70 }}
              value={egressText}
              placeholder={"api.wordpress.com\nen.wikipedia.org"}
              onChange={(e) => {
                setEgressText(e.target.value);
                const domains = e.target.value
                  .split("\n")
                  .map((line) => line.trim())
                  .filter(Boolean);
                const next = { ...perms };
                if (domains.length) next["egress_allow"] = domains;
                else delete next["egress_allow"];
                setEditing({ ...editing, tool_permissions: next });
              }}
            />
            <p className="help" style={{ margin: "4px 0 0" }}>
              Per-role egress scoping, enforced by the proxy config generator — this role's
              sandboxes can reach only these hosts, whatever the global list allows. Ports
              beyond 80/443 need an entry in egress ports below.
            </p>
            <label className="field">
              External MCP servers — one per line: name URL_ENV_VAR [bearer TOKEN_ENV_VAR] [sse]
            </label>
            <textarea
              className="mono"
              style={{ minHeight: 54 }}
              value={mcpText}
              placeholder={"ppc MCP_SERVER_URL bearer MCP_SERVER_TOKEN"}
              onChange={(e) => {
                setMcpText(e.target.value);
                const specs = parseMcpLines(e.target.value);
                const next = { ...perms };
                if (specs.length) next["mcp_servers"] = specs;
                else delete next["mcp_servers"];
                setEditing({ ...editing, tool_permissions: next });
              }}
            />
            <p className="help" style={{ margin: "4px 0 0" }}>
              The URL (and optional bearer token) come from service credentials granted to this
              role — store the server's full URL (https://host:port/path) as a credential, grant
              it here, and the sandbox mounts it as real MCP tools. The env var alone mounts
              nothing; the host must be on this role's egress allowlist (plus its port below).
            </p>
            <label className="field">
              Egress ports beyond 80/443 (comma-separated — e.g. an MCP server's port)
            </label>
            <input
              className="mono"
              value={portsText}
              placeholder="3845, 8443"
              onChange={(e) => {
                // Raw text kept separately so a trailing comma survives typing;
                // the parsed ports live in tool_permissions.egress_ports.
                setPortsText(e.target.value);
                const ports = e.target.value.split(",").map((p) => parseInt(p.trim(), 10))
                  .filter((p) => Number.isFinite(p) && p > 0 && p < 65536);
                const next = { ...perms };
                if (ports.length) next["egress_ports"] = ports;
                else delete next["egress_ports"];
                setEditing({ ...editing, tool_permissions: next });
              }}
            />
            <label className="field">Secret scopes (comma-separated names this role may receive)</label>
            <input
              value={editing.secret_scopes.join(", ")}
              onChange={(e) =>
                setEditing({
                  ...editing,
                  secret_scopes: e.target.value.split(",").map((s) => s.trim()).filter(Boolean),
                })
              }
            />
            {saved && <div className="notice-box">{saved}</div>}
            <div className="actions">
              <button type="button" onClick={() => setEditing(null)}>
                Close
              </button>
              <div className="spacer" />
              <button className="primary">
                {editing.version > 0 ? `Save as v${editing.version + 1}` : "Create role"}
              </button>
            </div>
          </form>
        )}
      </div>
    </>
  );
}
