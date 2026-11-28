// A project's Settings: a rail that reports each section's state, and the
// section. Every section follows the same shape (settings/kit.tsx): a head
// with one line on what it is for, its state and at most one main action;
// settings as rows in a card; edits that need several fields in a drawer;
// one bar at the bottom for anything unsaved; and what cannot be taken back
// in a danger zone, said before the click.
//
// The sections live in ./settings/ (Brief, Money, Rules, Integrations,
// Duplicate & export, Advanced) or beside this file (Data sources, Secrets,
// Email, People, Code Factory). This file is the frame and the shared state:
// the secrets list several sections read, and the rule edits Money and Rules
// in force share.

import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Dashboard, get, McpServerEntry, OrgView, SecretRow } from "../../api";
import { useProject, ProjectCtx } from "../../projectCtx";
import { SetupStrip } from "../../projectSetup";
import { CredentialsSection } from "../../credentialNeeds";
import { PinButton } from "../../shortcuts";
import { SETTINGS_NAV } from "../../nav";
import { budgetOf, fmtUnit } from "../../ui";
import { Icon } from "../../ui/Icon";
import { PageHead } from "../../ui/kit";
import { Flash, SectionHead } from "../../settings/kit";
import DataSources from "./DataSources";
import { EnvironmentsCard, FactoryGithub } from "./Factory";
import { MailboxSettings } from "./Mailbox";
import { MembersPanel } from "./Members";
import Secrets, { pendingNeeds } from "./Secrets";
import { BriefSection } from "./settings/brief";
import { AdvancedSection, DuplicateSection } from "./settings/housekeeping";
import { IntegrationsSection } from "./settings/integrations";
import { MoneySection } from "./settings/money";
import { RulesPending, RulesSection, useRules } from "./settings/rules";
import "./settings/settings.css";

type Tone = "critical" | "warn" | "good" | "";
interface RailNote { text: string; tone: Tone; count?: number }

/** "data" was the page Data sources and Secrets once shared; every old link
    to it still lands. */
function sectionOf(asked: string | null): string {
  const s = asked ?? "brief";
  return s === "data" ? "secrets" : s;
}

export default function Settings({ projectId, dash, org, reload }: {
  projectId: string;
  dash: Dashboard;
  /** The roster, polled once for the whole project page. */
  org: OrgView | null;
  reload: () => void;
}) {
  const { approvals } = useProject();
  const needs = pendingNeeds(approvals);
  // In the URL, so every "Settings → Rules in force" link can land exactly.
  const [params, setParams] = useSearchParams();
  const section = sectionOf(params.get("s"));
  const theme = params.get("t") ?? "";
  const go = useCallback((next: string, tab?: string) =>
    setParams(tab ? { s: next, t: tab } : { s: next }, { replace: true }), [setParams]);

  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => { setNote(null); }, [section]);
  const rules = useRules(projectId, dash);
  const [flashKey, setFlashKey] = useState<string | null>(null);
  const [factoryDone, setFactoryDone] = useState<number | null>(null);

  // One fetch for the page: Money, Data sources, Integrations and Secrets all
  // address a secret by name and need the list to find it.
  const [secrets, setSecrets] = useState<SecretRow[]>([]);
  const refreshSecrets = useCallback(() => {
    get<SecretRow[]>(`/admin/secrets?project_id=${projectId}`).then(setSecrets).catch(() => {});
  }, [projectId]);
  useEffect(refreshSecrets, [refreshSecrets, dash.billing_secret_id]);

  const saved = (text: string) => { setNote({ ok: true, text }); reload(); refreshSecrets(); };
  const failed = (text: string) => setNote({ ok: false, text });
  // The shape Data sources and Secrets were written against.
  const notice = (text: string) => saved(text);

  const openRules = (tab = "Money", key?: string) => {
    go("rules", tab);
    if (!key) return;
    setFlashKey(key);
    window.setTimeout(() => document.getElementById(`rule-${key}`)?.scrollIntoView({ behavior: "smooth", block: "center" }), 0);
    window.setTimeout(() => setFlashKey(null), 1600);
  };

  const runtime = String(dash.integrations["runtime"] ?? "agent_sdk");
  const notes = railNotes(dash, rules.count, secrets, needs.length, factoryDone);

  return (
    <div className="set-lay pset-lay">
      <ProjectRail dash={dash} section={section} notes={notes} onPick={(k) => go(k)} />

      <div className="set-main pset-main">
        {runtime === "mock" ? (
          <div className="pset-alert warn" role="status">
            <span className="dot warn" aria-hidden="true" />
            <span className="grow"><b>Test stand-ins are running this project.</b> Scripted fakes, free; nothing real happens.</span>
            {section !== "advanced" && <button type="button" className="small" onClick={() => go("advanced")}>Use real agents</button>}
          </div>
        ) : dash.billing_secret_id === null && section !== "money" ? (
          <div className="pset-alert critical" role="status">
            <span className="dot critical" aria-hidden="true" />
            <span className="grow"><b>Agents have no way to pay for their model.</b> Every real run fails until a credential is connected.</span>
            <button type="button" className="small" onClick={() => go("money")}>Open Money</button>
          </div>
        ) : null}
        <Flash note={note} />

        {section === "brief" && (
          <BriefSection projectId={projectId} dash={dash} onSaved={saved} onError={failed} />
        )}
        {section === "money" && (
          <MoneySection projectId={projectId} dash={dash} rules={rules} reload={reload}
            onSaved={saved} onError={failed} openRules={() => openRules("Money")} />
        )}
        {section === "factory" && (dash.mode === "code_factory" ? <>
          <SectionHead title="Code Factory"
            lede="Where this project builds and where it ships. The repository is needed before the first change; the environments only before the first release.">
            {factoryDone !== null && (
              <span className={`badge ${factoryDone === 4 ? "good" : "warn"}`}>Repository: {factoryDone} of 4 done</span>
            )}
          </SectionHead>
          <section className="set-group">
            <div className="set-group-h">Repository <span className="c">· four steps, in order</span></div>
            <FactoryGithub projectId={projectId} onCount={setFactoryDone} />
          </section>
          <section className="set-group">
            <div className="set-group-h">Environments <span className="c">· needed before the first release</span></div>
            <EnvironmentsCard projectId={projectId} dash={dash} onSaved={() => saved("Saved. The next deploy uses it.")} />
          </section>
        </> : (
          <>
            <SectionHead title="Code Factory" />
            <div className="set-card pad help">This project is not a Code Factory, so it has no repo or deploy settings of its own.
              Its GitHub workspace is under <a href={`#/projects/${projectId}/settings?s=integrations`}>Integrations</a>.</div>
          </>
        ))}
        {section === "sources" && (
          <DataSources projectId={projectId} dash={dash} org={org} secrets={secrets} notice={notice} reload={reload} />
        )}
        {section === "credentials" && (
          <CredentialsSection projectId={projectId} canSteer={dash.can_steer !== false}
            focus={params.get("need")} onSaved={reload} />
        )}
        {section === "secrets" && (
          <Secrets projectId={projectId} dash={dash} org={org} secrets={secrets}
            refreshSecrets={refreshSecrets} notice={notice} />
        )}
        {section === "integrations" && (
          <IntegrationsSection projectId={projectId} dash={dash} secrets={secrets} onSaved={saved} onError={failed} />
        )}
        {section === "mail" && <MailboxSettings projectId={projectId} />}
        {section === "people" && <MembersPanel projectId={projectId} dash={dash} reload={reload} />}
        {section === "rules" && (
          <RulesSection rules={rules} dash={dash} theme={theme} flashKey={flashKey}
            setTheme={(t) => go("rules", t)} />
        )}
        {section === "duplicate" && <DuplicateSection projectId={projectId} dash={dash} onError={failed} />}
        {section === "advanced" && (
          <AdvancedSection projectId={projectId} dash={dash} rules={rules} onSaved={saved} onError={failed}
            openRules={() => openRules("Money")} />
        )}

        {(section === "rules" || section === "money") && (
          <RulesPending projectId={projectId} rules={rules} onSaved={saved} onError={failed} />
        )}
      </div>
    </div>
  );
}

/** What each rail entry says about its section, from what the page already
    holds: a state, never a description. */
function railNotes(dash: Dashboard, ruleEdits: number, secrets: SecretRow[], needs: number,
                   factoryDone: number | null): Record<string, RailNote> {
  const n: Record<string, RailNote> = {};
  const integ = dash.integrations;
  n.brief = dash.brief_confirmed_at
    ? { text: `Confirmed ${new Date(dash.brief_confirmed_at).toLocaleDateString(undefined, { month: "short", day: "numeric" })}`, tone: "" }
    : { text: "Not confirmed yet", tone: "" };

  const b = budgetOf(dash);
  const said = (x: number) => b.unit === "usd" ? fmtUnit(x, "usd") : `${fmtUnit(x, "tokens")} tokens`;
  n.money = dash.billing_secret_id === null ? { text: "No billing credential", tone: "critical" }
    : dash.coding?.applies && !dash.coding.credential_id ? { text: "No coding key", tone: "critical" }
    : dash.fallback?.active ? { text: "Running on the fallback", tone: "warn" }
    : b.cap > 0 ? { text: `${said(b.spent)} of ${said(b.cap)} this month`, tone: b.frac >= b.warnRatio ? "warn" : "" }
    : { text: `${said(b.spent)} this month`, tone: "" };

  n.factory = factoryDone !== null
    ? { text: factoryDone === 4 ? "Repository connected" : `${factoryDone} of 4 steps done`, tone: factoryDone === 4 ? "" : "warn" }
    : dash.github_repo ? { text: dash.github_repo, tone: "" } : { text: "No repository yet", tone: "warn" };

  const sources = (integ["mcp_servers"] as McpServerEntry[] | undefined) ?? [];
  n.sources = { text: sources.length ? `${sources.length} connected` : "None", tone: "" };
  n.credentials = { text: "Access you promised", tone: "" };
  const own = secrets.filter((s) => s.kind !== "billing").length;
  n.secrets = needs
    ? { text: `${needs} asked for by an agent`, tone: "warn", count: needs }
    : { text: own ? `${own} variable${own === 1 ? "" : "s"}` : "None yet", tone: "" };

  const slack = (integ["slack"] ?? null) as { enabled?: boolean; channel_map?: Record<string, string> } | null;
  const rooms = Object.values(slack?.channel_map ?? {}).filter((v) => v.trim()).length;
  n.integrations = slack?.enabled === false ? { text: "Slack is off", tone: "" }
    : { text: rooms ? `Slack: ${rooms} room${rooms === 1 ? "" : "s"}` : "Slack not mapped", tone: "" };

  const mail = (integ["mail"] ?? null) as { enabled?: boolean; address?: string } | null;
  n.mail = dash.mail_start_refusal ? { text: "Needed to start", tone: "critical" }
    : mail?.address ? { text: mail.enabled === false ? "Off" : mail.address, tone: "" }
    : { text: "Not set up", tone: "" };

  n.people = { text: dash.chairman ? `Chaired by ${dash.chairman.display_name}` : "Who decides", tone: "" };
  n.rules = ruleEdits ? { text: `${ruleEdits} unsaved change${ruleEdits === 1 ? "" : "s"}`, tone: "warn" }
    : { text: "Every limit in force", tone: "" };
  n.duplicate = { text: "Twin or setup file", tone: "" };
  n.advanced = String(integ["runtime"] ?? "agent_sdk") === "mock"
    ? { text: "Test stand-ins", tone: "warn" } : { text: "Real agents", tone: "" };
  return n;
}

/** The rail: one entry per section, each carrying its state, grouped as the
    nav says, with a box that finds a section by what it holds. */
function ProjectRail({ dash, section, notes, onPick }: {
  dash: Dashboard;
  section: string;
  notes: Record<string, RailNote>;
  onPick: (key: string) => void;
}) {
  const [find, setFind] = useState("");
  const q = find.trim().toLowerCase();
  const items = SETTINGS_NAV.filter((item) => !("only" in item) || item.only === dash.mode);
  const rows: React.ReactNode[] = [];
  let group: string | null = null;
  for (const item of items) {
    if ("group" in item) { group = item.group; continue; }
    if (q && !`${item.label} ${item.words}`.toLowerCase().includes(q)) continue;
    if (group) { rows.push(<div className="set-rail-grp" key={`g-${group}`}>{group}</div>); group = null; }
    const note = notes[item.key] ?? { text: "", tone: "" };
    const on = section === item.key;
    rows.push(
      <button type="button" key={item.key} className={`set-rail-item${on ? " on" : ""}`}
        aria-current={on ? "page" : undefined} onClick={() => onPick(item.key)}>
        <span className="l">{item.label}</span>
        {note.count ? <span className="cnt" aria-label={`${note.count} need you`}>{note.count}</span> : <span />}
        <span className={`s ${note.tone === "critical" || note.tone === "warn" ? note.tone : ""}`}>
          {(note.tone === "critical" || note.tone === "warn") && <span className={`dot ${note.tone}`} aria-hidden="true" />}
          <span>{note.text || " "}</span>
        </span>
      </button>,
    );
  }
  return (
    <nav className="set-rail pset-rail" aria-label="Project settings sections">
      <label className="pset-find">
        <Icon name="search" size={14} />
        <input type="search" value={find} placeholder="Find a setting" aria-label="Find a setting"
          onChange={(e) => setFind(e.target.value)} />
      </label>
      {rows.length ? rows : <p className="help" style={{ padding: "6px 10px" }}>Nothing matches “{find.trim()}”.</p>}
    </nav>
  );
}

/** The project's Settings as a page of its own, under the gear on the project
    in the sidebar. While the project is being set up, its progress is one
    line across the top, with the next step one click away. */
export function SettingsPage({ ctx }: { ctx: ProjectCtx }) {
  const { projectId, dash } = ctx;
  const [params] = useSearchParams();
  const section = sectionOf(params.get("s"));
  const label = SETTINGS_NAV.find((i) => "key" in i && i.key === section);
  return (
    <>
      <PageHead title="Settings"
        actions={label && "key" in label
          ? <PinButton projectId={projectId} to={`settings?s=${section}`} label={label.label} />
          : undefined} />
      <SetupStrip ctx={ctx} section={section} compact />
      <Settings projectId={projectId} dash={dash} org={ctx.org} reload={() => { ctx.reload(); ctx.reloadSetup(); }} />
    </>
  );
}
