"""Prefixed, time-sortable identifiers.

Every row gets an id like ``task_018f2c3a9b4d5e6f7a8b9c0d``: a type prefix so ids
are self-describing in logs and traces, a millisecond timestamp so ids sort by
creation time, and random bits so they never collide.
"""

from __future__ import annotations

import secrets
import time

# Entity prefixes. Keep in sync with the models that use them.
USER = "user"
PROJECT = "proj"
CONSTITUTION = "cons"
AMENDMENT = "amnd"
ROLE = "role"
ROLE_VERSION = "rolv"
TEAM = "team"
AGENT = "agnt"
CHANNEL = "chan"
MESSAGE = "msg"
TASK = "task"
TASK_EVENT = "tske"
REQUIREMENT = "reqt"
REQUIREMENT_EVENT = "rqte"
REQUIREMENT_EDGE = "rqed"
HYPOTHESIS = "hypo"
EVIDENCE = "evid"
DECISION = "decn"
MEMORY = "mem"
LEDGER = "ledg"
RUN = "run"
SIDE_EFFECT = "sfx"
APPROVAL = "appr"
SECRET = "secr"
AUDIT = "audt"
NOTICE = "ntce"
SUGGESTION = "sugg"


def new_id(prefix: str) -> str:
    """A prefixed id: 12 hex chars of milliseconds since epoch + 12 random hex chars."""
    return f"{prefix}_{int(time.time() * 1000):012x}{secrets.token_hex(6)}"
