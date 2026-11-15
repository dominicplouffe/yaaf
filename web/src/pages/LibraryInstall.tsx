// One listing from a library source, before anything is installed: what it
// does, what it can touch, what it needs, every word of its prompt, and — for
// a project — the questions it asks. The website's "Open in yaaf" lands here,
// and so does every marketplace row in the dashboard. Only the query's three
// named parameters are read; anything else in the link is ignored.

import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { ApiError, get, post, ProjectBundle } from "../api";
import { canDoWords, ReceiptSummary } from "../library";
import { useMe } from "../me";

interface Preview {
  listing: {
    id: string; kind: "role" | "project"; name: string; summary: string; tier?: string;
    publisher?: string; status: string; status_reason?: string | null; license?: string;
    forked_from?: { id: string; version: number } | null;
    details?: { what_you_get?: string; what_it_costs?: string; what_you_need?: string };
    installs?: number;
  };
  versions: { version: number; published_at: string; changes: string }[];
  version: number;
  sha256: string;
  changes: string;
  roles: { slug: string; name: string; description: string; system_prompt: string;
           tools: string[]; secret_scopes: string[] }[];
  needs: NonNullable<ProjectBundle["needs"]>;
  inputs: NonNullable<ProjectBundle["inputs"]>;
  goal: string | null;
  receipt: Record<string, unknown> | null;
}

const BLANK = /\{\{\s*([A-Za-z0-9_]+)\s*\}\}/g;

function fill<T>(value: T, answers: Record<string, string>): T {
  if (typeof value === "string") {
    return value.replace(BLANK, (whole, id: string) => answers[id] ?? whole) as T;
  }
  if (Array.isArray(value)) return value.map((v) => fill(v, answers)) as T;
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, fill(v, answers)])) as T;
  }
  return value;
}

function Goal({ text }: { text: string }) {
  const parts = text.split(/(\{\{\s*[A-Za-z0-9_]+\s*\}\})/g);
  return (
    <p className="listing-goal">
      {parts.map((part, i) => part.startsWith("{{")
        ? <span key={i} className="blank">{part.replace(/[{}\s]/g, "").replace(/_/g, " ")}</span>
        : <span key={i}>{part}</span>)}
    </p>
  );
}

export default function LibraryInstall() {
  const [params] = useSearchParams();
  const source = params.get("source") ?? "";
  const id = params.get("id") ?? "";
  const version = Number(params.get("v")) || undefined;
  const { me } = useMe();
  const navigate = useNavigate();
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);

  useEffect(() => {
    if (!source || !id) {
      setError("This link names no listing. Open one from the marketplace.");
      return;
    }
    const q = new URLSearchParams({ source, id, ...(version ? { version: String(version) } : {}) });
    get<Preview>(`/admin/library/preview?${q.toString()}`)
      .then(setPreview)
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, [source, id, version]);

  async function installRole() {
    setBusy(true);
    setError(null);
    try {
      const r = await post<{ slug: string; version: number; action: string; renamed_from: string | null }>(
        "/admin/library/install", { source, id, ...(version ? { version } : {}) });
      setDone(r.action === "reused"
        ? `Your library already has exactly this role, as ${r.slug}.`
        : r.renamed_from
          ? `Installed as ${r.slug}. Your own ${r.renamed_from} is untouched.`
          : r.action === "updated" ? `${r.slug} updated to v${r.version}. Teams adopt it on their Team page.`
            : `Installed ${r.slug}. Any project can hire it now.`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function startProject() {
    setBusy(true);
    setError(null);
    try {
      const draft = await post<ProjectBundle>("/admin/library/draft",
        { source, id, ...(version ? { version } : {}) });
      const filled = fill({ ...draft, inputs: [] }, answers);
      navigate("/projects/new/form", { state: { foundingDraft: filled } });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      setBusy(false);
    }
  }

  if (error && !preview) {
    return (
      <>
        <Link className="crumb" to="/roles">&larr; Role library</Link>
        <h1>Marketplace</h1>
        <div className="error">{error}</div>
      </>
    );
  }
  if (!preview) return <p className="help">Checking the listing against its registry…</p>;

  const l = preview.listing;
  const missing = preview.inputs.filter((i) => i.required && !(answers[i.id] ?? "").trim());
  const removed = l.status === "removed";

  return (
    <div className="listing-page">
      <Link className="crumb" to={l.kind === "project" ? "/projects/new" : "/roles?tab=marketplace"}>
        &larr; {l.kind === "project" ? "New project" : "Marketplace"}
      </Link>
      <div className="listing-head">
        <h1>{l.name}</h1>
        <span className="mono help">{l.id} · v{preview.version}</span>
        {l.tier === "official" ? <span className="badge accent">official</span>
          : <span className="badge">community</span>}
        {l.status === "deprecated" && <span className="badge warn">deprecated</span>}
      </div>
      <p className="lede">{l.summary}</p>
      {removed && (
        <div className="error">Removed by the marketplace: {l.status_reason}. It cannot be installed.</div>
      )}
      {l.forked_from && <p className="help">Based on {l.forked_from.id} v{l.forked_from.version}.</p>}

      {l.details && (
        <dl className="listing-details">
          {l.details.what_you_get && <><dt>What you get</dt><dd>{l.details.what_you_get}</dd></>}
          {l.details.what_it_costs && <><dt>What it costs</dt><dd>{l.details.what_it_costs}</dd></>}
          {l.details.what_you_need && <><dt>What you need</dt><dd>{l.details.what_you_need}</dd></>}
        </dl>
      )}

      {preview.goal && (
        <section className="card">
          <h2>The goal</h2>
          <Goal text={preview.goal} />
        </section>
      )}

      <section className="card">
        <h2>{preview.roles.length === 1 ? "What it can do" : `The team: ${preview.roles.length} roles`}</h2>
        {preview.roles.map((role) => (
          <div key={role.slug} className="listing-role">
            <div><b>{role.name}</b> <span className="help">{role.description}</span></div>
            <div className={role.tools.includes("bash") ? "warn-text" : "help"}>
              {canDoWords(role.tools)}
              {role.secret_scopes.length ? ` · receives ${role.secret_scopes.join(", ")}` : ""}
            </div>
            <details open={preview.roles.length === 1}>
              <summary>Read every word of its prompt</summary>
              <pre className="prompt">{role.system_prompt}</pre>
            </details>
          </div>
        ))}
      </section>

      {(preview.needs.keys.length > 0 || preview.needs.data_sources.length > 0
        || preview.needs.mailbox || preview.needs.github_repo) && (
        <section className="card">
          <h2>What you bring</h2>
          <ul className="plain">
            {preview.needs.keys.map((k) => (
              <li key={k.name}>
                <b>{k.label}</b>{k.required ? "" : " (optional)"}
                {k.format ? <span className="help"> · {k.format}</span> : null}
                {" "}<span className="help">for {k.roles.join(", ")}</span>
                {k.help && <> · <a href={k.help} target="_blank" rel="noreferrer">how to get one</a></>}
              </li>
            ))}
            {preview.needs.data_sources.map((d) => (
              <li key={d.name}>
                <b>{d.serves}</b> ({d.name}){" "}
                <span className="help">
                  {d.address === "bring-your-own" ? "your own server" : d.address}
                  {" · "}{d.tools.length} tool{d.tools.length === 1 ? "" : "s"} allowed, everything else denied
                </span>
              </li>
            ))}
            {preview.needs.mailbox && <li>A mailbox for the project</li>}
            {preview.needs.github_repo && <li>A GitHub repository</li>}
          </ul>
          <p className="help">Nothing of the publisher&apos;s comes with it. You add your own keys after creating it.</p>
        </section>
      )}

      {preview.receipt && (
        <section className="card">
          <h2>When the publisher ran it</h2>
          <ReceiptSummary receipt={preview.receipt} />
          <p className="help">From yaaf&apos;s receipt on the publisher&apos;s install, as they reported it.</p>
        </section>
      )}

      {l.kind === "project" && preview.inputs.length > 0 && !removed && (
        <section className="card">
          <h2>Make it yours</h2>
          {preview.inputs.map((input) => (
            <label key={input.id} className="field" htmlFor={`blank-${input.id}`}>
              {input.question}{input.required ? "" : " (optional)"}
              {input.type === "choice" && input.choices ? (
                <select id={`blank-${input.id}`} value={answers[input.id] ?? ""}
                  onChange={(e) => setAnswers({ ...answers, [input.id]: e.target.value })}>
                  <option value="">— choose —</option>
                  {input.choices.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              ) : (
                <input id={`blank-${input.id}`} value={answers[input.id] ?? ""}
                  placeholder={`e.g. ${String(input.example ?? "")}`}
                  onChange={(e) => setAnswers({ ...answers, [input.id]: e.target.value })} />
              )}
            </label>
          ))}
        </section>
      )}

      {error && <div className="error">{error}</div>}
      {done && <div className="notice-box">{done} <Link to="/roles">Role library</Link></div>}

      {!removed && !done && (
        <div className="actions">
          {l.kind === "role" ? (
            me?.is_admin ? (
              <button className="primary" disabled={busy} onClick={installRole}>
                Install {l.name}
              </button>
            ) : (
              <p className="help">Only an instance admin can add a role to this install&apos;s library. Send them this page.</p>
            )
          ) : (
            <button className="primary" disabled={busy || missing.length > 0} onClick={startProject}
              title={missing.length ? `Answer: ${missing.map((m) => m.question).join("; ")}` : undefined}>
              Review and create
            </button>
          )}
        </div>
      )}
      <details className="help">
        <summary>Versions</summary>
        <ul className="plain">
          {preview.versions.map((v) => (
            <li key={v.version}>v{v.version} · {v.published_at} · {v.changes}</li>
          ))}
        </ul>
        <p className="mono">sha256 {preview.sha256}, checked against the registry before this page showed it.</p>
      </details>
    </div>
  );
}
