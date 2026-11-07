// App-wide settings: what is true above every project, and your own account.
//
// The question an administrator opens this page with is "can every project
// run, and what needs me?" — so it lands on an Overview that answers it, and
// every rail entry carries its section's state rather than a description of
// it. The sections themselves are built from one kit (settings/kit.tsx): one
// header, one status vocabulary in which green means a key has answered, one
// way to save, and confirmations that state the consequence before the press.
//
// The section lives in the URL, so a link to the credentials list is a link to
// the credentials list; every older link still lands where it did. On a phone
// the rail is the page: an index with a state per row, then the section with a
// way back.
//
// The box's own settings and its People belong to the instance admin, and the
// server refuses them to anyone else. So they are not drawn for anyone else
// either: a rail entry that opens onto a refusal is a door painted on a wall.
// Everybody gets Account, because everybody has a password.

import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import { PasswordCard, SessionsCard, TokensCard } from "../account";
import { ModelPrices } from "../llmCredentials";
import { useMe } from "../me";
import { People } from "../people";
import { ProvidersSection } from "../providerCards";
import { ModelsSection } from "../settingsModels";
import { Icon } from "../ui/Icon";
import { founderOf, useSettingsData } from "../settings/data";
import { FounderSection } from "../settings/founder";
import { SectionHead, useMedia } from "../settings/kit";
import { OverviewSection } from "../settings/overview";
import { SettingsRail } from "../settings/rail";
import { WebSearchSection } from "../settings/search";
import { SlackSection } from "../settings/slack";
import { SpendSection } from "../settings/spend";
import { needsOf, SECTIONS, SectionKey } from "../settings/state";

export default function Settings() {
  const { me } = useMe();
  const admin = Boolean(me?.is_admin);
  const data = useSettingsData(admin);
  const [params, setParams] = useSearchParams();
  const phone = useMedia("(max-width: 860px)");
  const keys = SECTIONS.filter((s) => admin || !s.admin).flatMap((s) => ("key" in s ? [s.key] : []));
  // "credentials" is what Providers was called before; refusal texts that
  // still say "Settings → LLM credentials" land on the same place.
  const raw = params.get("section");
  const asked = (raw === "credentials" ? "providers" : raw) as SectionKey | null;
  const named = asked && keys.includes(asked) ? asked : null;
  // On a phone with no section named, the index is the page.
  const section: SectionKey | null = named ?? (phone ? null : admin ? "overview" : "account");

  const go = useCallback((key: SectionKey, extra?: Record<string, string>) => {
    setParams({ section: key, ...(extra ?? {}) }, { replace: !phone });
    window.scrollTo({ top: 0 });
  }, [setParams, phone]);

  const founder = founderOf(data.settings);
  const loadingPlatform = (section === "founder" || section === "slack") && data.settings === null;

  if (!admin) {
    // One section is not worth a rail: a heading over one entry is a label for nothing.
    return <>
      <h1>Settings</h1>
      <div className="set-lay solo"><div className="set-main"><AccountSection /></div></div>
    </>;
  }

  const index = phone && section === null;
  const urgent = needsOf(data).filter((n) => n.tone !== "neutral").length;
  return (
    <>
      <h1>Settings</h1>
      {data.error && <div className="error" role="alert">{data.error}</div>}
      {index ? <div className="set-index">
        <button type="button" className="set-card set-tile" style={{ marginBottom: 14 }} onClick={() => go("overview")}>
          <span className="v" style={{ display: "flex", gap: 10, alignItems: "center" }}>
            {urgent ? <span className="badge you-count">{urgent}</span> : <span className="good-ink"><Icon name="check" size={16} strokeWidth={2.4} /></span>}
            {urgent ? `${urgent === 1 ? "thing needs" : "things need"} you` : "Nothing needs you"}
            <Icon name="right" size={14} /></span>
        </button>
        <SettingsRail data={data} section={null} email={me?.email ?? ""} onPick={(k) => go(k)} index />
      </div> : <div className="set-lay">
        {!phone && <SettingsRail data={data} section={section} email={me?.email ?? ""} onPick={(k) => go(k)} />}
        <div className="set-main">
          {phone && <button type="button" className="set-back" onClick={() => setParams({})}>
            <Icon name="right" size={14} /> All settings</button>}
          {loadingPlatform ? <div className="card muted">Loading…</div> : <>
            {section === "overview" && <OverviewSection data={data} go={go} />}
            {section === "providers" && <ProvidersSection onChanged={data.reload} />}
            {section === "models" && <ModelsSection onChanged={data.reload} />}
            {section === "prices" && <ModelPrices readiness={data.readiness} onChanged={data.reload} />}
            {section === "spend" && <SpendSection />}
            {section === "search" && <WebSearchSection readiness={data.readiness} onChanged={data.reload} />}
            {section === "founder" && founder && <FounderSection founder={founder} creds={data.creds}
              readiness={data.readiness} onChanged={data.reload} />}
            {section === "slack" && data.settings && <SlackSection settings={data.settings.slack}
              onChanged={data.reload} />}
            {section === "people" && <People users={data.users} loadError={data.usersError}
              reload={data.reloadUsers} />}
            {section === "account" && <AccountSection />}
          </>}
        </div>
      </div>}
    </>
  );
}

function AccountSection() {
  return <>
    <SectionHead title="Account" lede="Your password, the tokens your scripts use, and where you are signed in." />
    <h3 className="set-group-h">Password</h3>
    <PasswordCard />
    <h3 className="set-group-h" style={{ marginTop: "var(--s6)" }}>Personal tokens</h3>
    <TokensCard />
    <h3 className="set-group-h" style={{ marginTop: "var(--s6)" }}>Where you are signed in</h3>
    <SessionsCard />
  </>;
}
