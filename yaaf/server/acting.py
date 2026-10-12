"""Who is doing the work in progress — one answer, bound once.

The audit trail used to learn its actor from a contextvar that defaulted to
`"user:admin"`, and from handlers writing `f"user:{project.chairman_user_id}"`
by hand. Both were true while one static bearer was the only human credential.
Neither is true once several people sign in: the default named nobody in
particular, and the chairman's id named the chairman for a co-chair's click.

So the actor is bound where the credential is read — per request from the
session or personal token, per MCP call from the grant, per agent call that
reaches into an admin helper — and everything that records "who" reads it from
here. A write made with nothing bound is stamped UNATTRIBUTED rather than
guessed, and the test harness fails any test that leaves one in the audit log.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass

log = logging.getLogger("yaaf.acting")

#: What a record says when no door bound an actor. Never a person's name: an
#: unattributed write is a bug in the door, and the record should say so rather
#: than pin it on somebody.
UNATTRIBUTED = "system:unattributed"


@dataclass(frozen=True)
class Actor:
    kind: str  # user | agent | system
    id: str
    #: The door, when it is not the person's own hand: "mcp:<client>" for a
    #: model client, "token:<name>" for a script holding a personal token.
    via: str | None = None

    @property
    def stamp(self) -> str:
        base = f"{self.kind}:{self.id}"
        return f"{base} via {self.via}" if self.via else base

    @property
    def user_id(self) -> str | None:
        return self.id if self.kind == "user" else None


_CURRENT: ContextVar[Actor | None] = ContextVar("yaaf_actor", default=None)


def person(user_id: str, via: str | None = None) -> Actor:
    return Actor("user", user_id, via)


def agent(agent_id: str) -> Actor:
    return Actor("agent", agent_id)


def bind(actor: Actor) -> Token:
    """Bind for the rest of this context — a request, in practice. Use
    :func:`acting` instead wherever the work has a natural end."""
    return _CURRENT.set(actor)


@contextmanager
def acting(actor: Actor) -> Iterator[Actor]:
    token = _CURRENT.set(actor)
    try:
        yield actor
    finally:
        _CURRENT.reset(token)


def current() -> Actor | None:
    return _CURRENT.get()


def stamp() -> str:
    actor = _CURRENT.get()
    if actor is None:
        log.error("a write was recorded with no actor bound; stamping %s", UNATTRIBUTED)
        return UNATTRIBUTED
    return actor.stamp


def user_id() -> str | None:
    """The person whose authority this work carries, if a person's."""
    actor = _CURRENT.get()
    return actor.user_id if actor is not None else None
