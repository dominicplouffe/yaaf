"""Who a published file is written for, and the form that reader is owed.

Every file an agent publishes is read by one of two kinds of reader, and they
want opposite things.

**People** — the chairman, a client, the public. On 11 Nov 2026, 17 Markdown
files were published across the deployment in the day after a restart, and a
person opened 2 of them. A long document a person did not ask for is not
read, so its length buys nothing. The chairman said it plainly: "If the
findings are for humans, then a long document is completely useless, unless
the chairman is asking for it." So a file for people is one screen by default
— the answer, the number, what to do — and it is long only when he asked for
long, in words the platform can find (`asked_for`).

**Agents** — the CEO, a teammate, the next run. They are the readers who
actually read: in two days the Abani analysts read Markdown notes back in full
334 times. Each read lands in a context that is re-sent on every later step of
that run, so a note's length is paid for again on each one. An agent does not
need prose to follow a finding. It needs the facts the prose was wrapped
around, and the qualifiers that keep them true:

* the answer, first, in a line it can lift;
* every number with its unit, the window it covers, and where it came from
  — the "~16% since Prime Day" this platform spent a day reconciling was
  three different windows wearing one number;
* the caveats and negatives, stated as such ("no US rows", "not seasonally
  adjusted") — terse rewriting drops these first, and they are what stop the
  next reader misusing the number;
* where the evidence is: the script that computed it, the data file, the
  source. The script IS the method; reciting it in prose is a second copy.

Rows, tables and quotes past a screenful are data, and go in a CSV or JSON
beside the note, read by code. That is why data files carry no word rule here
and prose does.

Both readers keep the plain-English rule. Dense is not cryptic: no invented
abbreviations, no private shorthand — an agent that cannot tell what a
column means will guess, and the guess is the expensive part.

Words are counted the way the chairman's messages are (`plain.words`), on
prose files only; a dataset is never refused for its size here.
"""

from __future__ import annotations

import re

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from yaaf.core.models import Artifact, ChairmanNote, MailMessage, Message, Project, Reader
from yaaf.server import plain

#: One screen: the answer, the number behind it, what to do, and a link to the
#: detail. A printed page of plain prose is about 500 words.
PEOPLE_MAX_WORDS = 500

#: A note another agent reads in full and then carries for the rest of its
#: run — about 2,000 tokens. The median published file on this deployment was
#: about 1,600 words when this was set, almost all of it written for people
#: who did not open it; a note this size holds a finding, its numbers and its
#: caveats, with the rows in a data file beside it.
AGENTS_MAX_WORDS = 1500

#: The fewest words a quote must have to count as the chairman asking. Two
#: words ("full report") occur everywhere by accident; four do not.
ASKED_FOR_MIN_WORDS = 4

#: The Slack marker on a note for agents. The bridge pushes every row whose
#: marker is empty; this one was never the chairman's to receive.
NOT_FOR_PEOPLE = "for-agents"

#: Files that are prose, and so are read rather than processed.
PROSE_EXTENSIONS = (".md", ".markdown", ".txt", ".text", ".rst", ".html", ".htm")

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_EDGE = re.compile(r"^[\s\"'“”‘’«».,;:!?()\[\]-]+|[\s\"'“”‘’«».,;:!?()\[\]-]+$")

#: What every agent is told — in the house style, and in artifact_put's own
#: description, so the rule is in front of it at the moment it writes.
PROMPT = (
    "WHO READS IT SETS THE FORM. Before you write a file, name its reader. "
    "FOR AGENTS (the CEO, a teammate, the next run — most of what you write): "
    "no narrative and no prose for its own sake. Open with the answer in one or "
    "two lines. Then the facts as short lines or a table: every number with its "
    "unit, the window it covers and its source; the caveats and what is NOT "
    "known, stated plainly; the path of the script and data that produced it. "
    "Do not restate the method — the script is the method. Plain words, no "
    "invented abbreviations. Rows, tables and quotes past a screenful go in a "
    f"CSV or JSON beside the note. A note for agents is {AGENTS_MAX_WORDS:,} words "
    "at most. "
    "FOR PEOPLE (the chairman, a client, the public): one screen, "
    f"{PEOPLE_MAX_WORDS} words at most — the answer, the number that shows it, "
    "what he should do, and a link to the note for agents if he wants the "
    "detail. Longer ONLY when the chairman asked for a long piece (a full "
    "report, a 2,000-word post, a book chapter): then write the length he "
    "asked for, and pass his words, quoted exactly, as asked_for when you "
    "publish. "
    "WHEN YOU READ a note, read its opening lines first and open the rest only "
    "for the fact you need: everything you read stays in your context for the "
    "rest of the run."
)


def for_people():
    """The rows a person is meant to see as results: every file not marked as
    a note for agents, including every row from before the field existed."""
    return or_(Artifact.reader.is_(None), Artifact.reader != Reader.AGENTS)


def is_prose(name: str) -> bool:
    return name.lower().endswith(PROSE_EXTENSIONS)


def prose_words(name: str, data: bytes) -> int | None:
    """Words in a prose file, or None when it is not prose (or not text)."""
    if not is_prose(name):
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if name.lower().endswith((".html", ".htm")):
        text = _TAG.sub(" ", text)
    return plain.words(text)


def _norm(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return _SPACE.sub(" ", text).strip().lower()


def chairman_wrote(session: Session, project: Project, quote: str) -> bool:
    """Whether these exact words appear in something a person wrote to this
    project: its goal, its standing orders, a message a person posted, a note
    the chairman left, or an email a listed correspondent sent in.

    Exact, not similar. A paraphrase is the agent's reading of what he wanted,
    and the whole point is that the agent does not get to license its own
    length."""
    needle = _norm(_EDGE.sub("", quote))
    if len(needle.split()) < ASKED_FOR_MIN_WORDS:
        return False
    sources = [project.goal or "", *(project.standing_orders or [])]
    sources += session.execute(
        select(Message.body).where(Message.project_id == project.id,
                                   Message.sender_user_id.is_not(None))
    ).scalars().all()
    sources += session.execute(
        select(ChairmanNote.body).where(ChairmanNote.project_id == project.id)
    ).scalars().all()
    sources += session.execute(
        select(MailMessage.body).where(MailMessage.project_id == project.id,
                                       MailMessage.direction == "in")
    ).scalars().all()
    return any(needle in _norm(str(s)) for s in sources if s)


def refusal(session: Session, project: Project, *, name: str, data: bytes,
            reader: str, asked_for: str | None) -> str | None:
    """Why this file cannot be published as it is, in words the agent can
    rewrite against — or None when it can."""
    words = prose_words(name, data)
    if words is None:
        return None
    if reader == Reader.AGENTS:
        if words <= AGENTS_MAX_WORDS:
            return None
        return (
            f"Not published: {name} is {words:,} words, and a note for agents is "
            f"{AGENTS_MAX_WORDS:,} at most — every agent that reads it carries all of "
            "it for the rest of its run. Keep the answer, the numbers with their "
            "units, windows and sources, the caveats, and the paths to the script "
            "and data. Cut the narrative and any restated method. Move rows, tables "
            "and quotes into a CSV or JSON beside it, and publish that too."
        )
    if words <= PEOPLE_MAX_WORDS:
        return None
    if asked_for and asked_for.strip():
        if chairman_wrote(session, project, asked_for):
            return None
        return (
            f"Not published: asked_for must be the chairman's own words, quoted "
            f"exactly and at least {ASKED_FOR_MIN_WORDS} words long, from the "
            "project goal, a standing order, something he posted or a note he left "
            f"— and {asked_for.strip()[:120]!r} is not in any of them. If he did not "
            f"ask for a long piece, this is a one-screen file of {PEOPLE_MAX_WORDS} "
            "words at most."
        )
    return (
        f"Not published: {name} is {words:,} words for a person to read, and he "
        f"did not ask for a long piece — a file for people is one screen, "
        f"{PEOPLE_MAX_WORDS} words at most. Lead with the answer and the number "
        "that shows it, say what he should do, and stop. Put the detail in a note "
        "for agents (reader \"agents\") or a data file and link it in one line. "
        "If the chairman did ask for a long piece, pass his words, quoted exactly, "
        "as asked_for."
    )
