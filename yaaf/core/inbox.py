"""Words two sides of the desk share, kept where importing them costs nothing.

The Inbox's keys for the rows that are not one database row, and the reason a
project paused at its spending wall gives. The dashboard builds the keys
(`itemsFor` in web/src/desk.tsx) and selects `?item=` and snoozes by exact
match, so a key spelled differently anywhere opens or hides some other
decision; `tests/selftest_invariants/test_inbox_links.py` holds the spellings
together. Here rather than in yaaf.server.notify because `yaaf signal` reads
them every few seconds from a status bar, and notify brings the database
models with it.
"""

from __future__ import annotations

#: How a project paused at the wall says why. A reader that has to tell this
#: pause from the others — `yaaf signal` lights the bar for it, because only
#: the chairman restarts it — reads this rather than matching the sentence.
HARD_STOP = "hard stop"


def signoff_item(project_id: str) -> str:
    return f"{project_id}-signoff"


def scope_item(project_id: str) -> str:
    return f"{project_id}-plan"


def commitment_item(project_id: str) -> str:
    return f"{project_id}-commitment"


def verdict_item(project_id: str, requirement_key: str) -> str:
    return f"{project_id}-verdict-{requirement_key}"


def frozen_item(project_id: str) -> str:
    return f"{project_id}-frozen"
