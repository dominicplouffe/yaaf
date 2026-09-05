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
CREW = "crew"
TEAM = "team"
AGENT = "agnt"
CHANNEL = "chan"
MESSAGE = "msg"
TASK = "task"
TASK_EVENT = "tske"
REQUIREMENT = "reqt"
REQUIREMENT_EVENT = "rqte"
REQUIREMENT_EDGE = "rqed"
REQUIREMENT_RESULT = "rqcr"  # one standing requirement, judged for one cycle
FLAG = "flag"  # an obligation to speak, opened by the platform
SPEND_WINDOW = "spwn"  # a funded period for measuring what work costs
OVERRUN = "ovrn"  # a month that went past its plan, and the account of why
COMMITMENT = "cmit"
ARTIFACT = "arti"
AUDIT_SAMPLE = "asmp"
REVIEW = "revw"
CYCLE = "cycl"
HYPOTHESIS = "hypo"
EVIDENCE = "evid"
DECISION = "decn"
MEMORY = "mem"
LEDGER = "ledg"
RUN = "run"
SIDE_EFFECT = "sfx"
APPROVAL = "appr"
ASK = "ask"  # a question to the chairman, with the moment it stops waiting
SECRET = "secr"
AUDIT = "audt"
NOTICE = "ntce"
SUGGESTION = "sugg"
ACCOUNT = "acct"
PLATFORM_SETTING = "pset"
FOUNDING_THREAD = "fnd"
MCP_CLIENT = "mcpc"  # a model client holding the chairman's eyes
MCP_TOKEN = "mcpt"
OAUTH_CLIENT = "oacl"
OAUTH_CODE = "oacd"
MCP_CONFIRMATION = "mcpx"


def new_id(prefix: str) -> str:
    """A prefixed id: 12 hex chars of milliseconds since epoch + 12 random hex chars."""
    return f"{prefix}_{int(time.time() * 1000):012x}{secrets.token_hex(6)}"
