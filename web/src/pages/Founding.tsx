// The founding conversation: you talk, a project gets drafted.
//
// The chairman writes a brief; the Founder answers with a whole project. So
// this page is a conversation on the left and the draft it is producing on the
// right, and there is no form anywhere on it. "Review & create" hands the
// draft to the New Project screen through the same path a setup file takes.

import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError, del, get, post, ProjectBundle, put } from "../api";
import Markdown from "../Markdown";
import { SecretInput, SEND_HINT, sendOnEnter } from "../ui";

interface Turn {
  role: "chairman" | "founder";
  text: string;
}

interface Thread {
  id: string;
  title: string;
  turns: Turn[];
  draft_bundle: ProjectBundle | null;
  created_project_id: string | null;
  /** A turn is in flight on the server — not on this request. */
  running: boolean;
  /** A turn was under way when the process that ran it went away. */
  interrupted: boolean;
  /** Why the last turn failed, kept so it survives a reload. */
  last_error: string | null;
}

/** A step label is a line of the Founder's own markdown. It belongs on one
 *  row, so the emphasis is stripped rather than rendered. */
function plain(text: string): string {
  return text
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/(^|\s)\*([^*]+)\*/g, "$1$2")
    .replace(/^#+\s*/, "");
}

interface Step {
  label: string;
  state: "running" | "done" | "failed";
  seconds: number;
}

interface Progress {
  running: boolean;
  seconds: number;
  steps: Step[];
}

interface FounderStatus {
  credential_configured: boolean;
  billing_mode: string;
  model: string;
}

const PLACEHOLDER =
  "Tell me what you want to exist that doesn't yet.\n\n" +
  "Write it the way you'd brief a competent person you've just hired — messy is " +
  "fine, and you don't have to be complete. I'll draft the whole project and you " +
  "can argue with it.";

/** What the Founder is doing, while it does it.
 *
 *  A drafting turn takes minutes — it reads the role library, writes a goal,
 *  grades that goal against the grammar, and runs the fire drill on what it
 *  came up with. A spinner over five minutes of that is indistinguishable from
 *  a hang, which is exactly how it was first reported. */
function Steps({ progress }: { progress: Progress | null }) {
  const steps = progress?.steps ?? [];
  return (
    <div className="turn turn-founder">
      <div className="turn-who">The Founder</div>
      <div className="turn-text">
        <ul className="steps">
          {steps.map((step, i) => (
            <li key={i} className={`step step-${step.state}`}>
              <span className="step-mark">
                {step.state === "done" ? "✓" : step.state === "failed" ? "✕" : "•"}
              </span>
              <span className="step-label">{plain(step.label)}</span>
              <span className="step-secs">{step.seconds}s</span>
            </li>
          ))}
          {steps.length === 0 && <li className="step step-running">
            <span className="step-mark">•</span>
            <span className="step-label">Starting up</span>
          </li>}
        </ul>
        <p className="muted">
          {Math.round(progress?.seconds ?? 0)}s so far. It takes minutes — it is
          doing the work you would otherwise do on the form.
        </p>
      </div>
    </div>
  );
}

/** The one thing that has to be true before the Founder can talk at all. */
function CredentialGate({ onSaved }: { onSaved: () => void }) {
  const [value, setValue] = useState("");
  const [mode, setMode] = useState("api_key");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await put("/admin/founder/credential", { value: value.trim(), mode });
      onSaved();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <h2>The Founder needs a key of its own</h2>
      <p className="muted">
        This one is the platform's, not a project's. It pays for product-wide
        conversations like this one, and it is never handed to a project's
        agents — every project you create still pays its own way with its own
        key. Stored encrypted; only the server reads it.
      </p>
      <label>
        Key type
        <select value={mode} onChange={(e) => setMode(e.target.value)}>
          <option value="api_key">Anthropic API key</option>
          <option value="subscription">Claude subscription token</option>
        </select>
      </label>
      <SecretInput
        value={value}
        onChange={setValue}
        placeholder={mode === "subscription" ? "sk-ant-oat…" : "sk-ant-api…"}
      />
      <p className="muted">
        {mode === "subscription"
          ? "A subscription token comes from `claude setup-token` and starts sk-ant-oat."
          : "An API key comes from the Anthropic console and starts sk-ant-api."}{" "}
        Pasting one where the other belongs is refused here rather than failing
        on your first message.
      </p>
      {error && <p className="error">{error}</p>}
      <button className="primary" disabled={busy || value.trim().length < 8} onClick={save}>
        {busy ? "Saving…" : "Save and start"}
      </button>
      <p className="muted">
        You can also <a href="#/projects/new/form">fill the form yourself</a>{" "}
        instead — the fire drill runs for nothing either way. Once the key is
        set, it lives in <a href="#/settings">Settings</a>, where you can test,
        rotate or remove it.
      </p>
    </div>
  );
}

/** What the Founder has drafted so far, at a glance. */
function DraftPanel({ bundle }: { bundle: ProjectBundle | null }) {
  if (!bundle) {
    return (
      <div className="card muted">
        <h3>No draft yet</h3>
        <p>
          The project the Founder proposes will appear here — the goal, the
          shape, who it hires, and the rules it runs under. Nothing on this page
          creates anything or spends anything.
        </p>
      </div>
    );
  }
  const p = bundle.project;
  const headcount = (p.teams ?? []).reduce((n, t) => n + (t.employees?.length ?? 0), 0);
  const knobs = Object.keys(p.constitution ?? {});
  return (
    <div className="card">
      <h3>{p.name}</h3>
      <p className="muted">
        {p.mode} · {p.teams?.length ?? 0} teams · {headcount} people
        {p.deadline ? ` · by ${p.deadline.slice(0, 10)}` : ""}
      </p>

      <h4>Goal</h4>
      <pre className="goal-preview">{p.goal}</pre>

      <h4>Who pays</h4>
      <p className="muted">
        {p.billing_secret_name
          ? <>{p.billing_secret_name} — {p.billing_mode === "subscription" ? "a Claude subscription, metered in tokens" : "an Anthropic API key, billed in USD"}</>
          : "Not chosen yet. The Founder asks when there is more than one credential; you can also pick one on the form."}
      </p>

      <h4>Who it hires</h4>
      <ul>
        {(p.teams ?? []).map((t) => (
          <li key={t.name}>
            <strong>{t.name}</strong> — {t.role_slug} ({(t.employees ?? []).join(", ")})
          </li>
        ))}
      </ul>

      {knobs.length > 0 && (
        <>
          <h4>Rules it set</h4>
          <p className="muted">
            {knobs.length} of the constitution's rules; everything else takes its
            default.
          </p>
          <ul>
            {knobs.map((k) => (
              <li key={k}>
                {k}: {String((p.constitution as Record<string, unknown>)[k])}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

export default function Founding() {
  const navigate = useNavigate();
  const [status, setStatus] = useState<FounderStatus | null>(null);
  const [thread, setThread] = useState<Thread | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const bottom = useRef<HTMLDivElement | null>(null);

  async function loadStatus() {
    try {
      setStatus(await get<FounderStatus>("/admin/founder/status"));
    } catch (err) {
      setError((err as Error).message);
    }
  }

  /** Pick the conversation back up.
   *
   *  The thread is on the server, so losing it to a page refresh would be a
   *  choice — and an expensive one, because the chairman may have spent ten
   *  minutes arguing a draft into shape. Resume the most recent thread that
   *  has not already become a project. */
  async function resume() {
    try {
      const { threads } = await get<{
        threads: { id: string; created_project_id: string | null }[];
      }>("/admin/founder/threads");
      const open = threads.find((t) => !t.created_project_id);
      if (!open) return;
      const resumed = await get<Thread>(`/admin/founder/threads/${open.id}`);
      setThread(resumed);
      // A turn that was under way when the server went away. Saying so beats a
      // thread that looks idle for no reason and quietly lost five minutes.
      if (resumed.interrupted) {
        setError("That turn was interrupted before it finished — send it again.");
      } else if (resumed.last_error) {
        setError(resumed.last_error);
      }
    } catch {
      /* a fresh screen is a fine fallback */
    }
  }

  useEffect(() => {
    void loadStatus();
    void resume();
  }, []);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [thread?.turns.length, busy, progress?.steps.length]);

  // The turn belongs to the server, so this is the only thing waiting on it.
  // A turn started a second ago and one found still running after a reload are
  // the same case, which is what makes a refresh mid-draft survivable.
  const running = thread?.running ?? false;
  useEffect(() => {
    if (!running || !thread) return;
    let live = true;
    setBusy(true);
    const tick = async () => {
      try {
        const [next, state] = await Promise.all([
          get<Thread>(`/admin/founder/threads/${thread.id}`),
          get<Progress>(`/admin/founder/threads/${thread.id}/progress`),
        ]);
        if (!live) return;
        setProgress(state);
        if (!next.running) {
          setThread(next);
          setBusy(false);
          // The turn's own failure, which outlived the request that started it.
          if (next.last_error) setError(next.last_error);
        }
      } catch {
        /* a dropped poll is not worth reporting; the next one will do */
      }
    };
    void tick();
    const timer = setInterval(tick, 2000);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [running, thread?.id]);

  async function send() {
    const text = message.trim();
    if (!text || busy) return;
    setBusy(true);
    setError(null);
    setProgress(null);
    // Cleared up front so the turn the chairman just took is visible while the
    // Founder works, rather than sitting in the box looking unsent.
    setMessage("");
    try {
      // The turn runs on the server, not on this request: the POST comes back
      // in milliseconds with the chairman's own words already on the thread,
      // and the polling effect below waits for the rest.
      const next = thread
        ? await post<Thread>(`/admin/founder/threads/${thread.id}/say`, { message: text })
        : await post<Thread>("/admin/founder/threads", { message: text });
      setThread(next);
    } catch (err) {
      setMessage(text);
      setBusy(false);
      if (err instanceof ApiError && err.status === 422) void loadStatus();
      setError((err as Error).message);
    }
  }

  async function startOver() {
    if (thread && !confirm("Throw this conversation away and start again?")) return;
    if (thread) {
      try {
        await del(`/admin/founder/threads/${thread.id}`);
      } catch {
        /* it is going away from the screen either way */
      }
    }
    setThread(null);
    setMessage("");
    setError(null);
  }

  function review() {
    if (!thread?.draft_bundle) return;
    navigate("/projects/new/form", {
      state: { foundingDraft: thread.draft_bundle, foundingThreadId: thread.id },
    });
  }

  if (status && !status.credential_configured) {
    return (
      <>
        <Link className="crumb" to="/projects/new">&larr; Both ways to start</Link>
        <h1>Describe it, and yaaf drafts it</h1>
        <p className="subtitle">One key, once, before the first conversation.</p>
        <CredentialGate onSaved={loadStatus} />
      </>
    );
  }

  const turns = thread?.turns ?? [];

  return (
    <div className="founding">
      <header className="founding-head">
        <div>
          <Link className="crumb" to="/projects/new">&larr; Both ways to start</Link>
          <h1>Describe it, and yaaf drafts it</h1>
          <p className="subtitle">
            Describe what you want. The Founder drafts the whole thing — goal,
            shape, roster, rules — and then you argue with it. Nothing here is
            created until you say so on the next screen.
          </p>
        </div>
        <div className="founding-head-acts">
          {thread && (
            <button onClick={startOver} disabled={busy}>
              Start over
            </button>
          )}
          {/* Changing your mind is a two-way door, and it is only a door if it
              is on the screen you would change it on. A conversation that is
              not going anywhere should cost one click to leave, not a hunt
              through the nav for the other name for the same job. */}
          <Link className="crumb" to="/projects/new/form">
            I&apos;ll fill in the form myself &rarr;
          </Link>
        </div>
      </header>

      <div className="founding-body">
        <section className="founding-chat">
          {turns.length === 0 && !busy && (
            <div className="card muted">
              <h3>Start anywhere</h3>
              <p>
                A paragraph is plenty. Say what should be true at the end, who
                it is for, what you will and won't do yourself, and roughly what
                you are willing to spend. Anything you leave out, the Founder
                will assume and tell you what it assumed.
              </p>
            </div>
          )}
          {turns.map((turn, i) => (
            <div key={i} className={`turn turn-${turn.role}`}>
              <div className="turn-who">{turn.role === "chairman" ? "You" : "The Founder"}</div>
              <div className="turn-text">
                {/* The Founder writes markdown — headings, bold, lists, backticked
                    constitution keys. Rendering it as source was making the most
                    carefully written thing on the page the hardest to read. The
                    chairman types plain text, so his turns stay verbatim. */}
                {turn.role === "founder" ? <Markdown>{turn.text}</Markdown> : turn.text}
              </div>
            </div>
          ))}
          {busy && <Steps progress={progress} />}
          <div ref={bottom} />

          {error && <p className="error">{error}</p>}

          <div className="founding-compose">
            <textarea
              rows={turns.length ? 3 : 8}
              value={message}
              placeholder={turns.length ? "Push back, or answer its questions…" : PLACEHOLDER}
              disabled={busy}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={sendOnEnter(() => void send(), {
                disabled: busy || !message.trim(),
              })}
            />
            <div className="founding-actions">
              <span className="muted">{SEND_HINT}</span>
              <button className="primary" disabled={busy || !message.trim()} onClick={send}>
                {busy ? "Working (minutes)…" : turns.length ? "Send" : "Draft the project"}
              </button>
            </div>
          </div>
        </section>

        <aside className="founding-draft">
          <DraftPanel bundle={thread?.draft_bundle ?? null} />
          {thread?.draft_bundle && (
            <button className="primary" onClick={review} disabled={busy}>
              Review &amp; create →
            </button>
          )}
          {thread?.draft_bundle && (
            <p className="muted">
              This opens the New Project screen with everything filled in, where
              you can change anything, run the fire drill for nothing, and
              create it.
            </p>
          )}
        </aside>
      </div>
    </div>
  );
}
