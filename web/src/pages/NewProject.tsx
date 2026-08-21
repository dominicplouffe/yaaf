import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { get, McpServerEntry, post, ProjectBundle, RoleSummary } from "../api";
import { SecretInput } from "../ui";

interface TeamDraft {
  role_slug: string;
  name: string;
  employees: string; // comma-separated names
  lead: string;
}

interface CredentialDraft {
  name: string;
  value: string;
  grants: string[]; // role slugs
}

function envVarName(name: string): string {
  return name.replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, "").toUpperCase();
}

/** The same rule the server applies to a data source's address: a full
    http(s) address, not a bare host and not a token pasted into the wrong box. */
function isFullUrl(value: string): boolean {
  try {
    const parsed = new URL(value.trim());
    return (parsed.protocol === "http:" || parsed.protocol === "https:") && !!parsed.hostname;
  } catch {
    return false;
  }
}

const MODES = [
  { value: "explore", label: "Explore",
    blurb: "Chase a goal with no fixed end. Bets, proof, and your call on when it has earned its keep.",
    example: "\u201CFind an idea and get one sale in 60 days.\u201D" },
  { value: "deliver", label: "Deliver",
    blurb: "Ship one thing, then stop. You accept what done means; the platform proves it.",
    example: "\u201CA security audit of our AWS estate.\u201D" },
  { value: "operate", label: "Operate",
    blurb: "Run the same play on a rhythm, and get better at it every cycle.",
    example: "\u201CA weekly blog post, tracked and improved.\u201D" },
] as const;

// The authority dial (docs/AUTONOMY.md): who holds the verbs. A preset is
// nothing but a bundle of four constitution switches — each editable
// individually later in Settings → Rules in force, changed by amendment.
const AUTONOMY_PRESETS = [
  { value: "supervised", label: "Supervised",
    blurb: "You approve scope, sign the date, and judge unverifiable closes. " +
      "The CEO proposes — and executes any order you give it in the boardroom.",
    example: "Today's behavior. Right for a first project, or real money on the line.",
    constitution: {} },
  { value: "autonomous", label: "Autonomous",
    blurb: "The CEO accepts its own plan, signs the platform's projected date, and " +
      "judges unverifiable closes itself. Every decision is audited and sent to " +
      "you; all of it is reversible; your buttons never close.",
    example: "The CEO runs the company; you chair it. Budgets and brakes still bind.",
    constitution: { plan_authority: "ceo", scope_authority: "ceo",
      commitment_authority: "ceo", verdict_policy: "ceo_judgment" } },
] as const;

export default function NewProject() {
  const navigate = useNavigate();
  const [roles, setRoles] = useState<RoleSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [goal, setGoal] = useState("");
  const [chairmanEmail, setChairmanEmail] = useState("");
  const [chairmanName, setChairmanName] = useState("");
  const [billingMode, setBillingMode] = useState("api_key");
  const [mode, setMode] = useState("explore");
  // Set when a loaded setup file could not answer the shape question itself —
  // the one prefill that must never be guessed silently, because a project's
  // mode is frozen at creation and Explore hides the Plan tab by design.
  const [modeNotice, setModeNotice] = useState<string | null>(null);
  const [exploreDeadline, setExploreDeadline] = useState("");
  const [autonomy, setAutonomy] = useState("supervised");
  const [runtime, setRuntime] = useState("agent_sdk");
  const [controlledDomains, setControlledDomains] = useState("");
  const [teams, setTeams] = useState<TeamDraft[]>([
    { role_slug: "", name: "", employees: "", lead: "" },
  ]);
  const [credentials, setCredentials] = useState<CredentialDraft[]>([]);

  // Constitution knobs surfaced in the wizard (frozen at creation).
  const [queueCeiling, setQueueCeiling] = useState(8);
  const [droughtHours, setDroughtHours] = useState(24);
  const [usdCap, setUsdCap] = useState(1000);
  const [approvalThreshold, setApprovalThreshold] = useState(250);
  const [startNow, setStartNow] = useState(true);

  // A setup file downloaded from a project's Settings. Loading one prefills
  // the whole form; the file's full constitution/config and any role
  // definitions the library lacks ride along at submit time.
  const [bundle, setBundle] = useState<ProjectBundle | null>(null);
  const [solo, setSolo] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [webhooks, setWebhooks] = useState<{
    projectId: string;
    github: { url: string; secret: string };
    generic: { url: string; secret: string };
  } | null>(null);

  useEffect(() => {
    get<RoleSummary[]>("/admin/roles").then(setRoles).catch((e: Error) => setError(e.message));
  }, []);

  const workerRoles = roles.filter((r) => !r.is_ceo_role);
  const ceoRole = roles.find((r) => r.is_ceo_role);

  function setTeam(i: number, patch: Partial<TeamDraft>) {
    setTeams((prev) => prev.map((t, j) => (j === i ? { ...t, ...patch } : t)));
  }

  // Roles actually used by the drafted teams — the only sensible grant targets.
  const usedRoleSlugs = [...new Set(teams.map((t) => t.role_slug).filter(Boolean))];

  function setCredential(i: number, patch: Partial<CredentialDraft>) {
    setCredentials((prev) => prev.map((c, j) => (j === i ? { ...c, ...patch } : c)));
  }

  async function loadBundle(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setError(null);
    try {
      const parsed = JSON.parse(await file.text()) as ProjectBundle;
      if (parsed.yaaf_bundle !== 1 || !parsed.project) {
        throw new Error(
          "that file is not a project setup file — download one from a project's Settings",
        );
      }
      const p = parsed.project;
      setBundle(parsed);
      setName(p.name);
      setSlug(p.slug);
      setGoal(p.goal);
      setBillingMode(p.billing_mode);
      if (p.mode) {
        setMode(p.mode);
        setModeNotice(null);
      } else {
        // A file from before project shapes existed. Guessing Explore here
        // once silently overwrote a chairman's Deliver choice — keep whatever
        // is selected and say the file had no opinion.
        setModeNotice(
          "This setup file predates project shapes, so it does not say what kind of " +
          "company it was. Nothing was changed — confirm the choice above is what you want.",
        );
      }
      setRuntime(String(p.integrations["runtime"] ?? "agent_sdk"));
      setControlledDomains((p.controlled_domains ?? []).join(", "));
      setSolo(Boolean(p.solo));
      setTeams(
        p.teams.length
          ? p.teams.map((t) => ({
              role_slug: t.role_slug,
              name: t.name,
              employees: t.employees.join(", "),
              lead: t.lead ?? "",
            }))
          : [{ role_slug: "", name: "", employees: "", lead: "" }],
      );
      setCredentials(
        parsed.secrets
          .filter((s) => s.kind === "service")
          .map((s) => ({ name: s.name, value: "", grants: s.role_scopes })),
      );
      const cons = p.constitution as Record<string, number>;
      if (cons.queue_ceiling !== undefined) setQueueCeiling(cons.queue_ceiling);
      if (cons.drought_hours !== undefined) setDroughtHours(cons.drought_hours);
      if (cons.monthly_usd_cap !== undefined) setUsdCap(cons.monthly_usd_cap);
      if (cons.approval_threshold_usd !== undefined) {
        setApprovalThreshold(cons.approval_threshold_usd);
      }
    } catch (err) {
      setBundle(null);
      setError((err as Error).message);
    }
  }

  // Bundle roles the library doesn't have yet — offered in the pickers now,
  // created for real (POST /admin/roles) at submit time.
  const newBundleRoles = (bundle?.roles ?? []).filter(
    (br) => !roles.some((r) => r.slug === br.slug),
  );

  // A setup file carries its data sources' wiring but not their addresses —
  // those are secrets, and secrets stay behind. So some rows in the credential
  // list below are not opaque keys at all: they are URLs the sandbox hands
  // straight to an MCP client. Knowing which lets this page check them here,
  // rather than let an agent discover it three runs later. Secret name → source.
  const dataSourceUrls = new Map<string, string>(
    ((bundle?.project.integrations["mcp_servers"] as McpServerEntry[] | undefined) ?? []).map(
      (source) => [source.url_secret, source.name] as [string, string],
    ),
  );

  function toggleGrant(i: number, slug: string) {
    setCredentials((prev) =>
      prev.map((c, j) =>
        j === i
          ? {
              ...c,
              grants: c.grants.includes(slug)
                ? c.grants.filter((g) => g !== slug)
                : [...c.grants, slug],
            }
          : c,
      ),
    );
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      // A mistyped data source address costs nothing to catch here and a great
      // deal to catch later: the runner refuses to mount it, and says so only
      // inside the run summary of whichever agent went looking for the tools.
      const malformed = credentials.find(
        (c) => dataSourceUrls.has(c.name.trim()) && c.value.trim() && !isFullUrl(c.value),
      );
      if (malformed) {
        throw new Error(
          `"${malformed.name}" is the address of data source ` +
            `"${dataSourceUrls.get(malformed.name.trim())}" — it needs a full URL like ` +
            "https://host:3845/mcp, not a bare hostname or a token.",
        );
      }
      // Role definitions travel with a setup file — seed any the library
      // lacks before the server resolves slugs. Re-check against a fresh
      // list so a retried submit never stamps duplicate versions.
      if (newBundleRoles.length) {
        const library = await get<RoleSummary[]>("/admin/roles");
        for (const role of newBundleRoles) {
          if (!library.some((r) => r.slug === role.slug)) await post("/admin/roles", role);
        }
        setRoles(await get<RoleSummary[]>("/admin/roles"));
      }
      const ceoSlug = bundle?.project.ceo_role_slug ?? ceoRole?.slug;
      if (!ceoSlug) throw new Error("No CEO role in the library — create one on the Roles page first.");
      await post("/admin/users", {
        email: chairmanEmail.trim(),
        display_name: chairmanName.trim() || chairmanEmail.trim(),
      });
      const body = {
        slug: slug.trim(),
        name: name.trim(),
        goal: goal.trim(),
        chairman_email: chairmanEmail.trim(),
        billing_mode: billingMode,
        mode,
        ...(mode === "explore" && exploreDeadline
          ? { deadline: `${exploreDeadline}T23:59:59Z` } : {}),
        controlled_domains: controlledDomains
          .split(",")
          .map((d) => d.trim())
          .filter(Boolean),
        constitution: {
          // A setup file carries the source's full rulebook; the knobs and
          // the authority preset below override whatever you edited here.
          ...(bundle?.project.constitution ?? {}),
          queue_ceiling: queueCeiling,
          drought_hours: droughtHours,
          monthly_usd_cap: usdCap,
          approval_threshold_usd: approvalThreshold,
          ...(AUTONOMY_PRESETS.find((p) => p.value === autonomy)?.constitution ?? {}),
        },
        integrations: { ...(bundle?.project.integrations ?? {}), runtime },
        ceo_role_slug: ceoSlug,
        ceo_name: bundle?.project.ceo_name ?? "CEO",
        solo,
        teams: teams
          .filter((t) => t.role_slug && t.name && (solo || t.employees.trim()))
          .map((t) => {
            const employees = t.employees.split(",").map((s) => s.trim()).filter(Boolean);
            return {
              role_slug: t.role_slug,
              name: t.name.trim(),
              employees,
              lead: t.lead.trim() || undefined,
            };
          }),
      };
      const created = await post<{
        id: string;
        webhooks?: { github: { url: string; secret: string };
                     generic: { url: string; secret: string } };
      }>("/admin/projects", body);
      if (created.webhooks) setWebhooks({ projectId: created.id, ...created.webhooks });
      try {
        for (const cred of credentials) {
          if (!cred.name.trim() || !cred.value) continue;
          await post("/admin/secrets", {
            project_id: created.id,
            name: cred.name.trim(),
            value: cred.value,
            role_scopes: cred.grants.filter((g) => usedRoleSlugs.includes(g) || g === ceoSlug),
            kind: "service",
          });
        }
      } catch (err) {
        // The project exists either way — send them to Settings to finish.
        alert(`Project created, but storing a credential failed: ${(err as Error).message}. ` +
              "Add it in Settings → Service credentials.");
      }
      // Shape was checked above; this asks the harder question — does anything
      // answer at that address? A source that cannot answer is a project whose
      // agents will burn runs hunting for absent tools, so it does not start.
      const unreachable: string[] = [];
      for (const source of new Set(dataSourceUrls.values())) {
        try {
          const check = await post<{ ok: boolean; detail: string }>(
            `/admin/projects/${created.id}/mcp_servers/${source}/check`, {});
          if (!check.ok) unreachable.push(`"${source}": ${check.detail}`);
        } catch (err) {
          unreachable.push(`"${source}": ${(err as Error).message}`);
        }
      }
      if (unreachable.length) {
        alert(
          `Project created, but its data source${unreachable.length > 1 ? "s are" : " is"} not ` +
            `answering:\n\n${unreachable.join("\n")}\n\nThe project was NOT started. Fix the ` +
            "address in Settings → Data sources — that form tests it — then start the project.",
        );
        if (created.webhooks) setDone(created.id);
        else navigate(`/projects/${created.id}`);
        return;
      }
      if (startNow) await post(`/admin/projects/${created.id}/start`);
      if (created.webhooks) setDone(created.id);
      else navigate(`/projects/${created.id}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  // The project exists; the one thing this screen still owes the chairman is
  // the webhook secrets, shown exactly once with the endpoint beside each.
  if (done && webhooks) {
    const origin = window.location.origin;
    return (
      <>
        <h1>Project created</h1>
        <div className="card" style={{ maxWidth: 760 }}>
          <p style={{ marginTop: 0 }}>
            <b>Inbound evidence endpoints — copy the secrets now.</b> They are shown
            only this once. Evidence arriving signed with them is platform-attested:
            proof no agent can forge, because the secrets never enter a sandbox.
          </p>
          <table>
            <tbody>
              <tr>
                <td>GitHub</td>
                <td className="mono" style={{ wordBreak: "break-all" }}>
                  {origin}{webhooks.github.url}</td>
              </tr>
              <tr>
                <td>&nbsp;&nbsp;secret</td>
                <td className="mono" style={{ userSelect: "all", wordBreak: "break-all" }}>
                  {webhooks.github.secret}</td>
              </tr>
              <tr>
                <td>Generic</td>
                <td className="mono" style={{ wordBreak: "break-all" }}>
                  {origin}{webhooks.generic.url}</td>
              </tr>
              <tr>
                <td>&nbsp;&nbsp;secret</td>
                <td className="mono" style={{ userSelect: "all", wordBreak: "break-all" }}>
                  {webhooks.generic.secret}</td>
              </tr>
            </tbody>
          </table>
          <p className="help">
            GitHub signs with HMAC-SHA256 in <code>X-Hub-Signature-256</code>; other
            senders sign the raw body into <code>X-Yaaf-Signature</code>. Lost secrets
            are regenerated in Settings → Integrations, which invalidates these.
          </p>
          <button className="primary" onClick={() => navigate(`/projects/${done}`)}>
            I copied them — open the project
          </button>
        </div>
      </>
    );
  }

  return (
    <>
      <h1>New project</h1>
      <p className="subtitle">
        These are the limits your agents will run under. Set them here, and change any of them
        later in Settings → Rules in force — every change is recorded and takes effect at once.
      </p>
      {error && <div className="error">{error}</div>}
      <form onSubmit={submit} style={{ maxWidth: 760 }}>
        <div className="card" style={{ marginBottom: 10 }}>
          <label className="field">Start from a setup file (optional)</label>
          <p className="help" style={{ marginTop: 0 }}>
            Downloaded from a project's Settings → Setup file. Everything below prefills — review
            it, pick a slug, enter credential values, then create.
          </p>
          <input type="file" accept=".json,application/json" onChange={loadBundle} />
          {bundle && (
            <p className="help">
              Loaded "{bundle.project.name}" (exported from {bundle.source_slug}).
              {bundle.secrets.length > 0 &&
                " Credential names came along without their values — fill them in below, or skip and add them later in Settings."}
              {dataSourceUrls.size > 0 &&
                ` ${dataSourceUrls.size === 1 ? "One of them is a data source's" : "Some of them are data sources'"} ` +
                  "address: a full URL, tested before this project starts."}
              {" "}Billing is connected afterwards in Settings.
            </p>
          )}
        </div>
        <div className="card" style={{ marginBottom: 10 }}>
          <label className="field">What kind of company is this?</label>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
            gap: 10, marginTop: 6 }}>
            {MODES.map((m) => (
              <button key={m.value} type="button"
                onClick={() => { setMode(m.value); setModeNotice(null); }}
                style={{
                  font: "inherit", textAlign: "left", cursor: "pointer",
                  background: mode === m.value ? "rgba(57,135,229,0.07)" : "var(--surface)",
                  border: mode === m.value
                    ? "1px solid var(--accent)" : "1px solid var(--border)",
                  borderRadius: 8, padding: "12px 14px",
                }}>
                <div style={{ fontWeight: 650, fontSize: 15, color: "var(--ink)" }}>
                  {mode === m.value ? "◉" : "○"} {m.label}
                </div>
                <div style={{ fontSize: 12.5, color: "var(--ink-2)", marginTop: 6 }}>{m.blurb}</div>
                <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 8, fontStyle: "italic",
                  borderTop: "1px solid var(--grid, var(--border))", paddingTop: 7 }}>
                  {m.example}
                </div>
              </button>
            ))}
          </div>
          {modeNotice && (
            <p className="help" style={{ marginTop: 10, color: "var(--warn, var(--ink-2))" }}>
              {modeNotice}
            </p>
          )}
          {mode !== "explore" ? (
            <p className="help" style={{ marginTop: 10 }}>
              This project starts owing a plan: the CEO proposes requirements,
              {autonomy === "autonomous"
                ? " accepts them itself under the authority you grant below,"
                : " you accept them on the Plan tab,"}{" "}
              and no work may be created until the plan holds and adds up against the
              budget.
            </p>
          ) : (
            <div style={{ marginTop: 10, display: "flex", gap: 10, alignItems: "baseline",
              flexWrap: "wrap" }}>
              <label className="field" style={{ margin: 0 }}>
                Window (optional)
              </label>
              <input type="date" value={exploreDeadline}
                onChange={(e) => setExploreDeadline(e.target.value)}
                style={{ width: 170 }} />
              <span className="help">
                Open scope, bounded time — “one sale in the next 60 days.” When it
                passes you are told and the judgment is yours; nothing pauses itself.
              </span>
            </div>
          )}
        </div>
        <div className="card" style={{ marginBottom: 10 }}>
          <label className="field">Who runs it?</label>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
            gap: 10, marginTop: 6 }}>
            {AUTONOMY_PRESETS.map((p) => (
              <button key={p.value} type="button"
                onClick={() => setAutonomy(p.value)}
                style={{
                  font: "inherit", textAlign: "left", cursor: "pointer",
                  background: autonomy === p.value ? "rgba(57,135,229,0.07)" : "var(--surface)",
                  border: autonomy === p.value
                    ? "1px solid var(--accent)" : "1px solid var(--border)",
                  borderRadius: 8, padding: "12px 14px",
                }}>
                <div style={{ fontWeight: 650, fontSize: 15, color: "var(--ink)" }}>
                  {autonomy === p.value ? "◉" : "○"} {p.label}
                </div>
                <div style={{ fontSize: 12.5, color: "var(--ink-2)", marginTop: 6 }}>{p.blurb}</div>
                <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 8, fontStyle: "italic",
                  borderTop: "1px solid var(--grid, var(--border))", paddingTop: 7 }}>
                  {p.example}
                </div>
              </button>
            ))}
          </div>
          <p className="help" style={{ marginTop: 10 }}>
            A preset is four rules in the constitution's Autonomy group — each one editable
            individually later in Settings → Rules in force, with a written reason, binding
            from the next agent call. Turning the dial up or back is always yours.
          </p>
        </div>
        <div className="card">
          <div className="row">
            <div>
              <label className="field">Name</label>
              <input value={name} onChange={(e) => setName(e.target.value)} required />
            </div>
            <div>
              <label className="field">Slug (a-z, 0-9, dashes)</label>
              <input
                value={slug}
                onChange={(e) => setSlug(e.target.value)}
                pattern="[a-z0-9-]{2,64}"
                required
              />
            </div>
          </div>
          <label className="field">Goal — what this company exists to achieve</label>
          <textarea value={goal} onChange={(e) => setGoal(e.target.value)} required minLength={10} />
          <div className="row">
            <div>
              <label className="field">Chairman email (you)</label>
              <input
                type="email"
                value={chairmanEmail}
                onChange={(e) => setChairmanEmail(e.target.value)}
                required
              />
            </div>
            <div>
              <label className="field">Chairman display name</label>
              <input value={chairmanName} onChange={(e) => setChairmanName(e.target.value)} />
            </div>
          </div>
          <div className="row">
            <div>
              <label className="field">Runtime</label>
              <select value={runtime} onChange={(e) => setRuntime(e.target.value)}>
                <option value="agent_sdk">agent_sdk — real model in the sandbox</option>
                <option value="mock">mock — scripted, $0 (CI / tire-kicking)</option>
              </select>
            </div>
            <div>
              <label className="field">Billing mode</label>
              <select value={billingMode} onChange={(e) => setBillingMode(e.target.value)}>
                <option value="api_key">api_key — budget in USD</option>
                <option value="subscription">subscription — budget in tokens</option>
              </select>
            </div>
          </div>
          <label className="field">
            Controlled domains (comma-separated) — evidence from these can never count as external
          </label>
          <input
            value={controlledDomains}
            onChange={(e) => setControlledDomains(e.target.value)}
            placeholder="myproject.example, github.com/myorg"
          />
        </div>

        <h2>Teams</h2>
        {solo && (
          <p className="subtitle">
            This setup file is a solo project: the CEO is the only team's sole member, so the
            employee fields stay empty.
          </p>
        )}
        {teams.map((team, i) => (
          <div className="card" key={i} style={{ marginBottom: 10 }}>
            <div className="row">
              <div>
                <label className="field">Role</label>
                <select
                  value={team.role_slug}
                  onChange={(e) => setTeam(i, { role_slug: e.target.value })}
                >
                  <option value="">— pick a role —</option>
                  {workerRoles.map((r) => (
                    <option key={r.slug} value={r.slug}>
                      {r.name} ({r.model ?? "default"})
                    </option>
                  ))}
                  {newBundleRoles
                    .filter((r) => !r.is_ceo_role)
                    .map((r) => (
                      <option key={r.slug} value={r.slug}>
                        {r.name} — new, from the setup file
                      </option>
                    ))}
                </select>
              </div>
              <div>
                <label className="field">Team name</label>
                <input value={team.name} onChange={(e) => setTeam(i, { name: e.target.value })} />
              </div>
            </div>
            <div className="row">
              <div>
                <label className="field">Employees (comma-separated names)</label>
                <input
                  value={team.employees}
                  onChange={(e) => setTeam(i, { employees: e.target.value })}
                  placeholder="Ada, Lin"
                />
              </div>
              <div>
                <label className="field">Lead (defaults to first; a sole employee leads itself)</label>
                <input value={team.lead} onChange={(e) => setTeam(i, { lead: e.target.value })} />
              </div>
            </div>
            <div className="actions">
              <button
                type="button"
                className="small danger"
                onClick={() => setTeams((prev) => prev.filter((_, j) => j !== i))}
                disabled={teams.length === 1}
              >
                Remove team
              </button>
            </div>
          </div>
        ))}
        <button
          type="button"
          onClick={() => setTeams((prev) => [...prev, { role_slug: "", name: "", employees: "", lead: "" }])}
        >
          + Add team
        </button>

        <h2>Service credentials (optional)</h2>
        <p className="subtitle">
          Keys your agents will need for outside services (WordPress, GitHub, Stripe…). Encrypted
          at rest and injected only into the sandboxes of roles you tick — agents are told the
          variable's name, never shown the value. You can also add these later in Settings.
        </p>
        {credentials.map((cred, i) => {
          const source = dataSourceUrls.get(cred.name.trim());
          const badUrl = source !== undefined && !!cred.value.trim() && !isFullUrl(cred.value);
          return (
          <div className="card" key={i} style={{ marginBottom: 10 }}>
            <div className="row">
              <div>
                <label className="field">Name</label>
                <input
                  value={cred.name}
                  onChange={(e) => setCredential(i, { name: e.target.value })}
                  placeholder="wordpress, github-token…"
                />
                {cred.name.trim() && (
                  <div className="help" style={{ marginTop: 4 }}>
                    injected as <code>${envVarName(cred.name)}</code>
                  </div>
                )}
              </div>
              <div>
                <label className="field">
                  {source ? `Address of data source "${source}"` : "Value"}
                </label>
                <SecretInput
                  // An address is not a password: showing it is how a typo gets
                  // seen, and it is the one credential the server can judge.
                  defaultShown={!!source}
                  value={cred.value}
                  onChange={(value) => setCredential(i, { value })}
                  placeholder={source ? "https://host:3845/mcp" : "the key itself"}
                />
                {source && (
                  <div className={badUrl ? "error" : "help"} style={{ marginTop: 4 }}>
                    {badUrl
                      ? "That is not a full address — scheme included, like " +
                        "https://host:3845/mcp. A bare hostname or a token mounts nothing."
                      : cred.value.trim()
                        ? "Tested for real before the project starts."
                        : `Left blank, "${source}" mounts no tools — and the roles granted it ` +
                          "only find out mid-run."}
                  </div>
                )}
              </div>
            </div>
            <label className="field">Which roles receive it (nobody, until you grant)</label>
            {usedRoleSlugs.length === 0 ? (
              <p className="help">Pick your teams' roles above first.</p>
            ) : (
              <div className="pill-row">
                {usedRoleSlugs.map((slug) => (
                  <label key={slug} style={{ display: "flex", gap: 6, alignItems: "center" }}>
                    <input
                      type="checkbox"
                      style={{ width: "auto" }}
                      checked={cred.grants.includes(slug)}
                      onChange={() => toggleGrant(i, slug)}
                    />
                    {workerRoles.find((r) => r.slug === slug)?.name ??
                      newBundleRoles.find((r) => r.slug === slug)?.name ??
                      slug}
                  </label>
                ))}
              </div>
            )}
            <div className="actions">
              <button
                type="button"
                className="small danger"
                onClick={() => setCredentials((prev) => prev.filter((_, j) => j !== i))}
              >
                Remove credential
              </button>
            </div>
          </div>
          );
        })}
        <button
          type="button"
          onClick={() => setCredentials((prev) => [...prev, { name: "", value: "", grants: [] }])}
        >
          + Add credential
        </button>

        <h2>Constitution (frozen at creation)</h2>
        <div className="card">
          <div className="row">
            <div>
              <label className="field">Queue ceiling (open team tasks)</label>
              <input
                type="number"
                min={1}
                value={queueCeiling}
                onChange={(e) => setQueueCeiling(+e.target.value)}
              />
            </div>
            <div>
              <label className="field">Drought window (hours without verified external evidence)</label>
              <input
                type="number"
                min={1}
                value={droughtHours}
                onChange={(e) => setDroughtHours(+e.target.value)}
              />
            </div>
          </div>
          <div className="row">
            <div>
              <label className="field">Monthly budget cap (USD)</label>
              <input type="number" min={0} value={usdCap} onChange={(e) => setUsdCap(+e.target.value)} />
            </div>
            <div>
              <label className="field">Approval threshold (USD per spend)</label>
              <input
                type="number"
                min={0}
                value={approvalThreshold}
                onChange={(e) => setApprovalThreshold(+e.target.value)}
              />
            </div>
          </div>
          <p className="help">
            {bundle
              ? "Everything else (external-evidence floor, wake caps, memory caps, refusal caps…) " +
                "comes from the setup file, and every rule is editable later in Settings."
              : "Everything else (external-evidence floor, wake caps, memory caps, refusal caps…) " +
                "takes the lana-derived defaults, and every rule is editable later in Settings."}
          </p>
        </div>

        <div className="actions">
          <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <input
              type="checkbox"
              style={{ width: "auto" }}
              checked={startNow}
              onChange={(e) => setStartNow(e.target.checked)}
            />
            Start immediately
          </label>
          <div className="spacer" />
          <button className="primary" disabled={busy}>
            {busy ? "Creating…" : "Create project"}
          </button>
        </div>
      </form>
    </>
  );
}
