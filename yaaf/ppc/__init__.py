"""An Amazon PPC audit: the harness around the agents that do it.

The audit is not a list of checks. The crew investigates the account the way a
senior strategist would — the playbook (`yaaf/ppc/playbook/*.md`, handed to
every seat as skills) says how — and writes the result as a story of claims.
What lives here is what a harness must hold for that story to be trusted, each
one pure so the server, the founder and the tests ask the same questions:

* **What the data can support** (`CAPABILITIES`, `AREAS`, `coverage`): which
  kinds of question the connected source's tools can answer. A claim about an
  area the data cannot reach is refused, and every such area is reported as
  not covered by the platform itself — "if the MCP server does not give you the
  data, we don't do this section", held by code.
* **Where every number came from** (`check_report`): each claim cites
  evidence — a data pull the platform recorded (a receipt), the platform's own
  pull, the owner's answer, or (for context only) a published source — and the
  Amazon fields it used. Fields are checked against `yaaf.ppc.fields`, an
  allowlist that fails closed: nothing typed into a platform is evidence.
* **What a recommendation must carry** (`MOVE_ACTIONS`): a move is an Amazon
  change with today's value, the new value, why that number, its week, its
  check-in and its stop rule — or it is not accepted.
* **That somebody else checked it** (`check_review`): the audit stays a draft
  until a different seat publishes a review that passes the rubric and
  re-derives every load-bearing number.
* **The arithmetic** (`yaaf.ppc.kit`): statistics, economics, bids, the scale
  forecast and its backtest, one file the sandbox gets verbatim.

The readable page, the presentation and the workbook are rendered from the
accepted report (`outputs`), so they cannot say what it does not.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from importlib import resources

from yaaf.ppc import fields as evidence
from yaaf.ppc.kit import SCALE_TIERS

#: The report the crew publishes, the review a different seat publishes, and
#: the readable pages the platform writes from the accepted report.
REPORT = "ppc-audit.json"
REVIEW = "ppc-review.json"
REPORT_MD = "ppc-audit.md"
REPORT_DECK = "ppc-audit.html"
REPORT_WORKBOOK = "ppc-audit.xlsx"
REPORT_CHANGES = "ppc-changes.csv"
#: Written by the platform from the accepted report, never by the crew.
PLATFORM_PAGES = (REPORT_MD, REPORT_DECK, REPORT_WORKBOOK, REPORT_CHANGES)

__all__ = ["SCALE_TIERS"]


def outputs(report: dict, reviewed: bool = False) -> list[tuple[str, bytes]]:
    """Everything the owner receives, rendered from one checked report. Until a
    review passes, every page says it is a draft."""
    from yaaf.ppc import deck, workbook

    return [(REPORT_MD, render(report, reviewed).encode("utf-8")),
            (REPORT_DECK, deck.render(report, reviewed).encode("utf-8")),
            (REPORT_WORKBOOK, workbook.build(report, reviewed)),
            (REPORT_CHANGES, workbook.change_sheet_csv(report).encode("utf-8"))]


# ── what the data can answer ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Capability:
    key: str
    label: str
    #: Matched against a tool's name.
    names: str
    #: Matched against a tool's description, when the name says nothing.
    description: str
    #: Whether a general query tool (one that reads any dataset the source
    #: holds) can answer it. AMC runs its own queries, and recommendations are
    #: Amazon's own call, so neither comes from a generic query.
    generic: bool = True


CAPABILITIES: tuple[Capability, ...] = (
    Capability("ad_performance", "Ad performance by campaign, ad group and ad type",
               r"get_stats|timeseries|campaign_report|ads?_performance|list_campaigns?|"
               r"get_campaigns?",
               r"\bacos\b|\broas\b|ad spend|advertising performance|campaign performance"),
    Capability("total_sales", "Total sales, organic included (for TACOS)",
               r"get_stats|sales_and_traffic|business_report|orders?_report",
               r"total sales|organic sales|sales and traffic|\btacos\b"),
    Capability("targeting", "Keyword and target performance with bids",
               r"keyword|target", r"\bkeywords?\b.*\bbid|\btargeting\b"),
    Capability("search_terms", "Search terms that triggered the ads",
               r"search_term|\bstr\b", r"search terms?\b"),
    Capability("placements", "Performance by placement",
               r"placement", r"\bplacements?\b"),
    Capability("campaign_settings", "Budgets, bidding strategies and multipliers",
               r"campaign|budget|bid", r"daily budget|bid(ding)? strateg|bid multiplier"),
    Capability("product_economics", "Amazon's referral and fulfilment fees",
               r"fee_preview|fees?_estimate|referral_fee|fba_fee|get_fees",
               r"referral fee|fba fee|fulfil?ment fee|fee preview|fees estimate"),
    Capability("catalog", "The catalog: what is sold, category, price, rating",
               r"catalog|listing|get_products?|organization_products|item",
               r"catalog|product details|listing|browse node|item attributes"),
    Capability("retail_health", "Buy box, ratings and listing state",
               r"listing|offer|competitive|buy_?box",
               r"buy.?box|featured offer|\bratings?\b|suppressed"),
    Capability("inventory", "Inventory, inbound and days of cover",
               r"inventory|restock|afn|stock",
               r"inventory|in stock|out of stock|restock|days of (supply|cover)"),
    Capability("returns", "Customer returns and return reasons",
               r"return", r"customer returns?|return reasons?|refunds?"),
    Capability("repeat_purchase", "Repeat customers (Brand Analytics)",
               r"repeat_purchase|repeatpurchase|customer_loyalty",
               r"repeat purchase|repeat customers?|customer loyalty"),
    Capability("subscribe_save", "Subscribe & Save subscriptions and retention",
               r"subscribe|snsp?|s_and_s",
               r"subscribe (&|and) save|subscriptions?\b"),
    Capability("competitors", "Competitors: who wins the searches, their price and rating",
               r"competitive|competitor|itemoffer|item_offer|searchcatalog|share_of_voice",
               r"competitor|competitive|share of voice|other sellers"),
    Capability("impression_share", "Top-of-search impression share",
               r"impression_share", r"impression share"),
    Capability("search_market", "Brand Analytics search query performance",
               r"sqp|brand_analytics|search_query_performance",
               r"search query performance|brand analytics|\bsqpr?\b|brand share|"
               r"market share"),
    Capability("keyword_recommendations", "Suggested keywords and bids",
               r"recommend", r"suggested bid|keyword recommendations?", generic=False),
    Capability("hourly", "Performance by hour",
               r"hour", r"hourly|by hour|marketing stream"),
    Capability("halo", "Purchased products (halo sales)",
               r"purchased_product|halo", r"purchased product|\bhalo\b|other.sku"),
    Capability("new_to_brand", "New-to-brand orders",
               r"new_to_brand|\bntb\b", r"new.to.brand"),
    Capability("dsp", "Amazon DSP campaigns",
               r"\bdsp\b|dsp_", r"\bdsp\b|demand.side|programmatic display"),
    Capability("amc", "Amazon Marketing Cloud queries",
               r"\bamc\b|amc_|marketing_cloud", r"amazon marketing cloud|\bamc\b",
               generic=False),
)
BY_CAPABILITY = {c.key: c for c in CAPABILITIES}

#: A tool that reads any dataset the source holds: a structured query, or a
#: librarian that picks the dataset itself. Either can answer every report-
#: backed question — whether the account HAS that report is what an empty
#: result says, and an empty result is an area not covered, said as such.
GENERIC_QUERY = re.compile(
    r"^(query_dataset|consult_data_librarian|run_query|execute_query|query|sql|run_sql)$")


def tool_capabilities(name: str, description: str | None = None) -> set[str]:
    """Which kinds of question one tool can answer, from its name and what it
    says it does. Deterministic and conservative: a tool that matches nothing
    answers nothing, and a write tool is never asked (the caller passes reads)."""
    lowered_name = (name or "").lower()
    if GENERIC_QUERY.match(lowered_name):
        return {c.key for c in CAPABILITIES if c.generic}
    words = (description or "").lower()
    found = set()
    for cap in CAPABILITIES:
        if re.search(cap.names, lowered_name) or (words and re.search(cap.description, words)):
            found.add(cap.key)
    return found


@dataclass(frozen=True)
class Area:
    """One part of an investigation, and the data it cannot be done without.
    Not a section the report must fill: a claim names the area it is about,
    and an area the data cannot reach is reported as not covered."""
    key: str
    title: str
    question: str
    needs: tuple[str, ...]
    helps: tuple[str, ...] = field(default_factory=tuple)


AREAS: tuple[Area, ...] = (
    Area("baseline", "Where the account stands",
         "What does advertising cost, what does it return, and is it growing the business?",
         ("ad_performance",), ("total_sales",)),
    Area("business", "What the business sells",
         "What is sold, to whom, at what price, and how concentrated is it?",
         ("catalog",), ("total_sales",)),
    Area("economics", "What this business can afford to pay for a sale",
         "What does an order earn after Amazon, and what ACoS can each campaign job run at?",
         ("ad_performance",), ("product_economics", "returns", "repeat_purchase",
                               "subscribe_save", "new_to_brand")),
    Area("lifetime_value", "What a customer is worth after the first order",
         "How often do customers come back or subscribe, and what does that allow?",
         ("repeat_purchase",), ("subscribe_save", "new_to_brand")),
    Area("demand", "Where the market is searching and the brand's share",
         "On the searches that matter, how much does the brand see, click and buy?",
         ("search_market",)),
    Area("competitors", "Who wins the searches that matter",
         "Who beats the brand on its best queries, and can ads change that?",
         ("competitors",), ("search_market", "catalog")),
    Area("campaigns", "How the campaigns capture demand",
         "Structure, search terms, bids, placements, budgets and targeting.",
         ("ad_performance", "targeting"), ("search_terms", "placements",
                                           "campaign_settings", "impression_share", "hourly")),
    Area("incrementality", "Sales the ads create versus sales they take credit for",
         "How much spend defends sales the brand would get anyway?",
         ("search_terms",), ("search_market", "new_to_brand", "total_sales")),
    Area("inventory", "Whether the products can take more demand",
         "How many days of stock does each advertised product have?",
         ("inventory",)),
    Area("returns", "What comes back",
         "Which products are returned, why, and what that does to ACoS?",
         ("returns",)),
    Area("retail", "Buy box, ratings and listing health",
         "Can the advertised products convert the traffic they are sent?",
         ("retail_health",), ("catalog",)),
    Area("dsp", "Amazon DSP",
         "What does DSP spend buy, and is it new customers or sales already coming?",
         ("dsp",), ("new_to_brand", "amc")),
    Area("amc", "Paths to purchase (Amazon Marketing Cloud)",
         "How do shoppers reach a first purchase, and which ads bring new customers?",
         ("amc",)),
    Area("scale", "Where more money would go",
         "Where would $10k, $25k, $50k and $100k more a month sell the most at today's "
         "ACoS or TACoS?", ("ad_performance", "targeting"),
         ("search_terms", "impression_share", "search_market", "inventory")),
)
BY_AREA = {a.key: a for a in AREAS}


def available(area: Area, offered: set[str]) -> bool:
    return all(need in offered for need in area.needs)


def coverage(offered: set[str]) -> dict:
    """Which areas the connected data can reach, and which cannot, with the
    data each is missing — the platform's answer, never the crew's."""
    sections = []
    for a in AREAS:
        missing = [n for n in a.needs if n not in offered]
        sections.append({"key": a.key, "title": a.title, "available": not missing,
                         "missing": [BY_CAPABILITY[n].label for n in missing],
                         "thinner_without": [BY_CAPABILITY[h].label for h in a.helps
                                             if h not in offered]})
    scale = BY_AREA["scale"]
    scale_missing = [n for n in scale.needs if n not in offered]
    return {
        "capabilities": sorted(offered),
        "sections": sections,
        "scale": {"available": not scale_missing,
                  "missing": [BY_CAPABILITY[n].label for n in scale_missing]},
    }


# ── the playbook ───────────────────────────────────────────────────────────────

FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def playbook() -> list[dict]:
    """The playbook as skills: [{name, description, body}], runbook first.
    Each file is a skill a seat loads when its description says to."""
    root = resources.files("yaaf.ppc").joinpath("playbook")
    out = []
    for item in sorted(root.iterdir(), key=lambda p: p.name):
        if not item.name.endswith(".md"):
            continue
        text = item.read_text(encoding="utf-8")
        meta, body = {}, text
        m = FRONTMATTER.match(text)
        if m:
            for line in m.group(1).splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            body = text[m.end():]
        out.append({"name": meta.get("name") or item.name[:-3],
                    "description": meta.get("description") or "",
                    "body": body.strip()})
    out.sort(key=lambda s: (s["name"] != "ppc-audit-runbook", s["name"]))
    return out


def kit_source() -> str:
    """The kit, verbatim, as the sandbox receives it (`ppckit.py`)."""
    return resources.files("yaaf.ppc").joinpath("kit.py").read_text(encoding="utf-8")


# ── the report ──────────────────────────────────────────────────────────────────


class ReportProblem(ValueError):
    """Every reason a report was refused, so one refusal says all of them."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


CLAIM_KINDS = ("fix", "opportunity", "risk", "works", "context", "forecast")
CONFIDENCE = ("high", "medium", "low")
JOBS = ("all", "defend", "acquire", "conquest", "launch")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: A claim worth this much a month must say how it was tested, and a reviewer
#: must re-derive it.
LOAD_BEARING_USD = 1000
#: Amazon changes a brand manager makes in the console or by bulk upload, and
#: the other moves an audit may recommend.
MOVE_ACTIONS = ("budget", "bid", "placement", "bidding_strategy", "keyword_add",
                "negative_add", "target_add", "pause", "campaign_create",
                "listing", "inventory", "price", "test", "other")
AMAZON_ACTIONS = MOVE_ACTIONS[:9]
#: Changes that set a value on something that exists: today's value is owed.
CHANGES_A_VALUE = ("budget", "bid", "placement", "bidding_strategy", "pause")
EVIDENCE_KINDS = ("receipt", "artifact", "owner", "source")
#: Platform artifacts an analyst may cite as evidence (the platform's own pull).
CITABLE_ARTIFACTS = ("ppc-data.json", "ppc-analysis.json")


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _text(value, limit: int | None = None) -> bool:
    return isinstance(value, str) and bool(value.strip()) and \
        (limit is None or len(value) <= limit)


def _ids(value) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def load_bearing(report: dict) -> list[str]:
    """The claims a reviewer must re-derive: worth $1,000+ a month, in the
    answer, on the economics page, or behind a scale tier."""
    ids: list[str] = []
    for c in report.get("claims") or []:
        if _number(c.get("monthly_usd")) and abs(c["monthly_usd"]) >= LOAD_BEARING_USD:
            ids.append(c["id"])
    ids += _ids((report.get("answer") or {}).get("top"))
    ids += _ids((report.get("economics") or {}).get("claims"))
    for t in report.get("tiers") or []:
        if isinstance(t, dict) and isinstance(t.get("forecast_claim"), str):
            ids.append(t["forecast_claim"])
    known = {c.get("id") for c in report.get("claims") or [] if isinstance(c, dict)}
    seen, out = set(), []
    for i in ids:
        if i in known and i not in seen:
            seen.add(i)
            out.append(i)
    return out


def check_report(raw: bytes | str, offered: set[str], *, receipts=None,
                 artifacts: set[str] | None = None, today: date | None = None) -> dict:
    """The report, parsed, or every reason it cannot be accepted.

    Checked is what a careless, over-eager or invented report gets wrong —
    never whether a claim is wise, which is the reviewer's and the owner's:

    * every claim cites evidence that exists — a recorded data pull (looked up
      with `receipts(id)` → {"fields": set} or None), a platform artifact, the
      owner's answer, or, for context only, a published source — and every
      field it cites is Amazon's and was in that pull;
    * a claim about an area the connected data cannot reach is refused;
    * a claim worth $1,000+ a month says how it was tested;
    * every move is an executable change: entity, values, why, week, check-in,
      stop rule, and the claims that justify it;
    * the four tiers are answered with ranges that hold together, from moves
      that exist and a forecast claim that was tested.
    """
    try:
        report = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ReportProblem([f"{REPORT} is not valid JSON ({exc})"]) from None
    if not isinstance(report, dict):
        raise ReportProblem([f"{REPORT} must be one JSON object"])
    problems: list[str] = []
    today = today or date.today()

    w = report.get("window")
    if not (isinstance(w, dict) and DATE.match(str(w.get("start", "")))
            and DATE.match(str(w.get("end", "")))):
        problems.append('window is required as {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}')
    elif not (w["start"] < w["end"] <= today.isoformat()):
        problems.append("window must start before it ends, and end no later than today")
    if report.get("account_type") not in ("seller", "vendor"):
        problems.append('account_type is required: "seller" or "vendor" (it sets the '
                        "attribution window every recent number depends on)")

    claims = report.get("claims")
    if not isinstance(claims, list) or not claims:
        problems.append("claims is required: the findings, each with its evidence")
        claims = []
    by_id: dict[str, dict] = {}
    for i, c in enumerate(claims):
        where = f"claims[{i}]"
        if not isinstance(c, dict) or not _text(c.get("id"), 16):
            problems.append(f"{where}.id is required (e.g. \"C1\")")
            continue
        if c["id"] in by_id:
            problems.append(f"claim {c['id']} appears twice")
            continue
        by_id[c["id"]] = c
        problems += _check_claim(c, offered, receipts, artifacts or set(), report)

    def refs(ids, where: str) -> None:
        for ref in _ids(ids):
            if ref not in by_id:
                problems.append(f"{where} names claim {ref!r}, which is not in claims")

    answer = report.get("answer")
    if not isinstance(answer, dict) or not _text(answer.get("headline"), 400):
        problems.append("answer.headline is required (≤400 chars): what the owner should "
                        "take away, in one or two sentences")
    else:
        top = _ids(answer.get("top"))
        if not 1 <= len(top) <= 3:
            problems.append("answer.top names the one to three claims worth the most")
        refs(top, "answer.top")
        if not _text(answer.get("scale"), 400):
            problems.append("answer.scale is required: the scale answer in one line")

    business = report.get("business")
    if not isinstance(business, dict) or not _text(business.get("portrait"), 1500):
        problems.append("business.portrait is required (≤1500 chars): what is sold, to "
                        "whom, at what price, consumable or durable, concentration, stage")
    else:
        refs(business.get("claims"), "business.claims")

    problems += _check_economics(report.get("economics"), refs)

    chapters = report.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        problems.append("chapters is required: the story, each with a takeaway and its claims")
        chapters = []
    for i, ch in enumerate(chapters):
        where = f"chapters[{i}]"
        if not isinstance(ch, dict) or not _text(ch.get("title"), 120) or \
                not _text(ch.get("takeaway"), 300):
            problems.append(f"{where} needs a title (≤120) and a takeaway (≤300): no "
                            "chapter without a takeaway")
            continue
        if not _ids(ch.get("claims")):
            problems.append(f"{where} ({ch['title']}) names no claims")
        refs(ch.get("claims"), where)

    moves = report.get("moves") or []
    move_ids: dict[str, dict] = {}
    if not isinstance(moves, list):
        problems.append("moves must be a list")
        moves = []
    for i, m in enumerate(moves):
        if not isinstance(m, dict) or not _text(m.get("id"), 16):
            problems.append(f"moves[{i}].id is required (e.g. \"M1\")")
            continue
        if m["id"] in move_ids:
            problems.append(f"move {m['id']} appears twice")
            continue
        move_ids[m["id"]] = m
        problems += _check_move(m)
        refs(m.get("claims"), f"move {m['id']}.claims")
    for c in by_id.values():
        if c.get("kind") in ("fix", "opportunity") and _number(c.get("monthly_usd")) \
                and abs(c["monthly_usd"]) >= LOAD_BEARING_USD \
                and not any(c["id"] in _ids(m.get("claims")) for m in move_ids.values()):
            problems.append(f"claim {c['id']} is worth ${abs(c['monthly_usd']):,.0f} a month "
                            "and no move acts on it: say what to change, or it is not a "
                            "recommendation")

    problems += _check_tiers(report.get("tiers"), move_ids, by_id, offered)

    for key in ("leave_alone",):
        for i, x in enumerate(report.get(key) or []):
            if not isinstance(x, dict) or not _text(x.get("what")) or not _text(x.get("checked")):
                problems.append(f"{key}[{i}] needs what and checked (what was looked at)")
            else:
                refs(x.get("claims"), f"{key}[{i}]")
    for i, x in enumerate(report.get("assumptions") or []):
        if not isinstance(x, dict) or not _text(x.get("what")) or not _text(x.get("why")):
            problems.append(f"assumptions[{i}] needs what and why")
    for i, x in enumerate(report.get("owner_inputs") or []):
        if not isinstance(x, dict) or not _text(x.get("question")) or not _text(x.get("answer")):
            problems.append(f"owner_inputs[{i}] needs the question asked and his answer")
    for i, x in enumerate(report.get("not_covered") or []):
        if not isinstance(x, dict) or not _text(x.get("area")) or not _text(x.get("why")):
            problems.append(f"not_covered[{i}] needs area and why")

    if problems:
        raise ReportProblem(problems)

    # The platform's own word on what the data could not reach.
    named = {str(x.get("area")) for x in report.get("not_covered") or []}
    for a in AREAS:
        if not available(a, offered) and a.key not in named:
            report.setdefault("not_covered", []).append({
                "area": a.key, "title": a.title,
                "why": "the connected data source offers no "
                       + ", ".join(BY_CAPABILITY[n].label.lower() for n in a.needs
                                   if n not in offered),
                "would_answer": ", ".join(BY_CAPABILITY[n].label for n in a.needs
                                          if n not in offered),
                "by": "platform"})
    report["load_bearing"] = load_bearing(report)
    return report


def _check_claim(c: dict, offered: set[str], receipts, artifacts: set[str],
                 report: dict) -> list[str]:
    out: list[str] = []
    where = f"claim {c['id']}"
    if c.get("kind") not in CLAIM_KINDS:
        out.append(f"{where}.kind is one of {', '.join(CLAIM_KINDS)}")
    if not _text(c.get("statement"), 500):
        out.append(f"{where}.statement is required (≤500 chars), in plain English with "
                   "the number beside the claim")
    if c.get("confidence") not in CONFIDENCE:
        out.append(f"{where}.confidence is high, medium or low")
    area = c.get("area")
    if area not in BY_AREA:
        out.append(f"{where}.area is one of {', '.join(BY_AREA)}")
    elif not available(BY_AREA[area], offered) and c.get("kind") != "context":
        missing = ", ".join(BY_CAPABILITY[n].label.lower() for n in BY_AREA[area].needs
                            if n not in offered)
        out.append(f"{where} is about {area}, which the connected data cannot reach "
                   f"(no {missing}); it goes in not_covered, not in a claim")
    if c.get("monthly_usd") is not None and not _number(c.get("monthly_usd")):
        out.append(f"{where}.monthly_usd is a number of dollars a month, or null")
    owner_count = len(report.get("owner_inputs") or [])
    ev = c.get("evidence")
    if not isinstance(ev, list) or not ev:
        out.append(f"{where}.evidence is required: where its numbers came from")
        ev = []
    data_backed = False
    for j, e in enumerate(ev):
        w = f"{where}.evidence[{j}]"
        if not isinstance(e, dict):
            out.append(f"{w} must be an object")
            continue
        kinds = [k for k in EVIDENCE_KINDS if e.get(k) not in (None, "")]
        if len(kinds) != 1:
            out.append(f"{w} names exactly one of receipt, artifact, owner or source")
            continue
        kind = kinds[0]
        if kind in ("receipt", "artifact"):
            data_backed = True
            out += evidence.refusals(e.get("fields"), w)
            if kind == "receipt":
                found = receipts(str(e["receipt"])) if receipts else None
                if found is None:
                    out.append(f"{w} cites receipt {e['receipt']!r}, which is not a data "
                               "pull recorded for this project: cite the receipt data_fetch "
                               "returned")
                elif found.get("fields"):
                    pulled = {evidence.normalise(f) for f in found["fields"]}
                    absent = [f for f in e.get("fields") or [] if isinstance(f, str)
                              and evidence.classify(f) == "amazon"
                              and evidence.normalise(f) not in pulled]
                    if absent:
                        out.append(f"{w} cites {', '.join(map(repr, absent))}, which "
                                   f"receipt {e['receipt']} did not contain")
            elif e["artifact"] not in CITABLE_ARTIFACTS or e["artifact"] not in artifacts:
                out.append(f"{w} cites {e['artifact']!r}, which is not a platform pull of "
                           "this project")
        elif kind == "owner":
            idx = e["owner"]
            if not (isinstance(idx, int) and not isinstance(idx, bool)
                    and 0 <= idx < owner_count):
                out.append(f"{w}.owner is the index of the owner_inputs answer it rests on")
        elif kind == "source" and c.get("kind") != "context":
            out.append(f"{w} cites a published source; only a context claim may rest on "
                       "one — a finding about this account rests on its data")
    if c.get("kind") not in ("context", "works") and ev and not data_backed and \
            not any(isinstance(e, dict) and e.get("owner") is not None for e in ev):
        out.append(f"{where} rests on no data: cite the receipt or platform pull its "
                   "numbers came from")
    if c.get("kind") in ("fix", "opportunity", "risk", "forecast") and \
            (_number(c.get("monthly_usd")) and abs(c["monthly_usd"]) >= LOAD_BEARING_USD
             or c.get("kind") == "forecast"):
        t = c.get("test")
        if not (isinstance(t, dict) and _text(t.get("method")) and _text(t.get("result"))):
            out.append(f"{where} is load-bearing: test is required as {{\"method\", "
                       "\"result\"}} — what was done to try to prove it wrong (sample "
                       "size, recent vs prior, confounders, backtest) and what came out")
    return out


def _check_economics(econ, refs) -> list[str]:
    out: list[str] = []
    if not isinstance(econ, dict):
        return ["economics is required: what this business can afford by campaign job, "
                "beside today (bands, basis, claims)"]
    bands = econ.get("bands")
    if not isinstance(bands, list) or not bands:
        out.append("economics.bands is required: [{job, today_acos, affordable_acos, basis}]")
        bands = []
    for i, b in enumerate(bands):
        w = f"economics.bands[{i}]"
        if not isinstance(b, dict) or b.get("job") not in JOBS:
            out.append(f"{w}.job is one of {', '.join(JOBS)}")
            continue
        if not _number(b.get("today_acos")):
            out.append(f"{w}.today_acos is today's ACoS for that job (percent)")
        affordable = b.get("affordable_acos")
        if not (_number(affordable) or (isinstance(affordable, list) and len(affordable) == 2
                                        and all(_number(x) for x in affordable))):
            out.append(f"{w}.affordable_acos is a percent or a [low, high] range, derived "
                       "from this business's economics")
        if not _text(b.get("basis"), 600):
            out.append(f"{w}.basis says how it was derived (≤600 chars)")
    refs(econ.get("claims"), "economics.claims")
    if not _ids(econ.get("claims")):
        out.append("economics.claims names the claims the bands rest on")
    return out


def _check_move(m: dict) -> list[str]:
    out: list[str] = []
    where = f"move {m['id']}"
    action = m.get("action")
    if action not in MOVE_ACTIONS:
        return [f"{where}.action is one of {', '.join(MOVE_ACTIONS)}"]
    entity = m.get("entity")
    if action in AMAZON_ACTIONS:
        if not isinstance(entity, dict) or not _text(entity.get("name")) or \
                not _text(entity.get("type")):
            out.append(f"{where}.entity needs type and name (and Amazon IDs where the data "
                       "has them) so someone can find it")
        if action in CHANGES_A_VALUE and m.get("today") in (None, ""):
            out.append(f"{where}.today is required: the value it has now")
        if action != "pause" and m.get("change_to") in (None, ""):
            out.append(f"{where}.change_to is required: the value to set")
    elif not _text(m.get("what")) and not (isinstance(entity, dict) and _text(entity.get("name"))):
        out.append(f"{where} says what to do (what, or an entity)")
    if not _text(m.get("why"), 600):
        out.append(f"{where}.why is required (≤600): why this number")
    if not (isinstance(m.get("week"), int) and not isinstance(m.get("week"), bool)
            and 1 <= m["week"] <= 12):
        out.append(f"{where}.week is the rollout week, 1-12")
    chk = m.get("check")
    if not (isinstance(chk, dict) and isinstance(chk.get("day"), int)
            and _text(chk.get("expect"))):
        out.append(f"{where}.check is required as {{\"day\", \"expect\"}}: when to look and "
                   "what result to expect")
    if not _text(m.get("stop_rule")):
        out.append(f"{where}.stop_rule is required: what result turns it off")
    if not _ids(m.get("claims")):
        out.append(f"{where}.claims names the claims that justify it")
    exp = m.get("expected_monthly")
    if exp is not None and not (isinstance(exp, dict) and all(
            _number(exp.get(k)) for k in ("spend", "sales") if k in exp)):
        out.append(f"{where}.expected_monthly is {{\"spend\", \"sales\"}} in dollars a month")
    tier = m.get("tier")
    if tier is not None and tier not in (0, *SCALE_TIERS):
        out.append(f"{where}.tier is 0 (do now, whatever the budget) or one of "
                   f"{', '.join(str(t) for t in SCALE_TIERS)}")
    return out


def _check_tiers(tiers, moves: dict, claims: dict, offered: set[str]) -> list[str]:
    scale = BY_AREA["scale"]
    if not available(scale, offered):
        if tiers:
            return ["tiers cannot be answered: the connected data has no "
                    + ", ".join(BY_CAPABILITY[n].label.lower() for n in scale.needs
                                if n not in offered)]
        return []
    if not isinstance(tiers, list) or len(tiers) != len(SCALE_TIERS):
        return [f"tiers is required: one per extra monthly budget "
                f"({', '.join(f'${t:,}' for t in SCALE_TIERS)})"]
    out: list[str] = []
    seen = []
    for i, t in enumerate(tiers):
        where = f"tiers[{i}]"
        if not isinstance(t, dict) or t.get("monthly_usd") not in SCALE_TIERS:
            out.append(f"{where}.monthly_usd is one of {', '.join(map(str, SCALE_TIERS))}")
            continue
        usd = t["monthly_usd"]
        seen.append(usd)
        fits = t.get("fits_usd")
        if not _number(fits) or fits < 0 or fits > usd + 0.5:
            out.append(f"tier ${usd:,}: fits_usd is how much of it the evidence absorbs, "
                       "0 to the tier")
            continue
        lo, mid, hi = t.get("sales_low"), t.get("sales_mid"), t.get("sales_high")
        if not (all(_number(x) for x in (lo, mid, hi)) and lo <= mid <= hi):
            out.append(f"tier ${usd:,}: sales_low ≤ sales_mid ≤ sales_high are required — "
                       "a forecast is a range")
        if t.get("basis") not in ("acos", "tacos", "payback"):
            out.append(f"tier ${usd:,}: basis is acos, tacos or payback — the efficiency "
                       "the tier is held to")
        fc = t.get("forecast_claim")
        if fc not in claims:
            out.append(f"tier ${usd:,}: forecast_claim names the tested forecast claim")
        elif claims[fc].get("kind") != "forecast":
            out.append(f"tier ${usd:,}: {fc} is not a forecast claim")
        if fits + 1 < usd and not _text(t.get("binding")):
            out.append(f"tier ${usd:,} does not fill: binding says what stops it (search "
                       "volume, stock, efficiency) — never pad a tier")
        tier_moves = _ids(t.get("moves"))
        for ref in tier_moves:
            if ref not in moves:
                out.append(f"tier ${usd:,} names move {ref!r}, which is not in moves")
        if fits > 0.5 and not tier_moves:
            out.append(f"tier ${usd:,}: moves names the changes that spend it")
        spend = sum(float((moves[r].get("expected_monthly") or {}).get("spend") or 0)
                    for r in tier_moves if r in moves)
        if fits > 0.5 and tier_moves and abs(spend - fits) > max(0.15 * fits, 250):
            out.append(f"tier ${usd:,}: its moves spend ${spend:,.0f} a month but the tier "
                       f"says ${fits:,.0f} fits; they must agree within 15%")
    if sorted(seen) != sorted(SCALE_TIERS):
        out.append("tiers must answer each of " + ", ".join(f"${t:,}" for t in SCALE_TIERS)
                   + " once")
    return out


# ── the review ──────────────────────────────────────────────────────────────────

CRITERIA = ("R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10")
CRITERIA_TITLES = {
    "R1": "Knows the business", "R2": "Right yardstick", "R3": "Traceable",
    "R4": "Statistically sound", "R5": "Incremental", "R6": "Sees the demand",
    "R7": "Sees the constraints", "R8": "Executable", "R9": "Honest",
    "R10": "Tells a story"}
#: A re-derived number within this of the reported one matches.
REDERIVE_TOLERANCE = 0.05


def check_review(raw: bytes | str, audit: dict, audit_sha256: str) -> dict:
    """A review, parsed, or every reason it cannot be accepted. Whether its
    author may review (not the audit's) is the server's to check."""
    try:
        review = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ReportProblem([f"{REVIEW} is not valid JSON ({exc})"]) from None
    if not isinstance(review, dict):
        raise ReportProblem([f"{REVIEW} must be one JSON object"])
    out: list[str] = []
    if review.get("audit_sha256") != audit_sha256:
        out.append("audit_sha256 must be the sha256 of the latest ppc-audit.json — a review "
                   "is of one version, and this is not the one published")
    verdict = review.get("verdict")
    if verdict not in ("pass", "return"):
        out.append('verdict is "pass" or "return"')
    crit = review.get("criteria")
    if not isinstance(crit, dict):
        out.append("criteria is required: R1-R10, each {verdict, note}")
        crit = {}
    for r in CRITERIA:
        v = crit.get(r)
        if not isinstance(v, dict) or v.get("verdict") not in ("pass", "fail", "n/a") or \
                not _text(v.get("note")):
            out.append(f"criteria.{r} ({CRITERIA_TITLES[r]}) needs verdict pass, fail or n/a "
                       "and a note saying what was checked")
    if verdict == "pass":
        failed = [r for r in CRITERIA if isinstance(crit.get(r), dict)
                  and crit[r].get("verdict") == "fail"]
        if failed:
            out.append(f"a pass with failed criteria ({', '.join(failed)}) is a return")
        done = {}
        for i, d in enumerate(review.get("rederived") or []):
            if not isinstance(d, dict) or not isinstance(d.get("claim"), str) or \
                    not _number(d.get("rederived")) or not _text(d.get("note")):
                out.append(f"rederived[{i}] needs claim, rederived (your number) and note")
                continue
            done[d["claim"]] = d
        claims = {c["id"]: c for c in audit.get("claims") or []}
        for cid in audit.get("load_bearing") or load_bearing(audit):
            d = done.get(cid)
            if d is None:
                out.append(f"load-bearing claim {cid} was not re-derived")
                continue
            reported = d.get("reported")
            if not _number(reported):
                reported = claims.get(cid, {}).get("monthly_usd")
            if _number(reported) and reported != 0:
                if abs(d["rederived"] / reported - 1) > REDERIVE_TOLERANCE:
                    out.append(f"claim {cid}: re-derived {d['rederived']:,.2f} against "
                               f"{reported:,.2f} reported — more than "
                               f"{REDERIVE_TOLERANCE:.0%} apart is a return")
            elif d.get("matches") is not True:
                out.append(f"claim {cid}: say matches: true once your number agrees")
    if not _text(review.get("summary"), 2000):
        out.append("summary is required (≤2000 chars): what must change, or why it passes")
    if out:
        raise ReportProblem(out)
    return review


# ── the readable page ───────────────────────────────────────────────────────────


def _money(value) -> str:
    if not _number(value):
        return "—"
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.0f}"


def _pct(value) -> str:
    if isinstance(value, list) and len(value) == 2:
        return f"{value[0]:.0f}–{value[1]:.0f}%"
    return f"{value:.1f}%" if _number(value) else "—"


DRAFT = ("**Draft — not yet reviewed.** A separate reviewer has not yet re-derived the "
         "numbers and graded this audit; treat every figure as provisional.")


def claims_by_id(report: dict) -> dict:
    return {c["id"]: c for c in report.get("claims") or []}


def render(report: dict, reviewed: bool = False) -> str:
    """The readable audit, from the checked one."""
    by = claims_by_id(report)
    w = report["window"]
    a = report["answer"]
    out = [f"# Amazon advertising audit — {report.get('account') or 'the account'}", "",
           f"{w['start']} to {w['end']}"
           + (f" · {report['marketplace']}" if report.get("marketplace") else "")
           + f" · {report['account_type']} account", ""]
    if not reviewed:
        out += [DRAFT, ""]
    out += ["## The answer", "", a["headline"].strip(), ""]
    for cid in a["top"]:
        c = by[cid]
        out.append(f"- **{c['statement']}**"
                   + (f" — about {_money(c['monthly_usd'])} a month"
                      if _number(c.get("monthly_usd")) else ""))
    out += ["", f"**More money:** {a['scale']}", ""]
    out += ["## Your business", "", report["business"]["portrait"].strip(), ""]
    econ = report["economics"]
    out += ["## What you can afford to pay for a sale", "",
            "| Campaign job | ACoS today | Affordable | How we know |", "|---|---|---|---|"]
    for b in econ["bands"]:
        out.append(f"| {b['job']} | {_pct(b['today_acos'])} | {_pct(b['affordable_acos'])} | "
                   f"{b['basis']} |")
    out.append("")
    if econ.get("note"):
        out += [econ["note"], ""]
    for ch in report["chapters"]:
        out += [f"## {ch['title']}", "", f"*{ch['takeaway']}*", ""]
        for cid in ch["claims"]:
            out += _claim_lines(by[cid])
        out.append("")
    tiers = report.get("tiers") or []
    moves = {m["id"]: m for m in report.get("moves") or []}
    if tiers:
        out += ["## Where more money would go", "",
                "| Extra a month | Fits | Sales it buys | ACoS | TACoS | Held to |",
                "|---|---|---|---|---|---|"]
        for t in tiers:
            out.append(f"| {_money(t['monthly_usd'])} | {_money(t['fits_usd'])} | "
                       f"{_money(t['sales_low'])}–{_money(t['sales_high'])} | "
                       f"{_pct(t.get('acos'))} | {_pct(t.get('tacos'))} | {t['basis']} |")
        out.append("")
        for t in tiers:
            if t.get("binding"):
                out.append(f"- **{_money(t['monthly_usd'])}:** {t['binding']}")
        out.append("")
    if moves:
        out += ["## The plan", "",
                "| Week | Change | Today | Set to | Why | Check | Stop if |",
                "|---|---|---|---|---|---|---|"]
        for m in sorted(moves.values(), key=lambda m: (m["week"], m["id"])):
            ent = m.get("entity") or {}
            what = f"{m['action'].replace('_', ' ')}: {ent.get('name') or m.get('what', '')}"
            out.append(f"| {m['week']} | {what} | {m.get('today', '—')} | "
                       f"{m.get('change_to', '—')} | {m['why']} | "
                       f"day {m['check']['day']}: {m['check']['expect']} | {m['stop_rule']} |")
        out.append("")
    if report.get("leave_alone"):
        out += ["## Working — leave it alone", ""]
        out += [f"- **{x['what']}** {x['checked']}" for x in report["leave_alone"]]
        out.append("")
    if report.get("not_covered"):
        out += ["## Not covered", ""]
        out += [f"- **{x.get('title') or x['area']}.** {x['why']}"
                + (f" Would answer it: {x['would_answer']}." if x.get("would_answer") else "")
                for x in report["not_covered"]]
        out.append("")
    if report.get("assumptions") or report.get("owner_inputs"):
        out += ["## Assumptions and what you told us", ""]
        out += [f"- *Assumed:* {x['what']}" + (f" = {x['value']}" if x.get("value") else "")
                + f". {x['why']}" for x in report.get("assumptions") or []]
        out += [f"- *You told us:* {x['question']} — {x['answer']}"
                for x in report.get("owner_inputs") or []]
        out.append("")
    if report.get("questions"):
        out += ["## Questions that would sharpen this", ""]
        out += [f"- {q.get('question')} {q.get('why', '')}" for q in report["questions"]
                if isinstance(q, dict)]
        out.append("")
    return "\n".join(out)


def _claim_lines(c: dict) -> list[str]:
    worth = f" About {_money(c['monthly_usd'])} a month." if _number(c.get("monthly_usd")) else ""
    t = c.get("test") or {}
    tested = f" *How we checked:* {t['method']} → {t['result']}." if t.get("method") else ""
    falsifier = f" *Would change our mind:* {c['falsifier']}." if c.get("falsifier") else ""
    return [f"- **{c['statement']}**{worth} ({c['confidence']} confidence).{tested}{falsifier}"]
