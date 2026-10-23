"""What doctrine a project was founded under, and whether it is still current.

The Founder runs once. It writes a goal and a set of role charters, and those
become the text every agent on the project reads on every wake for the life of
the project. So a fix to the Founder reaches exactly the projects founded after
it, and no others — which is the whole of why this problem kept coming back.

Six serious, well-aimed fixes landed between 30 August and 3 September. The desk
the chairman was actually watching was founded on 26 August. It received none of
them, and will run to GW38 — nine months — on the doctrine of that day, unless
something notices.

The stamp is that something's record. It is a **content fingerprint**, not a
version number somebody has to remember to bump: a version number is a promise
to do a chore later, and the chore is exactly what was skipped six times.

It is not what the chairman reads. A fingerprint moving says the Founder's text
changed, not that this project is missing anything — the edit that taught the
Founder to interview in rounds moved it on every project, and none of them can
be interviewed again. What a running project is owed is each fix that applies
to it, said about it: no seat to disagree with the plan, a rhythm with no
standing scope, a pace that does not match the cycle, a role with a newer
charter. Those are findings on the health card, each with its way out. A
doctrine change that should reach running projects ships one more of them.

It is deliberately not a gate either. A project set up under older doctrine is
not broken and must not be stopped or silently rewritten mid-season.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_HERE = Path(__file__).resolve().parent
#: Everything the Founder is taught. Adding a file here is how a new piece of
#: doctrine joins the fingerprint.
DOCTRINE_FILES = ("DESIGN_DOCTRINE.md", "GOAL_GRAMMAR.md")

#: Where a doctrine file asks for the pace cards as they stand right now.
#: Substituted at read time rather than typed into the markdown, for the reason
#: `constitution._rule` already records: a hand-kept copy of a card goes stale,
#: and a stale card is worse than no card because it is believed. What prompted
#: it was the other half of the same problem — the file told the Founder to copy
#: a card verbatim, and no copy of one existed anywhere the Founder could read,
#: so a real run spent its whole turn ceiling guessing the rule names.
TEMPO_MARK = "<!-- tempo-cards -->"


def text(name: str) -> str:
    """One doctrine file as its readers see it, cards and all.

    Both drafting doors read through here — the Founder's system prompt and the
    MCP `yaaf://docs/design-doctrine` resource — so neither can be handed a
    different document. That is the invariant one level up, and it is why the
    substitution lives here rather than in either reader.
    """
    path = _HERE / name
    try:
        raw = path.read_text()
    except OSError:  # pragma: no cover - only if the wheel loses its package data
        return f"{name} is not present in this install."
    return raw.replace(TEMPO_MARK, tempo_cards()) if TEMPO_MARK in raw else raw


def tempo_cards() -> str:
    """The pace cards, written out of the rules the platform actually stamps.

    Every number here comes from `TEMPO_PRESETS` and every sentence from
    `TEMPO_COPY`, which is what the new-project form draws from too. A reader
    that writes the constitution by hand — the MCP door does — needs the rules;
    a reader that names the card — the Founder does — needs the sentence it
    owes the chairman about what the card costs. Both are below, once.
    """
    from yaaf.core.constitution import TEMPO_RULES, preset_cards

    cards = preset_cards()["tempo"]
    blurbs = "\n".join(
        f"- **`{card['value']}`** — {card['headline']}. {card['blurb']} {card['example']}"
        for card in cards
    )
    header = "| Rule | " + " | ".join(f"`{card['value']}`" for card in cards) + " |"
    rows = "\n".join(
        f"| `{rule}` | "
        + " | ".join(_written(card["constitution"].get(rule)) for card in cards)
        + " |"
        for rule in TEMPO_RULES
    )
    return f"{blurbs}\n\n{header}\n{'|---' * (len(cards) + 1)}|\n{rows}"


def _written(value) -> str:
    """How a rule's value is written into a constitution — `null` included.

    `ceo_daily_at` is genuinely null on the live card, and a card is only
    chosen when every one of its rules is written down (`stated_tempo`). Drawn
    as an em dash it reads as "leave this out", which is the one reading that
    produces a draft the doctrine check then refuses.
    """
    return "`null`" if value is None else f"`{value}`"


def fingerprint() -> str:
    """A short, stable digest of the doctrine as it stands right now.

    Of the doctrine as it is *read*, not as it is stored: the pace cards are
    part of what the Founder is taught, and a card whose numbers changed is a
    project founded on older doctrine even though no markdown moved.
    """
    digest = hashlib.sha256()
    for name in DOCTRINE_FILES:
        digest.update(name.encode())
        digest.update(text(name).encode())
    return digest.hexdigest()[:12]
