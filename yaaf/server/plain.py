"""Whether a piece of text the chairman reads is plain enough to read cold.

The rule was a prompt for months — "plain English, short sentences", "three
sentences is the size of a message", "every message must make sense on its own"
(`contextpack.chairman_writing`, `contextpack.HOUSE_STYLE`) — and it was read,
agreed with and not followed. What came back was this, posted to the boardroom
as one message:

    One flag on your screen and I am raising it rather than folding it into
    tonight's slot, because it is about the desk sitting empty: at 9:21am your
    time there was 1 task on the board and 0 claimable, because I hold
    Tuesday's MCP-server post until 6:35pm Monday so it does not go into
    WordPress three days early — that gate is deliberate, but it left my
    writer with nothing while the two fixes on that same post were still
    undone, so the fixes are now their own claimable job (…) and the desk is
    not empty any more.

That is ONE sentence of 112 words. The three-sentence ceiling was honoured to
the letter: the model met it by gluing every clause it had into three
sentences with colons, dashes and "because … because … so". A sentence count
is the wrong number; the one that tracks "I had to concentrate to read this"
is the length of the longest sentence, and the length of the whole message.

So both are measured here, and over either one the post is refused with what
to fix — the same way the report cadence stopped being a suggestion. A rule an
agent must remember is a suggestion; a refusal it has to rewrite against is
not.

Scoped to words a PERSON reads (the CEO's boardroom posts, its questions to
the chairman, heads-ups). Agent-to-agent traffic is untouched: the chairman
said the abstraction there is not what he minds.
"""

from __future__ import annotations

import re

#: The longest sentence a reader takes in without going back over it. Plain-
#: language guidance puts the comfortable average at 15–20 words; 30 is the
#: ceiling, not the target, so an ordinary sentence never trips it.
MAX_SENTENCE_WORDS = 30

#: The whole of a boardroom message. "Three sentences" for a status is about
#: 60 words; the decision shape in `chairman_writing` (what is going on, why,
#: two or three options, a recommendation, what happens if he says nothing)
#: is about six short lines. 150 holds both with room, and nothing else: the
#: message above was 252 words.
MAX_MESSAGE_WORDS = 150

# Things that are one "word" to a reader however they are spelled, and must
# not end a sentence on their own dots: code, links, URLs, numbers like 1.5.
_CODE = re.compile(r"```.*?```|`[^`]*`", re.S)
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+")
_DECIMAL = re.compile(r"(\d)\.(\d)")
_ABBREV = re.compile(r"\b(e\.g|i\.e|etc|vs|approx|Mr|Ms|Dr|St|No)\.", re.I)
# A sentence ends at . ! ? (optionally closed by a quote or bracket) before
# whitespace, or at a line break — a list item or a heading is its own line.
_SENTENCE_END = re.compile(r"(?<=[.!?])[\"'”’)\]]*\s+|\n+")
_WORD = re.compile(r"[A-Za-z0-9$€£%][\w$€£%'’,.\-/:]*")


def _sentences(text: str) -> list[str]:
    text = _CODE.sub(" code ", text)
    text = _LINK.sub(r"\1", text)
    text = _URL.sub("link", text)
    text = _DECIMAL.sub(r"\1\2", text)
    text = _ABBREV.sub(r"\1", text)
    return [s.strip() for s in _SENTENCE_END.split(text) if s and s.strip()]


def words(text: str) -> int:
    return sum(len(_WORD.findall(s)) for s in _sentences(text))


def problems(text: str, *, whole_message: bool = True) -> list[str]:
    """What makes this hard to read cold, one plain line each. Empty = fine.

    `whole_message=False` checks sentence length only — for a field that is
    one part of a larger structured item (a question's options, its default),
    where the platform, not the writer, decides how the parts add up.
    """
    found: list[str] = []
    for sentence in _sentences(text):
        n = len(_WORD.findall(sentence))
        if n > MAX_SENTENCE_WORDS:
            opening = " ".join(sentence.split()[:8])
            found.append(f"A {n}-word sentence (\"{opening} …\"). Break it up: one idea "
                         f"per sentence, {MAX_SENTENCE_WORDS} words at most.")
    if whole_message:
        total = words(text)
        if total > MAX_MESSAGE_WORDS:
            found.append(f"{total} words in all; a message to him is {MAX_MESSAGE_WORDS} "
                         "at most. Cut what would not change what he does next, or put "
                         "the detail in a file and name it in one line.")
    return found


#: Names that mean something only inside the platform: a requirement's key
#: (R-07) and a row's id (flag_018f…, cycl_x). A sentence can be short and
#: still unreadable cold when the thing it is about is called by one of these.
_KEY = re.compile(r"\bR-\d+\b")
_ROW_ID = re.compile(r"\b[a-z]{2,5}_[0-9a-f]{6,}\b")

#: The platform's own nouns for how work is organised. Fine in a sentence that
#: explains; not as the thing a headline is about, because a headline in these
#: words reads the same for every project — "a cycle ended with scope open" —
#: and the chairman cannot tell from it what happened to HIS.
_PLATFORM_NOUNS = re.compile(
    r"\b(cycles?|scope|requirements?|obligations?|flags?)\b", re.I)


def named_by_id(text: str) -> list[str]:
    """IDs where a name belongs, one plain line each. Empty = fine."""
    found = sorted(set(_KEY.findall(text)) | set(_ROW_ID.findall(text)))
    if not found:
        return []
    return [f"It names {', '.join(found[:4])} — he does not know what that is. Say "
            "what the thing is (\"the weekly results report\", \"the price check\")."]


def abstract_headline(text: str) -> list[str]:
    """A headline that names a category rather than the event. Empty = fine."""
    nouns = sorted({m.lower() for m in _PLATFORM_NOUNS.findall(text)})
    if not nouns:
        return []
    return [f"The headline is in platform words ({', '.join(nouns)}). Say what "
            "happened to what, in his words: \"The weekly results report did not go "
            "out at 6am\", \"Last week ended with the server schedule unfinished\"."]


def refusal_text(found: list[str]) -> str:
    return (
        "Not sent — the chairman would have to read this twice. "
        + " ".join(found)
        + " Rewrite it plain: the answer first, short sentences, one idea each. Do not "
        "join clauses with dashes, colons, semicolons or \"because … so …\" to stay "
        "under a sentence count — that is what made this hard to read. Name things by "
        "what they are, not by a platform word, a time-slot or an ID. Say only what "
        "changed, the number that shows it, and what (if anything) he needs to do."
    )
