// Failover: where a project's class seats go while its billing provider is
// down, and the banner that says they are there.
//
// The server keeps a circuit breaker on the billing credential: enough
// transient failures in a row open it, and while it is open class seats run on
// the fallback instead (yaaf.core.breakers). A seat pinned to a model stays
// put — it was put on that provider on purpose — and waits. The fallback is
// refused at the door unless every class seat could actually run there, so the
// time to find out it could not is now, not mid-outage; that refusal is shown
// here in the server's own words.

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { BreakerState, Dashboard, FallbackView, get, put } from "./api";
import type { Credential } from "./llmCredentials";
import { DEFAULT_PROVIDER, useProviders } from "./providers";
import { FIX_LINK } from "./readiness";
import { fmtAgo, fmtSoon } from "./ui";

export function breakerSentence(b: BreakerState | null): string | null {
  if (!b) return null;
  const why = b.last_error ? ` — last error: ${b.last_error}` : "";
  if (b.status === "open") {
    return `The billing credential's breaker is open after ${b.failures} failure${b.failures === 1 ? "" : "s"}`
      + `${b.opened_at ? `, since ${fmtAgo(b.opened_at)}` : ""}; it tries the billing provider again`
      + `${b.open_until ? ` ${fmtSoon(b.open_until)}` : " shortly"}${why}.`;
  }
  if (b.status === "half_open") {
    return `The billing provider is being tried again after an outage; one good run closes the breaker${why}.`;
  }
  return b.failures
    ? `The billing credential has failed ${b.failures} time${b.failures === 1 ? "" : "s"} in a row recently${why}.`
    : null;
}

/** "Mistral" rather than "Mistral (Mistral)" for a key named after its provider. */
const named = (name: string | null, label: string | null | undefined) =>
  !label || (name ?? "").toLowerCase() === label.toLowerCase() ? (name ?? label ?? "") : `${name} (${label})`;

/** Said on the project's own pages while class seats are on the fallback. */
export function FailoverBanner({ dash, settingsLink }: { dash: Dashboard; settingsLink?: string }) {
  const f = dash.fallback;
  const registry = useProviders();
  if (!f?.active) return null;
  const billing = (dash.credentials ?? []).find((c) => c.billing);
  const label = registry.find((p) => p.name === f.provider)?.label ?? f.provider;
  return <div className="strip serious" role="status">
    <div className="grow">
      <b>{billing?.label ?? "The billing provider"} is failing; class seats are running on {named(f.name, label)} until
        it recovers.</b>
      <div className="sb">{breakerSentence(f.breaker) ?? "The breaker moves them back on its own."}{" "}
        Seats pinned to a model wait rather than move.</div>
    </div>
    {settingsLink && <Link to={settingsLink}>failover settings →</Link>}
  </div>;
}

/** A select of the credentials a project could fail over to: another
    provider's, metered the same way. Ones metered differently are shown but
    not offered, so a key that is "missing" says why. A value that stops being
    offerable — the billing credential moved to its provider — is cleared. */
export function FallbackPicker({ value, onChange, billingProvider, billingMode, disabled, id, emptyLabel = "No fallback" }: {
  value: string; onChange: (id: string) => void;
  billingProvider: string | null; billingMode: string;
  disabled?: boolean; id?: string; emptyLabel?: string;
}) {
  const registry = useProviders();
  const [rows, setRows] = useState<Credential[] | null>(null);
  useEffect(() => {
    get<Credential[]>("/admin/settings/llm-credentials").then(setRows).catch(() => setRows([]));
  }, []);
  const labelOf = (name: string) => registry.find((p) => p.name === name)?.label ?? name;
  const others = (rows ?? []).filter((r) => (r.provider ?? DEFAULT_PROVIDER) !== billingProvider);
  const offerable = (r: Credential) => r.mode === billingMode;
  useEffect(() => {
    if (!rows || !value) return;
    const row = others.find((r) => r.id === value);
    if (!row || !offerable(row)) onChange("");
  }, [rows, value, billingProvider, billingMode]);
  return <>
    <select id={id} value={value} disabled={disabled || rows === null}
      onChange={(e) => onChange(e.target.value)}>
      <option value="">{rows === null ? "Loading credentials…" : emptyLabel}</option>
      {others.map((r) => <option key={r.id} value={r.id} disabled={!offerable(r)}>
        {r.name} — {labelOf(r.provider ?? DEFAULT_PROVIDER)}
        {offerable(r) ? "" : ` · metered in ${r.mode === "subscription" ? "tokens" : "dollars"}, unlike this project`}
      </option>)}
    </select>
    {rows !== null && others.length === 0 && <span className="help">No credential of another provider
      is on this install. <Link to="/settings?section=providers&connect=1" target="_blank" rel="noreferrer">
        Connect a provider ↗</Link> to have somewhere to fail over to.</span>}
  </>;
}

/** The fallback credential picker, under the project's billing credential. */
export function FallbackCard({ projectId, dash, disabled, onChanged }: {
  projectId: string; dash: Dashboard; disabled?: boolean; onChanged: () => void;
}) {
  const registry = useProviders();
  const [view, setView] = useState<FallbackView | null>(dash.fallback ?? null);
  const [pick, setPick] = useState(dash.fallback?.credential_id ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  useEffect(() => {
    setView(dash.fallback ?? null);
  }, [dash.fallback?.credential_id, dash.fallback?.active, dash.fallback?.breaker?.status]);
  useEffect(() => { setPick(dash.fallback?.credential_id ?? ""); }, [dash.fallback?.credential_id]);

  const billing = (dash.credentials ?? []).find((c) => c.billing) ?? null;
  const labelOf = (name: string | null | undefined) =>
    registry.find((p) => p.name === name)?.label ?? name ?? "";
  const billingLabel = billing?.label ?? "the billing provider";
  const current = view?.credential_id ?? "";
  // The provider of the credential just refused, so the refusal can point at
  // the card where its prices and lineup are fixed.
  const [pickedProvider, setPickedProvider] = useState<string | null>(null);
  useEffect(() => {
    if (!pick) { setPickedProvider(null); return; }
    get<Credential[]>("/admin/settings/llm-credentials")
      .then((rows) => setPickedProvider(rows.find((r) => r.id === pick)?.provider ?? null)).catch(() => {});
  }, [pick]);

  async function save(credentialId: string | null) {
    setBusy(true); setError(""); setNote("");
    try {
      const said = await put<FallbackView>(`/admin/projects/${projectId}/fallback-credential`,
        { credential_id: credentialId });
      setView(said);
      setPick(said.credential_id ?? "");
      setNote(said.credential_id ? `If ${billingLabel} keeps failing, class seats move to ${said.name}.`
        : "No fallback: if the billing provider fails, runs fail with it.");
      onChanged();
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }

  const breaker = breakerSentence(view?.breaker ?? null);
  return <div className="fallback" aria-label="Fallback credential">
    <h3>Fallback credential <span className="help">optional</span></h3>
    <p className="help">If {billingLabel} keeps failing, class seats move here until it recovers;
      seats pinned to a model wait. It must be another provider&apos;s credential, metered the same
      way as the billing one, with a model for every class this project&apos;s seats name.</p>
    {view?.active && <div className="strip serious" role="status">
      <div className="grow"><b>Failover is on: class seats are running on {named(view.name, labelOf(view.provider))}.</b>
        <div className="sb">{breaker ?? "The breaker moves them back once the billing provider answers again."}</div></div>
    </div>}
    {!view?.active && breaker && <p className="breaker">{breaker}</p>}
    {!billing ? <p className="help">Attach a billing credential first — a fallback is what the
      class seats move to when it fails.</p> : <>
      <div className="fallback-pick">
        <label className="field">Move class seats to
          <FallbackPicker value={pick} disabled={disabled || busy}
            billingProvider={billing.provider} billingMode={dash.billing_mode}
            onChange={(id) => { setPick(id); setError(""); setNote(""); }} /></label>
        <button type="button" className="primary" disabled={disabled || busy || pick === current}
          onClick={() => save(pick || null)}>{busy ? "Saving…" : pick || !current ? "Save fallback" : "Remove fallback"}</button>
      </div>
      {error && <p className="error" role="alert"><b>Not set.</b> {error}
        {pickedProvider && <> <Link to={FIX_LINK.provider(pickedProvider)}>Fix it on {labelOf(pickedProvider)}&apos;s card →</Link></>}</p>}
      {note && <p className="help ok-line" role="status">✓ {note}</p>}
    </>}
  </div>;
}
