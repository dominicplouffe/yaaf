"""What the chairman meant, read once, and what the CEO may do about it.

On 26 November 2026 the chairman of the dplouffe.ca blog vented — "all the hero
images are bad, ugly and did not follow our orders. Now tell me honestly, how
should we correct your error" — and the CEO answered by drawing test pictures
on a paid image key and uploading one to his live site. Eight minutes later he
wrote "you may not change anything", and the CEO drafted a new brief.

The 24 November fix (yaaf.server.discussion) had read his first message right:
a question. But it only ever barred one verb, the brief, and its word list read
"you may not change anything" as permission. So a line is now read for what it
is, by a small model on the project's own provider, into one of six labels:

- order, approval: the CEO may act on it.
- hold, question, venting, info: the CEO answers, and acts on nothing.

The reading is kept on the message, so every gate reads the same one, and the
chairman can correct it with one click (`read_by` "chairman"). A model reading
below `ACT_AT` confidence does not let the CEO act: the cost of a wrong "read
only" is a reply that asks; the cost of a wrong "act" is a changed site.

Where no model can be reached on the project's key — a subscription that pays
only its CLI, a provider with no fast model — the word rules read it instead.
Where the call fails, the line is read as nothing the CEO may act on.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.models import (
    Channel,
    ChannelKind,
    LedgerEntry,
    LedgerKind,
    Message,
    Project,
    utcnow,
)

log = logging.getLogger("yaaf.intent")

LABELS = ("order", "approval", "hold", "question", "venting", "info")
ACTS = frozenset({"order", "approval"})
#: Below this a model's "order" is not trusted to act on. Measured, not
#: chosen: DeepSeek's fast model says 0.62 of most lines it is sure of, so 0.7
#: held back six orders it had read right, and 0.6 let through one more line
#: than 0.7 did — a setting the inbox banner wrote for him (evals/read_as.py).
ACT_AT = 0.6
#: How long the chat lane waits for a reading before it answers read-only.
TIMEOUT_S = 20.0
#: Room for a fast model that thinks before it answers: at 200, DeepSeek's
#: spent it all thinking on one line in five and returned no label.
MAX_TOKENS = 1024
RULES = "rules"
CHAIRMAN = "chairman"
FAILED = "failed"

INSTRUCTIONS = """You read one message the chairman of a company wrote to his CEO, and say \
what kind of message it is. You are shown the CEO's message just before it for context. \
Pick exactly one label:

- order: he tells the CEO to do, change or stop something ("publish them", "fix the brief \
please", "can we remove it from the brief?", "don't do this anymore", "do not remove this \
rule", "use this prompt from now on", "stop all work for now", "stand down all agents"). A \
polite request is an order. Repeating a rule he already gave, even angrily ("one day at a \
time!!!!!"), is an order. Feedback that says what to change is an order.
- approval: he says yes to, or picks between, something the CEO proposed or asked \
("Approve as it stands", "Keep the nine words", "No Home Assistant: start calendar \
awareness first instead").
- hold: he tells the CEO to leave things exactly as they are: change nothing, or stay as \
already stopped ("you may not change anything", "stay quiet until Sunday", "Stay paused"). \
Telling it to stop work that is running is an order, not a hold.
- question: he asks something and wants an answer or a conversation, not an action \
("what do you think?", "why are we out of runs?", "how should we fix this?", "what changed \
since we last spoke?", "don't stop or change anything, just answer me").
- venting: he expresses frustration or criticism without telling the CEO what to do ("all \
the hero images are bad and ugly", "that is wrong and stupid").
- info: he tells the CEO a fact, acknowledges, or greets ("I added the key", "ok, got it", \
"hello"), with nothing to do and nothing asked.

Only order and approval let the CEO act. When unsure between an acting label and another, \
choose the other. Call the `label` tool once."""

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "enum": list(LABELS)},
        "confidence": {"type": "number",
                       "description": "0 to 1: how sure you are of this label"},
    },
    "required": ["label", "confidence"],
}


@dataclass(frozen=True)
class Reading:
    label: str
    confidence: float
    by: str

    @property
    def acts(self) -> bool:
        if self.label not in ACTS:
            return False
        return self.by == CHAIRMAN or self.confidence >= ACT_AT


# ── the word rules: the floor where no model can be reached ────────────────────

_HOLD = re.compile(
    r"\b(?:(?:you\s+)?(?:may|must|can|should)\s*(?:not|n'?t)\s+(?:change|touch|do|act|"
    r"edit|publish|move)|do(?:n'?t| not)\s+(?:change|touch|act|do)\s+any|"
    r"leave\s+(?:it|them|everything)\b|stay\s+(?:paused|stopped|quiet|dark|down)|"
    r"stand\s+down|nothing\s+(?:moves|changes)|hold\s+off|keep\s+everything)\b",
    re.I)
_JUST_ANSWER = re.compile(r"\bjust\s+(?:answer|tell|explain)\b", re.I)
_ACK_WORD = (r"(?:ok(?:ay)?|got it|thanks?(?: you)?|cool|great|hello|hi|hey|"
             r"sounds good|fair|understood)")
_ACK = re.compile(r"^\W*" + _ACK_WORD + r"(?:[\s,.!]+" + _ACK_WORD + r")*\W*$", re.I)


def rules(text: str) -> Reading:
    """The word rules: yaaf.server.discussion's question test, with the hold
    it lacked. Never sure enough to act on alone except where it names an
    order outright."""
    from yaaf.server import discussion

    plain = (text or "").strip()
    if _JUST_ANSWER.search(plain):
        return Reading("question", 0.6, RULES)
    if _HOLD.search(plain):
        return Reading("hold", 0.7, RULES)
    if _ACK.match(plain):
        return Reading("info", 0.6, RULES)
    if discussion.asks(plain):
        return Reading("question", 0.6, RULES)
    # Not a question by the old test: an order, as it always was read. The
    # confidence clears ACT_AT so a no-model install keeps the behaviour it had.
    return Reading("order", ACT_AT, RULES)


# ── the model ──────────────────────────────────────────────────────────────────


class Unavailable(Exception):
    """No model this project's key can call from here."""


def _providers(session: Session, project: Project) -> list[str]:
    """Where to read his line, best first: the provider the CEO itself runs on
    (it is the CEO's conversation, and its key is the one known to work for
    it), then the project's primary, then any other it holds a key for. The
    dplouffe.ca blog bills a Claude subscription and runs its CEO on DeepSeek:
    the primary alone would have read nothing."""
    from yaaf.core import model_policy, project_credentials
    from yaaf.runtimes.seats import seat_for
    from yaaf.server import policy
    from yaaf.server.contextpack import role_version_for

    order: list[str] = []
    ceo = policy.ceo_of(session, project.id)
    if ceo is not None:
        try:
            seat = seat_for(ceo, role_version_for(session, ceo),
                            runtime=(project.integrations or {}).get("runtime"),
                            context=model_policy.context(session, project))
            order.append(seat.provider)
        except Exception as exc:  # noqa: BLE001 — a seat that will not resolve is not fatal here
            log.info("no CEO seat to read with on %s: %r", project.id, exc)
    order.append(model_policy.primary_provider(session, project))
    order += list(project_credentials.held(session, project))
    return list(dict.fromkeys(order))


def _endpoint(session: Session, project: Project):
    """(endpoint, model) for a Fast model this project's own key pays, or
    Unavailable naming why none does."""
    from yaaf.core import model_policy, model_tokens, project_credentials
    from yaaf.core.crypto import decrypt
    from yaaf.runtimes import catalog, wire
    from yaaf.runtimes.providers import PROVIDERS, spec

    why: list[str] = []
    for provider in _providers(session, project):
        seller = PROVIDERS.get(provider)
        secret = project_credentials.credential_for(session, project, provider)
        if seller is None or secret is None or seller.protocol is None:
            why.append(f"no API credential for {provider}")
            continue
        if (secret.llm_mode or "api_key") != "api_key":
            why.append(f"{seller.label} is paid by a subscription that pays only its CLI")
            continue
        model = model_policy.resolve(session, catalog.class_ref("fast"), project,
                                     provider=provider)
        if not model or catalog.is_class(model):
            why.append(f"{seller.label} has no Fast model")
            continue
        key = decrypt(secret.ciphertext)
        if seller.mints_tokens:
            try:
                key, _ = model_tokens.mint(seller.name, key)
            except model_tokens.MintError as exc:
                why.append(str(exc))
                continue
        return wire.Endpoint.from_spec(spec(seller, secret.llm_mode, secret.base_url), key), model
    raise Unavailable("; ".join(why) or "no provider")


def _prompt(body: str, ceo_before: str | None) -> str:
    before = " ".join((ceo_before or "").split())[-800:] or "(nothing yet)"
    return (f"The CEO's message just before:\n{before}\n\n"
            f"The chairman's message to label:\n{body[:4000]}")


async def _call(endpoint, model: str, prompt: str):
    from yaaf.runtimes import catalog, wire

    tool = wire.ToolSpec("label", "Say which label the message is.", _TOOL_SCHEMA)
    replies = [r async for r in wire.call(endpoint, catalog.api_name(model), INSTRUCTIONS,
                                          [wire.Turn("user", text=prompt)], [tool], MAX_TOKENS)]
    return replies[-1] if replies else None


def ask_model(session: Session, project: Project, body: str,
              ceo_before: str | None) -> tuple[Reading, object | None]:
    """The model's reading and the reply's usage. Raises Unavailable where no
    model can be called; any other failure is the caller's."""
    from yaaf.runtimes import wire

    endpoint, model = _endpoint(session, project)
    reply = wire.run_sync(asyncio.wait_for(_call(endpoint, model, _prompt(body, ceo_before)),
                                           TIMEOUT_S))
    if reply is None:
        raise RuntimeError("the model returned nothing")
    for call in reply.turn.calls:
        if call.name == "label" and call.input.get("label") in LABELS:
            try:
                confidence = max(0.0, min(1.0, float(call.input.get("confidence", 0))))
            except (TypeError, ValueError):
                confidence = 0.0
            return Reading(call.input["label"], confidence, model), reply.usage
    # Some fast models answer in text rather than call the tool.
    found = re.search(r"\b(" + "|".join(LABELS) + r")\b", reply.turn.text or "", re.I)
    if found:
        return Reading(found.group(1).lower(), 0.5, model), reply.usage
    raise RuntimeError(f"the model answered without a label: {(reply.turn.text or '')[:200]}")


#: The model reading, as a seam: the suite is offline and sets this to None,
#: which reads every line by the word rules, as an install with no API key does.
#: A test of the model path stands a function of the same shape in.
READER = ask_model


def _book(session: Session, project: Project, model: str, usage) -> None:
    """The reading is a model call the project pays for, booked like one."""
    from yaaf.core import prices

    if usage is None:
        return
    price = prices.price(session, model)
    usd = None
    if usage.cost_usd is not None:
        usd = usage.cost_usd
    elif price is not None:
        usd = (usage.input * price.input + usage.output * price.output
               + usage.cache_read * price.cache_read) / 1_000_000
    session.add(LedgerEntry(id=ids.new_id(ids.LEDGER), project_id=project.id,
                            kind=LedgerKind.MODEL_USAGE, tokens_in=usage.input,
                            tokens_out=usage.output, cache_read_tokens=usage.cache_read,
                            usd=usd, note=f"read the chairman's message ({model})",
                            at=utcnow()))


# ── reading a message, once ────────────────────────────────────────────────────


def _boardroom_id(session: Session, project_id: str) -> str | None:
    return session.execute(
        select(Channel.id).where(Channel.project_id == project_id,
                                 Channel.kind == ChannelKind.BOARDROOM)
    ).scalar_one_or_none()


def newest_from_him(session: Session, project_id: str) -> Message | None:
    """His newest boardroom line. Platform lines have no sender; they are not him."""
    channel_id = _boardroom_id(session, project_id)
    if channel_id is None:
        return None
    return session.execute(
        select(Message).where(Message.channel_id == channel_id,
                              Message.sender_user_id.is_not(None))
        .order_by(Message.created_at.desc(), Message.id.desc()).limit(1)
    ).scalar_one_or_none()


def _ceo_before(session: Session, message: Message) -> str | None:
    return session.execute(
        select(Message.body).where(Message.channel_id == message.channel_id,
                                   Message.sender_agent_id.is_not(None),
                                   Message.created_at < message.created_at)
        .order_by(Message.created_at.desc()).limit(1)
    ).scalar_one_or_none()


def stored(message: Message) -> Reading | None:
    if message.read_as in LABELS:
        return Reading(message.read_as, message.read_confidence or 0.0, message.read_by or "")
    return None


def read(session: Session, message: Message) -> Reading:
    """What this line of his was read as, reading it now if nothing has."""
    kept = stored(message)
    if kept is not None:
        return kept
    project = session.get(Project, message.project_id)
    try:
        if READER is None:
            raise Unavailable("no model reader on this install")
        reading, usage = READER(session, project, message.body,
                                _ceo_before(session, message))
        _book(session, project, reading.by, usage)
    except Unavailable as exc:
        log.info("reading %s by the word rules: %s", message.id, exc)
        reading = rules(message.body)
    except Exception as exc:  # noqa: BLE001 — any failure reads as "do not act"
        log.warning("could not read %s: %r", message.id, exc)
        fallback = rules(message.body)
        reading = Reading(fallback.label, 0.0, FAILED)
    message.read_as, message.read_confidence, message.read_by = (
        reading.label, reading.confidence, reading.by[:80])
    return reading


def correct(message: Message, label: str) -> Reading:
    """His own reading of his own line. It outranks any model's."""
    if label not in LABELS:
        raise ValueError(f"unknown label {label!r}")
    message.read_as, message.read_confidence, message.read_by = label, 1.0, CHAIRMAN
    return stored(message)


@dataclass(frozen=True)
class Standing:
    """Whether the CEO may act right now, and on what reading of what."""
    acts: bool
    message: Message | None
    reading: Reading | None


def standing(session: Session, project_id: str, *, keep: bool = False) -> Standing:
    """`keep` commits a reading made just now. A caller that is about to refuse
    must keep it: the refusal rolls the request back, the reading with it, and
    every retry would pay for the same line to be read again."""
    said = newest_from_him(session, project_id)
    if said is None:
        return Standing(True, None, None)
    fresh = stored(said) is None
    reading = read(session, said)
    if fresh and keep:
        session.commit()
    return Standing(reading.acts, said, reading)


_WHY = {
    "hold": "he told you not to change anything",
    "question": "he asked you something",
    "venting": "he is telling you how he feels, not what to do",
    "info": "he told you something, and asked for nothing",
}


def refusal(standing: Standing, what: str = "That") -> str:
    """The sentence an action gets while he is talking, not deciding."""
    said = standing.message.body if standing.message is not None else ""
    opening = " ".join(said.split()[:12])
    why = _WHY.get(standing.reading.label if standing.reading else "", "it is not an order")
    return (f"Not done. {what} would act, and his last message (\"{opening} …\") is not an "
            f"order: {why}. Answer him now, in the boardroom: what you see, with the "
            "evidence, and what you would change. Do not do it yet. When he tells you to, "
            "that message lets you act.")


def label_line(reading: Reading | None) -> dict | None:
    """The reading as the chat screen shows it under his message."""
    if reading is None:
        return None
    return {"label": reading.label, "confidence": round(reading.confidence, 2),
            "by": reading.by, "acts": reading.acts}


def dumps(reading: Reading | None) -> str:
    return json.dumps(label_line(reading))
