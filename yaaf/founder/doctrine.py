"""What doctrine a project was founded under, and whether it is still current.

The Founder runs once. It writes a goal and a set of role charters, and those
become the text every agent on the project reads on every wake for the life of
the project. So a fix to the Founder reaches exactly the projects founded after
it, and no others — which is the whole of why this problem kept coming back.

Six serious, well-aimed fixes landed between 30 August and 3 September. The desk
the chairman was actually watching was founded on 26 August. It received none of
them, and will run to GW38 — nine months — on the doctrine of that day, unless
something notices.

This module is that something. The stamp is a **content fingerprint**, not a
version number somebody has to remember to bump: a version number is a promise
to do a chore later, and the chore is exactly what was skipped six times. Change
a word of the doctrine and every project founded before that word becomes
visibly older, with no ceremony.

It is deliberately not a gate. A project running old doctrine is not broken and
must not be stopped or silently rewritten mid-season — it is a project whose
chairman should be told, so he can decide.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_HERE = Path(__file__).resolve().parent
#: Everything the Founder is taught. Adding a file here is how a new piece of
#: doctrine joins the fingerprint.
DOCTRINE_FILES = ("DESIGN_DOCTRINE.md", "GOAL_GRAMMAR.md")


def fingerprint() -> str:
    """A short, stable digest of the doctrine as it stands right now."""
    digest = hashlib.sha256()
    for name in DOCTRINE_FILES:
        path = _HERE / name
        digest.update(name.encode())
        digest.update(path.read_bytes() if path.exists() else b"")
    return digest.hexdigest()[:12]


def is_current(stamp: str | None) -> bool:
    """An unstamped project is not current: it predates the stamp itself, which
    is the population this exists to find."""
    return bool(stamp) and stamp == fingerprint()
