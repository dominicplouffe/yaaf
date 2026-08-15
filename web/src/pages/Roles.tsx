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
  tool_permissions: Record<string, boolean>;
  secret_scopes: string[];
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

  function refresh() {
    get<RoleSummary[]>("/admin/roles").then(setRoles).catch((e: Error) => setError(e.message));
  }
  useEffect(refresh, []);

  async function openRole(slug: string) {
    setSaved(null);
    try {
      setEditing(await get<RoleDetail>(`/admin/roles/${slug}`));
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
            <button onClick={() => { setSaved(null); setEditing({ ...EMPTY }); }}>+ New role</button>
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
