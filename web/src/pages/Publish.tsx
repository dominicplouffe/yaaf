// Publishing a project or a role to the marketplace. The page never asks the
// chairman to go through their own brief: yaaf builds the listing, the Founder
// turns their details into blanks, and the page shows the result the way a
// stranger will see it, with at most a few questions already answered. The
// server refuses to send anything it still finds of theirs.

import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { ApiError, del, get, post } from "../api";
import { canDoWords } from "../library";

interface Draft {
  slug: string;
  listing: { kind: "project" | "role"; name: string; summary: string; category: string; domains: string[];
             details?: Record<string, string>; license: string };
  version: {
    kind: "project" | "role";
    bundle?: { project: { goal: string; name: string; teams: { name: string; role_slug: string }[] };
               roles: { slug: string; name: string; description: string; system_prompt: string;
                        tool_permissions: Record<string, boolean> }[] };
    role?: { slug: string; name: string; description: string; system_prompt: string;
             tool_permissions: Record<string, boolean> };
    needs: { keys: { name: string; label: string; format?: string; required: boolean }[];
             data_sources: { name: string; serves: string; address: string }[] };
    inputs?: { id: string; question: string; type: string; example: unknown; required: boolean }[];
    receipt?: Record<string, unknown>;
  };
}
interface Question {
  about: string; why: string; recommend: "keep" | "blank";
  blank: { id: string; question: string; type: string; example: unknown; required: boolean };
}
interface Leak { what: string; where: string }
interface Publication { listing_id: string; version: number; state: string; review_note: string | null;
                        pr_url: string | null }

function answer(draft: Draft, q: Question, choice: "keep" | "blank"): Draft {
  if (choice !== "blank" || !draft.version.bundle) return draft;
  const copy: Draft = JSON.parse(JSON.stringify(draft));
  const token = `{{${q.blank.id}}}`;
  const project = copy.version.bundle!.project;
  project.goal = project.goal.split(q.about).join(token);
  project.teams.forEach((t) => { t.name = t.name.split(q.about).join(token); });
  copy.version.inputs = [...(copy.version.inputs ?? []).filter((i) => i.id !== q.blank.id), q.blank];
  return copy;
}

function WithBlanks({ text }: { text: string }) {
  return (
    <p className="listing-goal">
      {text.split(/(\{\{\s*[A-Za-z0-9_]+\s*\}\})/g).map((part, i) => part.startsWith("{{")
        ? <span key={i} className="blank">{part.replace(/[{}\s]/g, "").replace(/_/g, " ")}</span>
        : <span key={i}>{part}</span>)}
    </p>
  );
}

type Input = NonNullable<Draft["version"]["inputs"]>[number];

/** The blanks' questions, by hand: what the Founder would have written when it
    cannot be asked. */
function BlankEditor({ inputs, onChange }: { inputs: Input[]; onChange: (inputs: Input[]) => void }) {
  function set(i: number, patch: Partial<Input>) {
    onChange(inputs.map((input, n) => (n === i ? { ...input, ...patch } : input)));
  }
  return (
    <div className="field">
      <div className="help">Each blank needs a question a stranger can answer, and an example that is not yours.</div>
      {inputs.map((input, i) => (
        <div key={i} className="pills">
          <input aria-label="Blank name" value={input.id} placeholder="business_name"
            onChange={(e) => set(i, { id: e.target.value.replace(/[^a-z0-9_]/g, "") })} />
          <input aria-label="Question" value={input.question} placeholder="What is the business called?"
            onChange={(e) => set(i, { question: e.target.value })} />
          <input aria-label="Example" value={String(input.example ?? "")} placeholder="Northwind Outfitters"
            onChange={(e) => set(i, { example: e.target.value })} />
          <button className="small" onClick={() => onChange(inputs.filter((_, n) => n !== i))}>Remove</button>
        </div>
      ))}
      <button className="small" onClick={() => onChange([...inputs,
        { id: "", question: "", type: "text", example: "", required: true }])}>Add a blank</button>
    </div>
  );
}

const STATE_WORDS: Record<string, string> = {
  submitted: "In review", changes_requested: "Changes requested", published: "Published", closed: "Closed",
};

export default function Publish() {
  const [params] = useSearchParams();
  const projectId = params.get("project");
  const roleSlug = params.get("role");
  const base = projectId ? `/admin/publish/projects/${projectId}` : `/admin/publish/roles/${roleSlug}`;
  const [draft, setDraft] = useState<Draft | null>(null);
  const [questions, setQuestions] = useState<Question[]>([]);
  const [choices, setChoices] = useState<Record<string, "keep" | "blank">>({});
  const [changes, setChanges] = useState<string[]>([]);
  const [leaks, setLeaks] = useState<Leak[]>([]);
  const [phase, setPhase] = useState<"drafting" | "founder" | "ready" | "submitting" | "done">("drafting");
  const [error, setError] = useState<string | null>(null);
  const [github, setGithub] = useState<{ connected: boolean; login: string | null; ready: boolean; why: string | null } | null>(null);
  const [device, setDevice] = useState<{ user_code: string; verification_uri: string; device_code: string; interval: number } | null>(null);
  const [submitted, setSubmitted] = useState<Publication | null>(null);
  const [history, setHistory] = useState<Publication[]>([]);
  const [showChanges, setShowChanges] = useState(false);
  const [editingGoal, setEditingGoal] = useState(false);
  const polling = useRef<number | null>(null);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const drafted = await post<{ draft: Draft; leaks: Leak[] }>(`${base}/draft`, {});
        if (!live) return;
        setDraft(drafted.draft);
        setLeaks(drafted.leaks);
        setPhase("founder");
        try {
          const made = await post<{ draft: Draft; questions: Question[]; changes: string[]; leaks: Leak[] }>(
            `${base}/generalize`, { draft: drafted.draft });
          if (!live) return;
          setDraft(made.draft);
          setQuestions(made.questions);
          setChoices(Object.fromEntries(made.questions.map((q) => [q.about, q.recommend])));
          setChanges(made.changes);
          setLeaks(made.leaks);
        } catch (e) {
          setError(`The Founder could not prepare it: ${(e as Error).message.replace(/\.$/, "")}. You can edit it below.`);
        }
        setPhase("ready");
      } catch (e) {
        setError((e as Error).message);
      }
    })();
    get<typeof github>("/admin/publish/github").then(setGithub).catch(() => {});
    const q = projectId ? `project_id=${projectId}` : `role_slug=${roleSlug}`;
    get<Publication[]>(`/admin/publish/publications?${q}`).then(setHistory).catch(() => {});
    return () => { live = false; if (polling.current) window.clearInterval(polling.current); };
  }, [base, projectId, roleSlug]);

  const final = useMemo(() => {
    if (!draft) return null;
    return questions.reduce((d, q) => answer(d, q, choices[q.about] ?? q.recommend), draft);
  }, [draft, questions, choices]);

  async function signIn() {
    setError(null);
    try {
      const started = await post<{ user_code: string; verification_uri: string; device_code: string; interval: number }>(
        "/admin/publish/github/start");
      setDevice(started);
      polling.current = window.setInterval(async () => {
        const polled = await post<{ state: string; login?: string }>("/admin/publish/github/poll",
          { device_code: started.device_code });
        if (polled.state === "done") {
          if (polling.current) window.clearInterval(polling.current);
          setDevice(null);
          setGithub((g) => g ? { ...g, connected: true, login: polled.login ?? null } : g);
        } else if (polled.state === "expired" || polled.state === "denied") {
          if (polling.current) window.clearInterval(polling.current);
          setDevice(null);
          setError(`GitHub sign-in ${polled.state}. Try again.`);
        }
      }, Math.max(5, started.interval) * 1000);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function submit() {
    if (!final) return;
    setPhase("submitting");
    setError(null);
    try {
      const row = await post<Publication>("/admin/publish/submit", {
        ...(projectId ? { project_id: projectId } : { role_slug: roleSlug }), draft: final });
      setSubmitted(row);
      setPhase("done");
    } catch (e) {
      const detail = e instanceof ApiError
        ? (e.data as { detail?: { leaks?: Leak[]; problems?: string[] } } | null)?.detail : null;
      if (detail?.leaks) setLeaks(detail.leaks);
      setError((e as Error).message);
      setPhase("ready");
    }
  }

  if (!projectId && !roleSlug) return <div className="error">Open this from a project&apos;s Settings or a role.</div>;
  const listing = final?.listing;
  const roles = final ? (final.version.role ? [final.version.role] : final.version.bundle?.roles ?? []) : [];

  return (
    <div className="listing-page">
      <Link className="crumb" to={projectId ? `/projects/${projectId}/settings?s=duplicate` : "/roles"}>
        &larr; {projectId ? "Project settings" : "Role library"}
      </Link>
      <h1>Publish to the marketplace</h1>
      {history.length > 0 && (
        <div className="card">
          {history.map((h) => (
            <div key={`${h.listing_id}${h.version}`}>
              <b>{h.listing_id} v{h.version}</b>: {STATE_WORDS[h.state] ?? h.state}
              {h.pr_url && <> · <a href={h.pr_url} target="_blank" rel="noreferrer">the review</a></>}
              {h.review_note && <div className="warn-text">Moderator: {h.review_note}</div>}
            </div>
          ))}
        </div>
      )}
      {phase === "drafting" && <p className="help">Leaving out everything that is yours…</p>}
      {phase === "founder" && <p className="help">The Founder is making it reusable. About a minute.</p>}
      {error && <div className="error">{error}</div>}

      {final && listing && phase !== "drafting" && phase !== "founder" && phase !== "done" && (
        <>
          <section className="card">
            <h2>This is what the next person will see</h2>
            <label className="field" htmlFor="pub-name">Name
              <input id="pub-name" value={listing.name}
                onChange={(e) => setDraft({ ...draft!, listing: { ...draft!.listing, name: e.target.value } })} />
            </label>
            <label className="field" htmlFor="pub-summary">One sentence: the outcome
              <input id="pub-summary" value={listing.summary} maxLength={160}
                onChange={(e) => setDraft({ ...draft!, listing: { ...draft!.listing, summary: e.target.value } })} />
            </label>
            {final.version.bundle && (editingGoal ? (
              <label className="field" htmlFor="pub-goal">The goal. Write a blank as {"{{name}}"}, and add its question below.
                <textarea id="pub-goal" rows={12} value={draft!.version.bundle!.project.goal}
                  onChange={(e) => {
                    const copy: Draft = JSON.parse(JSON.stringify(draft));
                    copy.version.bundle!.project.goal = e.target.value;
                    setDraft(copy);
                  }} />
              </label>
            ) : <WithBlanks text={final.version.bundle.project.goal} />)}
            {final.version.bundle && (
              <button className="small" onClick={() => setEditingGoal(!editingGoal)}>
                {editingGoal ? "Done editing" : "Edit the goal"}
              </button>
            )}
            {editingGoal && (
              <BlankEditor inputs={draft!.version.inputs ?? []} onChange={(inputs) => {
                const copy: Draft = JSON.parse(JSON.stringify(draft));
                copy.version.inputs = inputs;
                setDraft(copy);
              }} />
            )}
            {(final.version.inputs ?? []).length > 0 && (
              <p className="help">Asks whoever installs it: {(final.version.inputs ?? []).map((i) => i.question).join(" · ")}</p>
            )}
            {roles.map((r) => (
              <div key={r.slug} className="listing-role">
                <div><b>{r.name}</b> <span className="help">{canDoWords(Object.keys(r.tool_permissions ?? {}).filter((k) => r.tool_permissions[k]))}</span></div>
              </div>
            ))}
            {final.version.needs.keys.length > 0 && (
              <p className="help">They bring: {final.version.needs.keys.map((k) => k.label).join(", ")}. Your values never leave.</p>
            )}
            {final.version.receipt && <p className="help">Your receipt goes with it: what it cost and what yaaf verified.</p>}
            {changes.length > 0 && (
              <details open={showChanges} onToggle={(e) => setShowChanges((e.target as HTMLDetailsElement).open)}>
                <summary>Show what changed ({changes.length})</summary>
                <ul className="plain">{changes.map((c) => <li key={c}>{c}</li>)}</ul>
              </details>
            )}
          </section>

          {questions.length > 0 && (
            <section className="card">
              <h2>{questions.length === 1 ? "One question" : `${questions.length} questions`}</h2>
              {questions.map((q) => (
                <div key={q.about} className="field">
                  <div>&ldquo;{q.about}&rdquo;: {q.why}</div>
                  <div className="pills">
                    {(["keep", "blank"] as const).map((c) => (
                      <button key={c} className={(choices[q.about] ?? q.recommend) === c ? "active" : ""}
                        onClick={() => setChoices({ ...choices, [q.about]: c })}>
                        {c === "keep" ? "Keep it" : `Make it a blank: ${q.blank.question}`}
                        {q.recommend === c ? " (suggested)" : ""}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </section>
          )}

          {leaks.length > 0 && (
            <div className="error">
              Still yours, so it will not be sent:{" "}
              {leaks.map((l) => `${l.what} (in ${l.where})`).join("; ")}. Edit it above, or ask the Founder
              again by reloading this page. yaaf checks again when you submit.
            </div>
          )}

          <section className="card">
            <h2>Send it for review</h2>
            {github && !github.ready && <p className="help">You cannot publish from this install yet: {github.why}.</p>}
            {github?.ready && !github.connected && !device && (
              <button onClick={signIn}>Sign in with GitHub</button>
            )}
            {device && (
              <p>Open <a href={device.verification_uri} target="_blank" rel="noreferrer">{device.verification_uri}</a> and
                enter <b className="mono">{device.user_code}</b>. This page carries on by itself.</p>
            )}
            {github?.connected && (
              <>
                <p className="help">Signed in as {github.login}. It will be published as @{github.login}/{final.slug}.{" "}
                  <button className="small" onClick={async () => { await del("/admin/publish/github"); setGithub({ ...github, connected: false, login: null }); }}>
                    Sign out</button></p>
                {/* Not disabled by the leak list: it is from the last check, and an
                    edit may have fixed it. The server checks again either way. */}
                <button className="primary" disabled={phase === "submitting"}
                  onClick={submit}>{phase === "submitting" ? "Sending…" : "Submit for review"}</button>
              </>
            )}
          </section>
        </>
      )}

      {phase === "done" && submitted && (
        <div className="notice-box">
          Submitted {submitted.listing_id} v{submitted.version}. A moderator reads it next, and their answer appears
          here and on the project&apos;s Settings.{" "}
          {submitted.pr_url && <a href={submitted.pr_url} target="_blank" rel="noreferrer">See the review</a>}
        </div>
      )}
    </div>
  );
}
