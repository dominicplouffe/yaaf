import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { get, post, RoleSummary } from "../api";

interface TeamDraft {
  role_slug: string;
  name: string;
  employees: string; // comma-separated names
  lead: string;
}

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
  const [runtime, setRuntime] = useState("agent_sdk");
  const [controlledDomains, setControlledDomains] = useState("");
  const [teams, setTeams] = useState<TeamDraft[]>([
    { role_slug: "", name: "", employees: "", lead: "" },
  ]);

  // Constitution knobs surfaced in the wizard (frozen at creation).
  const [queueCeiling, setQueueCeiling] = useState(8);
  const [droughtHours, setDroughtHours] = useState(24);
  const [usdCap, setUsdCap] = useState(1000);
  const [approvalThreshold, setApprovalThreshold] = useState(250);
  const [startNow, setStartNow] = useState(true);

  useEffect(() => {
    get<RoleSummary[]>("/admin/roles").then(setRoles).catch((e: Error) => setError(e.message));
  }, []);

  const workerRoles = roles.filter((r) => !r.is_ceo_role);
  const ceoRole = roles.find((r) => r.is_ceo_role);

  function setTeam(i: number, patch: Partial<TeamDraft>) {
    setTeams((prev) => prev.map((t, j) => (j === i ? { ...t, ...patch } : t)));
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (!ceoRole) throw new Error("No CEO role in the library — create one on the Roles page first.");
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
        controlled_domains: controlledDomains
          .split(",")
          .map((d) => d.trim())
          .filter(Boolean),
        constitution: {
          queue_ceiling: queueCeiling,
          drought_hours: droughtHours,
          monthly_usd_cap: usdCap,
          approval_threshold_usd: approvalThreshold,
        },
        config: { runtime },
        ceo_role_slug: ceoRole.slug,
        teams: teams
          .filter((t) => t.role_slug && t.name && t.employees.trim())
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
      const created = await post<{ id: string }>("/admin/projects", body);
      if (startNow) await post(`/admin/projects/${created.id}/start`);
      navigate(`/projects/${created.id}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1>New project</h1>
      <p className="subtitle">
        The constitution freezes at creation — after that, changes need a chairman amendment with a
        72-hour cooling-off.
      </p>
      {error && <div className="error">{error}</div>}
      <form onSubmit={submit} style={{ maxWidth: 760 }}>
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
            Everything else (external-evidence floor, wake caps, memory caps, refusal caps…) takes the
            lana-derived defaults; amend later if you must — with the cooling-off.
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
