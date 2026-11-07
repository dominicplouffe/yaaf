"""Filling a provider's empty classes from what its key lists, by price.

The long tail — xAI, Moonshot, Z.ai, Groq, Together — ships no lineup, because
no model of theirs could be checked from here and their lineups move monthly.
So every class on them started empty and every seat on one was refused (R8)
until an administrator typed a model into each. For a provider that sells its
own family of models (`Provider.family`), a key's own list, described and
priced by the feed (yaaf.core.model_upkeep), is enough to pick from. A host of
many vendors' models — Bedrock, Together, Fireworks, Groq — is left to the
administrator: its dearest model is no family's frontier, and names from
unrelated families say nothing about which is newer.

- **Candidates** are the models of the provider's own family on the key's list
  (Qwen's Model Studio resells DeepSeek and GLM; those are not Qwen's ladder),
  chat models that have a price, take
  tool calls (a seat cannot work without them) and are not deprecated — not a
  `-latest` alias, which moves under the class without anybody choosing, and
  not a multi-agent model, which the feed lists as another kind.
- **Grouped by price**, ranked on a blend of three input tokens to one output
  token: a seat rereads its context every call and writes a little.
- **Frontier** is the dearest group, **Fast** the cheapest, **Balanced** the
  middle one — the lower middle when the count is even, since Balanced is
  "the one you would run all day".
- **Within a group, the newest**: by the date the provider's list gives, else
  by the version in the name read as a decimal, so `grok-4.20-0309` sorts
  below `grok-4.3` — names are a guess at age and the list's dates are not,
  so the dates win wherever the list has them. Of a reasoning and a
  non-reasoning twin, Frontier and Balanced take the reasoning one and Fast
  the other.

Only an **empty** class is filled: never one an administrator named, never one
yaaf ships. A filled class stays on its model until somebody changes it —
every seat on the class moves with it, so yaaf does not move it on its own. It
says instead, on the readiness report, when it would now pick another
(`changed`), and moves it on one press (`repick`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from yaaf.core import model_policy, model_upkeep, prices
from yaaf.runtimes import catalog
from yaaf.runtimes.providers import PROVIDERS

#: Input tokens per output token in the blend a group is ranked by.
INPUT_WEIGHT = 3
_VERSION = re.compile(r"\d+(?:\.\d+)?")
_TWIN = re.compile(r"-(non-)?reasoning(?=-|$)")


@dataclass(frozen=True)
class Candidate:
    ref: str
    name: str
    input: float
    output: float
    created: int
    version: tuple

    @property
    def blended(self) -> float:
        return (INPUT_WEIGHT * self.input + self.output) / (INPUT_WEIGHT + 1)

    @property
    def twin(self) -> tuple[str, str | None]:
        """(the name without its reasoning mark, "reasoning" | "non" | None)."""
        found = _TWIN.search(self.name)
        if found is None:
            return self.name, None
        return _TWIN.sub("", self.name), "non" if found.group(1) else "reasoning"


def version(name: str) -> tuple:
    """The numbers in a model's name, the first read as a decimal ("4.20" is
    4.2, below "4.3"), the rest — a date suffix like 0309 — breaking ties. A
    guess at age for a list that gives no dates."""
    found = _VERSION.findall(name)
    if not found:
        return ()
    return (Decimal(found[0]), *(Decimal(n) for n in found[1:]))


def _deprecated(said: dict) -> bool:
    when = said.get("deprecated")
    try:
        return bool(when) and date.fromisoformat(str(when)[:10]) <= date.today()
    except ValueError:
        return False


def candidates(session: Session, provider: str, found: dict | None) -> list[Candidate]:
    """The models on a key's list (a model_upkeep discovery record) a class
    could be filled with."""
    family = PROVIDERS[provider].family if provider in PROVIDERS else ()
    if not found or not family:
        return []
    details, created = found.get("details") or {}, found.get("created") or {}
    out = []
    for name in found.get("models") or ():
        if not name.rsplit("/", 1)[-1].lower().startswith(family):
            continue
        said = details.get(name) or {}
        if said.get("kind") != "chat" or said.get("tools") is False or _deprecated(said):
            continue
        if "latest" in name.split("-"):
            continue
        ref = catalog.by_api_name(provider, name)
        price = prices.price(session, ref)
        if price is None:
            continue
        out.append(Candidate(ref=ref, name=name, input=price.input, output=price.output,
                             created=int(created.get(name) or 0), version=version(name)))
    return out


def _newest(group: list[Candidate], *, reasoning: bool) -> Candidate:
    best = max(group, key=lambda c: (c.created, c.version, c.name))
    base, _ = best.twin
    want = "reasoning" if reasoning else "non"
    twins = [c for c in group if c.twin == (base, want)]
    return max(twins, key=lambda c: (c.created, c.version)) if twins else best


def pick(found: list[Candidate]) -> dict[str, Candidate]:
    """Each class's model from the candidates, by the rule above."""
    groups: dict[tuple[float, float], list[Candidate]] = {}
    for c in found:
        groups.setdefault((c.input, c.output), []).append(c)
    ranked = sorted(groups.values(), key=lambda g: (g[0].blended, g[0].output))
    if not ranked:
        return {}
    chosen = {"fast": ranked[0], "balanced": ranked[(len(ranked) - 1) // 2],
              "frontier": ranked[-1]}
    return {cls: _newest(group, reasoning=cls != "fast") for cls, group in chosen.items()}


def _found(session: Session, provider: str, secret_id: str | None = None) -> dict | None:
    if secret_id is not None:
        return model_upkeep.discovered(session, secret_id)
    return model_upkeep.served(session).get(provider)


def fill(session: Session, provider: str, secret_id: str | None = None) -> dict[str, str]:
    """Fill the provider's empty classes from a key's list — the one just read,
    else the freshest — and return what was filled. Nothing while upkeep is
    off: then yaaf changes no model or price on its own."""
    if provider not in PROVIDERS or not PROVIDERS[provider].family \
            or not model_upkeep.enabled(session):
        return {}
    lineup = catalog.lineup_of(provider, model_policy.lineups(session))
    empty = [cls for cls in catalog.CLASSES if not lineup.get(cls)]
    if not empty:
        return {}
    picks = pick(candidates(session, provider, _found(session, provider, secret_id)))
    filled = {cls: picks[cls].ref for cls in empty if cls in picks}
    if filled:
        from yaaf.server.api.admin import _audit

        for cls, model in filled.items():
            model_policy.set_lineup(session, provider, cls, model, auto=True)
        _audit(session, None, "models.lineup.auto", "provider", provider,
               {"filled": filled, "rule": "price group, newest"})
    return filled


def changed(session: Session, provider: str) -> dict[str, tuple[str, Candidate]]:
    """The classes yaaf picked that it would now pick otherwise — a newer
    model at the price, or a new price group — as {class: (current, pick)}."""
    mine = model_policy.auto_picked(session).get(provider) or {}
    if not mine or provider not in PROVIDERS or not PROVIDERS[provider].family:
        return {}
    picks = pick(candidates(session, provider, _found(session, provider)))
    return {cls: (model, picks[cls]) for cls, model in mine.items()
            if cls in picks and picks[cls].ref != model}


def repick(session: Session, provider: str, cls: str) -> str | None:
    """Move a class yaaf picked to what it would pick now, keeping it yaaf's.
    None when it is not yaaf's, or there is nothing else to pick."""
    found = changed(session, provider).get(cls)
    if found is None:
        return None
    model_policy.set_lineup(session, provider, cls, found[1].ref, auto=True)
    return found[1].ref
