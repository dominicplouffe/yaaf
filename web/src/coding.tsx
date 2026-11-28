// A Code Factory's coding key: the credential its repo seats code with.
//
// A factory spends on two different things. The lead plans, staffs, reads the
// room and answers the chairman — a great many small runs. The seats that hold
// the repo write the code — fewer runs, each worth the best model there is, on
// the provider's own coding CLI. One credential for both makes the coding
// model the price of every message, so a factory names two: the billing
// credential for the talking and this one for the coding. The server refuses
// to start a factory without it (yaaf.core.project_credentials.coding_refusal)
// and its refusal is shown here in its own words.

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { CodingView, Dashboard, get, put } from "./api";
import type { Credential } from "./llmCredentials";
import { DEFAULT_PROVIDER, useProviders } from "./providers";
import { Why } from "./settingsCopy";

/** A select of the credentials a factory could code with: the providers that
    ship a coding CLI of their own, which are the ones that also sell a login
    for it. The rest are not listed at all — there is nothing to fix on them. */
export function CodingPicker({ value, onChange, disabled, id }: {
  value: string; onChange: (id: string) => void; disabled?: boolean; id?: string;
}) {
  const registry = useProviders();
  const [rows, setRows] = useState<Credential[] | null>(null);
  useEffect(() => {
    get<Credential[]>("/admin/settings/llm-credentials").then(setRows).catch(() => setRows([]));
  }, []);
  const coders = registry.filter((p) => p.kinds.some((k) => k.mode === "subscription"));
  const labelOf = (name: string) => registry.find((p) => p.name === name)?.label ?? name;
  const offered = (rows ?? []).filter((r) => !r.hidden_at
    && coders.some((p) => p.name === (r.provider ?? DEFAULT_PROVIDER)));
  return <>
    <select id={id} value={value} disabled={disabled || rows === null}
      onChange={(e) => onChange(e.target.value)}>
      <option value="">{rows === null ? "Loading credentials…" : "No coding key"}</option>
      {offered.map((r) => <option key={r.id} value={r.id}>
        {r.name} — {labelOf(r.provider ?? DEFAULT_PROVIDER)}{r.bills ? ` · ${r.bills}` : ""}
      </option>)}
    </select>
    {rows !== null && offered.length === 0 && <span className="help serious">No{" "}
      {coders.map((p) => p.label).join(", ").replace(/, ([^,]*)$/, " or $1")} credential is on this
      install, and a Code Factory cannot start without one.{" "}
      <Link to="/settings?section=providers&connect=1" target="_blank" rel="noreferrer">
        Connect a provider ↗</Link></span>}
  </>;
}

/** The coding key, under the project's billing credential. Nothing on any
    project that is not a Code Factory. */
export function CodingCard({ projectId, dash, disabled, onChanged, bare }: {
  projectId: string; dash: Dashboard; disabled?: boolean; onChanged: () => void;
  /** Inside a drawer that already says what this is: no heading of its own. */
  bare?: boolean;
}) {
  const [view, setView] = useState<CodingView | null>(dash.coding ?? null);
  const [pick, setPick] = useState(dash.coding?.credential_id ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  useEffect(() => { setView(dash.coding ?? null); },
    [dash.coding?.credential_id, dash.coding?.refusal, dash.coding?.seats.join(",")]);
  useEffect(() => { setPick(dash.coding?.credential_id ?? ""); }, [dash.coding?.credential_id]);
  if (!view?.applies) return null;
  const current = view.credential_id ?? "";

  async function save(credentialId: string | null) {
    setBusy(true); setError(""); setNote("");
    try {
      const said = await put<CodingView>(`/admin/projects/${projectId}/coding-credential`,
        { credential_id: credentialId });
      setView(said);
      setPick(said.credential_id ?? "");
      setNote(said.credential_id
        ? `The repo seats code with ${said.name}${said.harness ? `, on ${said.harness}` : ""}.`
        : "No coding key: this Code Factory cannot be started.");
      onChanged();
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }

  return <div className="fallback" aria-label="Coding key">
    {!bare && <>
    <h3>Coding key <span className="help">required to start</span></h3>
    <p className="help">The seats that hold the repo code with this. Everyone else — the lead,
      the boardroom, seats that never touch the repo — runs on the billing credential above.</p>
    </>}
    <Why>An Anthropic, OpenAI or Google credential, driven by that provider&apos;s own coding
      tool. A coding seat whose role names no model runs the provider&apos;s Frontier model; a
      seat or a role that names one keeps it. It may be a second credential for the provider
      the billing one already pays.</Why>
    {view.refusal && <div className="strip serious" role="status">
      <div className="grow"><b>This project will not start.</b>
        <div className="sb">{view.refusal}</div></div>
    </div>}
    <div className="fallback-pick">
      <label className="field">Code with
        <CodingPicker value={pick} disabled={disabled || busy}
          onChange={(id) => { setPick(id); setError(""); setNote(""); }} /></label>
      <button type="button" className="primary" disabled={disabled || busy || pick === current}
        onClick={() => save(pick || null)}>{busy ? "Saving…" : pick || !current ? "Save coding key" : "Remove coding key"}</button>
    </div>
    {view.credential_id && <p className="help">{view.seats.length
      ? `Coding with it: ${view.seats.join(", ")}.`
      : "No seat on this project holds the repo yet, so nothing codes with it."}</p>}
    {error && <p className="error" role="alert"><b>Not set.</b> {error}</p>}
    {note && <p className="help ok-line" role="status">✓ {note}</p>}
  </div>;
}
