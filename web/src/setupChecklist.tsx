// Home's "Get set up": the three or four things that stand between a fresh
// install and a project that runs, in order, each ticking itself off.
//
// A new administrator used to learn what an install needs by being refused:
// the project is created, its first run is refused for a price, the next for a
// search backend. This is the same list the server would refuse on (the
// readiness report), shown before anything is created, and gone the moment the
// install can run a project — a checklist that outstays its use is wallpaper.

import { Link } from "react-router-dom";
import { FIX_LINK, IssueList, providerStatus, useReadiness } from "./readiness";
import { Icon } from "./ui/Icon";

export function SetupChecklist({ hasProjects }: { hasProjects: boolean }) {
  const { data, reload } = useReadiness();
  if (!data || data.install.ready) return null;
  const { install, providers } = data;
  // The provider to get ready first: the default if it is connected, else the
  // one closest to ready.
  const order = { good: 0, warn: 1, critical: 2 } as const;
  const focus = providers.find((p) => p.is_default)
    ?? [...providers].sort((a, b) => order[providerStatus(a).tone] - order[providerStatus(b).tone])[0];
  const blocking = focus ? focus.issues.filter((i) => i.severity === "block") : [];
  const searchNeeded = providers.some((p) => p.search === "none");
  const searchDone = install.web_search || (providers.length > 0 && !searchNeeded);

  const steps: { key: string; done: boolean; title: string; detail: React.ReactNode; go?: React.ReactNode; optional?: boolean }[] = [
    {
      key: "connect", done: install.connected,
      title: install.connected ? `Connected ${providers.map((p) => p.label).join(", ")}` : "Connect a provider",
      detail: install.connected ? "Who sells the tokens your projects run on."
        : "Pick who sells the tokens — Anthropic, OpenAI, DeepSeek, a cloud, or a server of your own — and paste its key. yaaf tests it on the spot.",
      go: !install.connected && <Link className="btnlink primary small" to="/settings?section=providers&connect=1">Connect</Link>,
    },
    {
      key: "ready", done: false,
      title: focus ? `Make ${focus.label}'s models ready to run` : "Make its models ready to run",
      detail: focus
        ? <>Each class needs a model with a price, so a budget can stop what it spends.
            {blocking.length > 0 && <IssueList provider={focus.provider} issues={blocking} onChanged={reload} />}</>
        : "Once a provider is connected, anything it still needs — a price, a model for a class — is listed here with its fix.",
      go: focus && <Link className="btnlink small" to={FIX_LINK.provider(focus.provider)}>Open its card</Link>,
    },
    {
      key: "search", done: searchDone, optional: true,
      title: "Web search (optional)",
      detail: searchDone ? (install.web_search ? "This install has a search backend of its own." : "Your providers search on their own APIs.")
        : "Some providers cannot search the web, and roles that browse are refused on them until this install has a search backend.",
      go: !searchDone && <Link className="btnlink small" to={FIX_LINK.search}>Set up search</Link>,
    },
    {
      key: "project", done: hasProjects,
      title: "Start a project",
      detail: "Describe what you want done in a paragraph; yaaf drafts the team, the goal and the budget.",
      go: <Link className="btnlink small" to="/projects/new">Start one</Link>,
    },
  ];
  const left = steps.filter((s) => !s.done && !s.optional).length;
  return <section className="card ov-card setup-card" aria-label="Get set up">
    <div className="card-h">
      <h3>Get set up</h3>
      <span className="count warn">{left} to go</span>
      <span className="grow" />
      <Link className="card-link" to="/settings?section=providers">Providers <Icon name="arrow" size={13} /></Link>
    </div>
    <p className="help" style={{ margin: 0 }}>Projects cannot run on this install yet. This list goes away once one can.</p>
    <ol className="setup-steps">
      {steps.map((s, i) => <li key={s.key} className={`setup-step${s.done ? " done" : ""}`}>
        <span className="mark" aria-hidden="true">{s.done ? "✓" : i + 1}</span>
        <div>
          <span className="st-t">{s.title}<span className="sr-only">{s.done ? " — done" : " — to do"}</span></span>
          <div className="st-d">{s.detail}</div>
        </div>
        {s.go && <span className="st-go">{s.go}</span>}
      </li>)}
    </ol>
  </section>;
}
