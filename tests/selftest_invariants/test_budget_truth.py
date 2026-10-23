"""Invariant selftests: every screen says where spending actually stops.

The budget is a plan. Crossing it records an overrun the CEO must account for
and stops nothing; the project pauses itself at `hard_stop_multiple` times the
budget — three times it, on every preset. The CEO's context pack was corrected
to say so long ago. The chairman's screens were not: the Spend page, the
creation form, Settings and the Inbox all said the project pauses at 100%,
so a chairman who set $1,000 read $1,000 as the most he could be charged
while the wall stood at $3,000.

The rules under attack here:

1. The cross-project rollup carries the wall, so a row can state it without a
   second request.
2. No dashboard source claims the project pauses at the cap or at 100%.
"""

from __future__ import annotations

import pathlib
import re

from tests.conftest import ADMIN, amend, make_project


def _row(client, project_id: str) -> dict:
    desk = client.get("/observe/desk", headers=ADMIN).json()
    return next(p for p in desk["projects"] if p["id"] == project_id)


def test_the_rollup_carries_the_wall_not_only_the_plan(client):
    project = make_project(client)
    assert _row(client, project["id"])["budget"]["hard_stop_multiple"] == 3.0


def test_and_it_moves_when_the_chairman_moves_it(client):
    project = make_project(client)
    amend(client, project["id"], reason="tighter wall", hard_stop_multiple=1.5)
    assert _row(client, project["id"])["budget"]["hard_stop_multiple"] == 1.5


# "pauses (itself|the project) at the cap" and "pauses … at 100%" — the two
# shapes the wrong sentence took, each of which a chairman reads as the wall.
FALSE_WALL = re.compile(
    r"pauses?\s+(?:itself\s+|the\s+project\s+(?:by\s+itself\s+)?)?at\s*"
    r"(?:the\s+cap|(?:<b>)?\s*100%)", re.IGNORECASE)


def test_no_screen_says_the_project_pauses_at_the_budget():
    web = pathlib.Path(__file__).resolve().parents[2] / "web" / "src"
    # The whole file, not line by line: JSX wraps a sentence wherever the
    # column runs out, and "pauses itself at" / "the cap" on two lines is the
    # same sentence to the person reading it.
    wrong = [f"{path.relative_to(web)}:{text.count(chr(10), 0, m.start()) + 1}: "
             f"{' '.join(m.group(0).split())}"
             for path in sorted(web.rglob("*.ts*"))
             for text in [path.read_text()]
             for m in FALSE_WALL.finditer(text)]
    assert not wrong, "\n".join(wrong)
