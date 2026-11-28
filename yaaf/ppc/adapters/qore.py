"""The Trellis Qore MCP server, read for Amazon's numbers only.

Qore serves Amazon's reports through rollup tables (`get_stats`,
`get_timeseries`) and raw datasets (`query_dataset`). It also carries Trellis's
own settings — ad plan goals, target and max ACOS, tags, bid previews, cost of
goods, daily goals — and none of those are requested here. Each pull names the
exact Amazon columns it reads.

Every section is pulled on its own: one that fails is recorded in
`missing` with the reason, the checks that need it do not run, and the report
says so, rather than the whole audit failing on one report Amazon has not
delivered for this account.
"""

from __future__ import annotations

import json

from yaaf.ppc.adapters import AdapterError, Call, parse

NAME = "qore"
REQUIRED = {"get_stats", "get_timeseries", "query_dataset"}
PAGE = 1000
MAX_PAGES = 50


def matches(names: set[str]) -> bool:
    return names >= REQUIRED


def _rows(call: Call, tool: str, args: dict) -> list[dict]:
    """Every page of one rollup query."""
    out, cursor = [], None
    for _ in range(MAX_PAGES):
        body = parse(call(tool, {**args, **({"cursor": cursor} if cursor else {})}))
        if not isinstance(body, dict):
            raise AdapterError(f"{tool} returned {type(body).__name__}, not rows")
        out += body.get("results") or []
        cursor = body.get("cursor")
        if not cursor:
            return out
    raise AdapterError(f"{tool} returned more than {MAX_PAGES} pages")


def _dataset(call: Call, query: dict) -> list[dict]:
    out = []
    for page in range(1, MAX_PAGES + 1):
        body = parse(call("query_dataset", {"query": {**query, "limit": 10_000, "page": page}}))
        if not isinstance(body, dict):
            raise AdapterError("query_dataset returned no rows object")
        rows = body.get("data") or []
        out += rows
        if len(rows) < 10_000:
            return out
    raise AdapterError("query_dataset returned more than 50 pages")


def _num(row: dict, *names: str) -> float:
    for n in names:
        v = row.get(n)
        if v is not None and v != "":
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return 0.0


def _opt(row: dict, name: str):
    v = row.get(name)
    return None if v in (None, "") else v


def _brand(creative) -> str | None:
    if isinstance(creative, str):
        try:
            creative = json.loads(creative)
        except ValueError:
            return None
    if isinstance(creative, dict):
        return creative.get("brandName") or creative.get("brand_name")
    return None


def pull(call: Call, *, start: str, end: str, history_start: str,
         marketplace: str = "USA") -> dict:
    bundle: dict = {"source": NAME, "marketplace": marketplace,
                    "window": {"start": start, "end": end}, "missing": []}

    def section(name: str, fn):
        try:
            bundle[name] = fn()
        except (AdapterError, KeyError, TypeError, ValueError) as exc:
            bundle[name] = None
            bundle["missing"].append({"table": name, "reason": str(exc)[:300]})

    series = {"filters": [], "date_group": "day", "start_date": history_start,
              "end_date": end, "market_place": marketplace, "limit": PAGE}

    def account_daily():
        return [{"date": str(r["start_date"])[:10], "spend": _num(r, "cost"),
                 "ad_sales": _num(r, "ad_sales", "attributed_sales_7d"),
                 "ad_orders": _num(r, "conversion", "attributed_conversions_7d"),
                 "total_sales": _opt(r, "total_sales")}
                for r in _rows(call, "get_timeseries",
                               {**series, "table_name": "org", "entity": ["org_id"]})]

    def campaign_daily():
        return [{"date": str(r["start_date"])[:10], "campaign_id": str(r["campaign_id"]),
                 "spend": _num(r, "cost"),
                 "ad_sales": _num(r, "ad_sales", "attributed_sales_7d"),
                 "ad_orders": _num(r, "conversion", "attributed_conversions_7d"),
                 "clicks": _num(r, "clicks"), "impressions": _num(r, "impressions"),
                 "serving_hours": _opt(r, "serving_hours"),
                 "tos_impression_share": _opt(r, "top_of_search_impression_share")}
                for r in _rows(call, "get_timeseries",
                               {**series, "table_name": "campaign", "entity": ["campaign_id"]})]

    def campaigns():
        rows = _dataset(call, {
            "from": "api_gvcampaignch",
            "select": [{"field": f} for f in (
                "campaign_id", "campaign_name", "campaign_type", "campaign_targeting_type",
                "campaign_state", "campaign_daily_budget", "campaign_bid_strategy",
                "campaign_placement_top", "campaign_placement_product_page", "creative")],
            "filters": [{"id": "market_place", "op": "eq", "value": marketplace}]})
        return [{"campaign_id": str(r["campaign_id"]), "name": r.get("campaign_name") or "",
                 "ad_type": r.get("campaign_type") or "unknown",
                 "targeting_type": _opt(r, "campaign_targeting_type"),
                 "state": r.get("campaign_state") or "unknown",
                 "daily_budget": _opt(r, "campaign_daily_budget"),
                 "bid_strategy": _opt(r, "campaign_bid_strategy"),
                 "placement_top_pct": _opt(r, "campaign_placement_top"),
                 "placement_product_page_pct": _opt(r, "campaign_placement_product_page"),
                 "brand_name": _brand(r.get("creative"))} for r in rows]

    totals = {"dimensions": {"total": False, "date_group": "day", "entity": None},
              "start_date": start, "end_date": end, "market_place": marketplace,
              "limit": PAGE, "ordering": "-cost"}

    def targets():
        groups = {str(r["ad_group_id"]): str(r["campaign_id"]) for r in _dataset(call, {
            "from": "api_gvadgroupch",
            "select": [{"field": "ad_group_id"}, {"field": "campaign_id"}]})}
        out = []
        for table, entity in (("keyword", "adv_keyword_id"), ("target", "adv_target_id")):
            args = {**totals, "table_name": table,
                    "dimensions": {**totals["dimensions"], "entity": entity}}
            for r in _rows(call, "get_stats", args):
                gid = str(r.get("ad_group_id") or "")
                out.append({"target_key": f"{table}:{r[entity]}", "campaign_id": groups.get(gid),
                            "ad_group_id": gid, "text": r.get("text") or "",
                            "match_type": r.get("match_type") or table,
                            "state": _opt(r, "state"), "bid": _opt(r, "bid"),
                            "impressions": _num(r, "impressions"), "clicks": _num(r, "clicks"),
                            "spend": _num(r, "cost"),
                            "ad_sales": _num(r, "attributed_sales_7d", "ad_sales"),
                            "ad_orders": _num(r, "attributed_conversions_7d", "conversion"),
                            "tos_impression_share": _opt(r, "top_of_search_impression_share")})
        return out

    window = [{"id": "start_date", "op": "gte", "value": start},
              {"id": "start_date", "op": "lte", "value": end}]
    sums = {k: {"fn": "sum", "field": k} for k in (
        "impressions", "clicks", "cost", "attributed_sales_7d", "attributed_conversions_7d")}

    def search_terms():
        groups = ["query", "campaign_id", "ad_group_id", "keyword_text", "target_name",
                  "match_type", "search_term_type"]
        rows = _dataset(call, {
            "from": "api_gvsearchtermreportch",
            "select": [{"field": g} for g in groups],
            "aggregations": {**sums, "sales": {"fn": "sum", "field": "sales"},
                             "conversions_14d": {"fn": "sum", "field": "conversions_14d"}},
            "group_by": groups,
            "filters": [*window, {"id": "marketplace", "op": "eq", "value": marketplace}]})
        # Sponsored Products rows carry 7-day attribution, Sponsored Brands rows
        # their 14-day `sales`; each row has one or the other.
        return [{"query": r.get("query") or "", "campaign_id": str(r["campaign_id"]),
                 "ad_group_id": str(r.get("ad_group_id") or ""),
                 "matched": r.get("keyword_text") or r.get("target_name") or None,
                 "match_type": _opt(r, "match_type"), "term_type": _opt(r, "search_term_type"),
                 "impressions": _num(r, "impressions"), "clicks": _num(r, "clicks"),
                 "spend": _num(r, "cost"),
                 "ad_sales": _num(r, "attributed_sales_7d") + _num(r, "sales"),
                 "ad_orders": _num(r, "attributed_conversions_7d") + _num(r, "conversions_14d")}
                for r in rows]

    def placements():
        rows = _dataset(call, {
            "from": "api_campaignplacementreportch",
            "select": [{"field": "campaign_id"}, {"field": "placementClassification"}],
            "aggregations": sums, "group_by": ["campaign_id", "placementClassification"],
            "filters": [*window, {"id": "market_place", "op": "eq", "value": marketplace}]})
        return [{"campaign_id": str(r["campaign_id"]),
                 "placement": r.get("placementClassification") or "unknown",
                 "impressions": _num(r, "impressions"), "clicks": _num(r, "clicks"),
                 "spend": _num(r, "cost"), "ad_sales": _num(r, "attributed_sales_7d"),
                 "ad_orders": _num(r, "attributed_conversions_7d")} for r in rows]

    def products():
        args = {**totals, "table_name": "product",
                "dimensions": {**totals["dimensions"], "entity": "product_id"}}
        return [{"asin": r.get("asin") or str(r.get("product_id")), "sku": _opt(r, "sku"),
                 "title": _opt(r, "product_title"), "total_sales": _opt(r, "total_sales"),
                 "total_units": _opt(r, "total_units"), "ad_sales": _opt(r, "ad_sales"),
                 "spend": _opt(r, "cost"), "sessions": _opt(r, "sessions"),
                 "buy_box_pct": _opt(r, "buy_box_percentage"),
                 "stock": _opt(r, "product_quantity"), "rating": _opt(r, "rating"),
                 "ratings_total": _opt(r, "ratings_total"), "price": _opt(r, "product_price")}
                for r in _rows(call, "get_stats", args)]

    for name, fn in (("account_daily", account_daily), ("campaign_daily", campaign_daily),
                     ("campaigns", campaigns), ("targets", targets),
                     ("search_terms", search_terms), ("placements", placements),
                     ("products", products)):
        section(name, fn)
    return bundle
