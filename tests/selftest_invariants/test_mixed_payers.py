"""Acceptance: one project, two providers, paid two ways — the chairman's
journey end to end.

The case that broke four times in production on 10/27–28: a project paid by a
Claude subscription moves its CEO onto `deepseek-reasoner` with a DeepSeek API
key, from the CEO's own page. Each test below is one sentence of what he sees:

- the seat door takes the key and the CEO's next run goes out on DeepSeek;
- that run's real dollars are booked, at finalize and on every boardroom turn
  — the key that paid decides whether dollars moved, not the project's mode;
- the other way round, a subscription's runs on a dollar budget are counted at
  their catalog price, so the hard stop still sees them;
- a seat that names no model runs on the app's model.
- a role that uses the web searches through the install's backend there.

Each run is counted in its project's own unit at that run's own price. No
exchange rate between tokens and dollars is ever invented: cache reads swing
tokens-per-dollar about tenfold, so a converted cap would be wrong by that much.
"""

from __future__ import annotations

from tests.conftest import ADMIN, bearer, live_token, make_project
from tests.selftest_invariants.test_providers import _credential, _id, _role
from yaaf.core.constitution import effective
from yaaf.core.db import db_session
from yaaf.core.models import LedgerEntry, Project, ProjectStatus, Run
from yaaf.runner import daemon
from yaaf.runner.daemon import _billing_env, execute_job, lease_and_prepare
from yaaf.runner.types import RunJob, RunResult
from yaaf.server.dispatcher import dispatch_once
from yaaf.server.janitor import enforce_budget, month_to_date


class _Bills:
    """A backend whose run comes back with a bill and tokens, as a harness reports."""

    def __init__(self, usd: float | None, tokens_out: int = 100):
        self.usd, self.tokens_out = usd, tokens_out

    def execute(self, job, stop_event=None):
        return RunResult(ok=True, tokens_out=self.tokens_out, cost_usd=self.usd,
                         priced_usd=self.usd)


def _install(client):
    """The chairman, the base roles, the shipped price snapshot."""
    make_project(client, start=False)
    imported = client.post("/admin/settings/model-prices/import", headers=ADMIN,
                           json={"source": "snapshot", "apply": True})
    assert imported.status_code == 200, imported.text


def _project(client, slug: str, **fields) -> dict:
    made = client.post("/admin/projects", headers=ADMIN, json={
        "slug": slug, "name": slug.title(), "goal": "Prove the framework works end to end.",
        "chairman_email": "chair@example.com", "ceo_role_slug": "ceo", "ceo_name": "CEO",
        "teams": [], **fields})
    assert made.status_code == 200, made.text
    project = made.json()
    assert client.post(f"/admin/projects/{project['id']}/start",
                       headers=ADMIN).status_code == 200
    return project


def _ceo_job(project: dict):
    with db_session() as session:
        dispatch_once(session)
    jobs = [j for j in lease_and_prepare(limit=10) if j.agent_id == project["ceo_agent_id"]]
    assert len(jobs) == 1, jobs
    return jobs[0]


def _ledger(project_id: str) -> list[LedgerEntry]:
    with db_session() as session:
        rows = session.query(LedgerEntry).filter_by(project_id=project_id).all()
        session.expunge_all()
        return rows


def _deepseek_ceo(client) -> dict:
    """Claude subscription pays the project; the CEO is moved onto DeepSeek
    through the seat door, which pays DeepSeek with an API key in passing."""
    _install(client)
    plan = _id(_credential(client, "Claude plan", mode="subscription"))
    deepseek = _id(_credential(client, "DeepSeek API", provider="deepseek"))
    project = _project(client, "journey", billing_secret_id=plan)
    paid = client.put(f"/admin/projects/{project['id']}/credentials/deepseek",
                      headers=ADMIN, json={"credential_id": deepseek})
    assert paid.status_code == 200, paid.text
    moved = client.patch(f"/admin/agents/{project['ceo_agent_id']}/seat", headers=ADMIN,
                         json={"model": "deepseek-reasoner"})
    assert moved.status_code == 200, moved.text
    return project


# ── a subscription project, a DeepSeek CEO ──────────────────────────────────────


def test_the_ceos_next_run_goes_out_on_deepseek_with_the_deepseek_key(client):
    project = _deepseek_ceo(client)
    job = _ceo_job(project)
    assert (job.model, job.provider) == ("deepseek-reasoner", "deepseek")
    with db_session() as session:
        env = _billing_env(session, session.get(Project, project["id"]), job.provider)
    assert list(env) == ["DEEPSEEK_API_KEY"]


def test_its_real_dollars_are_booked_though_the_project_pays_a_subscription(client):
    project = _deepseek_ceo(client)
    job = _ceo_job(project)
    execute_job(job, backend=_Bills(0.05))
    with db_session() as session:
        run = session.get(Run, job.run_id)
        assert run.cost_usd == 0.05, "the DeepSeek key billed dollars; they were dropped"
        _, tokens = month_to_date(session, project["id"])
    assert [r.usd for r in _ledger(project["id"])] == [0.05]
    # The project's own meter is tokens, and the run is on it.
    assert tokens == 100
    spend = client.get(f"/observe/projects/{project['id']}/spend?group_by=provider",
                       headers=ADMIN).json()
    assert [(r["label"], r["usd"]) for r in spend["rows"]] == [("DeepSeek", 0.05)]


def test_its_boardroom_turns_book_their_dollars_too(client):
    """The chat lane books every turn as it happens. The CEO holds the
    boardroom on its own seat, so its turns are DeepSeek's too."""
    project = _deepseek_ceo(client)
    run_id, token = live_token(project["id"], project["ceo_agent_id"], kind="chat_session")
    with db_session() as session:
        run = session.get(Run, run_id)
        run.provider, run.model = "deepseek", "deepseek-reasoner"
    booked = client.post("/agent/chat_usage", headers=bearer(token),
                         json={"tokens_in": 1200, "tokens_out": 340, "cost_usd": 0.02})
    assert booked.status_code == 200, booked.text
    assert [r.usd for r in _ledger(project["id"])] == [0.02]


def test_a_boardroom_session_pins_what_it_runs_on(client, monkeypatch):
    """Without it the turns above could not say who paid, and spend by
    provider filed every boardroom turn under nobody."""
    from tests.selftest_invariants.test_chat_lane import _fake_sandbox
    from yaaf.runner.chat import chat_supervisor_once

    project = _deepseek_ceo(client)
    _fake_sandbox(monkeypatch)
    chat_supervisor_once()
    with db_session() as session:
        run = session.query(Run).filter_by(project_id=project["id"],
                                           kind="chat_session").one()
        assert (run.provider, run.model) == ("deepseek", "deepseek-reasoner")


# ── a dollar project, a subscription seat ───────────────────────────────────────


def _claude_plan_on_a_dollar_project(client, **constitution) -> dict:
    """An OpenAI key pays the project in dollars; the CEO's role pins a Claude
    model, which the project pays with a Claude subscription."""
    _install(client)
    openai = _id(_credential(client, "OpenAI key", provider="openai"))
    plan = _id(_credential(client, "Claude plan", mode="subscription"))
    return _project(client, "dollars", billing_secret_id=openai,
                    credentials={"anthropic": plan},
                    **({"constitution": constitution} if constitution else {}))


def _finish(project: dict, *, provider: str, model: str, billed: float | None,
            tokens_out: int, priced: float | None = None) -> str:
    """A run finalized as its harness would report it: `billed` is the figure
    the harness gave, `priced` the sandbox's own catalog pricing of it."""
    run_id, _ = live_token(project["id"], project["ceo_agent_id"])
    job = RunJob(run_id=run_id, project_id=project["id"], agent_id=project["ceo_agent_id"],
                 kind="agent_run", trigger={}, task_id=None, attempt=1)
    job.provider, job.model, job.harness = provider, model, "claude_agent_sdk"
    daemon.finalize(job, RunResult(ok=True, tokens_out=tokens_out, cost_usd=billed,
                                   priced_usd=priced))
    return run_id


def test_a_subscriptions_run_on_a_dollar_budget_is_counted_at_its_catalog_price(client):
    """Not the harness's list-price figure, and the run row says the same as
    its ledger row — every reader that sums either agrees with the wall."""
    project = _claude_plan_on_a_dollar_project(client)
    run_id = _finish(project, provider="anthropic", model="claude-sonnet-5-5",
                     billed=9.99, tokens_out=100_000)
    with db_session() as session:
        from yaaf.core import prices

        price = prices.price(session, "claude-sonnet-5-5")
        cost = session.get(Run, run_id).cost_usd
    (row,) = _ledger(project["id"])
    assert row.usd == cost == 100_000 * price.output / 1_000_000
    assert "catalog price" in (row.note or "")


def test_the_sandboxs_own_pricing_is_preferred_to_repricing_by_hand(client):
    """It knows the 1-hour cache writes and the web searches; a ledger row
    rebuilt from token counts does not."""
    project = _claude_plan_on_a_dollar_project(client)
    _finish(project, provider="anthropic", model="claude-sonnet-5-5", billed=9.99,
            priced=0.123, tokens_out=100_000)
    assert [r.usd for r in _ledger(project["id"])] == [0.123]


def test_the_hard_stop_sees_a_subscriptions_runs_on_a_dollar_budget(client):
    """Before, these runs booked nothing, and a dollar wall could not stop them."""
    project = _claude_plan_on_a_dollar_project(
        client, monthly_usd_cap=0.5, hard_stop_multiple=1.0)
    _finish(project, provider="anthropic", model="claude-sonnet-5-5", billed=None,
            tokens_out=100_000)
    with db_session() as session:
        p = session.get(Project, project["id"])
        enforce_budget(session, p, effective(session, p.id))
    with db_session() as session:
        p = session.get(Project, project["id"])
        assert p.status == ProjectStatus.PAUSED, p.status_reason
        assert "hard stop" in (p.status_reason or "")


# ── the app's model ─────────────────────────────────────────────────────────────


def test_a_seat_that_names_no_model_runs_on_the_apps_model(client):
    _install(client)
    assert _role(client, "planner", model="@balanced", is_ceo_role=True).status_code == 200
    chose = client.put("/admin/settings/models/app-model", headers=ADMIN,
                       json={"model": "claude-haiku-4-5"})
    assert chose.status_code == 200, chose.text
    claude = _id(_credential(client, "Claude key"))
    project = _project(client, "defaults", billing_secret_id=claude, ceo_role_slug="planner")
    assert _ceo_job(project).model == "claude-haiku-4-5"


# ── an install that never imported prices ───────────────────────────────────────


def test_an_unpriced_model_is_priced_in_place_and_the_move_completes(client):
    """DeepSeek ships unpriced and daily price upkeep is off by default, so on
    most installs the first move is refused for want of a price. The seat
    dialog's price door does what Settings → Model prices would — import that
    one model's price from the shipped list, audited — and saves again."""
    make_project(client, start=False)
    plan = _id(_credential(client, "Claude plan", mode="subscription"))
    deepseek = _id(_credential(client, "DeepSeek API", provider="deepseek"))
    project = _project(client, "unpriced", billing_secret_id=plan)
    ceo = f"/admin/agents/{project['ceo_agent_id']}/seat"
    assert client.put(f"/admin/projects/{project['id']}/credentials/deepseek", headers=ADMIN,
                      json={"credential_id": deepseek}).status_code == 200
    refused = client.patch(ceo, headers=ADMIN, json={"model": "deepseek-reasoner"})
    assert refused.status_code == 422 and "has no price on this install" in refused.text

    imported = client.post("/admin/settings/model-prices/import", headers=ADMIN, json={
        "source": "snapshot", "models": ["deepseek-reasoner"], "apply": True}).json()
    assert imported["applied"] == ["deepseek-reasoner"]
    moved = client.patch(ceo, headers=ADMIN, json={"model": "deepseek-reasoner"})
    assert moved.status_code == 200, moved.text


# ── the CEO role yaaf ships, which uses the web ─────────────────────────────────


def test_a_web_role_moved_onto_deepseek_searches_through_the_installs_backend(client):
    """The test CEO above grants no web; the shipped one does, as 37 of the 40
    shipped roles do, and DeepSeek's API cannot search. The move is refused
    for want of a search backend (R2) — the seat dialog's search door
    configures one, as Settings → Web search would, and saves again — and
    the CEO's next run carries that backend's key, nothing more."""
    import json
    import pathlib

    from yaaf.runner.daemon import _search_env

    _install(client)
    shipped = json.loads((pathlib.Path(__file__).parents[2] / "roles" / "ceo.json").read_text())
    assert shipped["tool_permissions"]["web"]
    assert client.post("/admin/roles", headers=ADMIN, json=shipped).status_code == 200
    plan = _id(_credential(client, "Claude plan", mode="subscription"))
    deepseek = _id(_credential(client, "DeepSeek API", provider="deepseek"))
    project = _project(client, "searching", billing_secret_id=plan)
    ceo = f"/admin/agents/{project['ceo_agent_id']}/seat"
    assert client.put(f"/admin/projects/{project['id']}/credentials/deepseek", headers=ADMIN,
                      json={"credential_id": deepseek}).status_code == 200
    refused = client.patch(ceo, headers=ADMIN, json={"model": "deepseek-reasoner"})
    assert refused.status_code == 422 and "cannot search the web" in refused.text

    assert client.put("/admin/settings/web-search", headers=ADMIN,
                      json={"backend": "brave", "key": "brave-key"}).status_code == 200
    moved = client.patch(ceo, headers=ADMIN, json={"model": "deepseek-reasoner"})
    assert moved.status_code == 200, moved.text
    job = _ceo_job(project)
    assert (job.model, job.provider) == ("deepseek-reasoner", "deepseek")
    with db_session() as session:
        env = _search_env(session, job)
    assert env["YAAF_SEARCH_KEY"] == "brave-key"


# ── what the review of this change found ────────────────────────────────────────


def test_who_paid_is_pinned_at_lease_not_asked_at_finalize(client):
    """A failover that closes, or a billing credential moved, while a run is
    in flight must not book it to a payer that never paid it."""
    from yaaf.core.models import ProjectLlmCredential

    project = _deepseek_ceo(client)
    job = _ceo_job(project)
    with db_session() as session:
        assert session.get(Run, job.run_id).paid_in == "usd"
        # Whatever paid DeepSeek is gone by the time the run finishes.
        session.query(ProjectLlmCredential).filter_by(project_id=project["id"]).delete()
    execute_job(job, backend=_Bills(0.05))
    with db_session() as session:
        assert session.get(Run, job.run_id).cost_usd == 0.05


def test_a_provider_this_install_does_not_have_never_fails_a_booking(client):
    """A row a newer release wrote, then rolled back: the lease refuses to
    inject it; booking must not raise over it either."""
    from yaaf.server.budget import counted, paid_in

    project = make_project(client, billing_mode="subscription")
    with db_session() as session:
        p = session.get(Project, project["id"])
        assert paid_in(session, p, "not-a-provider") is None
        count = counted(session, p, provider="not-a-provider", model="x", billed_usd=0.2,
                        tokens_out=10)
    assert count.usd is None, "read by the billing mode, as before"


def test_an_unpriced_subscription_seat_is_refused_on_a_dollar_budget(client):
    """Its runs would be counted at a catalog price that does not exist, so
    the dollar wall could not see them. Refused at the door, in the words the
    seat dialog's price door listens for."""
    make_project(client, start=False)
    assert _role(client, "coder", model="gpt-5.5", is_ceo_role=True,
                 tool_permissions={"files": True, "bash": False, "web": False}
                 ).status_code == 200
    claude = _id(_credential(client, "Claude key"))
    login = _id(_credential(client, "ChatGPT login", provider="openai", mode="subscription",
                            value='{"tokens": {"access_token": "placeholder"}}'))
    refused = client.post("/admin/projects", headers=ADMIN, json={
        "slug": "unpriced-plan", "name": "Unpriced", "goal": "Prove the framework works.",
        "chairman_email": "chair@example.com", "ceo_role_slug": "coder", "ceo_name": "CEO",
        "billing_secret_id": claude, "credentials": {"openai": login}, "teams": []})
    assert refused.status_code == 422, refused.text
    assert "gpt-5.5 has no price on this install" in refused.text
    assert "budget is in dollars" in refused.text


def test_the_dollars_keys_billed_leave_out_real_world_purchases(client):
    """The Spend page's "also billed in dollars" line reads model usage only:
    a purchase the CEO made is money, but not a key's bill."""
    from yaaf.core import ids

    project = _deepseek_ceo(client)
    job = _ceo_job(project)
    execute_job(job, backend=_Bills(0.05))
    with db_session() as session:
        session.add(LedgerEntry(id=ids.new_id(ids.LEDGER), project_id=project["id"],
                                kind="external_spend", usd=50.0, run_id=job.run_id,
                                note="a real-world purchase"))
    billed = client.get(f"/observe/projects/{project['id']}/spend?group_by=provider"
                        "&window=month&kind=model_usage", headers=ADMIN).json()
    assert [(r["label"], r["usd"]) for r in billed["rows"]] == [("DeepSeek", 0.05)]


def test_an_overrun_opened_in_the_other_unit_is_not_written_in_this_one(client):
    """#81 kept dollar budgets in tokens for a day; an overrun it opened must
    not get dollars written into its token figures."""
    from yaaf.core import ids
    from yaaf.core.models import BudgetOverrun
    from yaaf.server.budget import month_start, record_overrun

    project = make_project(client, billing_mode="api_key")
    with db_session() as session:
        session.add(BudgetOverrun(id=ids.new_id(ids.OVERRUN), project_id=project["id"],
                                  period=month_start(), unit="tokens", budget=30_000_000,
                                  spent_at_crossing=31_000_000, final_spent=31_000_000,
                                  breakdown={}))
    with db_session() as session:
        p = session.get(Project, project["id"])
        record_overrun(session, p, effective(session, p.id), 4.2)
    with db_session() as session:
        row = session.query(BudgetOverrun).filter_by(project_id=project["id"]).one()
        assert (row.unit, row.final_spent) == ("tokens", 31_000_000)
