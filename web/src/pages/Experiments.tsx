import { useState } from "react";
import { Link } from "react-router-dom";
import { get, post } from "../api";
import { usePoll } from "../hooks";
import { AsOf, Skeleton } from "../ui";

/** The topology experiment: your suspicion, made answerable. Three org
    shapes, one goal, one requirement set, one constitution — compared on
    numbers no arm can flatter. */

interface ExperimentSummary {
  id: string;
  name: string;
  status: string;
  created_at: string;
  requirements: number;
  arms: number;
}

interface ArmMetrics {
  requirements_met: number;
  runs: number;
  spend_usd: number;
  cost_per_met_usd: number | null;
  hours_to_first_attested_evidence: number | null;
  share_of_spend_producing_nothing: number | null;
  internal_words: number;
  delivered_bytes: number;
  words_per_delivered_byte: number | null;
}

interface ArmView {
  arm_id: string;
  topology: string;
  project_id: string;
  project_slug: string;
  project_status: string;
  status_reason: string | null;
  stopped_reason: string | null;
  metrics: ArmMetrics;
}

interface ExperimentReport {
  id: string;
  name: string;
  goal: string;
  status: string;
  requirement_set: { statement: string; est_runs: number }[];
  arms: ArmView[];
  asymmetries: string[];
}

const TOPOLOGY_LABELS: Record<string, string> = {
  solo: "Solo — one brain plans and executes",
  ceo_team: "CEO + team — flat, no leads",
  ceo_leads_workers: "CEO + leads + workers — full hierarchy",
};

function metric(value: number | null, suffix = "", none = "—"): string {
  return value === null ? none : `${value}${suffix}`;
}

function Report({ id, onClose }: { id: string; onClose: () => void }) {
  const { data, at, error, reload } = usePoll<ExperimentReport>(
    () => get<ExperimentReport>(`/observe/experiments/${id}`), 10000);
  if (!data) return <Skeleton rows={4} />;
  const best = data.arms
    .filter((a) => a.metrics.cost_per_met_usd !== null)
    .sort((a, b) => (a.metrics.cost_per_met_usd! - b.metrics.cost_per_met_usd!))[0];
  return (
    <div className="card" style={{ padding: "14px 18px", marginBottom: 16 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
        <h2 style={{ margin: 0 }}>{data.name}</h2>
        <span className={`badge ${data.status === "running" ? "accent" : ""}`}>
          {data.status}
        </span>
        <span className="sp" style={{ flex: 1 }} />
        <AsOf at={at} error={error} />
        {data.status === "running" && (
          <button className="small danger" onClick={async () => {
            await post(`/admin/experiments/${id}/close`,
              { reason: "closed from the Experiments page" });
            reload();
          }}>Close the experiment</button>
        )}
        <button className="small" onClick={onClose}>back</button>
      </div>
      <p className="help" style={{ margin: "6px 0 0" }}>{data.goal}</p>
      <p className="help" style={{ margin: "2px 0 12px" }}>
        {data.requirement_set.length} identical requirements per arm, chairman-authored,
        accepted at birth — the CEOs plan how, never what.
      </p>

      <div style={{ overflowX: "auto" }}>
        <table style={{ minWidth: 720 }}>
          <thead>
            <tr>
              <th>Arm</th>
              <th className="num">Met</th>
              <th className="num">Spend</th>
              <th className="num">$ / met</th>
              <th className="num">Hours to attested proof</th>
              <th className="num">Spend on nothing</th>
              <th className="num">Words / delivered byte</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {data.arms.map((a) => (
              <tr key={a.arm_id} style={
                best && a.arm_id === best.arm_id
                  ? { background: "rgba(61,169,101,0.06)" } : undefined}>
                <td>
                  <Link to={`/projects/${a.project_id}`}>{a.topology}</Link>
                  <div className="help">{TOPOLOGY_LABELS[a.topology]}</div>
                </td>
                <td className="num">{a.metrics.requirements_met}</td>
                <td className="num">${a.metrics.spend_usd.toFixed(2)}</td>
                <td className="num"><b>{a.metrics.cost_per_met_usd === null
                  ? "—" : `$${a.metrics.cost_per_met_usd}`}</b></td>
                <td className="num">{metric(a.metrics.hours_to_first_attested_evidence, "h")}</td>
                <td className="num">{a.metrics.share_of_spend_producing_nothing === null
                  ? "—" : `${Math.round(a.metrics.share_of_spend_producing_nothing * 100)}%`}</td>
                <td className="num">{metric(a.metrics.words_per_delivered_byte)}</td>
                <td>
                  <span className="badge">{a.project_status}</span>
                  {a.stopped_reason && (
                    <div className="help" style={{ maxWidth: 200 }}>{a.stopped_reason}</div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data.asymmetries.map((note, i) => (
        <p key={i} className="help" style={{ marginTop: 10 }}>⚠ {note}</p>
      ))}
    </div>
  );
}

interface RequirementDraft { statement: string; command: string; est_runs: number }

function CreateForm({ onCreated }: { onCreated: (id: string) => void }) {
  const [name, setName] = useState("");
  const [slugBase, setSlugBase] = useState("");
  const [goal, setGoal] = useState("");
  const [chairmanEmail, setChairmanEmail] = useState("");
  const [workerRole, setWorkerRole] = useState("");
  const [teamSize, setTeamSize] = useState(2);
  const [usdCap, setUsdCap] = useState(50);
  const [requirements, setRequirements] = useState<RequirementDraft[]>([
    { statement: "", command: "", est_runs: 4 },
  ]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ready = name.trim().length >= 3 && slugBase.trim().length >= 2 &&
    goal.trim().length >= 10 && chairmanEmail.includes("@") && workerRole.trim() &&
    requirements.every((r) => r.statement.trim().length >= 10 &&
      r.command.trim().length >= 15 && r.est_runs >= 1);

  return (
    <div className="card" style={{ maxWidth: 760 }}>
      <h2 style={{ marginTop: 0 }}>New experiment</h2>
      <p className="help">
        Every arm gets this goal, these requirements (accepted at birth) and the same
        constitution. Command tests keep the grading self-judging — the platform runs
        them identically in every arm.
      </p>
      <div className="row">
        <div>
          <label className="field">Name</label>
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <label className="field">Slug base (a-z, 0-9, dashes)</label>
          <input value={slugBase} onChange={(e) => setSlugBase(e.target.value)} />
        </div>
      </div>
      <label className="field">The shared goal</label>
      <textarea rows={2} value={goal} onChange={(e) => setGoal(e.target.value)} />
      <div className="row">
        <div>
          <label className="field">Chairman email</label>
          <input value={chairmanEmail} onChange={(e) => setChairmanEmail(e.target.value)} />
        </div>
        <div>
          <label className="field">Worker role slug</label>
          <input value={workerRole} onChange={(e) => setWorkerRole(e.target.value)} />
        </div>
        <div>
          <label className="field">Employees per arm</label>
          <input type="number" min={2} max={6} value={teamSize}
            onChange={(e) => setTeamSize(Number(e.target.value))} />
        </div>
        <div>
          <label className="field">Monthly cap per arm (USD)</label>
          <input type="number" min={1} value={usdCap}
            onChange={(e) => setUsdCap(Number(e.target.value))} />
        </div>
      </div>
      <label className="field">Requirements — identical in every arm</label>
      {requirements.map((r, i) => (
        <div key={i} style={{ display: "flex", gap: 8, marginBottom: 6, flexWrap: "wrap" }}>
          <input style={{ flex: 2, minWidth: 220 }} placeholder="What must be TRUE at the end"
            value={r.statement}
            onChange={(e) => setRequirements(requirements.map(
              (x, j) => j === i ? { ...x, statement: e.target.value } : x))} />
          <input style={{ flex: 2, minWidth: 220 }}
            placeholder="Command that proves it, e.g. test -s out/report.csv"
            value={r.command}
            onChange={(e) => setRequirements(requirements.map(
              (x, j) => j === i ? { ...x, command: e.target.value } : x))} />
          <input type="number" min={1} style={{ width: 80 }} title="estimated runs"
            value={r.est_runs}
            onChange={(e) => setRequirements(requirements.map(
              (x, j) => j === i ? { ...x, est_runs: Number(e.target.value) } : x))} />
          {requirements.length > 1 && (
            <button className="small" type="button" onClick={() =>
              setRequirements(requirements.filter((_, j) => j !== i))}>✕</button>
          )}
        </div>
      ))}
      <button className="small" type="button" onClick={() =>
        setRequirements([...requirements, { statement: "", command: "", est_runs: 3 }])}>
        + requirement
      </button>
      {error && <div className="error" style={{ marginTop: 8 }}>{error}</div>}
      <div style={{ marginTop: 12 }}>
        <button className="primary" disabled={!ready || busy} onClick={async () => {
          setBusy(true);
          setError(null);
          try {
            const created = await post<{ id: string }>("/admin/experiments", {
              name, slug_base: slugBase, goal, chairman_email: chairmanEmail,
              worker_role_slug: workerRole, team_size: teamSize,
              constitution: { monthly_usd_cap: usdCap },
              requirements: requirements.map((r) => ({
                statement: r.statement,
                acceptance_test: { type: "command", command: r.command },
                est_runs: r.est_runs,
              })),
            });
            onCreated(created.id);
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}>
          {busy ? "Creating three companies…" : "Run the experiment"}
        </button>
      </div>
    </div>
  );
}

export default function Experiments() {
  const { data, reload } = usePoll<ExperimentSummary[]>(
    () => get<ExperimentSummary[]>("/observe/experiments"), 15000);
  const [openId, setOpenId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  if (openId) return (
    <>
      <h1>Experiments</h1>
      <Report id={openId} onClose={() => setOpenId(null)} />
    </>
  );

  return (
    <>
      <h1>Experiments</h1>
      <p className="subtitle">
        Is the employee layer worth it? Run the same goal through three org shapes
        under identical constitutions and identical chairman-authored scope, and read
        the answer in cost per requirement met.
      </p>
      {creating ? (
        <CreateForm onCreated={(id) => { setCreating(false); setOpenId(id); reload(); }} />
      ) : (
        <button className="primary" style={{ marginBottom: 14 }}
          onClick={() => setCreating(true)}>+ New experiment</button>
      )}
      {!data ? <Skeleton rows={3} /> : data.length === 0 ? (
        !creating && <p className="help">None yet.</p>
      ) : (
        <table style={{ maxWidth: 720 }}>
          <thead>
            <tr><th>Name</th><th>Status</th><th className="num">Arms</th>
              <th className="num">Requirements</th><th></th></tr>
          </thead>
          <tbody>
            {data.map((e) => (
              <tr key={e.id}>
                <td>{e.name}</td>
                <td><span className="badge">{e.status}</span></td>
                <td className="num">{e.arms}</td>
                <td className="num">{e.requirements}</td>
                <td><button className="small" onClick={() => setOpenId(e.id)}>
                  report</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
