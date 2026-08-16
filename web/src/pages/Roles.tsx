import { useEffect, useRef, useState } from "react";
import { get, post, RoleSummary } from "../api";
import { clip } from "../ui";

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

/** "claude-opus-5" → "opus-5": the family is the choice; the vendor prefix is noise. */
function shortModel(model: string | null): string {
  return (model ?? "—").replace(/^claude-/, "");
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
  const [portsText, setPortsText] = useState("");
  const formRef = useRef<HTMLFormElement>(null);

  function refresh() {
    get<RoleSummary[]>("/admin/roles").then(setRoles).catch((e: Error) => setError(e.message));
  }
  useEffect(refresh, []);
  useEffect(() => {
    if (editing) formRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [editing?.slug]); // eslint-disable-line react-hooks/exhaustive-deps

  async function openRole(slug: string) {
    setSaved(null);
    try {
      const detail = await get<RoleDetail>(`/admin/roles/${slug}`);
      // Roles never own MCP servers (data sources are per-project); a stale
      // key from imported data must not round-trip into a save refusal.
      delete detail.tool_permissions["mcp_servers"];
      setEditing(detail);
      setEgressText(egressLines(detail.tool_permissions));
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
  // The edit's blast radius, for the panel inside the editor.
  const editingUsage = editing ? roles.find((r) => r.slug === editing.slug)?.usage ?? [] : [];

  return (
    <>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <h1>Role library</h1>
        <div className="spacer" />
        <button
          className="small primary"
          onClick={() => {
            setSaved(null);
            setEditing({ ...EMPTY });
            setEgressText("");
            setPortsText("");
          }}
        >
          + New role
        </button>
      </div>
      <p className="subtitle">
        Versioned role templates — a role is a craft: its instructions, its brain, and the sandbox
        walls around it. Saving creates a new version; teams keep the version they pinned until you
        adopt the newer one (Today → adopt latest).
      </p>
      {error && <div className="error">{error}</div>}

      <div className="role-grid">
        {roles.map((role) => {
          const behind = role.usage.filter((u) => u.pinned_version < role.latest_version).length;
          const projects = new Set(role.usage.map((u) => u.project_name)).size;
          return (
            <div
              className="card clickable"
              key={role.slug}
              onClick={() => openRole(role.slug)}
              onKeyDown={(e) => e.key === "Enter" && openRole(role.slug)}
              tabIndex={0}
              role="button"
            >
              <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
                <strong>{role.name}</strong>
                {role.is_ceo_role && <span className="badge accent">CEO</span>}
                <div className="spacer" />
                <span className="chip">v{role.latest_version}</span>
              </div>
              <p style={{ margin: "6px 0 0", fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.5 }}>
                {role.description ? clip(role.description, 110) : <span className="help">no description yet</span>}
              </p>
              <div className="pill-row" style={{ marginTop: 8 }}>
                <span className="chip accent">{shortModel(role.model)}</span>
                {role.effort && <span className="chip">{role.effort} effort</span>}
                {role.tools.map((tool) => (
                  <span key={tool} className="chip">{tool}</span>
                ))}
              </div>
              <div className="help" style={{ marginTop: 8 }}>
                {role.usage.length === 0 ? (
                  "not running on any team yet"
                ) : (
                  <>
                    runs {role.usage.length} team{role.usage.length === 1 ? "" : "s"} in {projects}{" "}
                    project{projects === 1 ? "" : "s"}
                    {behind > 0 && (
                      <>
                        {" "}· <span className="chip warn" style={{ fontSize: 9.5 }}>{behind} on older versions</span>
                      </>
                    )}
                  </>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {editing && (
        <form className="card" onSubmit={save} ref={formRef} style={{ marginTop: 18 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
            <strong style={{ fontSize: 15 }}>
              {editing.version > 0 ? `${editing.name} — v${editing.version}` : "New role"}
            </strong>
            <div className="spacer" />
            <button type="button" className="small" onClick={() => setEditing(null)}>
              Close
            </button>
          </div>

          <div className="fs" style={{ borderTop: "none", paddingTop: 6 }}>
            <div className="fs-h">Identity</div>
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
            <label className="field">Description (one line, shown in pickers and on the cards above)</label>
            <input
              value={editing.description}
              onChange={(e) => setEditing({ ...editing, description: e.target.value })}
              required
            />
          </div>

          <div className="fs">
            <div className="fs-h">Brain</div>
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
            </div>
          </div>

          <div className="fs">
            <div className="fs-h">Craft</div>
            <label className="field">System prompt — the role's craft and discipline</label>
            <textarea
              style={{ minHeight: 220 }}
              value={editing.system_prompt}
              onChange={(e) => setEditing({ ...editing, system_prompt: e.target.value })}
              required
            />
          </div>

          <div className="fs">
            <div className="fs-h">Sandbox walls</div>
            <label className="field">Tool permissions</label>
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
            <p className="help" style={{ margin: "4px 0 0" }}>
              Looking for MCP servers? Data sources live on each project (Settings → Data
              sources) and are assigned to roles there — one form wires the URL, secrets,
              grants, egress, and proxy together. Roles never carry their own servers.
            </p>
            <label className="field">
              Egress ports beyond 80/443 (comma-separated — e.g. an internal API's port)
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
          </div>

          {editingUsage.length > 0 && (
            <div className="fs">
              <div className="fs-h">Where it runs — who feels this edit</div>
              <table style={{ maxWidth: 560 }}>
                <thead>
                  <tr>
                    <th>Project</th>
                    <th>Team</th>
                    <th className="num">Pinned</th>
                  </tr>
                </thead>
                <tbody>
                  {editingUsage.map((u) => (
                    <tr key={`${u.project_name}/${u.team_name}`}>
                      <td>{u.project_name}</td>
                      <td>{u.team_name}</td>
                      <td className="num">
                        v{u.pinned_version}
                        {u.pinned_version < editing.version && (
                          <span className="chip warn" style={{ marginLeft: 6, fontSize: 9.5 }}>behind</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="help" style={{ margin: "6px 0 0" }}>
                Saving never touches these teams — each keeps its pinned version until you adopt
                the new one from that project's Today tab.
              </p>
            </div>
          )}

          {saved && <div className="notice-box">{saved}</div>}
          <div className="actions">
            <span className="help">
              {editing.version > 0
                ? "Saving creates a new version; nothing changes for running teams until adopted."
                : "New roles are available to teams the moment they're created."}
            </span>
            <div className="spacer" />
            <button className="primary">
              {editing.version > 0 ? `Save as v${editing.version + 1}` : "Create role"}
            </button>
          </div>
        </form>
      )}
    </>
  );
}
