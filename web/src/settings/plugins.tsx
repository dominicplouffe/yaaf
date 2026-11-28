// Settings → Plugins: the factory plugins this install runs, installed from a
// public GitHub repo by the instance admin (docs/plans/factory-plugins.md §5).
//
// A plugin is a new kind of project. Its code runs sandboxed, but its words
// (the brief and playbook every seat of its projects reads) are trusted the
// way a role in the library is, so the screen says so above the install box.
// A version is offered to new projects only once it is ready: every
// requirement of the conformance report passed. The report is the page's
// content; a failed version stays listed so its reasons can be read.

import { useEffect, useState } from "react";
import { del, get, post, PluginRow, PluginVersionRow } from "../api";
import { Badge } from "../ui/kit";
import { Flash, Group, SectionHead } from "./kit";

const TONE: Record<PluginVersionRow["status"], "good" | "warn" | "critical" | "accent" | undefined> = {
  ready: "good", pending: "warn", failed: "critical", installing: "accent", disabled: undefined,
};

function Report({ plugin, version }: { plugin: string; version: string }) {
  const [row, setRow] = useState<PluginVersionRow | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    get<PluginVersionRow>(`/admin/plugins/${plugin}/versions/${version}`)
      .then(setRow).catch((e) => setError((e as Error).message));
  }, [plugin, version]);
  if (error) return <p className="help">{error}</p>;
  if (!row?.report) return <p className="help">Reading the report…</p>;
  const open = row.report.results.filter((r) => r.status === "fail" || r.status === "pending");
  return (
    <div className="set-plugin-report">
      {row.report.interrupted && <p className="help">{row.report.interrupted}</p>}
      {open.length === 0 ? <p className="help">Every requirement that applies passed.</p> : (
        <ul>
          {open.map((r) => (
            <li key={r.id}>
              <Badge tone={r.status === "fail" ? "critical" : "warn"}>{r.status}</Badge>{" "}
              <b>{r.id}</b> {r.text}
              {r.why.length > 0 && <ul>{r.why.map((w, i) => <li key={i} className="help">{w}</li>)}</ul>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function PluginsSection({ plugins, onChanged }: {
  plugins: PluginRow[] | null;
  onChanged: () => void;
}) {
  const [url, setUrl] = useState("");
  const [ref, setRef] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const installing = (plugins ?? []).some((p) => p.versions.some((v) => v.status === "installing"));
  useEffect(() => {
    if (!installing) return;
    const t = setInterval(onChanged, 3000);
    return () => clearInterval(t);
  }, [installing, onChanged]);

  async function install() {
    setBusy(true);
    setNote(null);
    try {
      const v = await post<PluginVersionRow>("/admin/plugins",
        { url: url.trim(), ...(ref.trim() ? { ref: ref.trim() } : {}) });
      setNote({ ok: true, text: `${v.plugin} at ${v.version.slice(0, 12)}: ${v.status}. The build `
        + "and the conformance suite run now; this page updates when they finish." });
      setUrl("");
      setRef("");
      onChanged();
    } catch (e) {
      setNote({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }
  async function act(v: PluginVersionRow, what: "disable" | "remove") {
    setNote(null);
    try {
      const path = `/admin/plugins/${v.plugin}/versions/${v.version}`;
      if (what === "disable") await post(`${path}/disable`);
      else await del(path);
      onChanged();
    } catch (e) {
      setNote({ ok: false, text: (e as Error).message });
    }
  }

  return (
    <>
      <SectionHead title="Plugins"
        lede="New kinds of project, each from a public GitHub repo. Its code runs sandboxed; its
          guidance reaches every seat of its projects, so install only what you would put in
          the role library." />
      <div className="card" style={{ marginBottom: 12 }}>
        <label className="field" htmlFor="plugin-url">GitHub repository</label>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <input id="plugin-url" value={url} onChange={(e) => setUrl(e.target.value)}
            placeholder="https://github.com/owner/repo" style={{ flex: "1 1 260px" }} />
          <input aria-label="Branch, tag or commit" value={ref} onChange={(e) => setRef(e.target.value)}
            placeholder="tag or branch (optional)" style={{ flex: "0 1 200px" }} />
          <button className="primary" disabled={busy || !url.trim()} onClick={install}>
            {busy ? "Fetching…" : "Install"}</button>
        </div>
        <p className="help">Pinned to the commit the ref names now: a tag moved later never
          changes what is installed.</p>
        <Flash note={note} />
      </div>
      {plugins === null ? <p className="help">Loading…</p> : plugins.length === 0 ? (
        <p className="help">No plugins installed.</p>
      ) : plugins.map((p) => (
        <Group key={p.id} title={p.name} count={p.versions.length}
          aside={<code>plugin:{p.kind_key}</code>}
          chip={p.versions.filter((v) => v.status === "failed").length}>
          {p.repo_url && <p className="help"><a href={p.repo_url} target="_blank" rel="noreferrer">
            {p.repo_url}</a></p>}
          {p.versions.map((v) => {
            const key = `${v.plugin}@${v.version}`;
            return (
              <div key={v.id} className="set-plugin-version">
                <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
                  <Badge tone={TONE[v.status]}>{v.status}</Badge>
                  <code title={v.version}>{v.version.slice(0, 14)}</code>
                  <span className="help">{v.source} · {new Date(v.installed_at).toLocaleString()}
                    {" · "}{v.counts.pass ?? 0} passed
                    {v.counts.fail ? `, ${v.counts.fail} failed` : ""}
                    {v.counts.pending ? `, ${v.counts.pending} pending` : ""}</span>
                  <span className="grow" />
                  <button onClick={() => setOpen(open === key ? null : key)}>
                    {open === key ? "Hide report" : "Report"}</button>
                  {v.status !== "disabled" && v.status !== "installing" && (
                    <button onClick={() => act(v, "disable")}>Disable</button>)}
                  {v.status !== "installing" && (
                    <button onClick={() => act(v, "remove")}>Remove</button>)}
                </div>
                {open === key && <Report plugin={v.plugin} version={v.version} />}
              </div>
            );
          })}
        </Group>
      ))}
    </>
  );
}
