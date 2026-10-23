"""Invariant selftests: an alarm links to the page that fixes it.

Alarms are the notices that always mail — the project paused, a promise
broken, a billing key dead — and each already named its fix in words
("reconnect billing in Settings"). Then the reader had to find the project,
the tab and the card, on the morning it stopped. Each now ends on a link to
that page, when the install knows its public address, and on nothing when it
does not: a link to the API's own origin opens nothing.
"""

from __future__ import annotations

from tests.conftest import make_project
from tests.selftest_invariants.test_budget_is_a_plan import _book, _enforce
from yaaf.core import config
from yaaf.server.notify import project_link


def test_the_link_names_the_page(monkeypatch):
    monkeypatch.setattr(config.settings(), "public_base_url", "https://yaaf.example.com/")
    assert project_link("proj_1", "settings?s=money", "Fix the credential") == (
        "\n\nFix the credential: https://yaaf.example.com/app/#/projects/proj_1/settings?s=money")


def test_without_a_public_origin_there_is_no_link(monkeypatch):
    monkeypatch.setattr(config.settings(), "public_base_url", "")
    assert project_link("proj_1", "spend") == ""


def test_the_hard_stop_alarm_ends_on_where_the_money_went(client, monkeypatch):
    monkeypatch.setattr(config.settings(), "public_base_url", "https://yaaf.example.com")
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr("yaaf.server.janitor.chairman_notify",
                        lambda session, proj, subject, body, **kw: sent.append((subject, body)))
    project = make_project(client, billing_mode="subscription",
                           constitution={"monthly_token_cap": 100_000,
                                         "hard_stop_multiple": 2.0})
    _book(project["id"], tokens=300_000)
    _enforce(project["id"])

    alarm = next(body for subject, body in sent if "hard stop" in subject)
    assert alarm.endswith(
        f"See where it went: https://yaaf.example.com/app/#/projects/{project['id']}/spend")
