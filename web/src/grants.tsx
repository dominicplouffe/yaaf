import type React from "react";
import { teamHue } from "./ui/kit";

/** Who receives something, drawn one way everywhere.

    The same team used to appear three ways on one page: as a card listing its
    people ("Data · data-analyst · Kim, Theo"), as a checkbox ("Data
    (data-analyst)"), and as a slug pill ("data-analyst"). The cards were the
    right instinct — a chairman knows who should have a key, not what the role
    is called — but drawn per team inside every credential row they made a row
    410px tall. This keeps the people and loses the height: one line per team,
    a switch, its colour, its people in grey.

    Grants are per role, so an option is a role; `name` is the team(s) that
    run it. A grant already held by a role nobody here has is shown so it can
    be removed — hiding it would make a dead grant permanent and invisible. */
export interface GrantOption {
  slug: string;
  /** The team name(s) running this role, as the reader knows them. */
  name: string;
  people?: string[];
  /** A short grey tag: "plans, does not build", "asked for this". */
  tags?: { text: string; tone?: "warn" | "accent" }[];
  /** The key a team's colour is derived from — the team name, everywhere. */
  hue?: string;
}

export function TeamDot({ hue }: { hue: string | null | undefined }) {
  return <span className={`team-dot${teamHue(hue)}`} aria-hidden="true" />;
}

/** The compact form, for a list row: up to three teams, then "+2". People on
    hover, because a row is for scanning and the drawer is for reading. */
export function GrantChips({ options, selected, max = 3 }: {
  options: GrantOption[];
  selected: string[];
  max?: number;
}) {
  if (selected.length === 0)
    return <span className="chip warn">Nobody yet</span>;
  const named = selected.map((slug) => options.find((o) => o.slug === slug)
    ?? { slug, name: slug, people: [], hue: slug, stranger: true });
  const shown = named.slice(0, max);
  const rest = named.slice(max);
  return (
    <span className="gchips">
      {shown.map((o) => (
        <span key={o.slug} className={`gchip${"stranger" in o ? " stranger" : ""}`}
              title={"stranger" in o ? "Nobody on this project has this role"
                : (o.people ?? []).join(", ") || o.name}>
          <TeamDot hue={o.hue ?? o.name} />
          {o.name}
        </span>
      ))}
      {rest.length > 0 && (
        <span className="gchip more" title={rest.map((o) => o.name).join(", ")}>
          +{rest.length}
        </span>
      )}
    </span>
  );
}

export function TeamSwitches({
  options, selected, onChange, disabled, what = "it", label = "Who gets it", emptyHint,
  credential = true,
}: {
  options: GrantOption[];
  selected: string[];
  onChange: (slugs: string[]) => void;
  disabled?: boolean;
  /** What arrives, for the sentence under the list: "$WORDPRESS", "its tools". */
  what?: React.ReactNode;
  label?: React.ReactNode;
  emptyHint?: React.ReactNode;
  /** A value can leak into chat; a data source's tools cannot, so the
      sentence only promises what is true of the thing being granted. */
  credential?: boolean;
}) {
  const strangers = selected.filter((slug) => !options.some((o) => o.slug === slug));
  const toggle = (slug: string) =>
    onChange(selected.includes(slug) ? selected.filter((s) => s !== slug) : [...selected, slug]);
  const all = options.length > 0 && options.every((o) => selected.includes(o.slug));

  if (options.length === 0 && strangers.length === 0) {
    return (
      <div className="tswitch-field">
        <div className="tswitch-head"><span className="lbl">{label}</span></div>
        <p className="help">
          {emptyHint ?? "No teams yet — add one on the Team page first. A grant to no one reaches no one."}
        </p>
      </div>
    );
  }

  const reached = options.filter((o) => selected.includes(o.slug));
  const people = reached.flatMap((o) => (o.people?.length ? o.people : [o.name]));
  return (
    <div className="tswitch-field">
      <div className="tswitch-head">
        <span className="lbl">{label}</span>
        {options.length > 1 && (
          <button type="button" className="linklike" disabled={disabled}
                  onClick={() => onChange(all
                    ? strangers
                    // Every team that exists now. Never "and whoever is hired
                    // later": that would hand a key to a hire nobody approved.
                    : [...new Set([...selected, ...options.map((o) => o.slug)])])}>
            {all ? "Clear all" : "Every team"}
          </button>
        )}
      </div>
      <div className="tswitch">
        {options.map((o) => {
          const on = selected.includes(o.slug);
          return (
            <label key={o.slug} className={`tswitch-row${on ? " on" : ""}`}>
              <input type="checkbox" role="switch" checked={on} disabled={disabled}
                     onChange={() => toggle(o.slug)} />
              <span className="sw" aria-hidden="true" />
              <span className="tswitch-name">
                <TeamDot hue={o.hue ?? o.name} />
                <b>{o.name}</b>
                {o.people && o.people.length > 0 && (
                  <span className="tswitch-people">{o.people.join(", ")}</span>
                )}
              </span>
              {o.tags?.map((t) => (
                <span key={t.text} className={`tswitch-tag${t.tone ? ` ${t.tone}` : ""}`}>{t.text}</span>
              ))}
            </label>
          );
        })}
        {strangers.map((slug) => (
          <label key={slug} className="tswitch-row on stranger">
            <input type="checkbox" role="switch" checked disabled={disabled}
                   onChange={() => toggle(slug)} />
            <span className="sw" aria-hidden="true" />
            <span className="tswitch-name"><b>{slug}</b>
              <span className="tswitch-people">nobody on this project has this role</span>
            </span>
            <span className="tswitch-tag warn">reaches no one</span>
          </label>
        ))}
      </div>
      {people.length > 0 ? (
        <p className="tswitch-sum">
          {listJoin(people)} {people.length === 1 ? "gets" : "get"} {what} on the next run.
          Nobody else does{credential ? ", and it never appears in chat" : ""}.
        </p>
      ) : (
        <p className="tswitch-sum none">
          {strangers.length > 0
            ? <>Nobody — the only role granted is one this project does not have.</>
            : <>Nobody gets {what} yet. It is stored, but no sandbox receives it until you switch a team on.</>}
        </p>
      )}
    </div>
  );
}

export function listJoin(items: string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}
