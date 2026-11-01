import { RefObject, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { get, post, RoleSummary } from "../api";
import { modelLabel, ModelsInfo, useModels } from "../providers";
import { CATEGORIES, DOMAINS, canDo, depthOf, effortWord, needsOf, purposeOf } from "../roles";
import { downloadCsv, ExportCsv } from "../ui";
import "./roles-copy.css";

interface RoleDetail {
  slug: string;
  name: string;
  is_ceo_role: boolean;
  category: string | null;
  domains: string[];
  version: number;
  description: string;
  system_prompt: string;
  // A model, or a class of one (`@balanced`) — the class runs on whichever
  // provider pays the project (docs/PROVIDERS.md).
  model: string;
  effort: string;
  // What drives the model; null is the default of whatever credential kind
  // pays the seat (docs/RUNTIMES.md).
  harness: string | null;
  // Booleans for the sandbox tools — unknown keys
  // are preserved verbatim on save (the proxy generator reads this dict too).
  tool_permissions: Record<string, unknown>;
  secret_scopes: string[];
}

const EMPTY: RoleDetail = {
  slug: "",
  name: "",
  is_ceo_role: false,
  category: null,
  domains: [],
  version: 0,
  description: "",
  system_prompt: "",
  model: "@balanced",
  effort: "high",
  harness: null,
  tool_permissions: { files: true, bash: false, data_processing: true, web: true },
  secret_scopes: [],
};

/** The permissions a role can hold, with what each actually buys. A bare
    checkbox row reads as jargon, and two of these grant the same tool for
    different reasons — which is exactly the thing a label has to say out loud. */
const PERMISSIONS = [
  { key: "files", label: "Files", help: "Read, write and search files in the workspace." },
  {
    key: "data_processing",
    label: "Data processing",
    help:
      "Python with pandas and duckdb, to aggregate data rather than read it. " +
      "Grants a shell, like Bash — kept separate so the reason is legible.",
  },
  {
    key: "bash",
    label: "Bash",
    help: "Any shell command, for roles whose craft is building and running things.",
  },
  { key: "web", label: "Web", help: "Fetch and search the open web." },
] as const;

/** The model a role thinks on, as a person says it. The default class is the
    install's own choice, so it is named as that rather than as a class. */
function modelWords(model: string | null): string {
  if (!model || model === "@balanced") return "the app's model";
  return modelLabel(model);
}

/** One role as one line: who it is, what it is for, and the few facts that
    tell it apart from its neighbours. Everything else is the role itself,
    which opens under the row. */
function RoleRow({ role, open, onOpen }: {
  role: RoleSummary; open: boolean; onOpen: (slug: string) => void;
}) {
  const depth = depthOf(role.model, role.effort);
  const behind = role.usage.filter((u) => u.behind ?? u.pinned_version < role.latest_version).length;
  const projects = new Set(role.usage.map((u) => u.project_name)).size;
  const teams = role.usage.length;
  const code = (role.tools ?? []).includes("bash");
  const openWeb = (role.tools ?? []).includes("egress_unrestricted");
  return (
    <button type="button" className={`rl-row${open ? " on" : ""}`} aria-expanded={open}
      onClick={() => onOpen(role.slug)} title={role.description || undefined}>
      <span className="rl-name">
        <b>{role.name}</b>
        {role.is_ceo_role && <span className="chip accent">CEO seat</span>}
        {code && <span className="chip" title="Can run shell commands">Code</span>}
        {/* The one permission whose blast radius is the whole internet reads
            as a warning, not a chip. */}
        {openWeb && <span className="chip warn">Open web</span>}
        {needsOf(role.secret_scopes ?? []).map((n) => (
          <span className="chip warn" key={n.scope} title={`Needs ${n.words}`}>{n.short}</span>
        ))}
      </span>
      <span className="rl-what">
        {role.description ? purposeOf(role.description) : <span className="rl-dim">No description yet</span>}
      </span>
      <span className="rl-depth"
        title={`${depth.band}: ${modelWords(role.model)}, ${effortWord(role.effort)} effort`}>
        {depth.band}
      </span>
      <span className="rl-use"
        title={role.is_ceo_role ? "Chosen when a project starts — every project runs one CEO"
          : teams ? `Runs ${teams} team${teams === 1 ? "" : "s"} in ${projects} project${projects === 1 ? "" : "s"}`
            : "Not on any team yet"}>
        {role.is_ceo_role ? "Per project"
          : teams ? `${teams} team${teams === 1 ? "" : "s"}` : <span className="rl-dim">—</span>}
        {behind > 0 && <span className="chip warn">{behind} behind</span>}
      </span>
      <span className="rl-ver">v{role.latest_version}</span>
    </button>
  );
}

/** A role, opened: every field it has, editable. Saving makes a new version. */
function RoleEditor({ editing, setEditing, onSave, onClose, saved, usage, harnesses, catalog, formRef }: {
  editing: RoleDetail;
  setEditing: (next: RoleDetail) => void;
  onSave: (e: React.FormEvent) => void;
  onClose: () => void;
  saved: string | null;
  /** The edit's blast radius: who runs this role, on which version. */
  usage: RoleSummary["usage"];
  harnesses: { name: string; label: string }[];
  catalog: ModelsInfo | null;
  formRef: RefObject<HTMLFormElement>;
}) {
  const perms = editing.tool_permissions ?? {};
  return (
    <form className="card rl-editor" onSubmit={onSave} ref={formRef}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
        <strong style={{ fontSize: "var(--t-body)" }}>
          {editing.version > 0 ? `${editing.name} — v${editing.version}` : "New role"}
        </strong>
        <div className="spacer" />
        <button type="button" className="small" onClick={onClose}>Close</button>
      </div>

      <div className="fs" style={{ borderTop: "none", paddingTop: 6 }}>
        <div className="fs-h">Identity</div>
        <div className="row">
          <div>
            <label className="field">ID — lowercase letters and dashes</label>
            <input
              value={editing.slug}
              disabled={editing.version > 0}
              pattern="[a-z0-9\-]{2,64}"
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
              <option value="worker">Team role</option>
              <option value="ceo">CEO seat</option>
            </select>
          </div>
        </div>
        {/* Where this role sits on the hiring desk. Changing it refiles the
            role for everyone immediately — it lives on the role, not the
            version, so no team goes behind and no prompt moves. */}
        <div className="row">
          <div>
            <label className="field">Category</label>
            <select
              value={editing.category ?? ""}
              onChange={(e) => setEditing({ ...editing, category: e.target.value || null })}
            >
              <option value="">Uncategorised</option>
              {CATEGORIES.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="field">Works on</label>
            <div className="pill-row" style={{ marginTop: 8 }}>
              {DOMAINS.map((d) => (
                <label key={d.id} style={{ display: "flex", gap: 6, alignItems: "center" }}>
                  <input
                    type="checkbox"
                    style={{ width: "auto" }}
                    checked={(editing.domains ?? []).includes(d.id)}
                    onChange={() => setEditing({
                      ...editing,
                      domains: (editing.domains ?? []).includes(d.id)
                        ? editing.domains.filter((x) => x !== d.id)
                        : [...(editing.domains ?? []), d.id],
                    })}
                  />
                  {d.label}
                </label>
              ))}
            </div>
          </div>
        </div>
        <label className="field">Description — its first sentence is the line in the list</label>
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
            <p className="help" style={{ margin: 0 }}>
              {editing.model === "@balanced"
                ? <>The app&apos;s — set in <Link to="/settings?section=models">Settings → Models</Link>.</>
                : <>{modelWords(editing.model)}, pinned by this role.</>}
            </p>
          </div>
          <div>
            <label className="field">Effort</label>
            <select
              value={editing.effort}
              onChange={(e) => setEditing({ ...editing, effort: e.target.value })}
            >
              {(catalog?.efforts ?? ["low", "medium", "high", "xhigh", "max"]).map((level) => (
                <option key={level} value={level}>{effortWord(level)}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="field">Harness</label>
            <select
              value={editing.harness ?? ""}
              onChange={(e) => setEditing({ ...editing, harness: e.target.value || null })}
            >
              <option value="">Default — the paying credential&apos;s</option>
              {harnesses.map((h) => (
                <option key={h.name} value={h.name}>{h.label}</option>
              ))}
            </select>
          </div>
        </div>
        <p className="help">
          The harness changes how a run is driven, never what it may do.
        </p>
      </div>

      <div className="fs">
        <div className="fs-h">Craft</div>
        <label className="field">System prompt</label>
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
        <div>
          {PERMISSIONS.map((perm) => {
            // Bash already includes the shell data processing asks for, so
            // say so rather than leaving a tick that appears to do nothing.
            const covered = perm.key === "data_processing" && !!perms["bash"];
            return (
              <label key={perm.key} style={{ display: "block", marginBottom: 8, fontWeight: "normal" }}>
                <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
                  <input
                    type="checkbox"
                    style={{ width: "auto" }}
                    checked={!!perms[perm.key] || covered}
                    disabled={covered}
                    onChange={(e) =>
                      setEditing({
                        ...editing,
                        tool_permissions: { ...perms, [perm.key]: e.target.checked },
                      })
                    }
                  />
                  {perm.label}
                </span>
                <span className="help" style={{ marginLeft: 22 }}>
                  {covered ? "already granted by Bash." : perm.help}
                </span>
              </label>
            );
          })}
        </div>
        <p className="help" style={{ margin: "4px 0 0" }}>
          MCP servers belong to each project, not to a role: the project&apos;s Settings → Data sources.
        </p>
        <label className="field">Secrets it may receive — comma-separated names</label>
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

      {usage.length > 0 && (
        <div className="fs">
          <div className="fs-h">Who feels this edit</div>
          <table style={{ maxWidth: 560 }}>
            <thead>
              <tr>
                <th>Project</th>
                <th>Team</th>
                <th className="num">Pinned</th>
              </tr>
            </thead>
            <tbody>
              {usage.map((u) => (
                <tr key={`${u.project_name}/${u.team_name}`}>
                  <td>{u.project_name}</td>
                  <td>{u.team_name}</td>
                  <td className="num">
                    v{u.pinned_version}
                    {(u.behind ?? u.pinned_version < editing.version) && (
                      <span className="chip warn" style={{ marginLeft: 6 }}>behind</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {saved && <div className="notice-box">{saved}</div>}
      <div className="actions">
        <span className="help">
          {editing.version > 0
            ? "Running teams keep their version until they adopt this one on their Team page."
            : "New roles can be hired the moment they're created."}
        </span>
        <div className="spacer" />
        <button className="primary">
          {editing.version > 0 ? `Save as v${editing.version + 1}` : "Create role"}
        </button>
      </div>
    </form>
  );
}

export default function Roles() {
  const [roles, setRoles] = useState<RoleSummary[]>([]);
  const [editing, setEditing] = useState<RoleDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [domain, setDomain] = useState("all");
  const [saved, setSaved] = useState<string | null>(null);
  const [why, setWhy] = useState(false);
  const [params, setParams] = useSearchParams();
  const formRef = useRef<HTMLFormElement>(null);

  // The harnesses a seat may run on, from the registry rather than a list
  // written into this page — the next one arrives without editing it.
  const [harnesses, setHarnesses] = useState<{ name: string; label: string }[]>([]);
  const catalog = useModels();
  function refresh() {
    get<RoleSummary[]>("/admin/roles").then(setRoles).catch((e: Error) => setError(e.message));
  }
  useEffect(refresh, []);
  useEffect(() => {
    get<{ name: string; label: string }[]>("/admin/harnesses")
      .then(setHarnesses)
      .catch(() => setHarnesses([]));
  }, []);
  // A person's page links here with ?role=, so "their role" opens on arrival.
  const linked = params.get("role");
  useEffect(() => {
    if (linked && editing?.slug !== linked) void openRole(linked);
  }, [linked]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (editing) formRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [editing?.slug]); // eslint-disable-line react-hooks/exhaustive-deps

  async function openRole(slug: string) {
    setSaved(null);
    if (editing?.slug === slug) {
      close();
      return;
    }
    try {
      const detail = await get<RoleDetail>(`/admin/roles/${slug}`);
      // Roles never own MCP servers (data sources are per-project); a stale
      // key from imported data must not round-trip into a save refusal.
      delete detail.tool_permissions["mcp_servers"];
      // Egress scoping was removed with the allowlist proxy (docs/AUTONOMY.md);
      // stale keys from old role data must not round-trip as false promises.
      delete detail.tool_permissions["egress_allow"];
      delete detail.tool_permissions["egress_ports"];
      setEditing(detail);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  function close() {
    setEditing(null);
    if (params.has("role")) setParams({}, { replace: true });
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
        // Carried through the save so editing a prompt never quietly
        // un-files a role — the taxonomy is identity, not version content.
        category: editing.category,
        domains: editing.domains,
        description: editing.description,
        system_prompt: editing.system_prompt,
        model: editing.model,
        effort: editing.effort,
        harness: editing.harness || null,
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

  // Thirty roles in one flat grid was already the pain point this page had;
  // the categories make it navigable, and search covers the case where the
  // chairman knows the word but not the aisle.
  const needle = query.trim().toLowerCase();
  const visible = roles.filter((role) => {
    if (domain !== "all" && !(role.domains ?? []).includes(domain)) return false;
    if (!needle) return true;
    return `${role.name} ${role.slug} ${role.description} ${role.category ?? ""}`
      .toLowerCase().includes(needle);
  });
  const groups = CATEGORIES
    .map((c) => ({ ...c, rows: visible.filter((r) => r.category === c.id) }))
    .filter((g) => g.rows.length);
  const loose = visible.filter(
    (r) => !CATEGORIES.some((c) => c.id === (r.category ?? "")));
  if (loose.length) {
    groups.push({
      id: "", name: "Uncategorised", rows: loose,
      line: "Roles written here, or from before the categories",
    });
  }

  function exportLibrary() {
    downloadCsv(
      "role-library.csv",
      ["slug", "name", "category", "domains", "depth", "cost", "model", "effort",
        "can_do", "needs", "version", "teams_running_it", "description"],
      roles.map((r) => {
        const d = depthOf(r.model, r.effort);
        return [
          r.slug, r.name, r.category ?? "uncategorised", (r.domains ?? []).join(" "),
          d.band, d.dollars, r.model ?? "", r.effort ?? "",
          canDo(r.tools).join(" "), (r.secret_scopes ?? []).join(" "),
          r.latest_version, r.usage.length, r.description,
        ];
      }),
    );
  }

  const editor = editing && (
    <RoleEditor
      editing={editing} setEditing={setEditing} onSave={save} onClose={close} saved={saved}
      usage={roles.find((r) => r.slug === editing.slug)?.usage ?? []}
      harnesses={harnesses} catalog={catalog} formRef={formRef}
    />
  );
  const isNew = editing !== null && editing.version === 0;

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
          }}
        >
          + New role
        </button>
      </div>
      <div className="rl-lede">
        <span>Every role a project can hire. Edits save as a new version; running teams keep theirs.</span>{" "}
        <button type="button" className="linkish inline" aria-expanded={why} onClick={() => setWhy(!why)}>
          {why ? "Hide" : "Why?"}
        </button>
        {why && (
          <div className="rl-why">
            <p>
              A role is a craft: its instructions, how deeply it thinks, and the sandbox walls
              around it. Saving one creates a new version. Teams keep the version they were hired
              on, so nothing running changes underneath them, until somebody adopts the newer one
              on that project&apos;s Team page.
            </p>
            <p>
              <b>Depth</b> is the model and the effort in one word: Light, Standard, Thorough or
              Deepest, cheapest first. <b>Code</b> means the role may run shell commands. Click a
              role for its full description, system prompt and permissions.
            </p>
          </div>
        )}
      </div>
      {error && <div className="error">{error}</div>}

      <div className="hire-tools">
        <div className="hire-search">
          <input type="search" value={query} onChange={(e) => setQuery(e.target.value)}
            placeholder="Search roles — name or what they do"
            aria-label="Search roles" />
        </div>
        <div className="pills" role="group" aria-label="What a role works on">
          <button className={domain === "all" ? "active" : ""}
            onClick={() => setDomain("all")}>All {roles.length}</button>
          {DOMAINS.map((d) => {
            const n = roles.filter((r) => (r.domains ?? []).includes(d.id)).length;
            if (!n) return null;
            return (
              <button key={d.id} className={domain === d.id ? "active" : ""}
                onClick={() => setDomain(d.id)}>{d.label} {n}</button>
            );
          })}
        </div>
        <ExportCsv onClick={exportLibrary} />
      </div>

      {isNew && editor}

      {visible.length === 0 && (
        <p className="help">
          {roles.length === 0
            ? <>The library is empty. Load the shipped roles with{" "}
                <code>yaaf seed-roles roles/</code>, or write one.</>
            : "No role matches that. Clear the search, or write a new one."}
        </p>
      )}

      {groups.length > 0 && (
        <div className="rl-list">
          <div className="rl-head" aria-hidden="true">
            <span>Role</span><span>What it does</span><span>Depth</span><span>In use</span><span />
          </div>
          {groups.map((group) => (
            <section key={group.id || group.name} aria-label={group.name}>
              <h2 className="rl-group">
                {group.name}<span className="cnt">{group.rows.length}</span>
                <span className="rl-line">{group.line}</span>
              </h2>
              {group.rows.map((role) => {
                const open = !isNew && editing?.slug === role.slug;
                return (
                  <div key={role.slug}>
                    <RoleRow role={role} open={open} onOpen={openRole} />
                    {open && <div className="rl-open">{editor}</div>}
                  </div>
                );
              })}
            </section>
          ))}
        </div>
      )}
    </>
  );
}
