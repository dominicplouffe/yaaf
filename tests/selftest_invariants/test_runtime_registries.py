"""Invariant selftests: the runtime registries are complete, and a run says what it ran on.

docs/RUNTIMES.md promises that adding a provider is a registry entry rather
than an edit to every file that ever assumed the first one — and that
forgetting part of an entry fails a test instead of shipping as a screen that
is quietly wrong. These are those tests. They read the registries, never a
named provider, so the next provider is held to them without anyone editing
this file.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import json
import pathlib
import re

import pytest

from tests.conftest import ADMIN, make_project
from yaaf.core.db import db_session
from yaaf.core.models import AuditLog, Run
from yaaf.runner.daemon import execute_job, lease_and_prepare
from yaaf.runner.types import RunResult
from yaaf.runtimes import catalog, harnesses, providers
from yaaf.runtimes.seats import effective_harness
from yaaf.server.dispatcher import dispatch_once

REPO = pathlib.Path(__file__).resolve().parents[2]
REAL_HARNESSES = [h for h in harnesses.HARNESSES.values() if h.family != "mock"]
#: The model a seat with no override runs on, on the default provider.
DEFAULT = catalog.resolve(catalog.DEFAULT_MODEL, providers.DEFAULT_PROVIDER)


# ── the registries are complete ─────────────────────────────────────────────────


@pytest.mark.parametrize("path", ["harnesses.py", "wire.py"])
def test_the_files_the_sandbox_shares_import_nothing_from_yaaf(path):
    """The sandbox carries single files, not the package; the harness facts
    and every provider's wire have to be readable from there without the
    control plane behind them."""
    tree = ast.parse((REPO / "yaaf" / "runtimes" / path).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.split(".")[0] == "yaaf" for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] != "yaaf", node.module


def test_every_model_is_served_by_a_registered_provider_and_a_harness():
    for model in catalog.MODELS:
        assert model.provider in providers.PROVIDERS, model.id
        assert any(model.provider in h.providers for h in REAL_HARNESSES), model.id
        assert model.tier > 0, f"{model.id}: tier 0 means 'never heard of it'"
        assert not catalog.is_class(model.id), model.id
        for rung in model.efforts or ():
            assert rung in catalog.EFFORTS, (model.id, rung)


def test_the_defaults_name_things_that_exist():
    """The default model is a class, and it resolves on the default provider to
    a model the catalog lists — a seat with no role version runs somewhere."""
    assert catalog.is_class(catalog.DEFAULT_MODEL)
    assert providers.DEFAULT_PROVIDER in providers.PROVIDERS
    resolved = catalog.resolve(catalog.DEFAULT_MODEL, providers.DEFAULT_PROVIDER)
    assert catalog.model(resolved) is not None
    assert catalog.DEFAULT_EFFORT in catalog.EFFORTS


def test_each_lineup_stays_within_its_provider():
    """`stakes: high` and every class resolve within the seat's own provider —
    never to another vendor's model, and never to one the catalog cannot rank
    where the provider has a ladder of its own. A provider with catalog models
    ships a whole lineup; one with none to rank (OpenRouter's hundreds, a
    custom server) ships none, and its lineup is the install's to name."""
    for provider in providers.PROVIDERS.values():
        lineup = dict(provider.lineup or {})
        assert set(lineup) <= set(catalog.CLASSES), provider.name
        for cls, model in lineup.items():
            assert catalog.provider_of(model) == provider.name, (provider.name, cls, model)
        if any(m.provider == provider.name for m in catalog.MODELS):
            assert set(lineup) == set(catalog.CLASSES), (
                f"{provider.name} has catalog models to climb; name its whole lineup")
            for model in lineup.values():
                assert catalog.model(model) is not None, model
            ranks = [catalog.tier(lineup[c]) for c in catalog.CLASSES]
            assert ranks == sorted(ranks), f"{provider.name}'s lineup is out of order: {ranks}"


def test_each_credential_kind_defaults_to_a_harness_that_can_take_it():
    for provider in providers.PROVIDERS.values():
        for kind in provider.kinds:
            harness = harnesses.HARNESSES[kind.default_harness]
            assert provider.name in harness.providers, (provider.name, kind.mode)
            assert f"{provider.name}:{kind.mode}" in harness.accepts, (provider.name, kind.mode)


def test_every_credential_kind_is_whole():
    for provider in providers.PROVIDERS.values():
        assert provider.kinds, provider.name
        assert provider.failure_markers, provider.name
        for kind in provider.kinds:
            assert kind.meter in ("usd", "tokens"), (provider.name, kind.mode)
            assert kind.env_var and kind.bills and kind.label, (provider.name, kind.mode)
            assert kind.help, f"{provider.name}:{kind.mode} says nowhere where a key comes from"
            assert callable(kind.shape_error)
            assert any(f"{provider.name}:{kind.mode}" in h.accepts for h in REAL_HARNESSES)


def test_every_provider_can_be_reached():
    """A provider is reached on a wire the wire file speaks, with a key it knows
    how to present, at an address it has — its own, or a template the
    credential form fills in."""
    from yaaf.runtimes import wire

    for provider in providers.PROVIDERS.values():
        assert provider.protocol in wire.PROTOCOLS, provider.name
        assert provider.auth in wire.AUTH_STYLES, provider.name
        if provider.credential_base_url:
            assert provider.base_url_template and provider.base_url is None, provider.name
        else:
            assert (provider.base_url or "").startswith("https://"), provider.name
        if provider.search:
            assert provider.search_usd > 0, f"{provider.name} searches for free?"
        if provider.local:
            assert provider.auth == "none", provider.name


def test_yaaf_loop_reaches_every_provider_with_a_wire():
    """The loop's provider list is written out (harnesses.py reads nothing from
    the registry); this holds the two to each other."""
    with_wire = {p.name for p in providers.PROVIDERS.values() if p.protocol}
    assert set(harnesses.LOOP_PROVIDERS) == with_wire
    loop = harnesses.HARNESSES["yaaf_loop"]
    assert set(loop.accepts) == {f"{p}:api_key" for p in with_wire}


def test_a_harness_alias_names_a_harness_that_exists():
    for old, new in harnesses.ALIASES.items():
        assert old not in harnesses.HARNESSES and new in harnesses.HARNESSES, old
        assert harnesses.find(old) is harnesses.HARNESSES[new]


def test_the_sandbox_fallback_model_is_the_catalog_default():
    """The sandbox cannot import the catalog, so it keeps a literal — which the
    runner always overrides, and which must not drift from what it overrides
    it with: the default class, resolved on the default provider. The
    FILE_CRED_PREFIX discipline, for the one copy that remains."""
    source = (REPO / "yaaf" / "runner" / "sandbox_entry.py").read_text()
    match = re.search(r'os\.environ\.get\("YAAF_MODEL", "([^"]+)"\)', source)
    assert match, "the sandbox no longer reads YAAF_MODEL with a fallback"
    assert match.group(1) == catalog.resolve(catalog.DEFAULT_MODEL, providers.DEFAULT_PROVIDER)


def test_every_file_the_loop_imports_is_in_the_image():
    dockerfile = (REPO / "deploy" / "sandbox" / "Dockerfile").read_text()
    for path in ("yaaf/runner/loop_entry.py", "yaaf/runner/loop_harness.py",
                 "yaaf/runtimes/wire.py", "yaaf/runner/sandbox_entry.py"):
        assert f"COPY {path} /opt/yaaf/{path.rsplit('/', 1)[-1]}" in dockerfile, path
    from yaaf.runner.backends import get_backend

    assert get_backend("yaaf_loop").entrypoint == "loop_entry.py"
    assert get_backend("anthropic_api").entrypoint == "loop_entry.py"


# ── what the registries decide, unchanged from before they existed ──────────────


def test_a_mode_nobody_registered_is_read_as_the_api_key():
    """The two-line split the runner always had: anything but "subscription"
    went out as ANTHROPIC_API_KEY. Kept, because only a hand-edited row can
    carry another mode, and refusing it would be a new rule smuggled into a
    refactor."""
    assert providers.credential_env(None, "something-else", "v") == {"ANTHROPIC_API_KEY": "v"}
    assert providers.shape_error(None, "something-else", "sk-ant-oat-x") is None


def test_an_unknown_model_is_served_by_the_default_provider():
    assert catalog.provider_of("a-model-nobody-listed") == providers.DEFAULT_PROVIDER
    assert catalog.tier("a-model-nobody-listed") == 0


def test_a_bare_class_has_no_provider_until_it_is_resolved():
    """A reader that hands a class to a provider without resolving it fails
    here, in a test — not by billing the default provider in production."""
    with pytest.raises(catalog.Unresolved):
        catalog.provider_of("@balanced")
    assert catalog.provider_of("openai/@balanced") == "openai"


def test_the_project_runtime_overrides_the_seat_and_nothing_else():
    assert effective_harness("claude_agent_sdk", None) == "claude_agent_sdk"
    assert effective_harness("claude_agent_sdk", harnesses.LEGACY_RUNTIME) == "claude_agent_sdk"
    assert effective_harness("claude_agent_sdk", "mock") == "mock"


# ── a run says what it ran on ───────────────────────────────────────────────────


def _lease(client, slug: str, **project) -> list:
    created = make_project(client, slug=slug, **project)
    with db_session() as session:
        dispatch_once(session)
    jobs = [j for j in lease_and_prepare(limit=10) if j.project_id == created["id"]]
    assert jobs, "starting a project should wake its CEO"
    return jobs


def test_a_leased_run_is_pinned_to_its_provider_and_harness(client):
    job = _lease(client, "pinned")[0]
    assert (job.provider, job.harness) == ("anthropic", "claude_agent_sdk")
    with db_session() as session:
        run = session.get(Run, job.run_id)
        assert (run.provider, run.harness, run.model) == (
            "anthropic", "claude_agent_sdk", job.model)


def test_a_mock_project_pins_the_harness_that_actually_ran(client):
    """The scripted backend executes every seat of a mock project, so that is
    what the row says. A run attributed to a harness it never touched is the
    misattribution `model` was pinned at lease time to prevent."""
    job = _lease(client, "scripted", integrations={"runtime": "mock"})[0]
    with db_session() as session:
        assert session.get(Run, job.run_id).harness == "mock"


class _Fails:
    def __init__(self, summary: str, error_kind: str | None):
        self.result = RunResult(ok=False, summary=summary, error_kind=error_kind)

    def execute(self, job, stop_event=None):
        return self.result


def _credential_failures(project_id: str) -> int:
    with db_session() as session:
        return session.query(AuditLog).filter(
            AuditLog.project_id == project_id,
            AuditLog.action == "billing.credential_failure").count()


def test_a_harness_that_says_auth_is_believed_without_a_vendor_string(client):
    """The pause on a dead credential used to need one vendor's error text in
    the summary. A harness that classified the failure itself is read first —
    which is what lets a second vendor's dead key pause a project at all."""
    job = _lease(client, "dead-key")[0]
    execute_job(job, backend=_Fails("the provider refused us", error_kind="auth"))
    assert _credential_failures(job.project_id) == 1
    with db_session() as session:
        assert session.get(Run, job.run_id).error_kind == "auth"


def test_a_failure_the_harness_did_not_call_auth_is_not_a_dead_credential(client):
    job = _lease(client, "plain-failure")[0]
    execute_job(job, backend=_Fails("the model gave up", error_kind="fatal"))
    assert _credential_failures(job.project_id) == 0


def test_the_vendor_string_still_counts_when_the_harness_said_nothing(client):
    """A run that died before its harness could classify anything — no result
    file, only a log tail — is still read the old way."""
    job = _lease(client, "old-reading")[0]
    execute_job(job, backend=_Fails("Not logged in · Please run /login", error_kind=None))
    assert _credential_failures(job.project_id) == 1


# ── the harness says how a run failed ───────────────────────────────────────────


def _sandbox_main_raising(monkeypatch, tmp_path, error: Exception) -> dict:
    # Skipped per test, not per file: everything above runs without the SDK.
    pytest.importorskip("claude_agent_sdk")
    for name, value in {"YAAF_API_BASE_URL": "http://api:8080", "YAAF_API_TOKEN": "t",
                        "YAAF_RUN_ID": "run_test", "YAAF_TOOL_PERMISSIONS": "{}"}.items():
        monkeypatch.setenv(name, value)
    import yaaf.runner.sandbox_entry as mod

    entry = importlib.reload(mod)
    (tmp_path / "system_prompt-run_test.txt").write_text("You are the CEO.")
    (tmp_path / "context-run_test.json").write_text("{}")
    monkeypatch.setattr(entry, "STAGING", tmp_path)
    monkeypatch.setattr(entry, "RESUME_BACKOFF_S", (0.0, 0.0))

    async def fake_query(prompt, options):
        if False:  # pragma: no cover — make this an async generator
            yield
        raise error

    monkeypatch.setattr(entry, "query", fake_query)
    assert asyncio.run(entry.main()) == 1
    return json.loads((tmp_path / "result-run_test.json").read_text())


def test_the_sandbox_classifies_a_dead_credential_as_auth(monkeypatch, tmp_path):
    result = _sandbox_main_raising(monkeypatch, tmp_path,
                                   RuntimeError("Not logged in: please run /login"))
    assert result["error_kind"] == "auth"


def test_the_sandbox_classifies_anything_else_as_fatal(monkeypatch, tmp_path):
    result = _sandbox_main_raising(monkeypatch, tmp_path, RuntimeError("the model exploded"))
    assert result["error_kind"] == "fatal"


def test_retries_spent_on_a_flaky_network_say_transient(monkeypatch, tmp_path):
    result = _sandbox_main_raising(monkeypatch, tmp_path, RuntimeError("connection reset"))
    assert result["error_kind"] == "transient"


def test_a_credential_for_a_provider_this_install_lacks_fails_its_own_project_only(client):
    """A row a newer release wrote, read after a rollback. It must not raise in
    the lease — one exception there aborts the transaction leasing every other
    project's runs too — so it is refused the way a file-shaped billing
    credential is: nothing injected, and this project's runs fail as
    unauthenticated until the credential is fixed or replaced."""
    from yaaf.core.models import Project, Secret
    from yaaf.runner.daemon import _billing_env

    created = client.post("/admin/settings/llm-credentials", headers=ADMIN, json={
        "name": "From the future", "value": "test-credential-value", "mode": "api_key"})
    assert created.status_code == 201, created.text
    project = make_project(client, slug="rolled-back", start=False,
                           billing_secret_id=created.json()["id"])
    with db_session() as session:
        session.get(Secret, created.json()["id"]).provider = "a-provider-from-m3"
    with db_session() as session:
        assert _billing_env(session, session.get(Project, project["id"])) == {}


# ── prices: every run is priced, and the price table is checked ─────────────────


class _Returns:
    def __init__(self, result: RunResult):
        self.result = result

    def execute(self, job, stop_event=None):
        return self.result


def test_a_finished_run_books_its_priced_cost_beside_its_reported_one(client):
    job = _lease(client, "priced")[0]
    # The lease hands the sandbox the model's price, which it cannot look up.
    assert job.price == catalog.price(job.model).as_dict()
    execute_job(job, backend=_Returns(RunResult(ok=True, tokens_out=10, cost_usd=1.0,
                                                priced_usd=1.2)))
    with db_session() as session:
        run = session.get(Run, job.run_id)
        assert (run.cost_usd, run.priced_usd) == (1.0, 1.2)


def test_a_model_the_catalog_does_not_list_is_leased_with_no_price(client):
    from yaaf.core.models import Agent

    project = make_project(client, slug="unlisted", start=False)
    with db_session() as session:
        session.get(Agent, project["ceo_agent_id"]).model_override = "claude-mystery-9"
    client.post(f"/admin/projects/{project['id']}/start", headers=ADMIN)
    with db_session() as session:
        dispatch_once(session)
    job = next(j for j in lease_and_prepare(limit=10) if j.project_id == project["id"])
    assert job.model == "claude-mystery-9"
    # No price, and nothing the catalog knows about it — except that on
    # Anthropic's own wire an unlisted model takes yaaf's effort names.
    assert job.price is None
    # No window known either: the loop compacts on the provider's refusal.
    assert job.model_flags == {"effort": True, "web_tools": "basic", "efforts": None,
                               "context": None}


def _bills(project_id: str, agent_id: str, model: str, reported: float, priced: float,
           harness: str = "claude_agent_sdk", runs: int = 10) -> None:
    from yaaf.core import ids

    with db_session() as session:
        for _ in range(runs):
            session.add(Run(id=ids.new_id(ids.RUN), project_id=project_id, agent_id=agent_id,
                            kind="agent_run", trigger={}, status="done", model=model,
                            harness=harness, cost_usd=reported, priced_usd=priced))


def _gap_checks(client, project_id: str) -> list[dict]:
    checks = client.get(f"/observe/projects/{project_id}/health", headers=ADMIN).json()["checks"]
    return [c for c in checks if c["key"].startswith("price-gap:")]


def _on_the_loop(client, slug: str) -> dict:
    from yaaf.core.models import Agent

    project = make_project(client, slug=slug, start=False)
    with db_session() as session:
        session.get(Agent, project["ceo_agent_id"]).harness_override = "anthropic_api"
    return project


def test_a_price_table_that_drifts_from_the_bills_is_on_the_health_checklist(client):
    """The catalog meters every seat on the loop, so it is checked against the
    bills the self-reporting harnesses send for the same model."""
    project = _on_the_loop(client, "drifted")
    model = DEFAULT
    _bills(project["id"], project["ceo_agent_id"], model, reported=0.50, priced=0.60)
    [gap] = _gap_checks(client, project["id"])
    assert gap["key"] == f"price-gap:{model}" and gap["status"] == "warn"
    assert "20% above" in gap["detail"] and "over-counted" in gap["detail"]
    assert "yaaf agent loop (the provider's own API)" in gap["detail"]
    assert catalog.price(model).as_of in gap["detail"]


def test_a_price_table_within_tolerance_says_nothing(client):
    project = _on_the_loop(client, "close-enough")
    _bills(project["id"], project["ceo_agent_id"], DEFAULT,
           reported=0.50, priced=0.52)
    assert _gap_checks(client, project["id"]) == []


def test_a_handful_of_small_runs_cannot_raise_it(client):
    project = _on_the_loop(client, "too-little")
    _bills(project["id"], project["ceo_agent_id"], DEFAULT,
           reported=0.01, priced=0.02, runs=5)
    assert _gap_checks(client, project["id"]) == []


def test_a_project_metered_by_its_own_bills_is_not_told_about_the_table(client):
    """Nothing it runs is metered by the catalog; the drift is not its problem."""
    project = make_project(client, slug="self-billed", start=False)
    _bills(project["id"], project["ceo_agent_id"], DEFAULT,
           reported=0.50, priced=0.60)
    assert _gap_checks(client, project["id"]) == []


def test_the_loops_own_runs_are_not_evidence_for_the_table(client):
    """A priced run's two figures are the same number: counting it would only
    pull the gap toward zero."""
    project = _on_the_loop(client, "circular")
    _bills(project["id"], project["ceo_agent_id"], DEFAULT,
           reported=0.50, priced=0.60)
    _bills(project["id"], project["ceo_agent_id"], DEFAULT,
           reported=5.0, priced=5.0, harness="anthropic_api")
    assert len(_gap_checks(client, project["id"])) == 1


@pytest.mark.parametrize(("harness", "named"), [
    ("claude_cli", "crm (mcp__crm__*)"),
    ("gemini_cli", "crm (mcp_crm_*)"),
    ("opencode", "crm (crm_*)"),
])
def test_a_prompt_names_a_data_sources_tools_as_the_seats_harness_shows_them(harness, named):
    """The data-source paragraph told every model its tools were `mcp__crm__*`
    — the Claude harnesses' names. A model told a name its harness does not
    use looks for a tool it does not have."""
    from yaaf.runner.daemon import _data_source_prompt
    from yaaf.runtimes.harnesses import HARNESSES

    assert f"Mounted as tools: {named}" in _data_source_prompt([{"name": "crm"}], harness)
    for found in HARNESSES.values():
        assert "{server}" in found.tool_ref and "{tool}" in found.tool_ref, found.name
