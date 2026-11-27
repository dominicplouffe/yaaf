"""How good the work has to be, read from the brief rather than asked for.

The new-project form used to ask it as four cards — Full send, Balanced, Keep
it cheap, Show me proof — beside the pace cards, and a chairman read the two
rows as one speed dial. Neither row was one. What the cards really asked is a
question the brief already answers: who reads the result, and what being wrong
costs. "A client will judge us by this" is high stakes whatever card is
clicked; "just curious which printer is best" is not.

So the bar is a reading of the brief: `{stakes, proof, quote}`, where `quote`
is the phrase it rests on, taken verbatim from the brief. One function settles
a reading into rules (`settle`), and both doors use it:

* the form, which asks a small model on the chosen credential (`read_brief`),
  falling back to word rules where no model can be called;
* the Founder, which is already a model reading the brief, and now has to say
  which phrase its stakes rest on.

The quote is what makes the reading checkable rather than asserted. A reading
whose quote is not in the brief is refused (`quote_problem`) — a rationale
nobody can find in what the chairman wrote is a rationale the model invented.
The money is not read here, and cannot be: a brief can say a client will read
the result, it cannot say whether the chairman will spend $20 or $2,000.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import asdict, dataclass

from sqlalchemy.orm import Session

from yaaf.core.constitution import BAR_PRESETS
from yaaf.core.models import Secret

log = logging.getLogger("yaaf.bar_reading")

STAKES = ("low", "normal", "high")
#: Which bar bundle each stakes level stands for. The bundles keep their names
#: so a project already on one is still recognised (`bar_name`).
BUNDLE_FOR = {"high": "full_send", "normal": "balanced", "low": "frugal"}
RULES = "rules"
DEFAULT = "default"
CHAIRMAN = "chairman"
TIMEOUT_S = 20.0
MAX_TOKENS = 1024


@dataclass(frozen=True)
class BarReading:
    stakes: str
    proof: bool
    #: The phrase of the brief the reading rests on, verbatim; None only for
    #: the default, which rests on nothing and says so.
    quote: str | None
    #: The model that read it, "rules", or "default".
    by: str

    def as_dict(self) -> dict:
        return asdict(self)


def settle(reading: BarReading) -> dict:
    """The quality rules a reading writes. No money among them: the bar
    bundles carry none (`constitution.BAR_RULES`), and the brief cannot say
    how much the chairman means to spend."""
    bundle = dict(BAR_PRESETS[BUNDLE_FOR[reading.stakes]])
    if reading.proof:
        bundle["require_evidence_verification"] = True
        bundle["drought_hours"] = BAR_PRESETS["show_me_proof"]["drought_hours"]
    return bundle


def _squash(text: str) -> str:
    return " ".join(text.lower().split()).strip(" .,;:!?\"'“”")


def quote_problem(goal: str, quote: str | None) -> str | None:
    """Why this quote cannot be what a stakes reading rests on, or None.

    Whitespace and case are forgiven; words are not. Long enough to be a
    reason rather than a single word that appears anywhere.
    """
    if not quote or not quote.strip():
        return "quote: name the phrase of the brief the stakes rest on"
    q = _squash(quote)
    if len(q) < 8:
        return f"quote: {quote!r} is too short to be a reason — quote the phrase it rests on"
    if q not in _squash(goal):
        return (f"quote: {quote[:80]!r} is not in the brief. Quote the chairman's own "
                "words, verbatim — a reason nobody can find in what he wrote is a reason "
                "the reader made up")
    return None


# ── the word rules: the floor where no model can be reached ────────────────────

_HIGH = re.compile(
    r"[^.!?\n]*\b(?:client|customer|paying|investor|board|public(?:ly)?|launch|production|"
    r"real money|real account|revenue|legal|contract|compliance|stranger|reputation|"
    r"live site|users? (?:will|depend))\b[^.!?\n]*", re.I)
_LOW = re.compile(
    r"[^.!?\n]*\b(?:curious|curiosity|just (?:wondering|a quick)|quick look|scratch|"
    r"throwaway|toy|experiment|for fun|rough idea)\b[^.!?\n]*", re.I)
_PROOF = re.compile(
    r"\b(?:verif(?:y|ied|iable)|evidence|prove|proof|cite|citations?|sources?|"
    r"fact-?check)\b", re.I)


def rules(goal: str) -> BarReading:
    """The word rules. Deliberately crude, and labelled as such (`by`)."""
    proof = bool(_PROOF.search(goal))
    for stakes, pattern in (("high", _HIGH), ("low", _LOW)):
        found = pattern.search(goal)
        if found:
            return BarReading(stakes, proof, " ".join(found.group(0).split())[:200], RULES)
    return BarReading("normal", proof, None, DEFAULT)


# ── the model ──────────────────────────────────────────────────────────────────

INSTRUCTIONS = """You read the brief for a project an AI team will carry out, and say how \
good the work has to be. Answer with the `bar` tool, once.

- stakes: "high" when a stranger will judge the person by the result, a client or paying \
customer receives it, real money or a live system rides on it, or being wrong is expensive; \
"low" when it is a scratch job, a curiosity, an experiment whose failure costs an \
afternoon; otherwise "normal". Most work is normal.
- proof: true only when the brief says claims must be verified, cited or proven.
- quote: the shortest phrase of the brief, copied EXACTLY, that your stakes rest on. If \
nothing in the brief points either way, choose "normal" and quote the goal's first clause.

Do not judge how much it should cost: that is the chairman's number, not the brief's."""

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "stakes": {"type": "string", "enum": list(STAKES)},
        "proof": {"type": "boolean"},
        "quote": {"type": "string"},
    },
    "required": ["stakes", "proof", "quote"],
}


class Unavailable(Exception):
    """No model this credential can call from here."""


def _endpoint(session: Session, secret: Secret | None):
    """(endpoint, model) for a Fast model this credential pays, or Unavailable."""
    from yaaf.core import model_policy, model_tokens
    from yaaf.core.crypto import decrypt
    from yaaf.runtimes import catalog, wire
    from yaaf.runtimes.providers import PROVIDERS, spec

    if secret is None:
        raise Unavailable("no credential chosen")
    seller = PROVIDERS.get(secret.provider or "")
    if seller is None or seller.protocol is None:
        raise Unavailable(f"no API for {secret.provider}")
    if (secret.llm_mode or "api_key") != "api_key":
        raise Unavailable(f"{seller.label} is paid by a subscription that pays only its CLI")
    model = model_policy.resolve(session, catalog.class_ref("fast"), None,
                                 provider=secret.provider)
    if not model or catalog.is_class(model):
        raise Unavailable(f"{seller.label} has no Fast model")
    key = decrypt(secret.ciphertext)
    if seller.mints_tokens:
        key, _ = model_tokens.mint(seller.name, key)
    return wire.Endpoint.from_spec(spec(seller, secret.llm_mode, secret.base_url), key), model


def ask_model(session: Session, secret: Secret | None, goal: str) -> BarReading:
    from yaaf.runtimes import catalog, wire

    endpoint, model = _endpoint(session, secret)
    tool = wire.ToolSpec("bar", "Say how good the work has to be.", _TOOL_SCHEMA)

    async def call():
        replies = [r async for r in wire.call(
            endpoint, catalog.api_name(model), INSTRUCTIONS,
            [wire.Turn("user", text=f"The brief:\n{goal[:6000]}")], [tool], MAX_TOKENS)]
        return replies[-1] if replies else None

    reply = wire.run_sync(asyncio.wait_for(call(), TIMEOUT_S))
    if reply is None:
        raise RuntimeError("the model returned nothing")
    for tool_call in reply.turn.calls:
        if tool_call.name == "bar" and tool_call.input.get("stakes") in STAKES:
            return BarReading(tool_call.input["stakes"], bool(tool_call.input.get("proof")),
                              str(tool_call.input.get("quote") or ""), model)
    raise RuntimeError("the model answered without a reading")


#: The model reading, as a seam: the suite is offline and sets this to None,
#: which reads every brief by the word rules, as an install with no API key does.
READER = ask_model


def read_brief(session: Session, goal: str, secret: Secret | None) -> BarReading:
    """The brief, read once. A model reading whose quote is not in the brief
    falls back to the word rules rather than being shown: the line on the form
    has to be one the chairman can check against his own words."""
    if READER is not None:
        try:
            reading = READER(session, secret, goal)
            if quote_problem(goal, reading.quote) is None:
                return reading
            log.info("model reading quoted outside the brief; using the word rules")
        except Unavailable as exc:
            log.info("reading the brief by the word rules: %s", exc)
        except Exception as exc:  # noqa: BLE001 — any failure reads by the rules
            log.warning("could not read the brief: %r", exc)
    return rules(goal)


def reading_problem(goal: str, constitution: dict, quality: dict | None) -> str | None:
    """Why a create request's stated reading cannot stand, or None.

    Checked at the create door so the form and the Founder are held to the
    same thing: the reading names a real stakes level, its quote is in the
    brief (unless it is the labelled default), and the constitution being
    created actually carries the stakes it claims.
    """
    if quality is None:
        return None
    stakes = quality.get("stakes")
    if stakes not in STAKES:
        return f"quality.stakes: {stakes!r} is not one of low, normal, high"
    # His own pick, or a setup file's, needs no quote: it is a decision, not a
    # reading. The labelled default rests on nothing and says so.
    if quality.get("by") not in (DEFAULT, CHAIRMAN) and (
            problem := quote_problem(goal, quality.get("quote"))):
        return f"quality.{problem}"
    if constitution.get("stakes", "normal") != stakes:
        return (f"quality: the brief was read as {stakes} stakes but the constitution "
                f"says {constitution.get('stakes', 'normal')!r}; one of them is wrong")
    return None
