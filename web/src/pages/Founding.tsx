// The founding conversation: you talk, a project gets drafted.
//
// The chairman writes a brief; the Founder answers with a whole project and a
// round of questions about it, each with its own answer already chosen. So
// this page is a conversation on the left and the draft it is producing on the
// right, and there is no form anywhere on it — a round he agrees with is one
// button. "Review & create" hands the draft to the New Project screen through
// the same path a setup file takes.

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError, del, get, post, ProjectBundle, put } from "../api";
import Markdown from "../Markdown";
import { BaseUrlFields, DEFAULT_PROVIDER, KeyHelp, KeyInput, useProviders } from "../providers";
import { SEND_HINT, sendOnEnter } from "../ui";
import { Badge } from "../ui/kit";
import type { Credential } from "../llmCredentials";
import {
  Aligned,
  Answer,
  initialPicks,
  Ledger,
  Pick,
  Picks,
  Round,
  RoundForm,
  roundKey,
  RoundRecap,
  toAnswers,
} from "../interview";

interface Turn {
  role: "chairman" | "founder";
  text: string;
  /** The Founder's round of questions, and the ledger as it stood. */
  round?: Round;
  /** The chairman's answers to the round before, as clicked. */
  answers?: Answer[];
  /** What he wrote beside them. */
  note?: string;
  /** Which parts of the draft this turn rewrote. */
  changed?: string[];
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
  /** The round, as soon as the Founder asked it — before the draft is done. */
  round?: Round | null;
}

interface FounderStatus {
  credential_configured: boolean;
  billing_mode: string;
  model: string;
}

const PLACEHOLDER =
  "Tell me what you want to exist that doesn't yet.\n\n" +
  "Write it the way you'd brief a competent person you've just hired — messy is " +
  "fine, and you don't have to be complete. I'll draft the whole project, then ask " +
  "about anything I had to guess, with my answer already picked.";

/** What the Founder is doing, while it does it.
 *
 *  A drafting turn takes minutes — it reads the role library, writes a goal,
 *  grades that goal against the grammar, and runs the fire drill on what it
 *  came up with. A spinner over five minutes of that is indistinguishable from
 *  a hang, which is exactly how it was first reported. */
function Steps({ progress, onStop, stopping }: {
  progress: Progress | null;
  onStop: () => void;
  stopping: boolean;
}) {
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
        <div className="steps-foot">
          <p className="muted">
            {Math.round(progress?.seconds ?? 0)}s so far. It takes minutes — it is
            doing the work you would otherwise do on the form.
          </p>
          {/* Minutes is a long time to watch a typo being taken seriously. */}
          <button onClick={onStop} disabled={stopping}
            title="Stop the Founder and put what you sent back in the box">
            {stopping ? "Stopping…" : "Stop"}
          </button>
        </div>
      </div>
    </div>
  );
}

/** The one thing that has to be true before the Founder can talk at all.

    Any provider with an API can pay for it: the Founder runs in the control
    plane, on that provider's own wire — or, for a Claude subscription, on the
    Claude Code CLI — and its model is the provider's Frontier class unless
    somebody names another in Settings. */
function CredentialGate({ onSaved }: { onSaved: () => void }) {
  const [value, setValue] = useState("");
  const [provider, setProvider] = useState(DEFAULT_PROVIDER);
  const [mode, setMode] = useState("api_key");
  const [baseUrl, setBaseUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sellers = useProviders().filter((p) => p.protocol);
  const seller = sellers.find((p) => p.name === provider);
  // A login the Founder can hold is one the Claude Code CLI holds; every other
  // provider is paid with its API key.
  const kinds = (seller?.kinds ?? []).filter(
    (k) => k.mode === "api_key" || k.default_harness === "claude_agent_sdk");
  const kind = kinds.find((k) => k.mode === mode) ?? kinds[0];
  // The keys this install already holds for projects. Most installs have
  // exactly one, and asking for it a second time under a second name was the
  // whole of this screen.
  const [existing, setExisting] = useState<Credential[]>([]);
  useEffect(() => {
    get<Credential[]>("/admin/settings/llm-credentials").then(setExisting).catch(() => {});
  }, []);

  async function borrow(id: string) {
    setBusy(true);
    setError(null);
    try {
      await put("/admin/founder/credential/llm", { credential_id: id });
      onSaved();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await put("/admin/settings/platform-credential", {
        value: value.trim(), mode: kind?.mode ?? "api_key", provider,
        ...(seller?.credential_base_url ? { base_url: baseUrl.trim() } : {}) });
      onSaved();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <h2>The Founder needs a key</h2>
      <p className="muted">
        It pays for drafting — conversations like this one — and is never handed
        to a project's agents. Every project still pays its own way.
      </p>
      {existing.length > 0 && (
        <>
          <p><strong>Use a key you already added</strong></p>
          <div className="actions" style={{ flexWrap: "wrap", justifyContent: "flex-start" }}>
            {existing.map((c) => (
              <button key={c.id} className="primary" disabled={busy} onClick={() => borrow(c.id)}>
                Use {c.name}{c.bills ? ` (${c.bills})` : ""}
              </button>
            ))}
          </div>
          <p className="muted">Or paste a different key just for drafting:</p>
        </>
      )}
      <label>
        Provider
        <select value={provider} disabled={busy}
          onChange={(e) => { setProvider(e.target.value); setMode("api_key"); setBaseUrl(""); }}>
          {sellers.length === 0 && <option value={provider}>Loading providers…</option>}
          {sellers.map((p) => <option key={p.name} value={p.name}>{p.label}</option>)}
        </select>
      </label>
      {kinds.length > 1 && <label>
        Key type
        <select value={kind?.mode} disabled={busy} onChange={(e) => setMode(e.target.value)}>
          {kinds.map((k) => <option key={k.mode} value={k.mode}>{k.bills.split(",")[0]}</option>)}
        </select>
      </label>}
      {seller?.credential_base_url && <BaseUrlFields provider={seller} value={baseUrl}
        onChange={setBaseUrl} secret={value} disabled={busy} />}
      <KeyInput kind={kind} value={value} onChange={setValue} disabled={busy}
        placeholder={seller?.local ? "Leave blank — it takes no key" : undefined} />
      <KeyHelp provider={seller} kind={kind} />
      <p className="muted">
        The Founder drafts on {seller?.label ?? "this provider"}&apos;s most capable model —
        its Frontier class{seller?.lineup.frontier ? <>, <code>{seller.lineup.frontier}</code></> : ""}{" "}
        — unless you name another in Settings. Pasting a key where another kind belongs is
        refused here rather than failing on your first message.
      </p>
      {error && <p className="error">{error}</p>}
      <button className="primary"
        disabled={busy || (!seller?.local && value.trim().length < 8)
          || (!!seller?.credential_base_url && !baseUrl.trim())}
        onClick={save}>
        {busy ? "Saving…" : "Save and start"}
      </button>
      <p className="muted">
        You can also <a href="#/projects/new/form">fill the form yourself</a>{" "}
        instead — the fire drill runs for nothing either way. Once the key is
        set, it lives in <a href="#/settings?section=founder">Settings</a>, where you can test,
        rotate or remove it.
      </p>
    </div>
  );
}

/** What the Founder has drafted so far, at a glance. */
/** "Updated" beside a part of the draft the last answers rewrote — the proof
 *  that answering was worth it. */
function Moved({ on }: { on: boolean }) {
  return on ? <> <Badge tone="accent">Updated</Badge></> : null;
}

function DraftPanel({ bundle, changed = [] }: { bundle: ProjectBundle | null; changed?: string[] }) {
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
        <Moved on={changed.includes("shape")} />
      </p>

      <h4>Goal<Moved on={changed.includes("goal")} /></h4>
      <pre className="goal-preview">{p.goal}</pre>

      <h4>Who pays<Moved on={changed.includes("pays")} /></h4>
      <p className="muted">
        {p.billing_secret_name
          ? <>{p.billing_secret_name} — {p.billing_mode === "subscription" ? "a subscription, metered in tokens" : "an API key, billed in USD"}</>
          : "Not chosen yet. The Founder asks when there is more than one credential; you can also pick one on the form."}
      </p>

      <h4>Who it hires<Moved on={changed.includes("team")} /></h4>
      <ul>
        {(p.teams ?? []).map((t) => (
          <li key={t.name}>
            <strong>{t.name}</strong> — {t.role_slug} ({(t.employees ?? []).join(", ")})
          </li>
        ))}
      </ul>

      {knobs.length > 0 && (
        <>
          <h4>Rules it set<Moved on={changed.includes("rules")} /></h4>
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
  // The open round's answers, held here rather than in the form: the round
  // first arrives mid-turn from the progress poll and then again on the saved
  // thread, and what he had already clicked must survive the hand-over.
  const [picks, setPicks] = useState<{ key: string; picks: Picks }>({ key: "", picks: {} });
  const [roundNote, setRoundNote] = useState("");
  // Answers sent while the Founder was still writing the draft. They go the
  // moment it finishes, so he never waits on the slow half of a turn to
  // start the next one.
  const [queued, setQueued] = useState<{ answers: Answer[]; message: string } | null>(null);
  // At the end the page has one question left — is this the project? — and a
  // composer under it would be a second answer competing with the first.
  // "Something's off" is what opens it.
  const [reopening, setReopening] = useState(false);
  // What he sent, kept from the moment he pressed Stop until the server takes
  // it off the thread — then it goes back where he typed it, to be fixed. A
  // ref, because the poll that sees the turn end is a closure from before.
  const stopped = useRef<{ turns: number; message: string; note: string } | null>(null);
  const [stopping, setStopping] = useState(false);
  const composer = useRef<HTMLTextAreaElement | null>(null);

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
          const was = stopped.current;
          stopped.current = null;
          setStopping(false);
          // Withdrawn, rather than answered in the instant before the press.
          if (was && next.turns.length < was.turns) {
            setMessage(was.message);
            setRoundNote(was.note);
          }
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

  const turns = thread?.turns ?? [];
  const last = turns[turns.length - 1];
  // The round on the table: the one the Founder just asked on a saved turn,
  // or — mid-turn, before the draft is finished — the one it has already asked.
  const savedRound = last?.role === "founder" && last.round ? last.round : null;
  const liveRound = busy && last?.role === "chairman" && progress?.round ? progress.round : null;
  const tableRound = liveRound ?? savedRound;
  const asking = tableRound && !tableRound.done && tableRound.questions.length ? tableRound : null;
  const aligned = !busy && savedRound?.done ? savedRound : null;
  const ledgerRound =
    liveRound ?? [...turns].reverse().find((t) => t.role === "founder" && t.round)?.round ?? null;
  const lastChanged = [...turns].reverse().find((t) => t.role === "founder")?.changed ?? [];

  // A new round starts from the Founder's own picks.
  const key = asking ? roundKey(asking) : "";
  useEffect(() => {
    if (asking && picks.key !== key) {
      setPicks({ key, picks: initialPicks(asking) });
      setRoundNote("");
    }
  }, [key]);

  // Follow the conversation, but only for a reader who is at the foot of it.
  // This used to scroll on every progress step the Founder reported, so
  // scrolling up to reread an earlier round lasted until the next step landed
  // — a second or two — and the page was dragged back down. Same rule the
  // boardroom keeps: whether you are following is decided by where you left
  // the page, and sending something puts you back at the end.
  //
  // Instant rather than smooth: a smooth scroll emits scroll events that
  // report "not at the bottom" mid-flight, which would unpin the reader the
  // page itself was moving.
  const following = useRef(true);
  useEffect(() => {
    const onScroll = () => {
      const doc = document.documentElement;
      following.current = doc.scrollHeight - window.scrollY - window.innerHeight < 120;
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  // The page's own foot, not a marker inside it: the composer sits below the
  // conversation, and stopping at a marker short of it would both hide the
  // place to type and leave the reader far enough up to count as unpinned.
  useLayoutEffect(() => {
    if (following.current) window.scrollTo(0, document.documentElement.scrollHeight);
  }, [turns.length, busy, progress?.steps.length, key]);

  async function post_(payload: { message: string; answers?: Answer[] }) {
    if (!thread) return;
    setBusy(true);
    setError(null);
    setProgress(null);
    try {
      setThread(await post<Thread>(`/admin/founder/threads/${thread.id}/say`, payload));
    } catch (err) {
      setBusy(false);
      if (err instanceof ApiError && err.status === 422) void loadStatus();
      setError((err as Error).message);
      throw err;
    }
  }

  // Answers queued during the slow half of a turn go the moment it ends.
  useEffect(() => {
    if (busy || !queued) return;
    const payload = queued;
    setQueued(null);
    void post_(payload).catch(() => {});
  }, [busy, queued]);

  async function send() {
    const text = message.trim();
    if (!text || busy) return;
    following.current = true;
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
      setReopening(false);
    } catch (err) {
      setMessage(text);
      setBusy(false);
      if (err instanceof ApiError && err.status === 422) void loadStatus();
      setError((err as Error).message);
    }
  }

  function answer() {
    if (!asking) return;
    const payload = { answers: toAnswers(asking, picks.picks), message: roundNote.trim() };
    following.current = true;
    if (busy) {
      setQueued(payload);
      return;
    }
    void post_(payload).catch(() => {});
  }

  function pick(id: string, value: Pick) {
    setPicks((cur) => ({ ...cur, picks: { ...cur.picks, [id]: value } }));
  }

  /** Reopen a settled decision: the composer, already addressed to it. */
  function reopen(topic?: string) {
    setReopening(true);
    setMessage(topic ? `About ${topic.toLowerCase()}: ` : "");
    setTimeout(() => composer.current?.focus(), 0);
  }

  async function stop() {
    if (!thread || stopping) return;
    // Answers come back as the round they answered, picks and all; only a
    // message typed into the composer needs putting back there.
    const sent = last?.role === "chairman" ? last : null;
    stopped.current = {
      turns: turns.length,
      message: sent && !sent.answers ? sent.text : "",
      note: sent?.answers ? sent.note ?? "" : "",
    };
    setStopping(true);
    setQueued(null);
    try {
      await post(`/admin/founder/threads/${thread.id}/stop`, {});
    } catch (err) {
      stopped.current = null;
      setStopping(false);
      setError((err as Error).message);
    }
  }

  async function startOver() {
    if (thread && !confirm("Throw this conversation away and start again?")) return;
    if (thread) {
      try {
        // Deleting a thread stops its turn on the server, so this is also the
        // way out of a turn that should never have started.
        await del(`/admin/founder/threads/${thread.id}`);
      } catch {
        /* it is going away from the screen either way */
      }
    }
    setThread(null);
    setMessage("");
    setError(null);
    setQueued(null);
    setReopening(false);
    setBusy(false);
    setProgress(null);
    stopped.current = null;
    setStopping(false);
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

  const openCount = asking?.questions.length ?? 0;

  return (
    <div className="founding">
      <header className="founding-head">
        <div>
          <Link className="crumb" to="/projects/new">&larr; Both ways to start</Link>
          <h1>Describe it, and yaaf drafts it</h1>
          <p className="subtitle">
            Describe what you want. The Founder drafts the whole thing — goal,
            shape, roster, rules — then asks about everything it had to guess,
            a few questions at a time, with its own answer already picked.
            Nothing is created until you say so on the next screen.
          </p>
        </div>
        <div className="founding-head-acts">
          {thread && (
            <button onClick={startOver}>
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
                it is for, and roughly what you are willing to spend. Whatever
                you leave out, the Founder asks about — with its recommended
                answer already chosen, so agreeing is one click.
              </p>
            </div>
          )}
          {turns.map((turn, i) => {
            const next = turns[i + 1];
            // Answers are read back on the round they answer, as question →
            // answer; the chairman's own turn then only carries his note.
            if (turn.answers) {
              return turn.note ? (
                <div key={i} className="turn turn-chairman">
                  <div className="turn-who">You</div>
                  <div className="turn-text">{turn.note}</div>
                </div>
              ) : null;
            }
            const round = turn.round;
            return (
              <div key={i} className={`turn turn-${turn.role}`}>
                <div className="turn-who">{turn.role === "chairman" ? "You" : "The Founder"}</div>
                {turn.text && (
                  <div className="turn-text">
                    {/* The Founder writes markdown — headings, bold, lists, backticked
                        constitution keys. Rendering it as source was making the most
                        carefully written thing on the page the hardest to read. The
                        chairman types plain text, so his turns stay verbatim. */}
                    {turn.role === "founder" ? <Markdown>{turn.text}</Markdown> : turn.text}
                  </div>
                )}
                {round && round.questions.length > 0 && next && (
                  <RoundRecap round={round} answers={next.answers ?? null} />
                )}
              </div>
            );
          })}

          {liveRound && asking && <div className="turn-who">The Founder</div>}
          {asking && (
            <RoundForm
              round={asking}
              picks={picks.key === key ? picks.picks : initialPicks(asking)}
              onPick={pick}
              note={roundNote}
              onNote={setRoundNote}
              onSubmit={answer}
              waiting={busy}
              queued={queued !== null}
            />
          )}
          {aligned && (
            <Aligned
              round={aligned}
              onConfirm={review}
              onReopen={() => reopen()}
              disabled={!thread?.draft_bundle}
            />
          )}

          {busy && <Steps progress={progress} onStop={stop} stopping={stopping} />}

          {error && <p className="error">{error}</p>}

          {/* One place to type at a time: while a round is open, its own note
              box is that place, and a second composer would be a second way
              to say the same thing with a different effect. */}
          {!asking && (!aligned || reopening) && (
            <div className="founding-compose">
              <textarea
                ref={composer}
                rows={turns.length ? 3 : 8}
                value={message}
                placeholder={
                  turns.length
                    ? aligned
                      ? "What's off? Name it and the Founder reopens whatever it touches…"
                      : "Push back, or tell it something it should know…"
                    : PLACEHOLDER
                }
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
          )}
        </section>

        <aside className="founding-draft">
          {ledgerRound && (
            <Ledger settled={ledgerRound.settled} open={openCount} onReopen={reopen} />
          )}
          <DraftPanel bundle={thread?.draft_bundle ?? null} changed={busy ? [] : lastChanged} />
          {thread?.draft_bundle && (
            <button className={asking || aligned ? "" : "primary"} onClick={review} disabled={busy}>
              Review &amp; create →
            </button>
          )}
          {thread?.draft_bundle && (
            <p className="muted">
              {openCount
                ? `${openCount} ${openCount === 1 ? "question is" : "questions are"} still open. ` +
                  "You can review now — the draft uses the Founder's picks for them."
                : "This opens the New Project screen with everything filled in, where " +
                  "you can change anything, run the fire drill for nothing, and create it."}
            </p>
          )}
        </aside>
      </div>
    </div>
  );
}
