import { ChangeEvent, Fragment, ReactNode, useEffect, useState } from "react";
import { CredentialField, CredentialNeedView, CredentialResult, get, post, put } from "./api";
import { fmtAgo } from "./ui";
import "./credentialNeeds.css";

/** Access the chairman promised a project: one card per service, from the
    promise to the proof (yaaf/server/credential_needs.py).

    A card says three things a pasted secret never did: what it is for and
    what it takes (yaaf's catalog when it knows the service, the Founder's
    suggestion when it does not, anything he added himself — each labelled),
    whether it is given, and how far it is proven: a verified test, a
    suggested call he chose to run, or an agent's report of using it. The
    same card is drawn before the project exists (creation) and after
    (Settings), so the two can never disagree about what a field is. */

export type Values = Record<string, string>;

/** Read a chosen file as base64, the shape a file credential is stored in. */
function readFile(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const bytes = new Uint8Array(reader.result as ArrayBuffer);
      let text = "";
      bytes.forEach((b) => { text += String.fromCharCode(b); });
      resolve(btoa(text));
    };
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(file);
  });
}

export function sourceLabel(known: boolean, service: string): ReactNode {
  return known
    ? <span className="cn-source known">Known to yaaf · verified test</span>
    : <span className="cn-source">Fields suggested by the assistant · check them against {service}'s docs</span>;
}

const ORIGIN: Record<CredentialField["origin"], string | null> = {
  catalog: null, suggested: "suggested", user: "added by you",
};

/** The fields as a form. File fields take a file; nothing already given is
    shown back, only that it is there. */
export function FieldInputs({ fields, values, filenames, onChange, idPrefix }: {
  fields: CredentialField[];
  values: Values;
  filenames: Values;
  onChange: (key: string, value: string, filename?: string) => void;
  idPrefix: string;
}) {
  return (
    <div className="cn-fields">
      {fields.map((f) => {
        const id = `${idPrefix}-${f.key}`;
        return (
          <div className="cn-field" key={f.key}>
            <label htmlFor={id}>{f.label}{ORIGIN[f.origin] && <span className="cn-ftag">{ORIGIN[f.origin]}</span>}</label>
            {f.file ? (
              <label className="cn-drop" htmlFor={id}>
                {filenames[f.key] ? <b>{filenames[f.key]}</b> : f.given ? "A file is saved. Choose another to replace it." : <>Choose the <b>file</b></>}
                <input id={id} type="file" hidden onChange={async (e: ChangeEvent<HTMLInputElement>) => {
                  const file = e.target.files?.[0];
                  if (file) onChange(f.key, await readFile(file), file.name);
                }} />
              </label>
            ) : (
              <input id={id} type={f.secret ? "password" : "text"} autoComplete="off"
                value={values[f.key] ?? ""}
                placeholder={f.given ? "Saved. Type to replace it." : ""}
                onChange={(e) => onChange(f.key, e.target.value)} />
            )}
            {f.help && <span className="help">{f.help}</span>}
          </div>
        );
      })}
    </div>
  );
}

/** "+ Add another value": a field the assistant missed. */
export function AddField({ onAdd }: { onAdd: (field: CredentialField) => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [secret, setSecret] = useState(true);
  if (!open) return <button type="button" className="cn-addfield" onClick={() => setOpen(true)}>+ Add another value</button>;
  const key = name.toUpperCase().replace(/[^A-Z0-9]+/g, "_").replace(/^_|_$/g, "");
  return (
    <div className="cn-addrow">
      <input aria-label="Name of the value" placeholder="e.g. Segment ID" value={name} onChange={(e) => setName(e.target.value)} />
      <label><input type="checkbox" checked={secret} onChange={(e) => setSecret(e.target.checked)} /> secret</label>
      <button type="button" className="small" disabled={!key} onClick={() => {
        onAdd({ key, label: name.trim(), help: "", secret, file: false, origin: "user" });
        setName(""); setOpen(false);
      }}>Add</button>
      <button type="button" className="linkish" onClick={() => setOpen(false)}>Cancel</button>
    </div>
  );
}

export function ResultBox({ result }: { result: CredentialResult }) {
  return (
    <div className={`cn-result ${result.ok ? "ok" : "bad"}`}>
      <span className="cn-ic" aria-hidden="true">{result.ok ? "✓" : "✕"}</span>
      <div>
        <b>{result.summary}</b>
        {result.fix.length > 0 && <ol>{result.fix.map((step, i) => <li key={i}>{step}</li>)}</ol>}
      </div>
    </div>
  );
}

/** The suggested call, shown in full before it runs. */
export function SuggestedCheck({ check, onRun, busy, primary }: {
  check: NonNullable<CredentialNeedView["check"]>; onRun: () => void; busy: boolean; primary: boolean;
}) {
  return (
    <div className="cn-suggest">
      <span><b>Suggested check</b> <span className="help">· proposed by the assistant, runs only if you press Run</span></span>
      <code>{`${check.method} ${check.url}${Object.entries(check.headers).map(([k, v]) => `\n${k}: ${v}`).join("")}`}</code>
      <span className="help">{check.reads ? `Reads ${check.reads}. ` : ""}It doesn't send, change or delete anything.</span>
      <div className="cn-acts">
        <button type="button" className={`small${primary ? " primary" : ""}`} disabled={busy} onClick={onRun}>
          {busy ? "Running…" : "Run this check"}
        </button>
      </div>
    </div>
  );
}

function pill(need: CredentialNeedView): ReactNode {
  const when = need.last_check?.at ? ` · checked ${fmtAgo(need.last_check.at)}` : "";
  switch (need.status) {
    case "ok":
      return <span className="cn-pill ok">✓ Works{need.last_check?.kind === "suggested" ? " · suggested check" : ""}{when}</span>;
    case "failed":
      return <span className="cn-pill bad">Needs attention</span>;
    case "saved":
      return <span className="cn-pill todo">Saved · not tested</span>;
    case "waived":
      return <span className="cn-pill todo">You said it isn't needed</span>;
    default:
      return <span className="cn-pill todo">{need.known ? "Not added" : "Needed · not added"}</span>;
  }
}

/** One promised credential on a project that exists (Settings, and the
    creation form once it has made the project). */
export function CredentialCard({ projectId, need, onChange, focused, canSteer }: {
  projectId: string;
  need: CredentialNeedView;
  onChange: (next: CredentialNeedView) => void;
  focused?: boolean;
  canSteer: boolean;
}) {
  const [editing, setEditing] = useState(need.status === "needed" || need.status === "waived");
  const [values, setValues] = useState<Values>({});
  const [filenames, setFilenames] = useState<Values>({});
  const [added, setAdded] = useState<CredentialField[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fields = [...need.fields, ...added];

  async function save() {
    setBusy(true); setError(null);
    try {
      const next = await put<CredentialNeedView>(`/admin/projects/${projectId}/credential_needs/${need.id}`, {
        values, filenames, add_fields: added.map((f) => ({ key: f.key, label: f.label, secret: f.secret })),
      });
      setValues({}); setFilenames({}); setAdded([]); setEditing(false);
      onChange(next);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function check() {
    setBusy(true); setError(null);
    try {
      onChange(await post<CredentialNeedView>(`/admin/projects/${projectId}/credential_needs/${need.id}/check`, {}));
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }

  const use = need.last_use;
  const tone = need.status === "ok" ? " ok" : need.status === "failed" ? " bad" : "";
  return (
    <div className={`cn-card${tone}${focused ? " cn-focus" : ""}`} id={`need-${need.id}`}>
      <div className="cn-head">
        <h3>{need.label}</h3>
        {need.purpose && <span className="help">{need.purpose}</span>}
        {sourceLabel(need.known, need.label)}
        <span className="grow" />
        {pill(need)}
      </div>
      {editing && canSteer && (
        <>
          <FieldInputs fields={fields} values={values} filenames={filenames} idPrefix={need.id}
            onChange={(key, value, filename) => {
              setValues((v) => ({ ...v, [key]: value }));
              if (filename) setFilenames((n) => ({ ...n, [key]: filename }));
            }} />
          <AddField onAdd={(f) => setAdded((a) => [...a, f])} />
        </>
      )}
      {need.last_check && <ResultBox result={need.last_check} />}
      {!need.known && need.check && need.status === "saved" && !need.last_check && canSteer && (
        <SuggestedCheck check={need.check} onRun={check} busy={busy} primary />
      )}
      {need.status === "saved" && !need.last_check && (
        <div className="cn-result info"><span className="cn-ic" aria-hidden="true">○</span><div>
          Saved, not tested. The agents can use it; nobody has confirmed it works yet.
          {need.check ? " Run the suggested check, or it" : " It"} will be marked the first time an agent reports using it.</div></div>
      )}
      <div className="cn-meta">
        <span>{need.used_by.length ? `Used by ${need.used_by.join(", ")}` : "Every role on the project receives it"}</span>
        <span>{use ? `Reported by ${use.by} ${fmtAgo(use.at)}: ${use.ok ? "worked" : "refused"}${use.detail ? ` (${use.detail})` : ""}` : "Not used by an agent yet"}</span>
        <span>{need.source === "ceo" ? "Asked for by the CEO" : "Promised by you, at creation"}</span>
      </div>
      {error && <div className="error">{error}</div>}
      {canSteer && (
        <div className="cn-acts">
          {editing ? (
            <>
              <button type="button" className="small primary" disabled={busy || (!Object.keys(values).length && !added.length)} onClick={save}>
                {busy ? "Saving…" : need.known ? "Save and test" : "Save"}
              </button>
              {need.status !== "needed" && <button type="button" className="linkish" onClick={() => setEditing(false)}>Cancel</button>}
              {!need.known && need.status === "needed" && <span className="help">then you can run the suggested check</span>}
            </>
          ) : (
            <>
              {(need.known || (need.check && need.last_check)) && need.status !== "waived" && (
                <button type="button" className={`small${need.status === "failed" ? " primary" : ""}`} disabled={busy} onClick={check}>
                  {busy ? "Testing…" : "Test again"}
                </button>
              )}
              <button type="button" className="small" onClick={() => setEditing(true)}>Replace</button>
            </>
          )}
        </div>
      )}
    </div>
  );
}

/** Settings › Credentials: every promised credential, and where a link from
    the setup strip lands (`need=<id>`). */
export function CredentialsSection({ projectId, canSteer, focus, onSaved }: {
  projectId: string; canSteer: boolean; focus: string | null; onSaved: () => void;
}) {
  const [needs, setNeeds] = useState<CredentialNeedView[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    get<CredentialNeedView[]>(`/observe/projects/${projectId}/credential_needs`).then(setNeeds, (e) => setError((e as Error).message));
  }, [projectId]);
  useEffect(() => {
    if (!focus || !needs) return;
    const t = setTimeout(() => document.getElementById(`need-${focus}`)?.scrollIntoView({ behavior: "smooth", block: "start" }), 200);
    return () => clearTimeout(t);
  }, [focus, needs]);
  if (error) return <div className="error">{error}</div>;
  if (!needs) return <p className="help">Loading…</p>;
  return (
    <>
      <h2>Credentials</h2>
      <p className="help" style={{ marginTop: 0 }}>
        What your agents sign in with. Each one is tested when you save it, and agents report when one is refused.
        Other keys and values live in Secrets &amp; variables.
      </p>
      {needs.length === 0 ? (
        <p className="help">No credentials were promised for this project. Add keys in Secrets &amp; variables.</p>
      ) : (
        <div className="cn-list">
          {needs.map((need) => (
            <Fragment key={need.id}>
              <CredentialCard projectId={projectId} need={need} canSteer={canSteer} focused={focus === need.id}
                onChange={(next) => { setNeeds((all) => (all ?? []).map((n) => (n.id === next.id ? next : n))); onSaved(); }} />
            </Fragment>
          ))}
        </div>
      )}
    </>
  );
}

// ── before the project exists: the creation form ──────────────────────────────

/** A promised credential as the creation form draws it, before the project
    exists (POST /admin/credential_promises/resolve). */
export interface PromiseCard {
  service: string; label: string; purpose: string; known: boolean;
  fields: CredentialField[];
  check: CredentialNeedView["check"];
}
/** What the chairman has typed into one card, kept by the form until it
    creates the project and stores them on the promise. */
export interface PromiseDraft {
  values: Values; filenames: Values; added: CredentialField[];
  /** Pressed Save on a service yaaf does not know: the suggested check opens. */
  saved: boolean;
  result: CredentialResult | null;
}
export const BLANK_DRAFT: PromiseDraft = { values: {}, filenames: {}, added: [], saved: false, result: null };

/** "Before it starts: the access you promised" — one card per service the
    Founder heard him promise, each tested here with nothing stored yet. */
export function PromisedSection({ cards, drafts, setDraft }: {
  cards: PromiseCard[];
  drafts: PromiseDraft[];
  setDraft: (i: number, next: PromiseDraft) => void;
}) {
  const [busy, setBusy] = useState<number | null>(null);
  if (!cards.length) return null;
  async function test(i: number, withCheck: boolean) {
    const card = cards[i];
    const draft = drafts[i] ?? BLANK_DRAFT;
    setBusy(i);
    try {
      const result = await post<CredentialResult>("/admin/credential_checks", {
        service: card.service, values: decodedForTest(card, draft), check: withCheck ? card.check : null,
      });
      setDraft(i, { ...draft, saved: true, result });
    } catch (e) {
      setDraft(i, { ...draft, result: { ok: false, summary: (e as Error).message, fix: [], kind: "none" } });
    } finally { setBusy(null); }
  }
  return (
    <>
      <h2>Before it starts: the access you promised</h2>
      <p className="help" style={{ marginTop: 0 }}>
        You said you'd give it access to {cards.map((c) => c.label).join(", ")}. Add each one here and test it, or add them
        later: until they're in, the CEO plans around them and the project's setup strip keeps them in view.
      </p>
      <div className="cn-list" style={{ marginBottom: 16 }}>
        {cards.map((card, i) => {
          const draft = drafts[i] ?? BLANK_DRAFT;
          const fields = [...card.fields, ...draft.added];
          const filled = card.fields.every((f) => draft.values[f.key]);
          const r = draft.result;
          const pillNode = r ? (r.ok
            ? <span className="cn-pill ok">✓ Works{r.kind === "suggested" ? " · suggested check" : ""}</span>
            : <span className="cn-pill bad">Test failed</span>)
            : draft.saved ? <span className="cn-pill todo">Saved · not tested</span>
              : <span className="cn-pill todo">{card.known ? "Not added" : "Needed · not added"}</span>;
          return (
            <div className={`cn-card${r ? (r.ok ? " ok" : " bad") : ""}`} key={`${card.service}-${i}`}>
              <div className="cn-head">
                <h3>{card.label}</h3>
                {card.purpose && <span className="help">{card.purpose}</span>}
                {sourceLabel(card.known, card.label)}
                <span className="grow" />
                {pillNode}
              </div>
              <FieldInputs fields={fields} values={draft.values} filenames={draft.filenames} idPrefix={`promise-${i}`}
                onChange={(key, value, filename) => setDraft(i, {
                  ...draft, result: null,
                  values: { ...draft.values, [key]: value },
                  filenames: filename ? { ...draft.filenames, [key]: filename } : draft.filenames,
                })} />
              <AddField onAdd={(f) => setDraft(i, { ...draft, added: [...draft.added, f] })} />
              {!card.known && card.check && draft.saved && (
                <SuggestedCheck check={shownCheck(card, draft)} busy={busy === i} primary={!r} onRun={() => test(i, true)} />
              )}
              {r && <ResultBox result={r} />}
              <div className="cn-acts">
                {card.known ? (
                  <button type="button" className="small primary" disabled={!filled || busy === i} onClick={() => test(i, false)}>
                    {busy === i ? "Testing…" : r ? "Test again" : "Save and test"}
                  </button>
                ) : !draft.saved ? (
                  <>
                    <button type="button" className="small primary" disabled={!filled}
                      onClick={() => setDraft(i, { ...draft, saved: true })}>Save</button>
                    {card.check && <span className="help">then you can run the suggested check</span>}
                  </>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}

/** The suggested call as he is shown it: plain values filled in, secrets
    masked — the same rule the server applies (credential_checks.shown). */
function shownCheck(card: PromiseCard, draft: PromiseDraft): NonNullable<CredentialNeedView["check"]> {
  const check = card.check!;
  const fields = [...card.fields, ...draft.added];
  const show = (text: string) => text.replace(/\{([A-Z0-9_]+)\}/g, (_, key: string) => {
    const f = fields.find((x) => x.key === key);
    return f && !f.secret && !f.file && draft.values[key] ? draft.values[key] : "••••••••••••";
  });
  return { ...check, url: show(check.url), headers: Object.fromEntries(Object.entries(check.headers).map(([k, v]) => [k, show(v)])) };
}

/** Values as a test reads them: a file field's base64 decoded back to text. */
function decodedForTest(card: PromiseCard, draft: PromiseDraft): Values {
  const out: Values = {};
  for (const [key, value] of Object.entries(draft.values)) {
    const f = [...card.fields, ...draft.added].find((x) => x.key === key);
    out[key] = f?.file ? atob(value) : value;
  }
  return out;
}

/** After the project exists: put what he typed onto its promises. */
export async function storePromised(projectId: string, cards: PromiseCard[], drafts: PromiseDraft[]): Promise<string[]> {
  const failed: string[] = [];
  const needs = await get<CredentialNeedView[]>(`/observe/projects/${projectId}/credential_needs`);
  for (const [i, card] of cards.entries()) {
    const draft = drafts[i];
    if (!draft || (!Object.keys(draft.values).length && !draft.added.length)) continue;
    const need = needs.find((n) => n.label === card.label) ?? needs[i];
    if (!need) continue;
    try {
      await put(`/admin/projects/${projectId}/credential_needs/${need.id}`, {
        values: draft.values, filenames: draft.filenames,
        add_fields: draft.added.map((f) => ({ key: f.key, label: f.label, secret: f.secret })),
      });
    } catch (e) { failed.push(`${card.label}: ${(e as Error).message}`); }
  }
  return failed;
}
