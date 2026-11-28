"""An Amazon PPC Factory's data, its gates, and the brief every seat reads.

The audit's contract is pure (`yaaf.ppc`); this is where it meets a project.
The crew investigates; the platform holds what makes the result trustworthy:

* **What the data can support is the platform's answer.** It is read from the
  project's data-source tools the gateway recorded (`DataSourceTool`) — only
  reads the chairman has not switched off, and only tools the source still
  lists. The crew is told the answer; it cannot widen it.
* **Every pull is on file.** Each read a PPC Factory makes through the gateway
  is recorded with the fields it returned (`record_read`); the receipt rides
  back to the agent, and a claim citing a receipt that is not on file — or a
  field the pull did not contain — is refused.
* **No data, no plan.** A PPC Factory whose sources cannot even give ad
  performance has nothing to audit, so its plan does not hold (`data_owed`).
* **Draft until reviewed.** `artifact_put` refuses a `ppc-audit.json` that
  fails `yaaf.ppc.check_report`, renders the pages from the one it accepts as
  a draft, and only a passing `ppc-review.json` — by a different seat, of the
  latest version, with every load-bearing number re-derived — makes it final.
  An audit cannot complete until that review exists (`report_owed`).
* **Every seat gets the playbook** as skills (`skills`), and the kit as a file
  (`/agent/ppc/kit`).
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf import ppc
from yaaf.core import shape
from yaaf.core.models import Artifact, AuditLog, DataSourceTool, Project, ToolPermission

#: The audit-log action a recorded PPC read carries; its id is the receipt.
READ_ACTION = "data_source.read"
#: Field names kept per receipt: enough to check a citation, never the rows.
RECEIPT_FIELDS = 400


def offered(session: Session, project: Project) -> set[str]:
    """Every kind of question this project's sources can answer today."""
    rows = session.execute(
        select(DataSourceTool.name, DataSourceTool.description).where(
            DataSourceTool.project_id == project.id,
            DataSourceTool.kind == "read",
            DataSourceTool.permission == ToolPermission.ALLOW,
            DataSourceTool.gone_at.is_(None))
    ).all()
    found: set[str] = set()
    for name, description in rows:
        found |= ppc.tool_capabilities(name, description)
    return found


def coverage(session: Session, project: Project) -> dict:
    """The platform's coverage for this project, with the tools behind it."""
    return ppc.coverage(offered(session, project))


def data_owed(session: Session, project: Project) -> str | None:
    """Why this audit's plan cannot hold yet, or None. Only an audit asks."""
    if shape.ppc_mode(project) is None:
        return None
    if ppc.available(ppc.BY_AREA["baseline"], offered(session, project)):
        return None
    return ("no advertising data yet. This Amazon PPC Factory audits an account "
            "through a data source (an MCP server) and none of the connected sources' "
            "tools offers ad performance. The chairman connects one under Settings → "
            "Data sources; until then there is nothing to audit and nothing to plan.")


def skills(project: Project) -> list[dict]:
    """The playbook, as skills for every seat of a PPC Factory; none elsewhere."""
    return ppc.playbook() if shape.is_ppc_factory(project) else []


# ── receipts ───────────────────────────────────────────────────────────────────


def _field_names(result: dict) -> list[str]:
    """The keys of the records a tool returned, from its text or structured
    content: what a claim may say it used."""
    found: list[str] = []
    seen: set[str] = set()

    def walk(node, depth: int) -> None:
        if depth > 4 or len(found) >= RECEIPT_FIELDS:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(k, str) and k not in seen:
                    seen.add(k)
                    found.append(k)
                walk(v, depth + 1)
        elif isinstance(node, list):
            for item in node[:50]:
                walk(item, depth + 1)

    walk(result.get("structuredContent"), 0)
    for block in result.get("content") or []:
        text = block.get("text") if isinstance(block, dict) else None
        if not text:
            continue
        try:
            walk(json.loads(text), 0)
        except ValueError:
            continue
    return found


def record_read(session: Session, project: Project, agent, source: str, tool: str,
                arguments: dict, result: dict, run_id: str | None) -> str | None:
    """Write down one PPC Factory read and return its receipt; None elsewhere."""
    if shape.ppc_mode(project) is None or result.get("isError"):
        return None
    from yaaf.core import ids

    body = json.dumps(result, sort_keys=True, default=str).encode()
    receipt = ids.new_id(ids.AUDIT)
    session.add(AuditLog(
        id=receipt, project_id=project.id,
        actor=f"agent:{agent.id}" if agent is not None else "system:ppc",
        action=READ_ACTION, entity="data_source", entity_id=source[:40],
        detail={"source": source, "tool": tool, "run_id": run_id,
                "arguments": json.dumps(arguments or {}, sort_keys=True, default=str)[:2000],
                "result_sha256": hashlib.sha256(body).hexdigest(),
                "bytes": len(body), "fields": _field_names(result)}))
    session.flush()
    return receipt


def receipts(session: Session, project: Project):
    """A lookup a report check calls: receipt id → {"fields": set} or None."""
    def find(receipt: str):
        row = session.get(AuditLog, receipt)
        if row is None or row.project_id != project.id or row.action != READ_ACTION:
            return None
        return {"fields": set((row.detail or {}).get("fields") or []),
                "tool": (row.detail or {}).get("tool")}
    return find


# ── the report and its review ──────────────────────────────────────────────────


def latest(session: Session, project: Project, name: str) -> Artifact | None:
    return session.execute(
        select(Artifact).where(Artifact.project_id == project.id, Artifact.name == name)
        .order_by(Artifact.version.desc()).limit(1)).scalars().first()


def _load(row: Artifact | None) -> dict | None:
    if row is None:
        return None
    from yaaf.core.artifacts import store

    return json.loads(store().get_bytes(row.storage_key))


def report_published(session: Session, project: Project) -> bool:
    return latest(session, project, ppc.REPORT) is not None


def reviewed(session: Session, project: Project) -> bool:
    """The latest audit has a passing review of exactly that version."""
    audit = latest(session, project, ppc.REPORT)
    review = latest(session, project, ppc.REVIEW)
    if audit is None or review is None:
        return False
    body = _load(review) or {}
    return body.get("verdict") == "pass" and body.get("audit_sha256") == audit.sha256


def report_owed(session: Session, project: Project) -> bool:
    """An audit whose checked, reviewed report does not exist yet."""
    return shape.ppc_mode(project) == "audit" and not reviewed(session, project)


def _refusal(name: str, exc: ppc.ReportProblem):
    from yaaf.server.policy import Refusal

    return Refusal(
        f"{name} was not accepted ({len(exc.problems)} problem"
        f"{'s' if len(exc.problems) != 1 else ''}): " + " · ".join(exc.problems)
        + ". Fix them and publish it again; nothing was stored.", status_code=422)


def check(session: Session, project: Project, data: bytes) -> dict:
    """The report, accepted, or a refusal naming every reason it was not."""
    artifacts = {n for n in (DATA, ANALYSIS) if latest(session, project, n) is not None}
    try:
        return ppc.check_report(data, offered(session, project),
                                receipts=receipts(session, project), artifacts=artifacts)
    except ppc.ReportProblem as exc:
        raise _refusal(ppc.REPORT, exc) from None


def check_review(session: Session, project: Project, agent_id: str | None,
                 data: bytes) -> tuple[dict, dict]:
    """The review and the audit it passes, or a refusal. A seat cannot review
    an audit it published."""
    from yaaf.server.policy import Refusal

    row = latest(session, project, ppc.REPORT)
    if row is None:
        raise Refusal(f"there is no {ppc.REPORT} to review yet", status_code=409)
    if agent_id is not None and row.published_by_agent_id == agent_id:
        raise Refusal(f"you published {ppc.REPORT} v{row.version}; a different seat reviews "
                      "it. Agents overrate their own work — that is what the review is for.",
                      status_code=403)
    audit = _load(row) or {}
    try:
        return ppc.check_review(data, audit, row.sha256), audit
    except ppc.ReportProblem as exc:
        raise _refusal(ppc.REVIEW, exc) from None


def block(session: Session, project: Project) -> str:
    """What every seat of a PPC Factory reads after the platform's own rules:
    where the playbook is, what the connected data can support, and the
    report and review the platform will accept. Empty for any other project."""
    mode = shape.ppc_mode(project)
    if mode is None:
        return ""
    cov = coverage(session, project)
    lines = []
    for s in cov["sections"]:
        if s["available"]:
            thin = (f" (thinner without: {', '.join(s['thinner_without'])})"
                    if s["thinner_without"] else "")
            lines.append(f"  - {s['key']}: reachable{thin}")
        else:
            lines.append(f"  - {s['key']}: NOT REACHABLE — no {', '.join(s['missing']).lower()}")
    state = ("REVIEWED — final" if reviewed(session, project)
             else "published, draft until reviewed" if report_published(session, project)
             else "not published yet")
    return (
        "AMAZON PPC FACTORY — " + ("AUDIT. " if mode == "audit" else "OPERATE. ")
        + "You investigate this account the way a senior Amazon strategist would. Your "
        "skills hold the playbook: load ppc-audit-runbook first, then each reference when "
        "the runbook points to it. There is no fixed list of findings: ask questions, pull "
        "the data that answers them, compute, try to prove yourself wrong, then write the "
        "claim.\n\n"
        "TOOLS — `ppc_kit` writes ppckit.py (statistics, economics, bids, forecast, "
        "backtest) into your workspace; compute with it so every seat gets the same number. "
        "`data_fetch` pulls to disk and returns a `receipt`: cite it. `ppc_analysis` "
        "(where the source has a platform adapter) pulls the account and runs a first pass "
        "of standard checks into ppc-data.json / ppc-analysis.json — leads to test, never "
        "findings by themselves; ppc-data.json may be cited as evidence.\n\n"
        "WHAT THE CONNECTED DATA CAN REACH — computed by the platform from the tools this "
        "project may call, not from anything an agent wrote. A claim about an area marked "
        "NOT REACHABLE is refused; the platform lists those areas as not covered itself.\n"
        + "\n".join(lines) + "\n\n"
        f"THE REPORT ({state}) — publish {ppc.REPORT} with artifact_put. The platform "
        f"checks it, renders {ppc.REPORT_DECK}, {ppc.REPORT_WORKBOOK} and {ppc.REPORT_CHANGES} "
        f"as a DRAFT, and they become final only when a different seat publishes a passing "
        f"{ppc.REVIEW} (skill ppc-review-rubric). Shape:\n"
        + CONTRACT + "\n")


def _contract() -> str:
    actions = "|".join(ppc.MOVE_ACTIONS)
    tiers = "|".join(map(str, ppc.SCALE_TIERS))
    return "\n".join([
        '{"account", "marketplace", "account_type": "seller"|"vendor", '
        '"window": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},',
        ' "answer": {"headline", "top": [1-3 claim ids], "scale": "one line"},',
        ' "business": {"portrait", "claims": [...]},',
        ' "economics": {"bands": [{"job": "all"|"defend"|"acquire"|"conquest"|"launch", '
        '"today_acos": %, "affordable_acos": % or [low, high], "basis"}], "claims": [...], '
        '"note"},',
        ' "chapters": [{"title", "takeaway", "claims": [...]}],',
        ' "claims": [{"id": "C1", "kind": "fix"|"opportunity"|"risk"|"works"|"context"|'
        '"forecast", "area": <an area above>, "statement", "monthly_usd": number|null, '
        '"confidence": "high"|"medium"|"low", "evidence": [{"receipt": id | "artifact": '
        '"ppc-data.json" | "owner": <owner_inputs index> | "source": url (context only), '
        '"fields": [Amazon fields used], "script": "analysis/x.py", "note"}], '
        '"test": {"method", "result"} (required at $1,000+/month and for forecasts), '
        '"falsifier", "rows": [{...}] (the proof table)}],',
        f' "moves": [{{"id": "M1", "action": {actions}, "entity": {{"type", "name", '
        '"campaign_id", "ad_group_id", "keyword_id", "target_id", "keyword_text", '
        '"match_type", "placement"}, "today", "change_to", "why", "expected_monthly": '
        '{"spend", "sales"}, "week": 1-12, "check": {"day", "expect"}, "stop_rule", '
        f'"claims": [...], "tier": 0 (do now) | {tiers}}}],',
        f' "tiers": [{{"monthly_usd": {tiers}, "fits_usd", "sales_low", "sales_mid", '
        '"sales_high", "acos", "tacos", "basis": "acos"|"tacos"|"payback", '
        '"forecast_claim": <a tested forecast claim>, "moves": [...], "binding": what stops '
        'it (required when it does not fill)}] — one per tier,',
        ' "leave_alone": [{"what", "checked", "claims"}], "not_covered": [{"area", "why", '
        '"would_answer"}], "assumptions": [{"what", "value", "why"}], "owner_inputs": '
        '[{"question", "answer"}], "questions": [{"question", "why"}]}',
        "Every field a claim cites must be Amazon's or computed from Amazon's alone "
        "(yaaf.ppc.fields); a value typed into a platform (target/max ACoS, tags, bid rules, "
        "cost of goods, margin) is refused wherever it appears. The owner's own answers are "
        "owner_inputs, labelled as his.",
    ])


CONTRACT = _contract()


# ── the platform's own analysis ─────────────────────────────────────────────────

#: The platform's artifacts: the account as pulled, and what it computed.
DATA = "ppc-data.json"
ANALYSIS = "ppc-analysis.json"
#: Days of daily history before the window, for the backtest and the fits.
HISTORY_DAYS = 60
#: A seam for tests: (tool, arguments) → raw result, in place of the gateway.
CALL_SEAM = None


def _adapter(session: Session, project: Project):
    """The first source with an adapter, its entry, and the reads he allows."""
    from yaaf.ppc import adapters

    for entry in (project.integrations or {}).get("mcp_servers", []) or []:
        names = {r.name for r in session.execute(
            select(DataSourceTool).where(
                DataSourceTool.project_id == project.id,
                DataSourceTool.source == entry.get("name"),
                DataSourceTool.kind == "read",
                DataSourceTool.permission == ToolPermission.ALLOW,
                DataSourceTool.gone_at.is_(None))).scalars()}
        adapter = adapters.for_tools(names)
        if adapter is not None:
            return adapter, entry, names
    return None, None, set()


def can_analyse(session: Session, project: Project) -> bool:
    return shape.ppc_mode(project) is not None and _adapter(session, project)[0] is not None


def load_analysis(session: Session, project: Project) -> dict | None:
    import json

    from yaaf.core.artifacts import store

    row = latest(session, project, ANALYSIS)
    if row is None:
        return None
    return json.loads(store().get_bytes(row.storage_key))


def analyse(session: Session, project: Project, *, run_id: str, call=None,
            today=None, marketplace: str | None = None) -> dict:
    """Pull the account through the gateway, compute every standard check, the
    pools, the forecast and its backtest, and store both as the platform's.

    `call` is a seam for tests: (tool, arguments) → the tool's raw result."""
    from datetime import date, timedelta

    from yaaf.ppc import backtest, checks, forecast
    from yaaf.ppc import data as D
    from yaaf.ppc.adapters import AdapterError
    from yaaf.server import datasource_tools as gateway
    from yaaf.server.policy import Refusal

    if shape.ppc_mode(project) is None:
        raise Refusal("only an Amazon PPC Factory has an account to analyse", 422)
    adapter, entry, allowed = _adapter(session, project)
    if adapter is None:
        raise Refusal(
            "none of this project's data sources has a platform adapter yet (Qore has one). "
            "The crew can still audit from the source's tools under the same rules; the "
            "platform's own checks need an adapter.", 422)
    if call is None and CALL_SEAM is not None:
        call = CALL_SEAM
    if call is None:
        url, token = gateway.credentials(session, project, entry)
        session.commit()

        def call(tool: str, arguments: dict):
            if tool not in allowed:
                raise AdapterError(f"{tool} is switched off for this source")
            with gateway.Upstream(url, token) as upstream:
                return upstream.call_tool(tool, arguments)
    else:
        inner = call

        def call(tool: str, arguments: dict):
            if tool not in allowed:
                raise AdapterError(f"{tool} is switched off for this source")
            return inner(tool, arguments)

    start, end = D.default_window(today or date.today())
    history_start = (date.fromisoformat(start) - timedelta(days=HISTORY_DAYS)).isoformat()
    market = marketplace or (project.integrations or {}).get("ppc_marketplace") or "USA"
    try:
        bundle = D.validate(adapter.pull(call, start=start, end=end,
                                         history_start=history_start, marketplace=market))
    except D.BundleProblem as exc:
        raise Refusal(f"the account could not be read: {exc}", 422) from None
    result = checks.run(bundle)
    base = result["baseline"]
    pools = checks.pools(bundle)
    tested = backtest.run(bundle.get("campaign_daily"))
    fc = None
    if pools and base.get("spend") and base.get("ad_sales"):
        fc = forecast.allocate(pools, {"spend": base["spend"], "ad_sales": base["ad_sales"],
                                       "total_sales": base.get("total_sales"),
                                       "ad_orders": base.get("ad_orders")},
                               D.window_days(bundle))
        band = tested["band"]
        for tier in fc["tiers"]:
            for answer in (tier["at_acos"], tier.get("at_tacos")):
                if answer:
                    answer["sales_low"] = round(answer["expected_sales_usd"] * (1 - band), 2)
                    answer["sales_high"] = round(answer["expected_sales_usd"] * (1 + band), 2)
    analysis = {"window": bundle["window"], "marketplace": market, "source": adapter.NAME,
                "missing": bundle["missing"], "baseline": base,
                "findings": result["findings"], "looked_at": result["looked_at"],
                "yardsticks": result["yardsticks"], "pools": pools, "forecast": fc,
                "backtest": tested}
    _store(session, project, run_id, DATA, bundle)
    _store(session, project, run_id, ANALYSIS, analysis)
    return analysis


def _store(session: Session, project: Project, run_id: str, name: str, body: dict) -> None:
    import hashlib
    import json

    from sqlalchemy import func

    from yaaf.core import ids
    from yaaf.core.artifacts import store
    from yaaf.core.models import ResultFor

    data = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
    key = f"projects/{project.id}/{run_id}/{name}"
    version = 1 + (session.execute(
        select(func.max(Artifact.version)).where(Artifact.project_id == project.id,
                                                 Artifact.name == name)).scalar_one() or 0)
    session.add(Artifact(
        id=ids.new_id(ids.ARTIFACT), project_id=project.id, name=name, version=version,
        sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data), storage_key=key,
        uri=store().put_bytes(key, data), published_by_agent_id=None, run_id=run_id,
        requirement_ids=[], opening=None, reader=ResultFor.TEAM, announced_at=None))
    session.flush()


def summary(analysis: dict) -> dict:
    """What an agent is handed back: enough to plan, the rest is in the files."""
    return {
        "window": analysis["window"], "baseline": analysis["baseline"],
        "missing": analysis["missing"],
        "findings": {k: [{"platform_id": f["platform_id"], "what": f["what"],
                          "expected_monthly_effect_usd": f["expected_monthly_effect_usd"],
                          "effect_kind": f["effect_kind"]} for f in v]
                     for k, v in analysis["findings"].items()},
        "pools": [p["name"] for p in analysis["pools"]],
        "backtest": analysis["backtest"],
        "files": [DATA, ANALYSIS],
    }
