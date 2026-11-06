"""Spend across the install (yaaf.server.spend_rollup): what each provider, model
and project cost, over time, for the person who runs the box.

Three promises are held here. It adds up to the ledger the meters read. It
shows dollars only where a provider billed dollars — a subscription is tokens,
never a list price. And it is a cross-project rollup, so it reaches exactly the
projects its reader may open, and naming any other one is refused in the words
a missing one gets.
"""

from __future__ import annotations

from datetime import UTC, datetime

from tests.conftest import ADMIN, headers_of, make_project, person
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import LedgerEntry, LedgerKind, Run, RunStatus, utcnow


def _spent(project_id: str, *, provider: str | None = "anthropic",
           model: str | None = "claude-opus-5-5", paid_in: str | None = "usd",
           usd: float | None = 1.0, tokens_out: int = 100, cache_read: int = 0,
           at: datetime | None = None, kind: str = LedgerKind.MODEL_USAGE,
           run: bool = True) -> str | None:
    """One run and the ledger row it booked, in the shape finalize writes."""
    run_id = ids.new_id(ids.RUN) if run else None
    with db_session() as session:
        if run_id:
            session.add(Run(id=run_id, project_id=project_id, agent_id=None,
                            trigger={"type": "test"}, status=RunStatus.DONE,
                            provider=provider, model=model, paid_in=paid_in,
                            cost_usd=usd, tokens_out=tokens_out,
                            cache_read_tokens=cache_read))
        session.add(LedgerEntry(id=ids.new_id(ids.LEDGER), project_id=project_id,
                                kind=kind, tokens_out=tokens_out,
                                cache_read_tokens=cache_read, usd=usd, run_id=run_id,
                                at=at or utcnow(), note="test"))
    return run_id


def _get(client, headers=ADMIN, **params):
    response = client.get("/observe/spend", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_it_adds_up_by_provider_model_and_project(client):
    acme = make_project(client, slug="acme")
    beta = make_project(client, slug="beta")
    _spent(acme["id"], provider="anthropic", model="claude-opus-5-5", usd=3.0, tokens_out=300)
    _spent(acme["id"], provider="deepseek", model="deepseek-chat", usd=0.5, tokens_out=900)
    _spent(beta["id"], provider="anthropic", model="claude-sonnet-5-5", usd=1.0,
           tokens_out=100, cache_read=5_000)

    by_provider = _get(client, group_by="provider")
    groups = {g["key"]: g for g in by_provider["groups"]}
    assert groups["anthropic"]["usd"] == 4.0
    assert groups["anthropic"]["runs"] == 2
    assert groups["anthropic"]["tokens"] == 300 + 100 + 5_000
    assert groups["deepseek"]["usd"] == 0.5
    assert by_provider["groups"][0]["key"] == "anthropic", "sorted by dollars, most first"
    assert by_provider["totals"]["usd"] == 4.5
    assert by_provider["totals"]["runs"] == 3

    by_model = {g["key"]: g["usd"] for g in _get(client, group_by="model")["groups"]}
    assert by_model == {"claude-opus-5-5": 3.0, "deepseek-chat": 0.5, "claude-sonnet-5-5": 1.0}

    by_project = {g["slug"]: g["usd"] for g in _get(client, group_by="project")["groups"]}
    assert by_project == {"acme": 3.5, "beta": 1.0}

    # The detail rows are the finest grain, and add up to the same total.
    rows = by_provider["rows"]
    assert {(r["slug"], r["provider"], r["model"]) for r in rows} == {
        ("acme", "anthropic", "claude-opus-5-5"), ("acme", "deepseek", "deepseek-chat"),
        ("beta", "anthropic", "claude-sonnet-5-5")}
    assert sum(r["usd"] for r in rows) == 4.5
    # And so does the series: every bucket together is the whole window.
    assert sum(b["usd"] for b in by_provider["series"]) == 4.5
    assert sum(b["tokens"] for b in by_provider["series"]) == by_provider["totals"]["tokens"]


def test_a_subscription_is_tokens_and_never_a_list_price(client):
    """On a dollar budget a subscription seat's call is booked at its catalog
    price — what it costs the budget. No provider billed it, so here it is
    tokens on a plan, and the dollars are what API keys billed."""
    project = make_project(client)
    _spent(project["id"], paid_in="usd", usd=2.0, tokens_out=1_000)
    # A subscription seat on a dollar budget: the ledger carries a catalog count.
    _spent(project["id"], paid_in="tokens", usd=7.0, tokens_out=4_000)
    # A run from before `paid_in` was pinned, on a token budget: no dollars booked.
    _spent(project["id"], paid_in=None, usd=None, tokens_out=500)

    report = _get(client)
    assert report["totals"]["usd"] == 2.0, "the catalog count is not a bill"
    assert report["totals"]["tokens"] == 5_500
    assert report["totals"]["plan_tokens"] == 4_500

    billed = _get(client, paid_in="usd")["totals"]
    assert (billed["usd"], billed["tokens"], billed["plan_tokens"]) == (2.0, 1_000, 0)
    on_plan = _get(client, paid_in="tokens")["totals"]
    assert (on_plan["usd"], on_plan["tokens"], on_plan["plan_tokens"]) == (0.0, 4_500, 4_500)


def test_revenue_never_counts_and_purchases_only_when_asked(client):
    project = make_project(client)
    _spent(project["id"], usd=1.0)
    _spent(project["id"], kind=LedgerKind.EXTERNAL_SPEND, usd=12.0, tokens_out=0)
    _spent(project["id"], kind=LedgerKind.REVENUE, usd=500.0, tokens_out=0, run=False)

    assert _get(client)["totals"]["usd"] == 1.0, "model usage is the default question"
    assert _get(client, kind="all")["totals"]["usd"] == 13.0, "revenue is money coming in"
    assert _get(client, kind="external_spend")["totals"]["usd"] == 12.0


def test_it_adds_up_to_the_project_meter(client):
    """`kind=all` on one project, month to date, is the budget meter's tokens:
    the same rows, read with the same exclusion. Settings and the project's
    own page must never disagree about one project's month."""
    project = make_project(client)
    _spent(project["id"], tokens_out=10, cache_read=900_000)
    _spent(project["id"], kind=LedgerKind.EXTERNAL_SPEND, usd=3.0, tokens_out=0)
    _spent(project["id"], run=False, usd=None, tokens_out=42)

    mtd = client.get(f"/observe/projects/{project['id']}", headers=ADMIN).json()["month_to_date"]
    meter = mtd["tokens_in"] + mtd["tokens_out"] + mtd["cache_creation"] + mtd["cache_read"]
    today = datetime.now(UTC).date()
    report = _get(client, kind="all", every="month", project=project["id"],
                  since=today.replace(day=1).isoformat(), until=today.isoformat(), tz="UTC")
    assert report["totals"]["tokens"] == meter > 0


def test_a_provider_has_one_name_on_every_screen(client):
    """The project's own card and this rollup resolve a run's provider through
    one helper, so one run can never sit under two names — including a run from
    before providers were recorded, which was Anthropic's."""
    project = make_project(client)
    _spent(project["id"], provider=None, usd=1.0)
    _spent(project["id"], provider="deepseek", usd=1.0)
    _spent(project["id"], run=False, usd=1.0)

    card = client.get(f"/observe/projects/{project['id']}/spend?group_by=provider",
                      headers=ADMIN).json()["rows"]
    install = _get(client, group_by="provider")["groups"]
    assert {(r["key"], r["label"]) for r in card} == {(g["key"], g["label"]) for g in install}
    assert "anthropic" in {g["key"] for g in install}
    assert "unattributed" in {g["key"] for g in install}, "a vanished run's money stays"


def test_weeks_and_months_fall_on_the_readers_calendar(client):
    """03:00 UTC on Monday 2 November is still Sunday 1 November in Toronto.
    The same row belongs to a different week depending on whose calendar it is
    read on, and the reader's is the one that counts."""
    project = make_project(client)
    _spent(project["id"], usd=2.0, at=datetime(2026, 11, 2, 3, 0, tzinfo=UTC))
    _spent(project["id"], usd=5.0, at=datetime(2026, 10, 30, 12, 0, tzinfo=UTC))

    window = {"since": "2026-10-20", "until": "2026-11-08", "every": "week"}
    utc = {b["start"]: b["usd"] for b in _get(client, tz="UTC", **window)["series"]}
    toronto = _get(client, tz="America/Toronto", **window)
    local = {b["start"]: b["usd"] for b in toronto["series"]}
    assert utc == {"2026-10-19": 0.0, "2026-10-26": 5.0, "2026-11-02": 2.0}
    assert local == {"2026-10-19": 0.0, "2026-10-26": 7.0, "2026-11-02": 0.0}
    # `since` snapped back to its Monday, so the first bar is a whole week.
    assert toronto["since"] == "2026-10-19"
    assert toronto["series"][-1]["end"] == "2026-11-08"

    months = _get(client, tz="UTC", every="month", since="2026-10-01", until="2026-11-30")
    assert [(b["start"], b["usd"]) for b in months["series"]] == [
        ("2026-10-01", 5.0), ("2026-11-01", 2.0)]
    days = _get(client, tz="UTC", every="day", since="2026-10-30", until="2026-11-02")
    assert [b["usd"] for b in days["series"]] == [5.0, 0.0, 0.0, 2.0], "empty days are drawn"
    # A bucket names its groups, so the chart can stack them.
    assert days["series"][0]["by"] == {"anthropic": {"usd": 5.0, "tokens": 100,
                                                     "plan_tokens": 0}}


def test_filters_narrow_the_money_but_not_the_choices(client):
    acme = make_project(client, slug="acme")
    beta = make_project(client, slug="beta")
    _spent(acme["id"], provider="anthropic", model="claude-opus-5-5", usd=3.0)
    _spent(acme["id"], provider="deepseek", model="deepseek-chat", usd=0.5)
    _spent(beta["id"], provider="anthropic", model="claude-sonnet-5-5", usd=1.0)

    deepseek = _get(client, provider="deepseek")
    assert deepseek["totals"]["usd"] == 0.5
    # A filter whose own options vanished once set could never be widened again.
    assert {o["key"] for o in deepseek["options"]["providers"]} == {"anthropic", "deepseek"}
    assert set(deepseek["options"]["models"]) == {
        "claude-opus-5-5", "deepseek-chat", "claude-sonnet-5-5"}

    # Repeated to choose several; a project by slug or id.
    two = _get(client, model=["claude-opus-5-5", "claude-sonnet-5-5"])
    assert two["totals"]["usd"] == 4.0
    by_slug = _get(client, project="beta")
    assert by_slug["totals"]["usd"] == 1.0
    assert by_slug["filters"]["project"] == [beta["id"]]
    # Every project the reader may open is offered, spent or not.
    assert {o["slug"]: o["spent"] for o in by_slug["options"]["projects"]} == {
        "acme": False, "beta": True}


def test_a_reader_sees_only_the_projects_they_may_open(client):
    """The attack: an observer on one project asks the install-wide rollup for
    everything, then names a project they are not on. They get their project's
    money and nothing else, and the stranger's project is refused in the words
    a project that does not exist gets — the filter is not an oracle."""
    mine = make_project(client, slug="mine")
    theirs = make_project(client, slug="theirs")
    _spent(mine["id"], usd=1.0)
    _spent(theirs["id"], usd=99.0)
    reader = person("observer@example.com")
    added = client.post(f"/admin/projects/{mine['id']}/members",
                        json={"email": "observer@example.com", "role": "observer"},
                        headers=ADMIN)
    assert added.status_code == 200, added.text
    as_reader = headers_of(reader)

    everything = _get(client, as_reader, group_by="project")
    assert everything["totals"]["usd"] == 1.0
    assert [g["slug"] for g in everything["groups"]] == ["mine"]
    assert [o["slug"] for o in everything["options"]["projects"]] == ["mine"]

    for ref in (theirs["id"], "theirs", "proj_does_not_exist"):
        refused = client.get("/observe/spend", params={"project": ref}, headers=as_reader)
        assert refused.status_code == 404, ref
        assert refused.json()["detail"] == "unknown project"

    # Somebody on no project at all reads an empty install, not an error.
    nobody = _get(client, headers_of(person("nobody@example.com")))
    assert nobody["totals"]["usd"] == 0 and nobody["groups"] == []
    # And nobody signed out reads anything.
    assert client.get("/observe/spend").status_code == 401
    # The instance admin reads the install.
    assert _get(client)["totals"]["usd"] == 100.0


def test_a_question_it_cannot_answer_is_refused(client):
    for params, status in [({"every": "fortnight"}, 400), ({"group_by": "team"}, 400),
                           ({"kind": "revenue"}, 400), ({"paid_in": "euros"}, 400),
                           ({"tz": "Mars/Olympus_Mons"}, 422), ({"since": "yesterday"}, 422),
                           ({"since": "2026-11-06", "until": "2026-11-01"}, 422),
                           ({"every": "day", "since": "2020-01-01", "until": "2026-01-01"}, 422)]:
        response = client.get("/observe/spend", params=params, headers=ADMIN)
        assert response.status_code == status, (params, response.text)

    # The default windows: a month of days, a quarter of weeks, a year of months.
    assert len(_get(client, every="day")["series"]) == 30
    assert len(_get(client, every="week")["series"]) == 13
    assert len(_get(client, every="month")["series"]) == 12


# ── `yaaf spend`: the same answer, from a terminal ──────────────────────────────


def _cli(client, monkeypatch, *args):
    import contextlib

    from starlette.testclient import TestClient
    from typer.testing import CliRunner

    from yaaf.cli import main as cli

    as_admin = TestClient(client.app, headers=ADMIN)
    monkeypatch.setattr(cli, "_client", lambda: contextlib.nullcontext(as_admin))
    return CliRunner().invoke(cli.app, ["spend", *args])


def test_the_cli_reads_the_same_rollup(client, monkeypatch):
    acme = make_project(client, slug="acme")
    _spent(acme["id"], provider="anthropic", model="claude-opus-5-5", usd=3.0,
           at=datetime(2026, 11, 2, 15, 0, tzinfo=UTC))
    _spent(acme["id"], provider="deepseek", model="deepseek-chat", usd=0.5,
           at=datetime(2026, 11, 4, 15, 0, tzinfo=UTC))
    _spent(acme["id"], paid_in="tokens", usd=None, tokens_out=40_000,
           at=datetime(2026, 11, 4, 16, 0, tzinfo=UTC))

    result = _cli(client, monkeypatch, "--since", "2026-11-01", "--until", "2026-11-05",
                  "--tz", "UTC")
    assert result.exit_code == 0, result.output
    assert "Billed in dollars: $3.50" in result.output
    assert "tokens: 40.2K (40K on subscriptions)" in result.output
    assert "Anthropic" in result.output and "DeepSeek" in result.output
    # A line per day, empty days included, the busiest one full width.
    days = [line for line in result.output.splitlines() if line.startswith("  2026-11-0")]
    assert len(days) == 5
    assert "$3.00" in days[1] and "█" * 32 in days[1]
    assert "$0.00" in days[0]

    tokens = _cli(client, monkeypatch, "--since", "2026-11-01", "--until", "2026-11-05",
                  "--tz", "UTC", "--tokens", "--billing", "tokens", "--by", "model")
    assert tokens.exit_code == 0, tokens.output
    assert "By model (tokens)" in tokens.output and "$3.00" not in tokens.output

    raw = _cli(client, monkeypatch, "--json", "--every", "month", "--project", "acme",
               "--since", "2026-11-01", "--until", "2026-11-05", "--tz", "UTC")
    assert raw.exit_code == 0, raw.output
    import json
    assert json.loads(raw.output)["totals"]["usd"] == 3.5


def test_the_cli_says_what_it_cannot_do_in_one_line(client, monkeypatch):
    clash = _cli(client, monkeypatch, "--days", "7", "--since", "2026-11-01")
    assert clash.exit_code == 1 and "not both" in clash.output
    refused = _cli(client, monkeypatch, "--project", "nobody-has-this")
    assert refused.exit_code == 1
    assert "unknown project" in refused.output and "Traceback" not in refused.output
    quiet = _cli(client, monkeypatch, "--days", "3")
    assert quiet.exit_code == 0 and "Nothing was spent in this window." in quiet.output


# ── the MCP reader: a model client's reach, not the admin's ─────────────────────


def test_a_model_client_reads_its_persons_projects_not_the_whole_box(client):
    """The suite's chair is the instance admin, so over HTTP it reads every
    project on the box. Its model client must not: a machine acting for a
    person stays on the projects that person is actually on. A project they do
    not chair is absent from the totals, and naming it is refused in the words
    a missing one gets."""
    import json

    from tests.selftest_invariants.test_mcp_auth import foreign_project, mint, rpc

    mine = make_project(client, slug="mine")
    theirs = foreign_project(client, slug="theirs")
    _spent(mine["id"], provider="anthropic", usd=2.0)
    _spent(theirs["id"], provider="openai", usd=50.0)
    assert _get(client)["totals"]["usd"] == 52.0, "the admin's own eyes read the box"

    token = mint(client)["token"]

    def call(arguments):
        response = rpc(client, token, "tools/call",
                       {"name": "spend_across", "arguments": arguments})
        assert response.status_code == 200, response.text
        return response.json()["result"]

    answer = json.loads(call({"group_by": "provider"})["content"][0]["text"])
    assert answer["totals"]["usd"] == 2.0
    assert [g["key"] for g in answer["groups"]] == ["anthropic"]
    assert {r["slug"] for r in answer["rows"]} == {"mine"}

    one = json.loads(call({"project": "mine", "every": "month"})["content"][0]["text"])
    assert one["totals"]["usd"] == 2.0 and one["window"]["every"] == "month"
    for ref in ("theirs", theirs["id"], "nowhere"):
        refused = call({"project": [ref]})
        assert refused["isError"] is True
        assert "unknown project" in refused["content"][0]["text"]
    assert call({"every": "fortnight"})["isError"] is True
    assert call({"provider": 7})["isError"] is True


# ── through the runner: what the lease pinned decides, end to end ───────────────


def test_a_plan_seat_on_a_dollar_budget_is_a_cost_there_and_not_a_bill_here(client):
    """The real path — dispatched, leased, executed, finalized — not rows by
    hand. An OpenAI key pays the project in dollars; its CEO sits on a Claude
    subscription. The project's budget counts that run at its catalog price,
    as it must, or the dollar wall would never see it. No provider billed it,
    so across the install it is tokens on a plan and no dollars at all."""
    from tests.selftest_invariants.test_mixed_payers import (
        _Bills,
        _ceo_job,
        _claude_plan_on_a_dollar_project,
    )
    from yaaf.runner.daemon import execute_job

    project = _claude_plan_on_a_dollar_project(client)
    job = _ceo_job(project)
    execute_job(job, backend=_Bills(9.99, tokens_out=100_000))
    with db_session() as session:
        run = session.get(Run, job.run_id)
        assert (run.provider, run.paid_in) == ("anthropic", "tokens"), "pinned at lease"
        catalog = run.cost_usd
    assert catalog and catalog > 0, "the budget counts it in dollars"

    card = client.get(f"/observe/projects/{project['id']}/spend?group_by=provider",
                      headers=ADMIN).json()["rows"]
    assert [(r["key"], r["usd"]) for r in card] == [("anthropic", catalog)]

    install = _get(client, project=project["id"])
    assert install["totals"]["usd"] == 0
    assert install["totals"]["plan_tokens"] == install["totals"]["tokens"] >= 100_000


def test_a_dollar_key_under_a_subscription_project_is_a_bill_here(client):
    """The mirror: a project paid by a Claude subscription, its CEO on a
    DeepSeek key. Those dollars moved, so they are billed here — the one figure
    a token budget's own meter does not show in dollars."""
    from tests.selftest_invariants.test_mixed_payers import _Bills, _ceo_job, _deepseek_ceo
    from yaaf.runner.daemon import execute_job

    project = _deepseek_ceo(client)
    execute_job(_ceo_job(project), backend=_Bills(0.05))
    install = _get(client, group_by="provider", project=project["id"])
    assert [(g["key"], g["usd"], g["plan_tokens"]) for g in install["groups"]] == [
        ("deepseek", 0.05, 0)]
