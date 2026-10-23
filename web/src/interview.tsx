// The founding interview: the draft, split into decisions.
//
// Every question arrives with the Founder's answer already chosen, so agreeing
// costs nothing and disagreeing costs one click. That is what lets the Founder
// be relentless — nothing silently assumed — without the chairman ever filling
// in a form: a round he agrees with is a single button.

import { useEffect, useRef } from "react";
import { Badge } from "./ui/kit";

export interface RoundOption {
  label: string;
  detail?: string;
}

export interface Question {
  id: string;
  title: string;
  question: string;
  options: RoundOption[];
  /** Index into options. Null only for who pays: it spends somebody's money. */
  recommended: number | null;
  affects?: string;
}

export interface Settled {
  topic: string;
  decision: string;
  source: "brief" | "answer" | "assumed";
}

export interface Round {
  number?: number;
  questions: Question[];
  settled: Settled[];
  done: boolean;
}

export interface Answer {
  id: string;
  choice: number | null;
  text: string | null;
}

/** One question's state on the page. `other` is "in my own words". */
export interface Pick {
  choice: number | null;
  other: boolean;
  text: string;
}

export type Picks = Record<string, Pick>;

export function initialPicks(round: Round): Picks {
  return Object.fromEntries(
    round.questions.map((q) => [q.id, { choice: q.recommended, other: false, text: "" }]),
  );
}

/** A round's identity, so answers half-given survive the round moving from
 *  the live turn onto the saved thread. */
export function roundKey(round: Round): string {
  return round.questions.map((q) => `${q.id}:${q.title}`).join("|");
}

export function toAnswers(round: Round, picks: Picks): Answer[] {
  return round.questions.map((q) => {
    const p = picks[q.id] ?? { choice: q.recommended, other: false, text: "" };
    const text = p.other ? p.text.trim() : "";
    return { id: q.id, choice: text ? null : p.choice, text: text || null };
  });
}

function changedFromRecommendation(q: Question, p: Pick | undefined): boolean {
  if (!p) return false;
  if (p.other) return p.text.trim().length > 0;
  return p.choice !== q.recommended;
}

function isOpen(q: Question, p: Pick | undefined): boolean {
  if (!p) return q.recommended === null;
  if (p.other) return p.text.trim().length === 0;
  return p.choice === null;
}

/** The open round: one card per question, the recommendation preselected. */
export function RoundForm({
  round,
  picks,
  onPick,
  note,
  onNote,
  onSubmit,
  waiting,
  queued,
}: {
  round: Round;
  picks: Picks;
  onPick: (id: string, pick: Pick) => void;
  note: string;
  onNote: (text: string) => void;
  onSubmit: () => void;
  /** The Founder is still revising the draft from the last answers. */
  waiting: boolean;
  /** Sent already, held until it is done. */
  queued: boolean;
}) {
  const qs = round.questions;
  const changed = qs.filter((q) => changedFromRecommendation(q, picks[q.id])).length;
  const open = qs.filter((q) => isOpen(q, picks[q.id])).length;

  // The button says what pressing it means, so "yes to everything" is a
  // sentence the chairman reads rather than a default he has to infer.
  let label: string;
  if (changed === 0 && open === 0) {
    label = qs.length === 1 ? "Yes, go with that" : `Yes to all ${qs.length}`;
  } else if (changed === 0 && open === qs.length) {
    label = "Leave it open for now";
  } else if (changed === 0) {
    label = `Yes to ${qs.length - open}, leave ${open} open`;
  } else {
    label = `Send answers · ${changed} changed`;
  }

  return (
    <div className="round">
      <div className="round-head">
        <span className="round-title">
          {round.number ? `Round ${round.number}` : "Next round"} · {qs.length}{" "}
          {qs.length === 1 ? "question" : "questions"}
        </span>
        <span className="muted">My pick is already chosen — change only what's wrong.</span>
      </div>

      {qs.map((q, i) => (
        <QuestionCard
          key={q.id}
          n={i + 1}
          q={q}
          pick={picks[q.id] ?? { choice: q.recommended, other: false, text: "" }}
          onPick={(p) => onPick(q.id, p)}
        />
      ))}

      <textarea
        className="round-note"
        rows={2}
        value={note}
        placeholder="Anything else? Optional — say it the way you'd say it to a person."
        onChange={(e) => onNote(e.target.value)}
      />

      <div className="round-foot">
        <span className="muted">
          {queued
            ? "Sent. It goes to the Founder the moment it finishes the draft."
            : waiting
              ? "The Founder is still writing the draft. Answer now — it goes the moment the draft is done."
              : open
                ? `${open} left open — the Founder will ask again.`
                : "Every answer lands in the draft on the right."}
        </span>
        <button className="primary" onClick={onSubmit} disabled={queued}>
          {queued ? "Queued…" : label}
        </button>
      </div>
    </div>
  );
}

function QuestionCard({
  n,
  q,
  pick,
  onPick,
}: {
  n: number;
  q: Question;
  pick: Pick;
  onPick: (p: Pick) => void;
}) {
  const otherRef = useRef<HTMLTextAreaElement | null>(null);
  useEffect(() => {
    if (pick.other) otherRef.current?.focus();
  }, [pick.other]);
  const name = `q-${q.id}`;

  return (
    <fieldset className="question">
      <legend className="question-head">
        <span className="question-n">{n}</span>
        <span className="question-title">{q.title}</span>
        {q.affects && <span className="question-affects">moves {q.affects}</span>}
      </legend>
      <p className="question-body">{q.question}</p>
      {q.recommended === null && (
        <p className="question-yours muted">Your call — this one spends money, so I don't pick it.</p>
      )}
      <div className="question-options" role="radiogroup" aria-label={q.title}>
        {q.options.map((o, i) => {
          const on = !pick.other && pick.choice === i;
          return (
            <label key={i} className={`option${on ? " on" : ""}`}>
              <input
                type="radio"
                name={name}
                checked={on}
                onChange={() => onPick({ ...pick, choice: i, other: false })}
              />
              <span className="option-text">
                <span className="option-label">{o.label}</span>
                {o.detail && <span className="option-detail">{o.detail}</span>}
              </span>
              {q.recommended === i && <Badge tone="accent">Recommended</Badge>}
            </label>
          );
        })}
      </div>
      {/* His own words are always one click away, but drawn as a way out
          rather than as a fourth option: on a round of four questions, four
          extra rows is what turns a conversation into a form. */}
      {pick.other ? (
        <div className="option-own">
          <textarea
            ref={otherRef}
            rows={2}
            value={pick.text}
            placeholder="In your own words"
            onChange={(e) => onPick({ ...pick, text: e.target.value })}
          />
          <button type="button" className="link" onClick={() => onPick({ ...pick, other: false, text: "" })}>
            Use an option instead
          </button>
        </div>
      ) : (
        <button type="button" className="link option-own-open" onClick={() => onPick({ ...pick, other: true })}>
          Something else…
        </button>
      )}
    </fieldset>
  );
}

/** A round already answered, read back as question → answer. */
export function RoundRecap({ round, answers }: { round: Round; answers: Answer[] | null }) {
  const by = new Map((answers ?? []).map((a) => [a.id, a]));
  return (
    <div className="round round-past">
      <div className="round-head">
        <span className="round-title">
          {round.number ? `Round ${round.number}` : "Round"} · {round.questions.length}{" "}
          {round.questions.length === 1 ? "question" : "questions"}
        </span>
        {!answers && <span className="muted">Answered in your own words</span>}
      </div>
      {answers && (
        <ul className="recap">
          {round.questions.map((q) => {
            const a = by.get(q.id);
            const choice = a ? a.choice : q.recommended;
            const said = a?.text
              ? `“${a.text}”`
              : choice !== null && choice !== undefined
                ? q.options[choice]?.label
                : null;
            const agreed = !a?.text && choice === q.recommended && choice !== null;
            return (
              <li key={q.id}>
                <span className="recap-q">{q.title}</span>
                <span className={`recap-a${said ? "" : " muted"}`}>
                  {said ?? "left open"}
                  {agreed && <span className="recap-mark" title="My recommendation"> ✓</span>}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/** The frontier is empty: confirm the ledger, or reopen it. */
export function Aligned({
  round,
  onConfirm,
  onReopen,
  disabled,
}: {
  round: Round;
  onConfirm: () => void;
  onReopen: () => void;
  disabled: boolean;
}) {
  return (
    <div className="round round-done">
      <div className="round-head">
        <span className="round-title">Nothing left to ask</span>
      </div>
      <p>
        We settled {round.settled.length} decisions. They're on the right, and every one of
        them is in the draft. If that's the project, review it and create it.
      </p>
      <div className="round-foot">
        <button onClick={onReopen} disabled={disabled}>
          Something's off
        </button>
        <button className="primary" onClick={onConfirm} disabled={disabled}>
          That's the project — review &amp; create →
        </button>
      </div>
    </div>
  );
}

const SOURCE: Record<Settled["source"], string> = {
  brief: "your brief",
  answer: "you chose",
  assumed: "my assumption",
};

/** What is decided so far — the interview's product, beside the draft it
 *  produced. An assumption is marked as one, because it is a decision the
 *  chairman has not seen yet. */
export function Ledger({
  settled,
  open,
  onReopen,
}: {
  settled: Settled[];
  open: number;
  onReopen: (topic: string) => void;
}) {
  if (settled.length === 0) return null;
  return (
    <div className="card ledger">
      <div className="ledger-head">
        <h3>What we've settled</h3>
        <span className="muted">
          {settled.length} settled{open ? ` · ${open} open` : ""}
        </span>
      </div>
      <ul>
        {settled.map((e, i) => (
          <li key={i} className={`ledger-row ledger-${e.source}`}>
            <div className="ledger-topic">
              {e.topic}
              <span className="ledger-source">{SOURCE[e.source] ?? e.source}</span>
            </div>
            <div className="ledger-decision">{e.decision}</div>
            <button className="ledger-change" onClick={() => onReopen(e.topic)}>
              Change
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
