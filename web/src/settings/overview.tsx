// Settings → Overview: can every project run, and what needs you.
//
// The page used to land on Providers, and the one blocked provider was its
// fourth card, 1,785px down, below a healthy one in green. Home's setup
// checklist goes away once any provider is ready, so a second provider going
// wrong had nowhere to show but there. This answers the question an
// administrator opens Settings with in one screen: a ranked list of what needs
// them, with the fix beside each; what each class runs on, per provider, with
// when its key last answered; and one tile per connection.
//
// Nothing here is a second copy. The list, the matrix and the rail read the
// same load (settings/data.ts), and the fixes are the readiness report's own
// (IssueList), so the Overview cannot disagree with the screens it links to.

import { useState } from "react";
import { post } from "../api";
import { IssueList, providerState, StatusBadge } from "../readiness";
import { Icon } from "../ui/Icon";
import { EmptyState } from "../ui/kit";
import { liveProjects, SettingsData, founderOf, names } from "./data";
import { EvidenceSeal, Group, NeedRow, SectionHead, TestSeal } from "./kit";
import { byHealth, needsOf, SectionKey } from "./state";

const perM = (n: number) => `$${n.toLocaleString(undefined, { maximumFractionDigits: 3 })}`;

export function OverviewSection({ data, go }: {
  data: SettingsData;
  go: (section: SectionKey, params?: Record<string, string>) => void;
}) {
  const [testing, setTesting] = useState<string | null>(null);
  const [said, setSaid] = useState<Record<string, string>>({});
  if (!data.readiness) return <div className="card muted">Loading…</div>;
  const needs = needsOf(data);
  const urgent = needs.filter((n) => n.tone !== "neutral");
  const later = needs.filter((n) => n.tone === "neutral");
  const providers = byHealth(data.readiness.providers);
  const defaultLabel = providers.find((p) => p.is_default)?.label ?? data.readiness.default_provider;
  const founder = founderOf(data.settings);
  const slack = data.settings?.slack;
  const search = data.search;

  async function test(key: string, credentialId: string) {
    setTesting(key);
    try {
      const r = await post<{ ok: boolean | null; detail: string }>(
        `/admin/settings/llm-credentials/${credentialId}/smoke`, {});
      setSaid((prev) => ({ ...prev, [key]: r.detail }));
    } catch (e) {
      setSaid((prev) => ({ ...prev, [key]: (e as Error).message }));
    } finally {
      setTesting(null);
      data.reload();
    }
  }

  const row = (n: ReturnType<typeof needsOf>[number]) => (
    <NeedRow key={n.key} tone={n.tone} icon={n.icon} title={n.title}
      facts={n.facts?.map((f) => <span key={f} className="sg-fact">{f}</span>)}
      actions={<>
        {n.test && <button type="button" className="small" disabled={testing !== null}
          title="One real, trivial call on the key — a few hundred tokens"
          onClick={() => test(n.key, n.test!.credentialId)}>{testing === n.key ? "Calling…" : "Test"}</button>}
        <button type="button" className={`small${n.tone === "critical" ? " primary" : ""}`}
          onClick={() => go(n.go.section, n.go.params)}>{n.go.label}</button>
      </>}>
      {n.body}
      {n.provider && <IssueList provider={n.provider.provider} issues={n.provider.issues}
        onChanged={data.reload} />}
      {said[n.key] && <span className="help" role="status"> {said[n.key]}</span>}
    </NeedRow>
  );

  return <>
    <SectionHead title="Overview"
      lede="Can every project run, and what needs you. Read from the same checks that refuse a seat." />

    <Group title="Needs you" chip={urgent.length}>
      <div className="set-card set-needs">
        {urgent.length ? urgent.map(row) : <div className="set-allclear">
          <span className="good-ink"><Icon name="check" size={16} strokeWidth={2.4} /></span>
          <span>Nothing is stopping a seat.{later.length ? " A few things haven’t been confirmed yet:" : ""}</span>
        </div>}
      </div>
    </Group>

    {later.length > 0 && <Group title="Not confirmed yet" count={later.length}>
      <div className="set-card set-needs">{later.map(row)}</div>
    </Group>}

    {providers.length > 0 ? <Group title="What each class runs on" count={`${providers.length} provider${providers.length === 1 ? "" : "s"}`}>
      <div className="set-card" style={{ overflow: "hidden" }}>
        <div className="table-scroll"><table className="set-matrix">
          <thead><tr><th>Provider</th>
            {providers[0].tiers.map((t) => <th key={t.class}>{t.label}</th>)}
            <th>Pays</th><th>Last answered</th></tr></thead>
          <tbody>{providers.map((p) => {
            const pays = liveProjects(data.creds, p.provider);
            return <tr key={p.provider}>
              <td><div className="who"><b>{p.label}</b><span><StatusBadge p={p} />
                {p.is_default && <span className="chip accent">Default</span>}
                {p.local && <span className="chip">Your network</span>}</span></div></td>
              {p.tiers.map((t) => <td key={t.class}>
                {t.model ? <><span className="m">{t.model}</span>
                  <span className="p">{t.price ? `${perM(t.price.input)} / ${perM(t.price.output)}`
                    : t.states_cost ? "stated per call" : <span className="crit-ink">no price</span>}</span></>
                  : <button type="button" className="unset link"
                      onClick={() => go("providers", { provider: p.provider })}>Not set</button>}
              </td>)}
              <td className="nowrap">{pays.length ? `${pays.length} project${pays.length === 1 ? "" : "s"}`
                : <span className="muted">None</span>}</td>
              <td><EvidenceSeal evidence={p.evidence} /></td>
            </tr>;
          })}</tbody>
        </table></div>
        <div className="set-matrix-foot">USD per million tokens, input / output. A project that pays nobody yet runs on {defaultLabel}, the default.</div>
      </div>
    </Group> : <EmptyState icon="plug" title="No provider is connected yet."
      foot={<button type="button" className="primary" onClick={() => go("providers", { connect: "1" })}>
        Connect a provider</button>}>
      Projects need one to run. Paste a key and yaaf tests it on the spot.
    </EmptyState>}

    <Group title="Connections">
      <div className="set-tiles">
        <button type="button" className="set-tile" onClick={() => go("founder")}>
          <span className="k">Founder <Icon name="arrow" size={14} /></span>
          <span className="v">{founder?.configured ? founder.model : "Not set up"}</span>
          <span className="sub">{founder?.configured
            ? <>{founder.source?.name ? <>On <b>{founder.source.name}</b></> : "On a key of its own"}
                <EvidenceSeal evidence={founder.evidence} /></>
            : "Drafts a new project with you. It needs one of your keys."}</span>
        </button>
        <button type="button" className="set-tile" onClick={() => go("slack")}>
          <span className="k">Slack <Icon name="arrow" size={14} /></span>
          <span className="v">{!slack?.bot_token_configured ? "Off"
            : slack.app_token_configured && slack.chairman_slack_user ? "Mirrors, and you can chat in"
            : "Mirrors to Slack"}</span>
          <span className="sub">{slack?.bot_token_configured
            ? <>{!(slack.app_token_configured && slack.chairman_slack_user) && "Chat from Slack needs an app token. "}
                <TestSeal test={slack.last_test} /></>
            : "One workspace for every project, its rooms made for you."}</span>
        </button>
        <button type="button" className="set-tile" onClick={() => go("search")}>
          <span className="k">Web search <Icon name="arrow" size={14} /></span>
          <span className="v">{search?.configured ? `${search.configured.label} · $${search.configured.price_usd} a query` : "Off"}</span>
          <span className="sub">{(() => {
            const leaning = providers.filter((p) => p.search === "platform").map((p) => p.label);
            const without = providers.filter((p) => p.search === "none").map((p) => p.label);
            if (search?.configured) {
              return <>{leaning.length ? `Searches for ${names(leaning)}, which can’t on their own.`
                : "Every connected provider searches on its own."} <TestSeal test={search.last_test} /></>;
            }
            return without.length ? `${names(without)} can’t search, so roles that browse are refused there.`
              : "Every connected provider searches on its own.";
          })()}</span>
        </button>
      </div>
    </Group>
    {providers.some((p) => providerState(p) === "untested") && <p className="help" style={{ marginTop: 12 }}>
      Green appears once a key has answered: a Test, its model list, a graded check or a run.</p>}
  </>;
}
