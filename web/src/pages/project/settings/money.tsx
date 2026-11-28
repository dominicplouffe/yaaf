// Settings → Money: what pays for the agents' model calls, and how much the
// project may spend. It opens on the month so far, then who pays — one row
// each, with its state and the one thing to do about it — then the budget,
// edited here through the same reasoned change as Rules in force.

import { ReactNode, useState } from "react";
import { Link } from "react-router-dom";
import { Dashboard, patch, post } from "../../../api";
import { CodingCard } from "../../../coding";
import { FallbackCard } from "../../../fallback";
import { CredentialPicker, PlanMeter, ProviderCredentials } from "../../../llmCredentials";
import { useProviders } from "../../../providers";
import { budgetOf, Drawer, fmtUnit } from "../../../ui";
import { DangerZone, Group, SectionHead } from "../../../settings/kit";
import { dormantReason, MONEY_ELSEWHERE, RuleRow, Rules } from "./rules";

type Panel = "billing" | "coding" | "fallback" | "providers" | null;

export function MoneySection({ projectId, dash, rules, reload, onSaved, onError, openRules }: {
  projectId: string;
  dash: Dashboard;
  rules: Rules;
  reload: () => void;
  onSaved: (note: string) => void;
  onError: (message: string) => void;
  /** Rules in force, on its Money tab. */
  openRules: () => void;
}) {
  const registry = useProviders();
  const [panel, setPanel] = useState<Panel>(null);
  const [pick, setPick] = useState(dash.billing_secret_id ?? "");
  const [busy, setBusy] = useState(false);
  const [test, setTest] = useState<{ ok: boolean; text: string } | null>(null);

  const billing = (dash.credentials ?? []).find((c) => c.billing) ?? null;
  const seller = registry.find((p) => p.name === billing?.provider);
  const ready = dash.billing_secret_id !== null;
  const subscription = dash.billing_mode === "subscription";
  const coding = dash.coding;
  const codingMissing = !!coding?.applies && !coding.credential_id;
  const others = (dash.credentials ?? []).filter((c) => !c.billing && c.credential_id !== dash.fallback?.credential_id);

  async function saveBilling() {
    setBusy(true);
    try {
      await patch(`/admin/projects/${projectId}`, { billing_secret_id: pick });
      setPanel(null);
      setTest(null);
      onSaved("Billing credential updated. New runs pay with it.");
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function testBilling() {
    setBusy(true);
    setTest(null);
    try {
      const check = await post<{ ok: boolean; detail: string }>(`/admin/projects/${projectId}/billing_check`, {});
      setTest({ ok: check.ok, text: check.detail });
    } catch (e) {
      setTest({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  const money = schemaMoney(rules, dash);
  return (
    <>
      <SectionHead title="Money" lede="What pays for the agents' model calls, and how much this project may spend.">
        {!ready ? <span className="badge critical">Can&apos;t run: no billing credential</span>
          : codingMissing ? <span className="badge critical">Can&apos;t start: no coding key</span>
          : dash.fallback?.active ? <span className="badge warn">Running on the fallback</span>
          : <span className="badge good">Paid for</span>}
        <span className="help">{subscription ? "A subscription: budgets count tokens" : "An API key: budgets count dollars"}</span>
      </SectionHead>

      <Group title="This month">
        <MonthMeter dash={dash} openRules={openRules} />
        {subscription && ready && (
          <PlanMeter credentialId={dash.billing_secret_id} mode={dash.billing_mode} meters={!!seller?.plan_meter}
            title={`Plan usage for ${dash.billing_secret_name ?? "this credential"}`} />
        )}
      </Group>

      <Group title="Who pays">
        <div className="set-card">
          <Row label="Billing credential" hint="Every seat that names a model class"
            tone={ready ? undefined : "critical"}
            value={ready
              ? <><b>{dash.billing_secret_name}</b>
                  <span className="help"> · {billing?.label ?? "provider"} {subscription
                    ? "subscription, plan rate limits" : "API key, billed per call"}</span>
                  {test && <span className={`pset-line ${test.ok ? "good-ink" : "crit-ink"}`}>{test.text}</span>}</>
              : <><b className="crit-ink">None.</b> Every real run fails until one is assigned.</>}
            actions={<>
              {ready && <button type="button" className="small" disabled={busy} onClick={testBilling}>
                {busy && !panel ? "Testing…" : "Test"}</button>}
              <button type="button" className={`small${ready ? "" : " primary"}`}
                onClick={() => { setPick(dash.billing_secret_id ?? ""); setPanel("billing"); }}>
                {ready ? "Change" : "Choose a credential"}</button>
            </>} />
          {coding?.applies && (
            <Row label="Coding key" hint="Required for a Code Factory" tone={codingMissing ? "critical" : undefined}
              value={codingMissing
                ? <><b className="crit-ink">Not set.</b> {coding.refusal ?? "The seats that hold the repo code with this."}</>
                : <><b>{coding.name}</b>{coding.harness && <span className="help"> · on {coding.harness}</span>}
                    <span className="pset-line help">{coding.seats.length
                      ? `Coding with it: ${coding.seats.join(", ")}.` : "No seat holds the repo yet."}</span></>}
              actions={<button type="button" className={`small${codingMissing ? " primary" : ""}`}
                onClick={() => setPanel("coding")}>{codingMissing ? "Choose a coding key" : "Change"}</button>} />
          )}
          <Row label="Fallback" hint="Optional"
            tone={dash.fallback?.active ? "warn" : undefined}
            value={dash.fallback?.credential_id
              ? <><b>{dash.fallback.name}</b>{dash.fallback.active
                  ? <span className="warn-ink"> · class seats are running on it now</span>
                  : <span className="help"> · class seats move here if {billing?.label ?? "the billing provider"} keeps failing</span>}</>
              : <>None. If {billing?.label ?? "the billing provider"} keeps failing, runs fail with it.</>}
            actions={<button type="button" className="small" disabled={!ready} onClick={() => setPanel("fallback")}>
              {dash.fallback?.credential_id ? "Change" : "Add a fallback"}</button>} />
          <Row label="Other providers" hint="Seats pinned to another provider's model"
            value={others.length
              ? others.map((c) => `${c.label} · ${c.name}`).join(", ")
              : <>None. {dash.install_payer
                  ? <>Anything this project doesn&apos;t pay for runs on the workspace&apos;s {dash.install_payer.label} key.</>
                  : "Every seat runs on the billing provider."}</>}
            actions={<button type="button" className="small" onClick={() => setPanel("providers")}>Manage</button>} />
        </div>
      </Group>

      <Group title="Budget" aside={<span className="help">changing one takes a written reason, shown to the CEO</span>}>
        {money.live.length === 0 ? <p className="help">Loading the budget…</p> : (
          <div className="set-card">
            {money.live.map(({ field, tag }) => <RuleRow key={field.key} field={field} rules={rules} dormant={null} tag={tag} />)}
          </div>
        )}
        {money.asleep.length > 0 && (
          <details className="pset-asleep">
            <summary>Not enforced under {subscription ? "subscription" : "API-key"} billing: {money.asleep.length} rule{money.asleep.length === 1 ? "" : "s"}</summary>
            <div className="set-card">
              {money.asleep.map((f) => <RuleRow key={f.key} field={f} rules={rules} dormant={dormantReason(f.key, dash)} />)}
            </div>
          </details>
        )}
        <p className="help" style={{ marginTop: 10 }}>
          <button type="button" className="linklike" onClick={openRules}>Every money rule</button> is under Rules in force.
        </p>
      </Group>

      {ready && <DetachRow projectId={projectId} name={dash.billing_secret_name ?? "the billing credential"}
        onSaved={onSaved} onError={onError} />}

      {panel === "billing" && (
        <Drawer title="Billing credential" sub="Every seat that names a model class pays with this."
          onClose={() => setPanel(null)}
          footer={<>
            <span className="grow help">Applies to new runs.</span>
            <button type="button" onClick={() => setPanel(null)}>Cancel</button>
            <button type="button" className="primary" disabled={busy || !pick || pick === (dash.billing_secret_id ?? "")}
              onClick={saveBilling}>{busy ? "Saving…" : "Save"}</button>
          </>}>
          <CredentialPicker value={pick} onChange={(id) => setPick(id)} disabled={busy} />
          <p className="help">The provider it pays becomes the project&apos;s own: every seat whose role names a
            class (Frontier, Balanced, Fast) runs on that provider&apos;s model. A subscription makes budgets
            count tokens; an API key, dollars.</p>
        </Drawer>
      )}
      {panel === "coding" && (
        <Drawer title="Coding key" sub="The seats that hold the repo code with this, through the provider's own coding tool."
          onClose={() => setPanel(null)}>
          <CodingCard projectId={projectId} dash={dash} onChanged={reload} bare />
        </Drawer>
      )}
      {panel === "fallback" && (
        <Drawer title="Fallback credential"
          sub={`If ${billing?.label ?? "the billing provider"} keeps failing, class seats move here until it recovers.`}
          onClose={() => setPanel(null)}>
          <FallbackCard projectId={projectId} dash={dash} onChanged={reload} bare />
        </Drawer>
      )}
      {panel === "providers" && (
        <Drawer title="Other providers"
          sub="Seats on another provider's model pay with that provider's credential, against this project's same budget."
          onClose={() => { setPanel(null); reload(); }}>
          <ProviderCredentials projectId={projectId} fallbackId={dash.fallback?.credential_id ?? null} bare />
        </Drawer>
      )}
    </>
  );
}

/** The budget rules Money shows: the Money tab's, then the two money rules
    filed elsewhere, each tagged with where it lives. */
function schemaMoney(rules: Rules, dash: Dashboard) {
  const own = rules.schema.filter((f) => f.theme === "Money");
  const borrowed = MONEY_ELSEWHERE.map((m) => rules.field(m.key)).filter((f) => !!f);
  const live = [
    ...own.filter((f) => !dormantReason(f.key, dash)).map((field) => ({ field, tag: undefined as string | undefined })),
    ...borrowed.map((field) => ({ field: field!, tag: field!.theme })),
  ];
  const asleep = own.filter((f) => dormantReason(f.key, dash));
  return { live, asleep };
}

function Row({ label, hint, value, actions, tone }: {
  label: string; hint?: string; value: ReactNode; actions?: ReactNode; tone?: "critical" | "warn";
}) {
  return (
    <div className={`set-row${tone ? ` pset-${tone}` : ""}`}>
      <span className="set-row-lab">{label}{hint && <span className="set-row-hint">{hint}</span>}</span>
      <div className="set-row-val">{value}</div>
      <div className="set-row-act">{actions}</div>
    </div>
  );
}

/** Spent so far this month against the warning line, the budget, and the
    wall where the project pauses itself — one bar, in the billing unit. */
function MonthMeter({ dash, openRules }: { dash: Dashboard; openRules: () => void }) {
  const b = budgetOf(dash);
  const say = (n: number) => b.unit === "usd" ? fmtUnit(n, "usd") : `${fmtUnit(n, "tokens")} tokens`;
  const scale = Math.max(b.wall, b.spent, 1);
  const at = (n: number) => `${Math.min(100, (n / scale) * 100)}%`;
  const over = b.cap > 0 && b.spent >= b.cap;
  const warn = b.cap > 0 && b.frac >= b.warnRatio;
  return (
    <div className="set-card pset-meter">
      <div className="pset-meter-top">
        <span className="big">{say(b.spent)}</span>
        <span className="help">{b.cap > 0 ? `of a ${say(b.cap)} monthly budget` : "spent, with no monthly budget set"}</span>
        <span className="grow" />
        <span className={`badge ${over ? "critical" : warn ? "warn" : "good"}`}>
          {over ? "Over budget" : warn ? "Near the budget" : "On plan"}</span>
      </div>
      {b.cap > 0 && (
        <div className="pset-bar" role="img"
          aria-label={`${say(b.spent)} spent; warns at ${say(b.cap * b.warnRatio)}, budget ${say(b.cap)}, pauses at ${say(b.wall)}`}>
          <i className={over ? "over" : warn ? "warn" : ""} style={{ width: at(b.spent) }} />
          <span className="mk" style={{ left: at(b.cap * b.warnRatio) }}><em>Warns</em></span>
          <span className="mk" style={{ left: at(b.cap) }}><em className="up">Budget</em></span>
          <span className="mk end" style={{ left: at(b.wall) }}><em>Pauses at {say(b.wall)}</em></span>
        </div>
      )}
      <div className="pset-meter-foot">
        <span>Last 24 hours: <b>{b.unit === "usd" ? fmtUnit(dash.spend_24h, "usd") : `${fmtUnit(dash.spend_24h, "tokens")} tokens`}</b></span>
        <Link to={`/projects/${dash.id}/details/activity/runs`}>Every run and what it cost</Link>
        <button type="button" className="linklike" onClick={openRules}>Change the budget</button>
      </div>
    </div>
  );
}

/** Detaching the billing credential frees it to be deleted, and stops the
    project until another is assigned. It used to be done by clearing a
    dropdown; it is a decision of its own now, said before the click. */
function DetachRow({ projectId, name, onSaved, onError }: {
  projectId: string; name: string; onSaved: (n: string) => void; onError: (m: string) => void;
}) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  return (
    <DangerZone>
      <div className="set-row">
        <span className="set-row-lab">Detach the billing credential<span className="set-row-hint">Frees it to be deleted</span></span>
        <div className="set-row-val">Agents can&apos;t run until another is assigned. The project keeps its rules and history.</div>
        <div className="set-row-act">
          {asking ? <>
            <button type="button" className="small" disabled={busy} onClick={() => setAsking(false)}>Cancel</button>
            <button type="button" className="small danger" disabled={busy} onClick={async () => {
              setBusy(true);
              try {
                await patch(`/admin/projects/${projectId}`, { billing_secret_id: "" });
                onSaved("Credential detached. Agents can't run until one is assigned again.");
              } catch (e) {
                onError((e as Error).message);
              } finally {
                setBusy(false);
                setAsking(false);
              }
            }}>Detach {name}</button>
          </> : <button type="button" className="small danger" onClick={() => setAsking(true)}>Detach…</button>}
        </div>
      </div>
    </DangerZone>
  );
}
