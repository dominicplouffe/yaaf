"""Invariant selftests: the Amazon PPC Factory, a fifth shape, and its audit harness.

An audit is an investigation the crew runs; the platform holds what makes the
result trustworthy. Attacked here, each over HTTP the way an agent or a setup
file would:

  * the shape: an audit reads as Deliver, operate as Operate; ppc_mode is a
    PPC Factory's word only; operate is refused at creation for now
  * the lead: a PPC Factory is led by the PPC Lead, filled when unnamed,
    refused when another lead is named, and the PPC Lead leads nothing else
  * the data decides what can be investigated: read from the tools the gateway
    recorded, only reads he allowed and still listed; no data, no plan
  * every seat gets the playbook as skills and the kit as a file
  * every pull is on file: a PPC read through the gateway carries a receipt,
    and a claim citing a receipt not on file, or a field the pull did not
    contain, is refused
  * only Amazon's data: a field somebody typed into a platform (target ACOS,
    max ACOS, tags, cost of goods) is refused wherever it is cited
  * a claim about an area the data cannot reach is refused, and the platform
    lists those areas as not covered itself
  * load-bearing claims say how they were tested; every move is executable;
    tiers hold together with their moves and a tested forecast
  * draft until reviewed: the pages say draft, a seat cannot review its own
    audit, a review of an older version or one that skips a load-bearing
    number is refused, and only a passing review makes the audit final and
    lets it complete
  * the kit's arithmetic: statistics, economics, bids, forecast, backtest
"""

from __future__ import annotations

import base64
import json

import pytest

from tests.conftest import ADMIN, agent_by_name, bearer, live_token, make_project
from yaaf import ppc as catalogue
from yaaf.core import ids, shape
from yaaf.core.constitution import effective
from yaaf.core.db import db_session
from yaaf.core.models import (
    Agent,
    Artifact,
    AuditLog,
    DataSourceTool,
    Project,
    ToolPermission,
    utcnow,
)
from yaaf.server import policy
from yaaf.server import ppc as server_ppc


def audit(client, slug="acme-ads", **kwargs) -> dict:
    return make_project(client, slug=slug, mode="ppc_factory", **kwargs)


def _body(slug, **extra):
    return {"slug": slug, "name": "X", "goal": "Audit the Acme Amazon advertising account",
            "chairman_email": "chair@example.com",
            "teams": [{"role_slug": "worker", "name": "A", "employees": ["E1"]}], **extra}


def connect(project_id: str, *tools: tuple[str, str], source="qore",
            permission=ToolPermission.ALLOW, kind="read", gone=False) -> None:
    """Record tools as the gateway would after listing a source."""
    with db_session() as session:
        for name, description in tools:
            session.add(DataSourceTool(
                id=ids.new_id(ids.DATA_SOURCE_TOOL), project_id=project_id, source=source, name=name,
                description=description, kind=kind, kind_reason="test",
                permission=permission, decided_by="default",
                gone_at=utcnow() if gone else None))


#: The tools a Trellis Qore MCP lists, by name — enough to support every lever.
QORE = (("get_stats", "Retrieve aggregated performance statistics: sales, cost, acos"),
        ("query_dataset", "Execute a structured query against any registered dataset"),
        ("get_keyword_recommendations", "Keyword recommendations with suggested bid"),
        ("get_amc_report_data", "Amazon Marketing Cloud report rows"))


def _row(project) -> Project:
    with db_session() as session:
        row = session.get(Project, project["id"])
        session.expunge(row)
        return row


def publish(client, project, name, payload) -> object:
    _, token = live_token(project["id"], project["ceo_agent_id"])
    data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return client.post("/agent/artifacts", headers=bearer(token), json={
        "filename": name, "content_base64": base64.b64encode(data).decode()})


def receipt(project_id: str, *fields: str, tool="query_dataset") -> str:
    """A pull on file, as the gateway records one."""
    rows = [{f: 1 for f in fields}]
    result = {"content": [{"type": "text", "text": json.dumps({"rows": rows})}]}
    from yaaf.core.models import AgentKind

    with db_session() as session:
        project = session.get(Project, project_id)
        agent = session.query(Agent).filter_by(project_id=project_id,
                                               kind=AgentKind.CEO).first()
        return server_ppc.record_read(session, project, agent, "qore", tool, {}, result, None)


FIELDS = {
    "perf": ("campaign_id", "campaign_name", "date", "spend", "sales", "clicks", "orders",
             "impressions", "search_term", "keyword_text", "match_type", "bid",
             "daily_budget", "serving_hours"),
    "econ": ("asin", "price", "referral_fee", "fba_fee", "repeat_customers",
             "unique_customers", "orders"),
    "stock": ("asin", "fulfillable_quantity", "units_shipped_last_30_days"),
}


def good_report(project_id: str, **over) -> dict:
    """A report a crew would publish for a covered account, citing real receipts."""
    perf = receipt(project_id, *FIELDS["perf"])
    econ = receipt(project_id, *FIELDS["econ"])
    claims = [
        {"id": "C1", "kind": "context", "area": "baseline", "confidence": "high",
         "statement": "$61,000 a month of ads at 27% ACoS and 9% TACoS.",
         "evidence": [{"receipt": perf, "fields": ["spend", "sales"]}]},
        {"id": "C2", "kind": "context", "area": "economics", "confidence": "high",
         "statement": "An order earns $20 after Amazon's fees before product cost.",
         "evidence": [{"receipt": econ, "fields": ["price", "referral_fee", "fba_fee"]}]},
        {"id": "C3", "kind": "opportunity", "area": "lifetime_value", "confidence": "medium",
         "monthly_usd": 6400,
         "statement": "Customers reorder: acquisition can afford about 90% ACoS.",
         "evidence": [{"receipt": econ, "fields": ["repeat_customers", "unique_customers"],
                       "script": "analysis/ltv.py"}],
         "test": {"method": "repeat-rate model, per ASIN, ±30% sensitivity",
                  "result": "ceiling 70-110%"},
         "falsifier": "repeat rate under 20% on the next quarter's cohort",
         "rows": [{"asin": "B0A", "repeat_rate": 0.41}]},
        {"id": "C4", "kind": "works", "area": "campaigns", "confidence": "high",
         "statement": "Exact-match generic campaigns convert at 14%.",
         "evidence": [{"receipt": perf, "fields": ["clicks", "orders"]}]},
        {"id": "C5", "kind": "forecast", "area": "scale", "confidence": "medium",
         "statement": "Spend-response curves fitted per pool on 120 days.",
         "evidence": [{"receipt": perf, "fields": ["date", "spend", "sales"],
                       "script": "analysis/forecast.py"}],
         "test": {"method": "backtest on 12 campaigns whose spend moved",
                  "result": "model missed by 14%, naive by 27%"}},
    ]
    moves = [{"id": "M1", "action": "budget",
              "entity": {"type": "campaign", "name": "SP - Line 1", "campaign_id": "1001"},
              "today": 150, "change_to": 210, "why": "out of budget by 14:00",
              "expected_monthly": {"spend": 1800, "sales": 6400}, "week": 1,
              "check": {"day": 10, "expect": "serving 20h+, ACoS under 31%"},
              "stop_rule": "ACoS over 34% after 60 extra clicks: back to $150",
              "claims": ["C3"], "tier": 10000}]
    tiers = [{"monthly_usd": t, "fits_usd": 1800, "sales_low": 5000, "sales_mid": 6400,
              "sales_high": 7800, "acos": 26.9, "tacos": 9.1, "basis": "acos",
              "forecast_claim": "C5", "moves": ["M1"],
              "binding": "only one capped winner; search volume caps the rest"}
             for t in catalogue.SCALE_TIERS]
    report = {"account": "Acme", "marketplace": "USA", "account_type": "seller",
              "window": {"start": "2026-09-01", "end": "2026-10-30"},
              "answer": {"headline": "Healthy account under-investing in new customers.",
                         "top": ["C3"], "scale": "About $1,800 more a month fits."},
              "business": {"portrait": "Vitamins, $30 average, 41% of buyers reorder.",
                           "claims": ["C2"]},
              "economics": {"bands": [{"job": "acquire", "today_acos": 31,
                                       "affordable_acos": [70, 110],
                                       "basis": "12-month value of a new customer"}],
                            "claims": ["C2", "C3"]},
              "chapters": [{"title": "New customers",
                            "takeaway": "A new customer is worth three first orders.",
                            "claims": ["C3", "C4"]},
                           {"title": "Forecast", "takeaway": "Tested on your own history.",
                            "claims": ["C5"]}],
              "claims": claims, "moves": moves, "tiers": tiers,
              "leave_alone": [{"what": "Exact generic.", "checked": "14% on 3,100 clicks",
                               "claims": ["C4"]}]}
    report.update(over)
    return report


def review(audit_sha: str, report: dict, verdict="pass", **over) -> dict:
    body = {"audit_sha256": audit_sha, "verdict": verdict,
            "criteria": {r: {"verdict": "pass", "note": "checked"} for r in catalogue.CRITERIA},
            "rederived": [{"claim": c, "reported": 1, "rederived": 1, "matches": True,
                           "note": "re-ran the script on the receipt"}
                          for c in catalogue.load_bearing(report)],
            "summary": "Numbers re-derive; the economics page is the story."}
    body.update(over)
    return body


def publish_as(client, project, agent_id, name, payload):
    _, token = live_token(project["id"], agent_id)
    data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return client.post("/agent/artifacts", headers=bearer(token), json={
        "filename": name, "content_base64": base64.b64encode(data).decode()})


# ── the shape, as pure answers ──────────────────────────────────────────────────


@pytest.mark.parametrize("mode,ppc_mode,scope,once,cycles,reads_as", [
    ("ppc_factory", "audit", True, True, False, "deliver"),
    ("ppc_factory", None, True, True, False, "deliver"),
    ("ppc_factory", "operate", True, False, True, "operate"),
])
def test_each_ppc_mode_answers_the_three_questions(mode, ppc_mode, scope, once, cycles,
                                                    reads_as):
    assert shape.has_scope(mode) is scope
    assert shape.ships_once(mode, ppc_mode) is once
    assert shape.runs_on_cycles(mode, ppc_mode) is cycles
    assert shape.behaves_as(mode, ppc_mode) == reads_as
    assert shape.is_ppc_factory(mode) and not shape.is_factory(mode)


def test_only_a_ppc_factory_has_a_ppc_mode():
    assert shape.initial_ppc_mode("deliver", "audit") is None
    assert shape.initial_ppc_mode("ppc_factory", None) == "audit"
    assert shape.ppc_mode({"mode": "code_factory", "factory_phase": "build"}) is None
    assert shape.lead_role("ppc_factory") == shape.PPC_LEAD_ROLE
    assert shape.label("ppc_factory") == "Amazon PPC Factory"


# ── creation ────────────────────────────────────────────────────────────────────


def test_an_audit_is_created_led_by_the_ppc_lead(client):
    project = audit(client)
    row = _row(project)
    assert row.mode == "ppc_factory" and row.ppc_mode == "audit"
    assert row.factory_phase is None
    # Kept with the wiring, so a setup file restores an audit as an audit.
    assert (row.integrations or {}).get("ppc_mode") == "audit"
    seen = client.get(f"/observe/projects/{project['id']}", headers=ADMIN).json()
    assert seen["ppc_mode"] == "audit" and seen["shape"] == "deliver"
    assert seen["ppc"]["data_owed"]
    exported = client.get(f"/admin/projects/{project['id']}/export", headers=ADMIN).json()
    assert exported["project"]["integrations"]["ppc_mode"] == "audit"


def test_ppc_mode_is_a_ppc_factorys_word_and_operate_is_not_open(client):
    audit(client, slug="seed", start=False)  # users and roles
    stray = client.post("/admin/projects", json=_body(
        "stray", mode="deliver", ceo_role_slug="ceo", ppc_mode="audit"), headers=ADMIN)
    assert stray.status_code == 422
    assert "Amazon PPC Factory" in stray.json()["detail"]

    operate = client.post("/admin/projects", json=_body(
        "run-it", mode="ppc_factory", ppc_mode="operate"), headers=ADMIN)
    assert operate.status_code == 422
    assert "not open yet" in operate.json()["detail"]

    # The same refusal reached through a setup file's wiring.
    smuggled = client.post("/admin/projects", json=_body(
        "run-it-2", mode="ppc_factory", integrations={"ppc_mode": "operate"}), headers=ADMIN)
    assert smuggled.status_code == 422

    invented = client.post("/admin/projects", json=_body(
        "x-mode", mode="ppc_factory", ppc_mode="manage"), headers=ADMIN)
    assert invented.status_code == 422


def test_the_ppc_lead_leads_ppc_factories_and_nothing_else(client):
    audit(client, slug="seed", start=False)
    other_lead = client.post("/admin/projects", json=_body(
        "wrong-lead", mode="ppc_factory", ceo_role_slug="ceo"), headers=ADMIN)
    assert other_lead.status_code == 422
    assert shape.PPC_LEAD_ROLE in other_lead.json()["detail"]

    borrowed = client.post("/admin/projects", json=_body(
        "borrowed", mode="deliver", ceo_role_slug=shape.PPC_LEAD_ROLE), headers=ADMIN)
    assert borrowed.status_code == 422
    assert "Amazon PPC Factory projects only" in borrowed.json()["detail"]


# ── the data decides the levers ─────────────────────────────────────────────────


def test_a_general_query_tool_answers_every_report_backed_question():
    caps = catalogue.tool_capabilities("query_dataset", "anything")
    assert "placements" in caps and "search_market" in caps
    # AMC runs its own queries and recommendations are Amazon's: neither is a
    # dataset a general query reads.
    assert "amc" not in caps and "keyword_recommendations" not in caps
    assert catalogue.tool_capabilities("send_email", "Send a message") == set()


def test_only_allowed_listed_reads_count(client):
    project = audit(client)
    connect(project["id"], ("get_campaign_placement_stats", "Placement performance"),
            permission=ToolPermission.DENY)
    connect(project["id"], ("search_term_report", "Search terms"), gone=True)
    connect(project["id"], ("update_bids", "Change keyword bids"), kind="write")
    with db_session() as session:
        assert server_ppc.offered(session, session.get(Project, project["id"])) == set()


def test_a_narrow_source_supports_only_its_levers(client):
    project = audit(client)
    connect(project["id"], ("list_campaigns", "Campaigns with ACOS, spend and budget"),
            ("search_term_report", "Search terms that triggered the ads"))
    with db_session() as session:
        cov = server_ppc.coverage(session, session.get(Project, project["id"]))
    by_key = {s["key"]: s for s in cov["sections"]}
    assert by_key["baseline"]["available"] and by_key["incrementality"]["available"]
    assert not by_key["inventory"]["available"]
    assert not by_key["demand"]["available"]
    assert not cov["scale"]["available"]  # no target-level performance


# ── no data, no plan ────────────────────────────────────────────────────────────


def test_the_plan_cannot_hold_without_ad_data(client):
    project = audit(client)
    with db_session() as session:
        row = session.get(Project, project["id"])
        missing = policy.evaluate_plan(session, row, effective(session, row.id))
    assert any("no advertising data" in m for m in missing)

    connect(project["id"], *QORE)
    with db_session() as session:
        row = session.get(Project, project["id"])
        missing = policy.evaluate_plan(session, row, effective(session, row.id))
    assert not any("no advertising data" in m for m in missing)
    steps = client.get(f"/observe/projects/{project['id']}/setup", headers=ADMIN)
    assert steps.status_code == 200, steps.text
    ads = [s for s in steps.json()["steps"] if s["key"] == "ads_data"]
    assert ads and ads[0]["state"] == "done" and ads[0]["required"]


# ── the playbook and the kit reach every seat ───────────────────────────────────


def test_every_seat_gets_the_playbook_as_skills(client):
    from yaaf.runner.daemon import _offer_kind_skills
    from yaaf.runner.types import RunJob

    names = [s["name"] for s in catalogue.playbook()]
    assert names[0] == "ppc-audit-runbook"
    assert {"ppc-business-economics", "ppc-statistics", "ppc-demand-sqp",
            "ppc-review-rubric", "ppc-execution-plan"} <= set(names)
    assert all(s["description"] and len(s["body"]) > 500 for s in catalogue.playbook())

    project = audit(client)
    other = make_project(client, slug="plain")
    for proj, expected in ((project, set(names)), (other, set())):
        job = RunJob.__new__(RunJob)
        job.tool_permissions = {"skills": [{"name": "own", "description": "d", "body": "b"}]}
        _offer_kind_skills(_row(proj), job)
        got = {s["name"] for s in job.tool_permissions["skills"]}
        assert got == {"own"} | expected


def test_the_brief_points_at_the_runbook_and_the_reach(client):
    from yaaf.server.contextpack import build_system_prompt

    project = audit(client)
    connect(project["id"], ("list_campaigns", "Campaigns with ACOS, spend and budget"))
    with db_session() as session:
        row = session.get(Project, project["id"])
        prompt = build_system_prompt(session, session.get(Agent, project["ceo_agent_id"]), row)
    assert "ppc-audit-runbook" in prompt
    assert "inventory: NOT REACHABLE" in prompt
    assert catalogue.REPORT in prompt and catalogue.REVIEW in prompt


def test_the_kit_is_served_verbatim_to_a_ppc_factory_only(client):
    project = audit(client)
    _, token = live_token(project["id"], project["ceo_agent_id"])
    got = client.get("/agent/ppc/kit", headers=bearer(token))
    assert got.status_code == 200, got.text
    assert got.json()["source"] == catalogue.kit_source()
    plain = make_project(client, slug="plain")
    _, token = live_token(plain["id"], plain["ceo_agent_id"])
    assert client.get("/agent/ppc/kit", headers=bearer(token)).status_code == 422


def test_the_kit_runs_on_the_standard_library_alone():
    import ast

    tree = ast.parse(catalogue.kit_source())
    imported = {n.names[0].name.split(".")[0] for n in ast.walk(tree)
                if isinstance(n, ast.Import)} | \
        {n.module.split(".")[0] for n in ast.walk(tree)
         if isinstance(n, ast.ImportFrom) and n.module}
    assert imported <= {"__future__", "math", "dataclasses", "types"}, imported


# ── every pull is on file ────────────────────────────────────────────────────────


def test_a_ppc_read_through_the_gateway_carries_its_receipt(client, monkeypatch):
    from tests import fake_mcp

    vendor = fake_mcp.install(monkeypatch)
    vendor.results["get_stats"] = {"content": [{"type": "text", "text": json.dumps(
        {"rows": [{"campaign_id": "1", "spend": 10.0, "sales": 40.0}]})}]}
    project = audit(client)
    attached = client.post(f"/admin/projects/{project['id']}/mcp_servers", headers=ADMIN, json={
        "name": "qore", "url": "https://vendor.example.com/mcp", "bearer": "t",
        "roles": [shape.PPC_LEAD_ROLE], "access": "bulk"})
    assert attached.status_code == 200, attached.text
    _, token = live_token(project["id"], project["ceo_agent_id"])
    result = fake_mcp.rpc(client, "qore", bearer(token), "tools/call",
                          {"name": "get_stats", "arguments": {"days": 30}})["result"]
    rid = result["_meta"]["yaaf/receipt"]
    with db_session() as session:
        row = session.get(AuditLog, rid)
        assert row.project_id == project["id"] and row.action == server_ppc.READ_ACTION
        assert {"campaign_id", "spend", "sales"} <= set(row.detail["fields"])

    # The same read on any other project is not written down.
    plain = make_project(client, slug="plain")
    client.post(f"/admin/projects/{plain['id']}/mcp_servers", headers=ADMIN, json={
        "name": "qore", "url": "https://vendor.example.com/mcp", "bearer": "t",
        "roles": ["worker"], "access": "bulk"})
    _, token = live_token(plain["id"], agent_by_name(plain, "Lead"))
    result = fake_mcp.rpc(client, "qore", bearer(token), "tools/call",
                          {"name": "get_stats", "arguments": {}})["result"]
    assert "yaaf/receipt" not in (result.get("_meta") or {})


def test_a_claim_must_cite_a_pull_on_file_and_a_field_it_returned(client):
    project = audit(client)
    connect(project["id"], *QORE)
    report = good_report(project["id"])
    report["claims"][0]["evidence"][0]["receipt"] = "audt_invented"
    refused = publish(client, project, catalogue.REPORT, report)
    assert refused.status_code == 422
    assert "not a data pull recorded for this project" in refused.json()["error"]

    report = good_report(project["id"])
    report["claims"][0]["evidence"][0]["fields"] = ["spend", "ordered_product_sales"]
    refused = publish(client, project, catalogue.REPORT, report)
    assert "did not contain" in refused.json()["error"]

    # Another project's pull is not this project's.
    other = audit(client, slug="other-ads")
    report = good_report(project["id"])
    report["claims"][0]["evidence"][0]["receipt"] = receipt(other["id"], "spend", "sales")
    assert publish(client, project, catalogue.REPORT, report).status_code == 422


# ── only Amazon's data ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("field,kind", [
    ("attributed_sales_14d", "amazon"), ("top_of_search_impression_share", "amazon"),
    ("search_query_volume", "amazon"), ("repeat_customers", "amazon"),
    ("return_reason", "amazon"), ("subscribe_and_save_units", "amazon"),
    ("purchases_brand_share", "amazon"), ("amc:ntb_purchases", "amazon"),
    ("serving_hours", "derived"), ("days_of_cover", "derived"), ("tacos", "derived"),
    ("target_acos", "entered"), ("max_acos", "entered"), ("tags", "entered"),
    ("total_cogs", "entered"), ("margin", "entered"), ("campaign_goal", "entered"),
    ("trellis_score", "entered"), ("vibes", "unknown"),
])
def test_each_field_is_amazons_derived_or_refused(field, kind):
    from yaaf.ppc import fields

    assert fields.classify(field) == kind


@pytest.mark.parametrize("field,says", [
    ("target_acos", "somebody entered"), ("total_cogs", "somebody entered"),
    ("vibes", "not recognised"),
])
def test_an_entered_field_is_refused_wherever_it_is_cited(client, field, says):
    project = audit(client)
    connect(project["id"], *QORE)
    report = good_report(project["id"])
    report["claims"][2]["evidence"][0]["fields"].append(field)
    refused = publish(client, project, catalogue.REPORT, report)
    assert refused.status_code == 422 and says in refused.json()["error"]


# ── the report is checked on publish ────────────────────────────────────────────


def test_a_good_report_is_accepted_as_a_draft(client):
    project = audit(client)
    connect(project["id"], *QORE)
    put = publish(client, project, catalogue.REPORT, good_report(project["id"]))
    assert put.status_code == 200, put.text
    stored = _load(project, catalogue.REPORT)
    assert set(stored["load_bearing"]) == {"C2", "C3", "C5"}
    for name in catalogue.PLATFORM_PAGES:
        assert _load(project, name, raw=True)
    assert "Draft — not yet reviewed" in _load(project, catalogue.REPORT_DECK, raw=True)
    assert "Draft" in _load(project, catalogue.REPORT_MD, raw=True)
    with db_session() as session:
        assert not server_ppc.reviewed(session, session.get(Project, project["id"]))


def test_the_pages_are_the_platforms_to_write(client):
    project = audit(client)
    for name in (*catalogue.PLATFORM_PAGES, "ppc-analysis.json"):
        refused = publish(client, project, name, b"# a page I wrote")
        assert refused.status_code == 422, name
        assert "written by the platform" in refused.json()["error"]


@pytest.mark.parametrize("break_it,says", [
    (lambda r: r.pop("economics"), "economics is required"),
    (lambda r: r["economics"]["bands"][0].update(affordable_acos="lots"), "affordable_acos"),
    (lambda r: r.pop("account_type"), "account_type"),
    (lambda r: r["business"].update(portrait=""), "business.portrait"),
    (lambda r: r["chapters"][0].update(takeaway=""), "no chapter without a takeaway"),
    (lambda r: r["answer"].update(top=["C9"]), "C9"),
    (lambda r: r["claims"][2].pop("test"), "load-bearing"),
    (lambda r: r["claims"][4].pop("test"), "load-bearing"),
    (lambda r: r["claims"][2].update(kind="guess"), "kind is one of"),
    (lambda r: r["claims"][3].update(evidence=[{"source": "https://blog.example"}]),
     "only a context claim"),
    (lambda r: r["claims"][2].update(evidence=[]), "evidence is required"),
    (lambda r: r.update(moves=[]), "no move acts on it"),
    (lambda r: r["moves"][0].pop("stop_rule"), "stop_rule"),
    (lambda r: r["moves"][0].pop("today"), "the value it has now"),
    (lambda r: r["moves"][0].update(week=0), "rollout week"),
    (lambda r: r["moves"][0].pop("check"), "when to look"),
    (lambda r: r["tiers"].pop(), "one per extra monthly budget"),
    (lambda r: r["tiers"][0].update(fits_usd=20_000), "0 to the tier"),
    (lambda r: r["tiers"][1].update(sales_low=9000), "a forecast is a range"),
    (lambda r: r["tiers"][1].update(binding=""), "never pad a tier"),
    (lambda r: r["tiers"][2].update(forecast_claim="C3"), "not a forecast claim"),
    (lambda r: r["tiers"][3].update(fits_usd=9000), "must agree within 15%"),
    (lambda r: r.update(window={"start": "2026-09-01", "end": "2099-01-01"}), "no later"),
])
def test_a_careless_report_is_refused(client, break_it, says):
    project = audit(client)
    connect(project["id"], *QORE)
    report = good_report(project["id"])
    break_it(report)
    refused = publish(client, project, catalogue.REPORT, report)
    assert refused.status_code == 422, refused.text
    assert says in refused.json()["error"]
    with db_session() as session:
        assert session.query(Artifact).filter_by(project_id=project["id"],
                                                 name=catalogue.REPORT).count() == 0


def test_an_area_the_data_cannot_reach_is_not_claimed_and_is_listed(client):
    project = audit(client)
    connect(project["id"], ("list_campaigns", "Campaigns with ACOS, spend and budget"),
            ("keyword_report", "Keyword targeting performance"),
            ("search_term_report", "Search terms that triggered the ads"),
            ("get_listing", "Catalog listing details"))
    report = good_report(project["id"])
    report["claims"].append({
        "id": "C6", "kind": "risk", "area": "inventory", "confidence": "high",
        "statement": "D3 has 12 days of cover.",
        "evidence": [{"receipt": receipt(project["id"], "fulfillable_quantity"),
                      "fields": ["fulfillable_quantity"]}]})
    report["chapters"][0]["claims"].append("C6")
    refused = publish(client, project, catalogue.REPORT, report)
    assert "connected data cannot reach" in refused.json()["error"]

    report["claims"].pop()
    report["chapters"][0]["claims"].pop()
    # lifetime value needs repeat purchase data this source does not offer
    report["claims"][2]["area"] = "economics"
    put = publish(client, project, catalogue.REPORT, report)
    assert put.status_code == 200, put.text
    named = {x["area"]: x for x in _load(project, catalogue.REPORT)["not_covered"]}
    assert {"inventory", "returns", "lifetime_value", "dsp", "demand"} <= set(named)
    assert named["inventory"]["by"] == "platform"


def test_an_owner_answer_is_his_and_labelled(client):
    project = audit(client)
    connect(project["id"], *QORE)
    report = good_report(project["id"], owner_inputs=[
        {"question": "What does a bottle cost you?", "answer": "$4.80 landed"}])
    report["claims"][1]["evidence"].append({"owner": 0})
    assert publish(client, project, catalogue.REPORT, report).status_code == 200
    report["claims"][1]["evidence"][-1] = {"owner": 3}
    refused = publish(client, project, catalogue.REPORT, report)
    assert "index of the owner_inputs answer" in refused.json()["error"]


# ── draft until reviewed ────────────────────────────────────────────────────────


def _published(client):
    project = audit(client)
    connect(project["id"], *QORE)
    report = good_report(project["id"])
    put = publish(client, project, catalogue.REPORT, report)
    assert put.status_code == 200, put.text
    return project, _load(project, catalogue.REPORT), put.json()["sha256"]


def test_a_seat_cannot_review_its_own_audit(client):
    project, report, sha = _published(client)
    refused = publish(client, project, catalogue.REVIEW, review(sha, report))
    assert refused.status_code == 403
    assert "a different seat reviews it" in refused.json()["error"]


def test_a_review_of_another_version_or_skipping_a_number_is_refused(client):
    project, report, sha = _published(client)
    reviewer = agent_by_name(project, "Emp2")
    stale = publish_as(client, project, reviewer, catalogue.REVIEW, review("0" * 64, report))
    assert stale.status_code == 422 and "latest ppc-audit.json" in stale.json()["error"]

    skipped = review(sha, report)
    skipped["rederived"] = [d for d in skipped["rederived"] if d["claim"] != "C3"]
    refused = publish_as(client, project, reviewer, catalogue.REVIEW, skipped)
    assert "load-bearing claim C3 was not re-derived" in refused.json()["error"]

    off = review(sha, report)
    off["rederived"][0].update(reported=6400, rederived=5000)
    refused = publish_as(client, project, reviewer, catalogue.REVIEW, off)
    assert "more than 5% apart" in refused.json()["error"]

    failed = review(sha, report)
    failed["criteria"]["R4"] = {"verdict": "fail", "note": "no recent-vs-prior split"}
    refused = publish_as(client, project, reviewer, catalogue.REVIEW, failed)
    assert "is a return" in refused.json()["error"]


def test_only_a_passing_review_makes_the_audit_final_and_complete(client):
    project, report, sha = _published(client)
    reviewer = agent_by_name(project, "Emp2")
    with db_session() as session:
        row = session.get(Project, project["id"])
        assert any(catalogue.REVIEW in o for o in policy.delivery_outstanding(session, row))

    returned = review(sha, report, verdict="return")
    returned["criteria"]["R8"] = {"verdict": "fail", "note": "M1 has no IDs"}
    assert publish_as(client, project, reviewer, catalogue.REVIEW, returned).status_code == 200
    with db_session() as session:
        assert not server_ppc.reviewed(session, session.get(Project, project["id"]))

    passed = publish_as(client, project, reviewer, catalogue.REVIEW, review(sha, report))
    assert passed.status_code == 200, passed.text
    with db_session() as session:
        row = session.get(Project, project["id"])
        assert server_ppc.reviewed(session, row)
        assert not any(catalogue.REVIEW in o for o in policy.delivery_outstanding(session, row))
    deck = _load(project, catalogue.REPORT_DECK, raw=True)
    assert "Draft — not yet reviewed" not in deck and "re-derived every number" in deck
    seen = client.get(f"/observe/projects/{project['id']}", headers=ADMIN).json()
    assert seen["ppc"]["reviewed"] is True

    # A new version of the audit is a draft again until it is reviewed.
    assert publish(client, project, catalogue.REPORT,
                   good_report(project["id"])).status_code == 200
    with db_session() as session:
        assert not server_ppc.reviewed(session, session.get(Project, project["id"]))


# ── what the owner receives ─────────────────────────────────────────────────────


def _checked(project_id: str, **over) -> dict:
    report = good_report(project_id, **over)
    with db_session() as session:
        project = session.get(Project, project_id)
        return catalogue.check_report(json.dumps(report), server_ppc.offered(session, project),
                                      receipts=server_ppc.receipts(session, project))


def test_the_deck_and_workbook_carry_the_reports_numbers(client):
    import io

    import openpyxl

    from yaaf.ppc import deck, workbook

    project = audit(client)
    connect(project["id"], *QORE)
    report = _checked(project["id"])
    page = deck.render(report, reviewed=True)
    assert page.startswith("<!DOCTYPE html>") and "http" not in page.split("<body>")[1]
    assert "$6,400/mo" in page and "70–110%" in page and "SP - Line 1" in page
    wb = openpyxl.load_workbook(io.BytesIO(workbook.build(report, reviewed=True)))
    assert {"Summary", "Claims", "Plan", "Change sheet", "Limits"} <= set(wb.sheetnames)
    plan = wb["Plan"]
    total = next(r for r in plan.iter_rows(values_only=True) if r[7] == "Total")
    assert str(total[8]).startswith("=SUM(")
    sheet = workbook.change_sheet_csv(report).splitlines()
    assert sheet[0].startswith("Product,Entity,Operation,Campaign ID")
    assert "Campaign,Update,1001" in sheet[1] and ",210," in sheet[1]


def test_the_deck_escapes_what_it_did_not_write(client):
    from yaaf.ppc import deck

    project = audit(client)
    connect(project["id"], *QORE)
    report = _checked(project["id"], account="<script>alert(1)</script>")
    assert "<script>alert(1)" not in deck.render(report)


# ── the kit's arithmetic ────────────────────────────────────────────────────────


def test_zero_orders_is_evidence_only_after_enough_clicks():
    from yaaf.ppc import kit

    assert kit.zero_order_probability(10, 0.10) == pytest.approx(0.3487, abs=1e-3)
    assert kit.zero_order_probability(30, 0.10) == pytest.approx(0.0424, abs=1e-3)
    assert kit.clicks_needed(0.10, 0.95) == 29
    assert kit.one_in(0.05) == "about 1 in 20"


def test_a_real_drop_is_told_from_a_quiet_month():
    from yaaf.ppc import kit

    real = kit.rate_change(30, 200, 2, 180)       # 15% → 1.1%
    assert real["significant"] and real["p_value"] < 0.001
    noise = kit.rate_change(3, 20, 1, 15)          # 15% → 6.7% on tiny counts
    assert not noise["significant"]


def test_small_samples_are_shrunk_and_given_an_interval():
    from yaaf.ppc import kit

    assert kit.shrunk_rate(3, 12, prior_rate=0.10, prior_strength=20) == pytest.approx(5 / 32)
    lo, hi = kit.beta_interval(3, 12)
    assert lo < 0.25 < hi and hi - lo > 0.3
    assert kit.beta_cdf(0.5, 2, 2) == pytest.approx(0.5, abs=1e-9)
    assert kit.prob_rate_below(0, 60, 0.05, 0.10, 20) > 0.9  # Beta(2, 78) below 5%


def test_lifetime_value_can_carry_acquisition_past_first_order_break_even():
    from yaaf.ppc import kit

    first = kit.first_order_break_even(30, 4.5, 5.2, product_cost=6.0)
    assert first["break_even_acos"] == pytest.approx((30 - 4.5 - 5.2 - 6.0) / 30, abs=1e-3)
    unknown = kit.first_order_break_even(30, 4.5, 5.2)
    assert [r["cost_share"] for r in unknown["by_cost_share"]] == list(kit.COST_SHARES)
    subscriber = kit.lifetime_value(first["contribution"], 12.0, monthly_churn=0.05)
    ceiling = kit.ltv_acos_ceiling(subscriber["lifetime_contribution"], 30)
    assert ceiling > 1.0  # a subscription vitamin can afford more than 100% ACoS
    one_off = kit.lifetime_value(first["contribution"], 12.0)
    assert kit.ltv_acos_ceiling(one_off["lifetime_contribution"], 30) == \
        pytest.approx(first["break_even_acos"], abs=1e-3)
    assert kit.payback_months(40, 14.3, 12.0) == pytest.approx((40 - 14.3) / 12)


def test_bid_and_budget_math():
    from yaaf.ppc import kit

    assert kit.target_bid(34, 0.15, 0.28) == 1.43
    assert kit.budget_for_full_day(150, 14.2) == pytest.approx(253.52)
    assert kit.budget_for_full_day(150, 2) == 450  # capped at 3×
    assert kit.placement_multiplier(1.10, 1.60) == 45
    assert kit.placement_multiplier(1.0, 50.0) == 900


# ── the scale forecast ──────────────────────────────────────────────────────────


def _pool(**over):
    return {"name": "p", "kind": "impression_share", "traffic": "non_branded",
            "window_spend": 3_000, "window_sales": 15_000, "impression_share": 0.25,
            "evidence": "x", "fields": ["cost"], **over}


BASE = {"spend": 30_000, "ad_sales": 100_000, "total_sales": 300_000, "ad_orders": 3_000}


def test_the_forecast_never_runs_hotter_than_todays_acos():
    from yaaf.ppc import forecast

    out = forecast.allocate([_pool(), _pool(name="q", kind="budget_capped",
                                            serving_hours=12, impression_share=None)],
                            BASE, 30)
    previous = 0.0
    for tier in out["tiers"]:
        a = tier["at_acos"]
        assert a["absorbable_usd"] <= tier["monthly_usd"] + 0.5
        assert a["expected_acos"] <= out["today"]["acos"] + 1e-6
        assert sum(x["usd"] for x in a["allocation"]) == pytest.approx(
            a["absorbable_usd"], abs=2)
        assert a["absorbable_usd"] + 1 >= previous  # a bigger budget never fits less
        previous = a["absorbable_usd"]
        if tier["at_tacos"]:
            assert tier["at_tacos"]["expected_tacos"] <= out["today"]["tacos"] + 1e-6


def test_the_forecast_never_fills_past_the_evidence():
    from yaaf.ppc import forecast

    out = forecast.allocate([_pool(window_spend=500, window_sales=5_000)], BASE, 30)
    top = out["tiers"][-1]
    assert top["at_acos"]["absorbable_usd"] < 100_000
    assert top["note"] and "nowhere proven to go" in top["note"]
    # A lower ceiling from stock cover is honoured; it can only shrink the room.
    capped = forecast.allocate([_pool(window_spend=500, window_sales=5_000,
                                      ceiling_sales_usd=6_000)], BASE, 30)
    assert capped["tiers"][-1]["at_acos"]["absorbable_usd"] < \
        top["at_acos"]["absorbable_usd"]


def test_the_curve_is_fitted_from_history_and_shrunk_when_thin():
    import random

    from yaaf.ppc import forecast

    rng = random.Random(7)
    true_b = 0.45
    history = []
    for _ in range(90):
        spend = 50 * rng.uniform(0.5, 2.5)
        history.append([spend, 40 * spend ** true_b * rng.uniform(0.95, 1.05)])
    b, basis = forecast.fit_exponent(history, prior=0.6)
    w = 90 / (90 + forecast.SHRINK_DAYS)
    assert b == pytest.approx(w * true_b + (1 - w) * 0.6, abs=0.03)
    assert "fitted" in basis

    thin, why = forecast.fit_exponent(history[:10], prior=0.6)
    assert thin == 0.6 and "assumed" in why
    flat, why = forecast.fit_exponent([[100, 400 + i] for i in range(40)], prior=0.6)
    assert flat == 0.6 and "barely moved" in why


def test_branded_traffic_counts_little_toward_tacos():
    from yaaf.ppc import forecast

    # A cheap pool on an account with room under its TACOS, so both can fit some.
    base = {**BASE, "total_sales": 150_000}
    branded = forecast.allocate([_pool(traffic="branded", window_sales=80_000)], base, 30)
    generic = forecast.allocate([_pool(window_sales=80_000)], base, 30)
    assert generic["tiers"][0]["at_tacos"]["absorbable_usd"] > 0
    assert branded["tiers"][0]["at_tacos"]["absorbable_usd"] < \
        generic["tiers"][0]["at_tacos"]["absorbable_usd"]


# ── the platform's own first pass (leads, not findings) ─────────────────────────────────────────────────

from datetime import date, timedelta  # noqa: E402

from tests.ppc_fake_qore import BAIT, TOOLS, Account  # noqa: E402

TODAY = date.today()


def _pulled(healthy=False):
    from yaaf.ppc import data as D
    from yaaf.ppc.adapters import qore

    start, end = D.default_window(TODAY)
    history = (date.fromisoformat(start) - timedelta(days=60)).isoformat()
    return D.validate(qore.pull(Account(healthy=healthy), start=start, end=end,
                                history_start=history))


def test_the_adapter_reads_amazons_numbers_and_nothing_else():
    bundle = _pulled()
    assert bundle["missing"] == []
    text = json.dumps(bundle)
    for field in BAIT:
        assert f'"{field}"' not in text, field


def test_the_checks_find_what_was_planted():
    from yaaf.ppc import checks

    found = checks.run(_pulled())["findings"]
    assert set(found) >= {"search_term_waste", "harvesting", "structure", "placements",
                          "retail_readiness", "budgets"}
    waste = found["search_term_waste"][0]
    assert {r["query"] for r in waste["rows"]} == {f"free widget {j}" for j in range(6)}
    assert {r["query"] for r in found["harvesting"][0]["rows"]} == \
        {f"blue widget pro {j}" for j in range(4)}
    assert found["structure"][0]["rows"][0]["query"] == "widget set"
    assert found["placements"][0]["rows"][0]["placement"] == "Detail Page on-Amazon"
    assert found["retail_readiness"][0]["rows"][0]["asin"] == "B0AAAAAAA2"
    from yaaf.ppc import fields

    for fs in found.values():
        for f in fs:
            assert fields.refusals(f["fields"], f["platform_id"]) == []


def test_a_healthy_account_comes_back_with_nothing_to_fix():
    """If an account is well run, the audit says so: no padding."""
    from yaaf.ppc import checks

    assert checks.run(_pulled(healthy=True))["findings"] == {}


def test_the_backtest_beats_the_naive_guess_on_a_known_curve():
    from yaaf.ppc import backtest

    tested = backtest.run(_pulled()["campaign_daily"])
    assert tested["band_measured"] and tested["campaigns_with_spend_moves"] >= 5
    assert tested["model_error"] < tested["naive_error"]
    assert tested["model_error"] < 0.08


def _seated(client):
    from yaaf.server import ppc as server

    project = audit(client)
    connect(project["id"], *TOOLS)
    with db_session() as session:
        row = session.get(Project, project["id"])
        row.integrations = {**(row.integrations or {}), "mcp_servers": [{"name": "qore"}]}
    account = Account()
    server.CALL_SEAM = account
    return project, account


@pytest.fixture
def seam():
    from yaaf.server import ppc as server

    yield
    server.CALL_SEAM = None


def _analyse(client, project):
    _, token = live_token(project["id"], project["ceo_agent_id"])
    return client.post("/agent/ppc/analysis", headers=bearer(token), json={})


def test_the_platform_pulls_and_publishes_the_analysis(client, seam):
    project, account = _seated(client)
    with db_session() as session:
        session.get(Project, project["id"])  # the project exists
    done = _analyse(client, project)
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["files"] == ["ppc-data.json", "ppc-analysis.json"]
    assert "search_term_waste" in body["findings"]
    # The platform asked only for the tools the chairman allows, and only reads.
    assert {tool for tool, _ in account.calls} <= {t for t, _ in TOOLS}


def test_a_switched_off_tool_is_not_called_by_the_platform(client, seam):
    project, account = _seated(client)
    with db_session() as session:
        row = session.query(DataSourceTool).filter_by(
            project_id=project["id"], name="query_dataset").one()
        row.permission = ToolPermission.DENY
    refused = _analyse(client, project)
    assert refused.status_code == 422  # no adapter matches what he allows
    assert not account.calls


def _load(project, name, raw=False):
    from yaaf.core.artifacts import store

    with db_session() as session:
        row = session.query(Artifact).filter_by(project_id=project["id"], name=name) \
            .order_by(Artifact.version.desc()).first()
        data = store().get_bytes(row.storage_key)
    if name.endswith(".xlsx"):
        return data
    return data.decode() if raw else json.loads(data)


# ── end to end: the Founder's draft, the form's create, every seat's first run ──


def _crew():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    return json.loads((root / "crews" / "amazon-ppc-audit.json").read_text())


def test_a_founder_drafted_ppc_audit_is_one_create_accepts():
    """The Founder's draft is validated against Create's own rules, so a draft
    the review screen shows is one Create will take."""
    from yaaf.founder import tools as founder_tools

    crew = _crew()
    project = {"slug": "acme-ads", "name": "Acme ads", "mode": "ppc_factory",
               "ppc_mode": "audit", "goal": "Audit the Acme US Amazon advertising account.",
               "teams": crew["teams"]}
    drafted = founder_tools.as_bundle(project)
    assert drafted["project"]["ceo_role_slug"] == shape.PPC_LEAD_ROLE
    assert founder_tools.validate(drafted) == []

    wrong = founder_tools.as_bundle({**project, "ceo_role_slug": "ceo"})
    assert any(p.startswith("ceo_role_slug") for p in founder_tools.validate(wrong))
    operate = founder_tools.as_bundle({**project, "ppc_mode": "operate"})
    assert any("not open yet" in p for p in founder_tools.validate(operate))
    stray = founder_tools.as_bundle({**project, "mode": "deliver", "ceo_role_slug": "ceo"})
    assert any(p.startswith("ppc_mode") for p in founder_tools.validate(stray))


def test_the_founder_knows_the_ppc_factory_and_its_crew(client):
    import pathlib

    from yaaf.founder import agent
    from yaaf.founder.prompt import system_prompt

    root = pathlib.Path(__file__).resolve().parents[2]
    client.post("/admin/users", headers=ADMIN,
                json={"email": "chair@example.com", "display_name": "Chair"})
    for path in sorted((root / "roles").glob("*.json")):
        assert client.post("/admin/roles", headers=ADMIN,
                           json=json.loads(path.read_text())).status_code == 200
    crew = _crew()
    assert client.post("/admin/crews", headers=ADMIN, json=crew).status_code < 400
    prompt = system_prompt()
    assert "Amazon PPC Factory" in prompt and "amazon-ppc-audit" in prompt
    with db_session() as session:
        library = agent.library(session)
    assert "amazon-ppc-audit" in library and "ppc-reviewer" in library


def test_a_ppc_audit_from_the_shipped_crew_starts_and_every_seat_is_equipped(client):
    """Created the way the New Project form posts it — the shipped roles, the
    shipped crew, no lead named — started, and every seat's first run leased:
    each carries the playbook, the brief and the audit's tools."""
    import pathlib

    from yaaf.core.models import RunQueue
    from yaaf.runner.daemon import lease_and_prepare

    root = pathlib.Path(__file__).resolve().parents[2]
    client.post("/admin/users", headers=ADMIN,
                json={"email": "chair@example.com", "display_name": "Chair"})
    for path in sorted((root / "roles").glob("*.json")):
        assert client.post("/admin/roles", headers=ADMIN,
                           json=json.loads(path.read_text())).status_code == 200
    assert client.post("/admin/settings/model-prices/import", headers=ADMIN, json={
        "source": "snapshot", "apply": True}).status_code == 200
    billing = client.post("/admin/settings/llm-credentials", headers=ADMIN, json={
        "name": "key", "mode": "api_key", "provider": "anthropic",
        "value": "sk-ant-api03-acceptance"}).json()["id"]
    crew = _crew()
    created = client.post("/admin/projects", headers=ADMIN, json={
        "slug": "acme-ads", "name": "Acme ads", "chairman_email": "chair@example.com",
        "goal": "Audit the Acme US Amazon advertising account.", "mode": "ppc_factory",
        "ppc_mode": "audit", "ceo_name": "Mara", "billing_secret_id": billing,
        "teams": crew["teams"]})
    assert created.status_code == 200, created.text
    project = created.json()
    with db_session() as session:
        from yaaf.core.models import Role, RoleVersion, Team

        versions = [a.role_version_id for a in session.query(Agent).filter_by(
            project_id=project["id"], team_id=None)]
        versions += [t.role_version_id for t in session.query(Team).filter_by(
            project_id=project["id"])]
        hired = {session.get(Role, session.get(RoleVersion, v).role_id).slug
                 for v in versions}
    assert {shape.PPC_LEAD_ROLE, "ppc-analyst", "ppc-strategist", "data-skeptic",
            "executability-checker", "ppc-reviewer"} <= hired
    assert client.post(f"/admin/projects/{project['id']}/start",
                       headers=ADMIN).status_code == 200

    agents = [project["ceo_agent_id"]] + [a["id"] for t in project["teams"]
                                           for a in t["agents"]]
    with db_session() as session:
        for agent_id in agents:
            session.add(RunQueue(id=ids.new_id(ids.RUN), project_id=project["id"],
                                 agent_id=agent_id, kind="agent_run",
                                 trigger={"type": "acceptance"}))
    jobs = [j for j in lease_and_prepare(limit=50) if j.project_id == project["id"]]
    assert {j.agent_id for j in jobs} == set(agents)
    playbook = {s["name"] for s in catalogue.playbook()}
    for job in jobs:
        assert playbook <= {s["name"] for s in job.tool_permissions["skills"]}, job.agent_id
        assert "ppc-audit-runbook" in job.system_prompt
        assert catalogue.REVIEW in job.system_prompt
    lead = next(j for j in jobs if j.agent_id == project["ceo_agent_id"])
    with db_session() as session:
        assert session.get(Agent, lead.agent_id).name == "Mara"
    seen = client.get(f"/observe/projects/{project['id']}", headers=ADMIN).json()
    assert seen["ppc_mode"] == "audit" and seen["ppc"]["data_owed"]
    assert seen["ppc"]["reviewed"] is False
