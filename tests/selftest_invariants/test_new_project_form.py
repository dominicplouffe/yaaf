"""Invariant selftests: the new-project form keeps the promises it prints.

Three traps sat on the shortest path through the form — a goal, one role,
every default — and the suite
stayed green through all three:

1. "Short name — picked for you" on the first step, and a Name nobody picked.
   Create waited for the chairman to type one, and any other door that sent
   a blank name stored it, so the project's heading was empty on every screen.
2. Every quality card asks for the work to be read by somebody who did not
   write it, the preselected one included, and the server refuses a roster
   that hires nobody who can. The refusal is right and stays. What was wrong
   was meeting it at Create, in a paragraph about `deliverable_review`, after
   the step that chose the roster had said nothing.
3. A role hired with its team name cleared was dropped on the way to the
   server, and the people typed into it with it — a project with fewer teams
   than the form showed, silently.

The server halves are attacked over HTTP like every other rule. The form
halves are TypeScript this suite cannot run, so they are held against the
source the way `test_web_hygiene.py` holds the rest of the cockpit: by naming
what has to be there, and failing when it is not.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.conftest import ADMIN, seed_role
from yaaf.core.db import db_session
from yaaf.core.models import Project
from yaaf.server.api import admin

PAGES = Path(__file__).resolve().parents[2] / "web" / "src" / "pages"
FORM = PAGES / "NewProject.tsx"
START = PAGES / "StartProject.tsx"


def _library(client) -> None:
    seed_role(client, "ceo", is_ceo=True)
    seed_role(client, "worker")


def _create(client, slug: str, **fields):
    return client.post("/admin/projects", json={
        "slug": slug, "name": slug.title(),
        "goal": "Book our February trip — I want a confirmation number.",
        "constitution": {"deliverable_review": "none"},
        "teams": [{"role_slug": "worker", "name": "Desk", "employees": ["Ada"]}],
        **fields,
    }, headers=ADMIN)


def _between(source: str, start: str, end: str) -> str:
    """The source from `start` to the first `end` after it."""
    at = source.index(start)
    return source[at:source.index(end, at + len(start))]


# ── 1. the name it says it picks ───────────────────────────────────────────────


@pytest.mark.parametrize(("goal", "picked"), [
    ("Book our February trip — I want a confirmation number.", "Book our February trip"),
    ("Audit the customer's Amazon PPC account and produce a plain-English deck.",
     "Audit the customer's Amazon PPC account"),
    ("publish a weekly newsletter for indie game developers, every Friday.",
     "Publish a weekly newsletter for indie game developers"),
    ("Find an idea and get one sale in 60 days.", "Find an idea"),
    ("A security audit of our AWS estate.", "A security audit of our AWS estate"),
    ("Write the history of the company for its fiftieth year in business, "
     "with interviews.", "Write the history of the company"),
])
def test_a_name_is_the_opening_clause_of_the_goal(goal, picked):
    """The goal is the one thing the chairman has always written by the time a
    name is needed, and its first clause says what the project is for."""
    assert admin.name_from_goal(goal) == picked


def test_a_blank_name_is_picked_from_the_goal_never_stored(client):
    """Any door that sends no name — the form before it picked one, an agent
    on the MCP surface, a hand-written spec — used to store the blank, and the
    project's heading was empty on every screen after."""
    _library(client)
    for slug, sent in (("empty", ""), ("spaces", "   ")):
        created = _create(client, slug, name=sent)
        assert created.status_code == 200, created.text
        with db_session() as session:
            assert session.get(Project, created.json()["id"]).name == "Book our February trip"

    kept = _create(client, "named", name="  Lisbon, in February  ")
    with db_session() as session:
        assert session.get(Project, kept.json()["id"]).name == "Lisbon, in February"


def test_the_form_picks_the_name_it_says_it_picks():
    """The label promised a pick and the field stayed empty until Create said
    "This project needs a name". The name now follows the goal until the
    chairman types one, and the address follows the name."""
    form = FORM.read_text()
    follows = _between(form, "function changeGoal", "\n  }\n")
    assert "nameFromGoal(" in follows, "typing the goal must pick the name it says it picks"
    assert "slugify(" in follows, "and the address must follow the picked name"
    assert "picked from your goal" in form, "say where the name came from, where it is asked"
    # A bad or taken address is named by the form, not by the browser's own
    # "Please match the requested format" bubble on Create.
    problems = _between(form, "function problemsOn", "\n  }\n")
    assert "SLUG_OK" in problems and "existingSlugs" in problems


def test_the_form_and_the_server_pick_the_same_name():
    """Two copies of one small rule — the form shows the pick while you type,
    the server makes it for every other door — held together here so that a
    name previewed on the form is the name any door would have chosen."""
    form = FORM.read_text()
    ts_filler = set(re.findall(r'"(\w+)"', _between(form, "const TRAILING_FILLER", ";")))
    assert ts_filler == admin._TRAILING_FILLER
    ts_clause = re.search(r"const CLAUSE_END = /(.*)/i;", form).group(1)
    assert ts_clause.replace("\\s", " ") == admin._CLAUSE_END.pattern.replace("\\s", " ")


# ── 2. a card that wants a second check, said where the roster is chosen ───────


def test_the_roster_step_says_when_the_card_wants_a_second_check():
    """The server refuses a roster that asks for an independent read and hires
    nobody who can give one, and every quality card asks for one. The step that
    picks the people has to say so, with both answers one click away: add the
    reviewer, or skip the check."""
    form = FORM.read_text()
    roster = _between(form, "{on(2) && <>", "{on(3) && <>")
    assert "<ReviewSeat" in roster, "the roster step must say the card wants a second check"
    seat = _between(form, "function ReviewSeat", "\n}\n")
    assert "onAdd" in seat and "onSkip" in seat, "one click to add the reviewer, one to skip"


def test_create_names_the_one_gap_the_form_cannot_fill():
    """With a checking role installed the form hires one at Create and says so.
    Without one there is nobody to hire, and Create used to run straight into
    the server's refusal; now it waits, says why, and offers the skip."""
    form = FORM.read_text()
    problems = _between(form, "function problemsOn", "\n  }\n")
    assert "Nobody on this install can check the work" in problems
    bar = _between(form, "Still needed:", "Create project")
    assert "onClick={skipCheck}" in bar, "the blocker carries its one-click answer"


def test_the_forms_default_roster_is_one_the_server_creates(client):
    """What the form sends on its shortest path: the preselected card's rule,
    the one role hired, and the reviewer the roster step said it would add."""
    _library(client)
    seed_role(client, "editor", category="challenge")
    refused = _create(client, "as-hired",
                      constitution={"deliverable_review": "independent_read"})
    assert refused.status_code == 422, "without the reviewer this is refused — the rule stays"
    created = _create(client, "as-sent",
                      constitution={"deliverable_review": "independent_read"},
                      teams=[{"role_slug": "worker", "name": "Desk", "employees": ["Ada"]},
                             {"role_slug": "editor", "name": "Review", "employees": ["Juno"]}])
    assert created.status_code == 200, created.text


# ── 3. a hired role is never dropped ───────────────────────────────────────────


def test_a_team_hired_with_nobody_on_it_is_refused(client):
    """The server half: a team with no employees is refused by name of the
    problem rather than created empty. Only solo mode, where the CEO is the
    team, may send one — and that exemption is attacked in test_the_bar.py."""
    _library(client)
    for teams in ([{"role_slug": "worker", "name": "Desk", "employees": []}],
                  [{"role_slug": "worker", "name": "Desk"}]):
        refused = _create(client, "nobody", teams=teams)
        assert refused.status_code == 422, refused.text
        assert "at least one employee" in refused.text


def test_a_hired_role_is_never_dropped_on_the_way_to_create():
    """The form half. A team whose name had been cleared was filtered out of
    what Create sends, with everyone typed into it; now it is named after its
    role, and a row with people and no role stops Create with a reason."""
    form = FORM.read_text()
    roster = _between(form, "const effectiveTeams", "})();")
    assert not re.search(r"teams\.filter\([^;]*name", roster), (
        "what Create sends must not drop a hired role for having no team name")
    assert "teams: effectiveTeams" in _between(form, "function projectBody", "\n  }\n")
    problems = _between(form, "function problemsOn", "\n  }\n")
    assert "A team has no role yet" in problems


# ── reading load ───────────────────────────────────────────────────────────────


def _visible_words(source: str) -> int:
    """Roughly the words a component renders before anything is opened: its
    JSX text and capitalised string literals, less comments and whatever sits
    folded under a <Why>."""
    body = source[source.rindex("  return ("):]
    body = re.sub(r"\{/\*.*?\*/\}|//[^\n]*", "", body, flags=re.S)
    body = re.sub(r"<Why\b.*?</Why>", "", body, flags=re.S)
    text = " ".join(re.findall(r">([^<>{}]+)<", body))
    text += " " + " ".join(s for s in re.findall(r'"([^"\n]+)"', body)
                           if " " in s and s[:1].isupper())
    return len(re.findall(r"[A-Za-z0-9$]+(?:['’][a-z]+)?", text))


def test_the_start_page_asks_one_question():
    """It was 305 words to choose between two paths: a paragraph and three
    facts per card, and a footnote. It is one question and two answers of a few
    words each; the trade each side makes is one click down, under Which one?"""
    start = START.read_text()
    assert "What do you want to exist?" in start
    words = _visible_words(start)
    assert words <= 70, f"the start page reads {words} words before anything is opened"
