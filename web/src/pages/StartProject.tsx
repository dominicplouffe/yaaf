// The one door into project creation.
//
// There used to be three: "+ Design a project" and "Fill the form myself" as
// two verbs wedged into a sidebar of four nouns, and a "+ New project" button
// on the portfolio that skipped both and dropped you straight on the form. So
// the product asked the chairman to choose between a conversation and a form
// before it had told him what either one was, gave the two answers names that
// described our implementation rather than his situation, and then pointed
// every other create affordance in the app at one of them anyway — the wrong
// one for a first project.
//
// This screen is that question, asked once, in the place the answer is needed,
// with the trade named on each side. It creates nothing and spends nothing;
// both paths land on the same review screen, and either can be abandoned for
// the other without losing the choice you already made.

import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { get } from "../api";

/** One card on the new-project form: a named bundle of constitution rules,
    with the words that explain it. Served rather than mirrored — these used to
    live in NewProject.tsx and nowhere else, so nothing but that screen could
    offer one and the Settings page could not tell you which one you were on. */
export interface PresetCard {
  value: string;
  label: string;
  blurb: string;
  example?: string;
  /** Tempo cards only: what this costs, in the one unit that maps to money. */
  headline?: string;
  constitution: Record<string, unknown>;
}

export interface FounderStatus {
  credential_configured: boolean;
  billing_mode: string;
  model: string;
  /** What a run has actually cost on this install; null until five are priced. */
  avg_usd_per_run: number | null;
  presets: { autonomy: PresetCard[]; bar: PresetCard[]; tempo: PresetCard[] };
}

export default function StartProject() {
  const navigate = useNavigate();
  // Whether the Founder can actually talk. The old flow only discovered this
  // after the chairman had committed to the conversation and landed on a key
  // form he did not ask for; here it is on the card, before the click.
  const [status, setStatus] = useState<FounderStatus | null>(null);
  const [statusFailed, setStatusFailed] = useState(false);

  useEffect(() => {
    get<FounderStatus>("/admin/founder/status")
      .then(setStatus)
      // An unreachable status endpoint must not hide the recommended path —
      // the conversation's own screen asks for the key if it is missing.
      .catch(() => setStatusFailed(true));
  }, []);

  const keyed = status?.credential_configured ?? false;
  const unknown = !status && !statusFailed;

  return (
    <>
      <h1>Start a project</h1>
      <p className="subtitle">
        Two ways in, one destination: both end on the same review screen, and
        nothing exists — or costs anything — until you sign it there.
      </p>

      <div className="startpick">
        <button
          type="button"
          className="startcard rec"
          onClick={() => navigate("/projects/new/describe")}
        >
          <div className="sc-head">
            <h2>Describe it, and yaaf drafts it</h2>
            {unknown ? null : keyed ? (
              <span className="chip accent">recommended</span>
            ) : (
              <span className="chip warn">needs a key first</span>
            )}
          </div>
          <p className="sc-lede">
            Write a paragraph about what you want to exist. The Founder comes
            back with the whole project — the goal, the shape, who it hires,
            what it may spend, the rules it runs under — and tells you what it
            assumed. You argue with it until it is right.
          </p>
          <ul className="sc-facts">
            <li>
              <b>Choose this if</b> it is your first project, or you know the
              outcome you want but not the org that gets you there.
            </li>
            <li>
              <b>Costs</b> a few minutes of the Founder's thinking per draft,
              on the platform key — never a project's budget.
            </li>
            <li>
              <b>Ends</b> on the same form, filled in, where you change anything
              you like before creating it.
            </li>
          </ul>
          <span className="sc-go">
            {keyed || unknown
              ? "Describe it →"
              : "Set up the key, then describe it →"}
          </span>
        </button>

        <button
          type="button"
          className="startcard"
          onClick={() => navigate("/projects/new/form")}
        >
          <div className="sc-head">
            <h2>Fill it in myself</h2>
          </div>
          <p className="sc-lede">
            The form, in four steps: the job, the org, the money and the rules,
            then review. Defaults everywhere, a fire drill before you commit,
            and everything on one page whenever you would rather see it all.
          </p>
          <ul className="sc-facts">
            <li>
              <b>Choose this if</b> you have run a project here before, or you
              already know the goal and who you want on it.
            </li>
            <li>
              <b>Costs</b> nothing but your time — about ten minutes, most of
              it on the goal.
            </li>
            <li>
              <b>Ends</b> on review, with the fire drill run for free against
              what you wrote.
            </li>
          </ul>
          <span className="sc-go">Open the form &rarr;</span>
        </button>
      </div>

      {/* A third way in, and a rarer one: it is not a way of *deciding* what
          the project should be, it is a project already decided somewhere
          else. Giving it a card of equal size would have made the real
          question a three-way one. */}
      <p className="startalt">
        Already have a setup file exported from another project?{" "}
        <Link to="/projects/new/form?start=file">Load it into the form</Link> —
        everything prefills and you review it.
      </p>

      {status && !keyed && (
        <p className="help startnote">
          The Founder needs a key, set once — one you already added for
          projects will do. It pays for drafting and is never handed to a
          project's agents; every project still pays its own way. You can set
          it on the way in, or in <Link to="/settings">Settings</Link>.
        </p>
      )}
    </>
  );
}
