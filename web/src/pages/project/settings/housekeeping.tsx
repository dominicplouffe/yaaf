// Settings → Duplicate & export, and Settings → Advanced. Each is a short
// card of rows — what it does, the one button — with the form, the file's
// warning, or the JSON behind its own drawer or dialog.

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { CloneResult, Dashboard, get, patch, post, ProjectBundle } from "../../../api";
import { ConfirmModal, CopyField, Drawer } from "../../../ui";
import { Seg } from "../../../ui/kit";
import { modeLabel } from "../../../ui/project";
import { SectionHead } from "../../../settings/kit";
import type { Rules } from "./rules";

/** "amazon-audit-v2" → "amazon-audit-v3"; "Acme" → "Acme (clone)". Twins come
    in numbered runs far more often than they come in ones. */
function nextInSeries(value: string, fallback: string): string {
  const match = value.match(/^(.*?)(\d+)$/);
  return match ? `${match[1]}${Number(match[2]) + 1}` : `${value}${fallback}`;
}

type Note = (text: string) => void;

export function DuplicateSection({ projectId, dash, onError }: {
  projectId: string; dash: Dashboard; onError: Note;
}) {
  const [cloning, setCloning] = useState(false);
  const [exporting, setExporting] = useState(false);

  async function downloadSetup() {
    try {
      const bundle = await get<ProjectBundle>(`/admin/projects/${projectId}/export?include_values=true`);
      const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${bundle.source_slug}-setup.json`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setExporting(false);
    }
  }

  return (
    <>
      <SectionHead title="Duplicate & export"
        lede="Start a twin of this project on this install, or take its whole setup elsewhere as one file." />
      <div className="set-card">
        <div className="set-row">
          <span className="set-row-lab">Duplicate this project<span className="set-row-hint">A twin that starts stopped</span></span>
          <div className="set-row-val">Same teams, people, rules and role versions. None of the history. Point it at a new goal as you make it.</div>
          <div className="set-row-act"><button type="button" className="small primary" onClick={() => setCloning(true)}>Duplicate…</button></div>
        </div>
        <div className="set-row">
          <span className="set-row-lab">Download setup file<span className="set-row-hint">One JSON file</span></span>
          <div className="set-row-val">Upload it on the New Project page and the form fills itself in.{" "}
            <span className="badge warn">Holds credential values, decrypted</span></div>
          <div className="set-row-act"><button type="button" className="small" onClick={() => setExporting(true)}>Download…</button></div>
        </div>
      </div>

      {cloning && <CloneDrawer projectId={projectId} dash={dash} onClose={() => setCloning(false)} onError={onError} />}
      {exporting && (
        <ConfirmModal title="Download the setup file?" confirmLabel="Download"
          onConfirm={() => void downloadSetup()} onCancel={() => setExporting(false)}>
          <p style={{ marginTop: 0 }}>It holds this project&apos;s teams, schedules, role definitions, rules,
            configuration and data sources, and its service credential <b>values</b>, Slack tokens and
            webhook signing keys, decrypted. Store it the way you store passwords.</p>
          <p className="help">Shared LLM credentials stay on this install; on the New Project page, pick one
            for billing. To duplicate on this install, the twin is simpler.</p>
        </ConfirmModal>
      )}
    </>
  );
}

function CloneDrawer({ projectId, dash, onClose, onError }: {
  projectId: string; dash: Dashboard; onClose: () => void; onError: Note;
}) {
  const navigate = useNavigate();
  const [slug, setSlug] = useState(() => nextInSeries(dash.slug, "-2"));
  const [name, setName] = useState(() => nextInSeries(dash.name, " (clone)"));
  // The usual reason to duplicate is to aim it at the next thing, so the goal
  // is a field here, not something to fix afterwards.
  const [goal, setGoal] = useState(dash.goal);
  const [busy, setBusy] = useState(false);
  const okSlug = /^[a-z0-9-]{2,64}$/.test(slug.trim());

  async function clone() {
    setBusy(true);
    try {
      const twin = await post<CloneResult>(`/admin/projects/${projectId}/clone`, {
        slug: slug.trim(), name: name.trim() || null, goal: goal.trim() || null,
      });
      // What did not come along is news worth stopping for, said before the
      // chairman lands on the twin rather than found three days later.
      const said: string[] = [];
      const broken = twin.copied.data_sources.filter((s) => !s.check.ok);
      if (broken.length) said.push(`${broken.length === 1 ? "A data source is" : "Its data sources are"} not answering: `
        + `${broken.map((s) => `"${s.name}": ${s.check.detail}`).join("; ")}. Fix the address in the twin's Settings → Data sources.`);
      if (twin.copied.slack_rooms_dropped) said.push("The Slack tokens came along but not the rooms, so two projects never post into one room. Map the twin's rooms in its Settings → Integrations.");
      if (twin.copied.deadline_dropped) said.push("No deadline: the window it was copied from has already passed. Set a new one in the twin's Settings.");
      if (twin.copied.billing_credential_hidden) said.push(`No billing credential: "${twin.copied.billing_credential_hidden}" has since been hidden. Pick one in the twin's Settings → Money.`);
      if (said.length) alert(`Twin "${twin.slug}" created.\n\n${said.join("\n\n")}`);
      navigate(`/projects/${twin.id}`);
    } catch (e) {
      onError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <Drawer title="Duplicate this project" sub="A running twin that starts stopped. Review it, then press Start."
      onClose={onClose}
      footer={<>
        <span className="grow" />
        <button type="button" onClick={onClose}>Cancel</button>
        <button type="button" className="primary" disabled={busy || !okSlug} onClick={() => void clone()}>
          {busy ? "Duplicating…" : "Create the twin"}</button>
      </>}>
      <label className="field" htmlFor="clone-name">Name</label>
      <input id="clone-name" value={name} onChange={(e) => setName(e.target.value)} />
      <label className="field" htmlFor="clone-slug">Address</label>
      <input id="clone-slug" className="mono" value={slug} pattern="[a-z0-9-]{2,64}" onChange={(e) => setSlug(e.target.value)} />
      <span className={okSlug ? "help" : "help serious"}>Lowercase letters, digits and dashes.</span>
      <label className="field" htmlFor="clone-goal">Goal</label>
      <textarea id="clone-goal" rows={6} value={goal} onChange={(e) => setGoal(e.target.value)} />
      <span className="help">Point it wherever you like.</span>
      <div className="pset-twin">
        <div><b>Comes along</b>
          <ul>
            <li>Teams, and the exact role versions this project runs</li>
            <li>People and rules</li>
            <li>Service credentials, copied</li>
            <li>The billing credential</li>
            <li>Data sources, tested again with the twin&apos;s copies</li>
          </ul></div>
        <div><b>Stays behind</b>
          <ul className="gone">
            <li>Runs, tasks, messages, memory</li>
            <li>Files and rule changes</li>
            <li>The Slack room map</li>
            <li>Webhook senders; the twin makes its own signing secrets</li>
          </ul></div>
      </div>
      <p className="help">Kind ({modeLabel(dash.mode)}) and teams are fixed at creation. To change them, download
        the setup file and use New Project.</p>
    </Drawer>
  );
}

export function AdvancedSection({ projectId, dash, rules, onSaved, onError, openRules }: {
  projectId: string;
  dash: Dashboard;
  rules: Rules;
  onSaved: Note;
  onError: Note;
  openRules: () => void;
}) {
  const runtime = String(dash.integrations["runtime"] ?? "agent_sdk");
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState<string | null>(null);
  const [json, setJson] = useState<string | null>(null);

  async function setRuntime(next: string) {
    setBusy(true);
    try {
      await patch(`/admin/projects/${projectId}`, { integrations: { runtime: next } });
      onSaved(next === "mock" ? "Test stand-ins from the next run. Nothing real happens." : "Real agents from the next run.");
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
      setAsking(null);
    }
  }

  return (
    <>
      <SectionHead title="Advanced" lede="Settings you will almost never need. Each says what it changes before you change it." />
      <div className="set-card">
        <div className="set-row">
          <span className="set-row-lab">Agents<span className="set-row-hint">Real, or scripted stand-ins</span></span>
          <div className="set-row-val">
            <Seg value={runtime} onPick={(v) => { if (v !== runtime) setAsking(v); }}
              options={[{ key: "agent_sdk", label: "Real agents" }, { key: "mock", label: "Test stand-ins" }]} />
            <span className={`pset-line ${runtime === "mock" ? "warn-ink" : "help"}`}>{runtime === "mock"
              ? "Stand-ins are free and do nothing real. For platform development only."
              : "Real agents, each on its own harness. Runs cost money."}</span>
          </div>
          <div className="set-row-act" />
        </div>
        <div className="set-row">
          <span className="set-row-lab">Project ID</span>
          <div className="set-row-val"><CopyField value={projectId} /></div>
          <div className="set-row-act" />
        </div>
        <div className="set-row">
          <span className="set-row-lab">Change rules as JSON<span className="set-row-hint">For bulk edits and scripts</span></span>
          <div className="set-row-val">Staged like any rule change: it asks for your reason and shows what changes before it lands.</div>
          <div className="set-row-act">
            <button type="button" className="small" onClick={() => setJson(rules.json === "{}" ? "{\n  \n}" : rules.json)}>
              Open JSON editor</button>
          </div>
        </div>
      </div>

      {asking && (
        <ConfirmModal title={asking === "mock" ? "Switch to test stand-ins?" : "Switch to real agents?"}
          confirmLabel={asking === "mock" ? "Use test stand-ins" : "Use real agents"} danger={asking === "mock"} busy={busy}
          onConfirm={() => void setRuntime(asking)} onCancel={() => setAsking(null)}>
          <p style={{ marginTop: 0 }}>{asking === "mock"
            ? "From the next run, scripted fakes answer instead of models. They are free and do nothing real; the project's work stops moving."
            : `From the next run, agents call real models and spend against the budget${dash.billing_secret_id ? "" : " — once a billing credential is connected under Money"}.`}</p>
        </ConfirmModal>
      )}
      {json !== null && (
        <Drawer title="Change rules as JSON" sub="Keys you set here are staged as a rule change, with the same reason and review."
          onClose={() => setJson(null)}
          footer={<>
            <span className="grow" />
            <button type="button" onClick={() => setJson(null)}>Cancel</button>
            <button type="button" className="primary" onClick={() => {
              rules.setJson(json.trim() || "{}");
              setJson(null);
              openRules();
            }}>Stage changes</button>
          </>}>
          <textarea className="mono" rows={14} aria-label="Rule changes as JSON" value={json}
            onChange={(e) => setJson(e.target.value)} />
          <p className="help">An object of rule keys and values, e.g. {"{ \"max_concurrent_runs\": 4 }"}.</p>
        </Drawer>
      )}
    </>
  );
}
