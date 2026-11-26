"""When the chairman asks the CEO a question, the answer is a conversation.

On 25 November 2026 the chairman of the dplouffe.ca blog wrote "what is the
best way of collaborating with you on this?", then "how can we change the posts
… to be more interesting, less like an AI wrote it? how can we pick more
current, more popular projects?". He wanted a partner to think it through with.
He got a finished process with a default-if-silent deadline, then three
rewritten briefs in thirty minutes, one of which made a template of a passing
remark ("every post opens with something Dominic ran"). His verdict: "that is
wrong and stupid."

Nothing was broken. Every rule the CEO carries makes his words into a decision
for him or an action for it: "when he asks for action, act", "write the brief
back the same turn", status in three sentences, decisions with a default and a
clock. There was no rule for thinking together, so a question became a brief.

Three things here, all keyed to the same fact the CEO does not control — the
chairman's newest boardroom message asks something:

- `brief_open_question`: a draft of the brief is refused while it does. The
  brief changes on what he decided, and a question is not a decision. The CEO
  answers, he says which change he wants, and that reply opens the door.
- `brief_already_drafted`: one draft per thing he said. A second draft with
  nothing new from him is the CEO's mind changing, not his.
- `max_words`: a reply to his question may run longer than a status. Seeing two
  example posts side by side does not fit in 150 words.
"""

from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yaaf.core.models import Approval, ApprovalKind, Channel, ChannelKind, Message
from yaaf.server import plain

#: A reply to his question. Twice a status, still one screen: room to set two
#: directions side by side and ask him one thing, not room for a memo — those
#: still go in a file.
DISCUSSION_MAX_WORDS = 300

# A note on a decision arrives as `Re: “<the decision's title>” — <his words>`.
# The title is the CEO's ("… cover three, or run over?"), not his question.
_NOTE_PREFIX = re.compile(r"^\s*Re:\s*“.*?”\s*[—-]\s*", re.S)
_CODE = re.compile(r"```.*?```|`[^`]*`", re.S)
_URL = re.compile(r"https?://\S+")


# Tuned against the 60 newest real boardroom messages, 24 November 2026.
#
# An open question opens like one, punctuated or not: the message that turned
# into a rule for every post read "How can we pick more current, more popular
# projects." — full stop. Filler he leads with is skipped.
_FILLER = r"(?:(?:so|ok|okay|and|but|maybe|well|also|then|now|hmm|yeah),?\s+)*"
_OPEN = re.compile(
    r"^\W*" + _FILLER +
    r"(?:how|why|which"
    r"|what(?:'s|\s+(?:is|are|was|do|does|did|should|would|could|can|if|about|else))"
    r"|(?:should|shall|do|does|did|is|are|was|were|have|has)"
    r"\s+(?:you|we|i|it|there|they)"  # not "do the post", an order
    r"|any\s+(?:ideas|thoughts|suggestions)|thoughts\b|help\s+me|let'?s\s+(?:think|talk))\b",
    re.I,
)
# "Can we remove it from the brief?" is an order said politely: the answer is
# to do it, not to talk it over.
_REQUEST = re.compile(r"^\W*" + _FILLER + r"(?:can|could|would|will)\s+(?:you|we)\b"
                      r"|^\W*" + _FILLER + r"please\b", re.I)
# Words that make the whole message an order, whatever else it asks: "Why do we
# post this in the change log.. don't do this anymore."
_ORDER = re.compile(r"\b(?:don'?t|do not|never|anymore|any more|no more|from now on"
                    r"|stop\s+\w+ing|stop\s+all)\b", re.I)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


def asks(text: str) -> bool:
    """Does this, in his own words, open a discussion? A sentence that asks an
    open question — by its words or by its question mark — in a message that
    does not also give an order. "How can we pick better projects? If we need
    tokens I will add some" ends on a statement and still asks; "can we remove
    it from the brief?" asks nothing that needs talking over."""
    text = _NOTE_PREFIX.sub("", text or "", count=1)
    text = _URL.sub(" ", _CODE.sub(" ", text))
    if _ORDER.search(text):
        return False
    for sentence in (s.strip() for s in _SENTENCE.split(text)):
        if not sentence or _REQUEST.match(sentence):
            continue
        if _OPEN.match(sentence) or sentence.endswith("?"):
            return True
    return False


def _boardroom_id(session: Session, project_id: str) -> str | None:
    return session.execute(
        select(Channel.id).where(Channel.project_id == project_id,
                                 Channel.kind == ChannelKind.BOARDROOM)
    ).scalar_one_or_none()


def last_word(session: Session, project_id: str) -> Message | None:
    """His newest boardroom message. The chairman is a set sender_user_id:
    platform lines have neither sender, and they are not him."""
    channel_id = _boardroom_id(session, project_id)
    if channel_id is None:
        return None
    return session.execute(
        select(Message).where(Message.channel_id == channel_id,
                              Message.sender_user_id.is_not(None))
        .order_by(Message.created_at.desc(), Message.id.desc()).limit(1)
    ).scalar_one_or_none()


def open_question(session: Session, project_id: str) -> Message | None:
    """His newest message, when it is not an order or an approval; else None.

    Read by yaaf.server.intent since 26 November 2026: this module's word test
    alone read "you may not change anything" as leave to act."""
    from yaaf.server import intent

    standing = intent.standing(session, project_id)
    if standing.acts or standing.reading is None or standing.reading.label == "info":
        # A fact or a greeting asks nothing to be talked through; the talk
        # lane still answers it without acting (yaaf.server.intent).
        return None
    return standing.message


def max_words(session: Session, project_id: str) -> int:
    """How long the CEO's reply to him may be right now."""
    return (DISCUSSION_MAX_WORDS if open_question(session, project_id) is not None
            else plain.MAX_MESSAGE_WORDS)


def brief_open_question(session: Session, project_id: str) -> str | None:
    """Why a brief draft must wait, or None. Read before filing one."""
    from yaaf.server import intent

    said = open_question(session, project_id)
    if said is None:
        return None
    opening = " ".join(_NOTE_PREFIX.sub("", said.body, count=1).split()[:12])
    reading = intent.stored(said)
    what = {"hold": "tells you to change nothing", "venting": "tells you how he feels",
            "info": "tells you something"}.get(reading.label if reading else "",
                                               "asks you something")
    return (
        f"Not filed. His last message {what} (\"{opening} …\"), and that is not a "
        "decision. Talk it through with him first. Say what you "
        "see, with the evidence: what got read and what did not, an example or two. "
        "Put two or three directions side by side and ask him the one thing you "
        "need to know. Give no deadline and no default for silence. When he tells "
        "you which change he wants, write the brief back then."
    )


def brief_already_drafted(session: Session, project_id: str) -> str | None:
    """Why a second draft with nothing new from him must wait, or None."""
    said = last_word(session, project_id)
    since: datetime | None = said.created_at if said is not None else None
    drafted = session.execute(
        select(func.max(Approval.created_at)).where(
            Approval.project_id == project_id, Approval.kind == ApprovalKind.BRIEF)
    ).scalar_one()
    if drafted is None or since is None or drafted < since:
        return None
    return (
        "Not filed. You already wrote back what he last said, and he has not "
        "spoken since. A new draft now is your mind changing, not his. If the "
        "draft on his desk is wrong, tell him in a line and let him answer; he "
        "can also edit it as he confirms it."
    )
