// Settings → Web search: the install's own search backend, for roles on
// providers that cannot search on their own.
//
// The section was already short. What it lacked: which providers lean on it,
// whether the key had ever answered, and the consequence of removing it before
// the press — the seats it stranded were listed only after it was gone, from a
// browser confirm(). Removal now asks the server first (a dry run) and names
// them in the confirmation.

import { useCallback, useEffect, useState } from "react";
import { del, get, post, put, SearchSettings } from "../api";
import { Readiness } from "../readiness";
import { ConfirmModal, SecretInput } from "../ui";
import { Why } from "../settingsCopy";
import { names } from "./data";
import { Flash, Group, SaveBar, SectionHead, TestSeal } from "./kit";

const URL = "/admin/settings/web-search";

export function WebSearchSection({ readiness, onChanged }: { readiness: Readiness | null; onChanged: () => void }) {
  const [data, setData] = useState<SearchSettings | null>(null);
  const [backend, setBackend] = useState("");
  const [key, setKey] = useState("");
  const [replacing, setReplacing] = useState(false);
  const [baseUrl, setBaseUrl] = useState("");
  const [price, setPrice] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [removing, setRemoving] = useState<{ unrunnable: string[] | null } | null>(null);

  const adopt = useCallback((d: SearchSettings) => {
    setData(d);
    const chosen = d.configured?.backend ?? d.backends[0]?.name ?? "";
    setBackend(chosen);
    setBaseUrl(d.configured?.base_url ?? "");
    setPrice(String(d.configured?.price_usd ?? d.backends.find((b) => b.name === chosen)?.suggested_usd ?? ""));
    setKey(""); setReplacing(false);
  }, []);
  useEffect(() => {
    get<SearchSettings>(URL).then(adopt).catch((e) => setNote({ ok: false, text: (e as Error).message }));
  }, [adopt]);

  async function run(fn: () => Promise<string>) {
    setBusy(true); setNote(null);
    try { setNote({ ok: true, text: await fn() }); onChanged(); }
    catch (e) { setNote({ ok: false, text: (e as Error).message }); } finally { setBusy(false); }
  }

  if (!data) return <div className="card muted">Loading…</div>;
  const chosen = data.backends.find((b) => b.name === backend);
  const current = data.configured;
  const sameBackend = current?.backend === backend;
  const keySaved = !!current?.has_key && sameBackend;
  const leaning = (readiness?.providers ?? []).filter((p) => p.search === "platform").map((p) => p.label);
  const without = (readiness?.providers ?? []).filter((p) => p.search === "none").map((p) => p.label);
  const dirty = !current || !sameBackend || String(current.price_usd) !== price
    || (current.base_url ?? "") !== baseUrl || !!key.trim();
  const ready = !!backend && (!chosen?.key || !!key.trim() || keySaved);
  const test = data.last_test;

  async function askToRemove() {
    setRemoving({ unrunnable: null });
    try {
      const said = await del<{ unrunnable: string[] }>(`${URL}?dry_run=true`);
      setRemoving({ unrunnable: said.unrunnable ?? [] });
    } catch (e) {
      setRemoving(null);
      setNote({ ok: false, text: (e as Error).message });
    }
  }

  return <>
    <SectionHead title="Web search"
      lede="For roles on providers that can’t search on their own. Each query is billed into the run at the price below."
      actions={current && <><TestSeal test={test} />
        <button type="button" disabled={busy} onClick={() => run(async () => {
          const said = await post<{ ok: boolean; detail: string }>(`${URL}/test`);
          adopt(await get<SearchSettings>(URL));
          if (!said.ok) throw new Error(said.detail);
          return said.detail;
        })}>Test</button></>}>
      {current ? <>
        <span className={`badge ${!test ? "hollow" : test.ok ? "good" : "critical critical-fill"}`}>
          {!test ? "Untested" : test.ok ? "Ready" : "Test failed"}</span>
        <span><b>{current.label}</b> · ${current.price_usd} a query</span>
        <span>{leaning.length ? `Searches for ${names(leaning)}.` : "Every connected provider searches on its own."}</span>
      </> : <>
        <span className="badge plain">Off</span>
        <span>{without.length ? `${names(without)} can’t search, so roles that browse are refused there.`
          : "Every connected provider searches on its own, so nothing needs it yet."}</span>
      </>}
    </SectionHead>
    <Flash note={note} />
    <Why>A role granted the web is promised search. Claude, GPT and Gemini search on their own APIs;
      most other providers, and the OpenCode CLI, do not, and a web role on one of them is refused until
      this install has a search backend. Queries count under the run’s cap, and the key never reaches an
      agent’s shell.</Why>

    <Group title={current ? "Backend" : "Choose a backend"}>
      <div className="set-card">
        <div className="set-row">
          <label className="set-row-lab" htmlFor="ws-backend">Backend</label>
          <div className="set-row-val"><select id="ws-backend" value={backend} disabled={busy} onChange={(e) => {
            setBackend(e.target.value);
            const next = data.backends.find((b) => b.name === e.target.value);
            setPrice(String(next?.suggested_usd ?? "")); setBaseUrl(next?.base_url ?? "");
            setKey(""); setReplacing(false);
          }}>{data.backends.map((b) => <option key={b.name} value={b.name}>{b.label}</option>)}</select></div>
          <div className="set-row-act">{chosen?.console_url && <a href={chosen.console_url} target="_blank"
            rel="noreferrer" className="small">Get a key ↗</a>}</div>
        </div>
        <div className="set-row">
          <label className="set-row-lab" htmlFor="ws-price">Price per query
            <span className="set-row-hint">USD, billed into the run</span></label>
          <div className="set-row-val"><input id="ws-price" type="number" min={0} step="any" value={price}
            disabled={busy} onChange={(e) => setPrice(e.target.value)} style={{ maxWidth: 160 }} />
            {chosen?.suggested_usd !== undefined && <span className="help" style={{ display: "block", marginTop: 4 }}>
              {chosen.label}’s entry rate is ${chosen.suggested_usd}. Set what your plan charges.</span>}</div>
          <div className="set-row-act" />
        </div>
        {chosen && !chosen.base_url && <div className="set-row">
          <label className="set-row-lab" htmlFor="ws-url">Address</label>
          <div className="set-row-val"><input id="ws-url" value={baseUrl} disabled={busy}
            placeholder="http://searxng:8080" onChange={(e) => setBaseUrl(e.target.value)} /></div>
          <div className="set-row-act" />
        </div>}
        {chosen?.key && <div className={`set-row${replacing || !keySaved ? " editing" : ""}`}>
          <label className="set-row-lab" htmlFor="ws-key">API key</label>
          <div className="set-row-val">{keySaved && !replacing
            ? <span className="set-row-state">Saved · encrypted, never shown again</span>
            : <SecretInput value={key} onChange={setKey} disabled={busy} />}</div>
          <div className="set-row-act">{keySaved && (replacing
            ? <button type="button" className="small" onClick={() => { setKey(""); setReplacing(false); }}>Cancel</button>
            : <button type="button" className="small" onClick={() => setReplacing(true)}>Replace</button>)}</div>
        </div>}
      </div>
    </Group>

    <SaveBar dirty={dirty && ready} busy={busy} label={current ? "Save changes" : "Turn on web search"}
      onSave={() => run(async () => {
        adopt(await put<SearchSettings>(URL, {
          backend, key: key.trim() || null, base_url: baseUrl.trim() || null,
          price_usd: price === "" ? null : Number(price) }));
        return "Saved. From the next run, roles that browse are honoured on every provider.";
      })}
      danger={current && <button type="button" className="quiet-danger" disabled={busy}
        onClick={askToRemove}>Remove…</button>} />

    {removing && <ConfirmModal title="Remove the search backend?" confirmLabel="Remove" danger
      busy={busy || removing.unrunnable === null} onCancel={() => setRemoving(null)}
      onConfirm={() => run(async () => {
        await del(URL);
        adopt(await get<SearchSettings>(URL));
        setRemoving(null);
        return "Removed.";
      })}>
      {removing.unrunnable === null ? <p role="status">Working out what it would stop…</p>
        : removing.unrunnable.length ? <>
          <p><b>These seats can’t run once it is gone:</b></p>
          <ul>{removing.unrunnable.map((line) => <li key={line}>{line}</li>)}</ul>
          <p className="help">Add a backend again to bring them back.</p></>
        : <p>No seat depends on it right now. Roles that browse on {without.length || leaning.length
            ? names([...leaning, ...without]) : "providers that can’t search"} will be refused from their next run.</p>}
    </ConfirmModal>}
  </>;
}
