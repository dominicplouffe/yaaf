"""The account as the platform pulled it: one shape, whatever server it came from.

An adapter (`yaaf.ppc.adapters`) turns one MCP vendor's answers into these
tables; the checks, the pools and the backtest read nothing else. Every column
is Amazon's or computed from Amazon's alone (`yaaf.ppc.fields`) — there is no
column for a target, a tag or a cost of goods, so no check can reach for one.

Sales and orders are attributed: 7-day for Sponsored Products, 14-day for
Sponsored Brands and display, as Amazon reports them.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

#: Table → its columns: (name, kind, required). kind is "str" or "num".
TABLES: dict[str, tuple[tuple[str, str, bool], ...]] = {
    "account_daily": (("date", "str", True), ("spend", "num", True),
                      ("ad_sales", "num", True), ("ad_orders", "num", True),
                      ("total_sales", "num", False)),
    "campaign_daily": (("date", "str", True), ("campaign_id", "str", True),
                       ("spend", "num", True), ("ad_sales", "num", True),
                       ("ad_orders", "num", True), ("clicks", "num", True),
                       ("impressions", "num", True), ("serving_hours", "num", False),
                       ("tos_impression_share", "num", False)),
    "campaigns": (("campaign_id", "str", True), ("name", "str", True),
                  ("ad_type", "str", True), ("targeting_type", "str", False),
                  ("state", "str", True), ("daily_budget", "num", False),
                  ("bid_strategy", "str", False), ("placement_top_pct", "num", False),
                  ("placement_product_page_pct", "num", False), ("brand_name", "str", False)),
    "targets": (("target_key", "str", True), ("campaign_id", "str", False),
                ("ad_group_id", "str", True), ("text", "str", True),
                ("match_type", "str", True), ("state", "str", False), ("bid", "num", False),
                ("impressions", "num", True), ("clicks", "num", True), ("spend", "num", True),
                ("ad_sales", "num", True), ("ad_orders", "num", True),
                ("tos_impression_share", "num", False)),
    "search_terms": (("query", "str", True), ("campaign_id", "str", True),
                     ("ad_group_id", "str", True), ("matched", "str", False),
                     ("match_type", "str", False), ("term_type", "str", False),
                     ("impressions", "num", True), ("clicks", "num", True),
                     ("spend", "num", True), ("ad_sales", "num", True),
                     ("ad_orders", "num", True)),
    "placements": (("campaign_id", "str", True), ("placement", "str", True),
                   ("impressions", "num", True), ("clicks", "num", True),
                   ("spend", "num", True), ("ad_sales", "num", True),
                   ("ad_orders", "num", True)),
    "products": (("asin", "str", True), ("sku", "str", False), ("title", "str", False),
                 ("total_sales", "num", False), ("total_units", "num", False),
                 ("ad_sales", "num", False), ("spend", "num", False),
                 ("sessions", "num", False), ("buy_box_pct", "num", False),
                 ("stock", "num", False), ("rating", "num", False),
                 ("ratings_total", "num", False), ("price", "num", False),
                 ("referral_fee", "num", False), ("fba_fee", "num", False)),
}

#: The Amazon fields each table's columns came from, cited by every finding a
#: check makes from it — the same citation a crew's finding must carry.
SOURCE_FIELDS = {
    "account_daily": ["date", "cost", "attributed_sales_7d", "attributed_conversions_7d",
                      "total_sales"],
    "campaign_daily": ["date", "campaign_id", "cost", "attributed_sales_7d",
                       "attributed_conversions_7d", "clicks", "impressions", "serving_hours",
                       "top_of_search_impression_share"],
    "campaigns": ["campaign_id", "campaign_name", "campaign_type", "campaign_targeting_type",
                  "campaign_state", "campaign_daily_budget", "campaign_bid_strategy",
                  "placement_bid_adjustment"],
    "targets": ["keyword_id", "target_id", "ad_group_id", "keyword_text", "match_type",
                "state", "bid", "impressions", "clicks", "cost", "attributed_sales_7d",
                "attributed_conversions_7d", "top_of_search_impression_share"],
    "search_terms": ["search_term", "campaign_id", "ad_group_id", "keyword_text",
                     "match_type", "impressions", "clicks", "cost", "attributed_sales_7d",
                     "attributed_conversions_7d"],
    "placements": ["campaign_id", "placement_classification", "impressions", "clicks",
                   "cost", "attributed_sales_7d", "attributed_conversions_7d"],
    "products": ["asin", "sku", "total_sales", "total_units", "ad_sales", "cost",
                 "sessions", "buy_box_percentage", "product_quantity", "rating",
                 "ratings_total", "product_price", "referral_fee", "fba_fee"],
}


class BundleProblem(ValueError):
    pass


def validate(bundle: dict) -> dict:
    """The bundle with every row's types settled, or why it cannot be read."""
    out: dict = {"window": dict(bundle.get("window") or {}),
                 "marketplace": bundle.get("marketplace"), "source": bundle.get("source"),
                 "missing": list(bundle.get("missing") or [])}
    try:
        start = date.fromisoformat(out["window"]["start"])
        end = date.fromisoformat(out["window"]["end"])
    except (KeyError, TypeError, ValueError):
        raise BundleProblem("the bundle needs window.start and window.end as YYYY-MM-DD") from None
    if start > end:
        raise BundleProblem("window.start is after window.end")
    for table, columns in TABLES.items():
        rows = bundle.get(table)
        if rows is None:
            out[table] = None
            continue
        clean = []
        for i, row in enumerate(rows):
            fixed = {}
            for name, kind, required in columns:
                value = row.get(name)
                if value is None or value == "":
                    if required:
                        raise BundleProblem(f"{table}[{i}] is missing {name}")
                    fixed[name] = None
                    continue
                if kind == "num":
                    try:
                        fixed[name] = float(value)
                    except (TypeError, ValueError):
                        raise BundleProblem(f"{table}[{i}].{name} is not a number") from None
                else:
                    fixed[name] = str(value)
            clean.append(fixed)
        out[table] = clean
    if not out.get("campaign_daily") and not out.get("account_daily"):
        raise BundleProblem("the bundle has no daily performance at all")
    return out


def window_days(bundle: dict) -> int:
    w = bundle["window"]
    return (date.fromisoformat(w["end"]) - date.fromisoformat(w["start"])).days + 1


def in_window(rows, bundle: dict):
    w = bundle["window"]
    return [r for r in rows or [] if w["start"] <= r["date"] <= w["end"]]


def totals(rows, keys=("spend", "ad_sales", "ad_orders", "clicks", "impressions")) -> dict:
    out = dict.fromkeys(keys, 0.0)
    for r in rows:
        for k in keys:
            out[k] += r.get(k) or 0.0
    return out


def by(rows, key: str) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r[key]].append(r)
    return groups


def daily_series(rows, start: str, end: str) -> list[tuple[str, float, float]]:
    """(date, spend, sales) for every day in the range, zero-filled."""
    spend: dict[str, float] = defaultdict(float)
    sales: dict[str, float] = defaultdict(float)
    for r in rows:
        spend[r["date"]] += r["spend"]
        sales[r["date"]] += r["ad_sales"]
    day, last, out = date.fromisoformat(start), date.fromisoformat(end), []
    while day <= last:
        key = day.isoformat()
        out.append((key, spend.get(key, 0.0), sales.get(key, 0.0)))
        day += timedelta(days=1)
    return out


def default_window(today: date, days: int = 60, settle: int = 3) -> tuple[str, str]:
    """The last `days` complete days, ending `settle` days ago so attribution has
    mostly arrived (most Sponsored Products conversions land within days)."""
    end = today - timedelta(days=settle)
    return (end - timedelta(days=days - 1)).isoformat(), end.isoformat()
