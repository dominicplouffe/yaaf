"""A synthetic Amazon advertising account, served the way the Qore MCP serves one.

Built from a known truth, so a test can say what the platform should find and
check that it did: every campaign's real response curve, which terms waste
money, which convert and are not harvested, which placement is mispriced,
which product has lost the buy box. `healthy=True` builds the same account
with none of the planted problems — the audit of a well-run account must come
back saying so.

Every row also carries Trellis's own settings (target and max ACOS, tags, bid
previews, cost of goods, goals) as Qore's do, so a test can prove none of them
travels past the adapter.
"""

from __future__ import annotations

import json
import random
from datetime import date, timedelta

#: Fields a vendor adds that are not Amazon's. Planted on every row.
BAIT = {"target_acos": 0.31, "max_acos": 0.45, "tags": ["hero"], "preview_bid": 9.99,
        "total_cogs": 123.0, "daily_sales_goal": 5000, "campaign_goal": "acos_target"}
TOOLS = (("get_stats", "Retrieve aggregated performance statistics: sales, cost, acos"),
         ("get_timeseries", "Retrieve time-indexed performance statistics"),
         ("query_dataset", "Execute a structured query against any registered dataset"))


class Account:
    def __init__(self, *, healthy: bool = False, seed: int = 11, end: date | None = None,
                 days: int = 123):
        rng = random.Random(seed)
        self.end = end or (date.today() - timedelta(days=1))
        self.days = [(self.end - timedelta(days=days - 1 - i)).isoformat() for i in range(days)]
        self.campaigns = []
        for i in range(10):
            b = rng.uniform(0.5, 0.75)
            capped = (not healthy) and i in (1, 2)
            self.campaigns.append({
                "campaign_id": str(1000 + i), "campaign_name": f"SP - Line {i}",
                "campaign_type": "sponsoredProducts", "campaign_targeting_type": "manual",
                "campaign_state": "enabled", "campaign_daily_budget": 150.0,
                "campaign_bid_strategy": "legacyForSales", "campaign_placement_top": 0.5,
                "campaign_placement_product_page": 0.4 if i == 3 and not healthy else 0.0,
                "creative": json.dumps({"brandName": "Acme"}),
                "b": b, "base": rng.uniform(60, 140), "capped": capped,
                "share": 0.3 if i in (4, 5) else 0.7,
                # Efficient campaigns are where the headroom is: the capped ones
                # and the low-share winners run well under the account's ACOS.
                "acos": 0.16 if i in (1, 2) else 0.14 if i in (4, 5) else 0.24})
        # Daily rows from each campaign's true curve: sales = k · spend^b, noise ±8%.
        self.daily = []
        for c in self.campaigns:
            level = c["base"]
            k = (c["base"] / c["acos"]) / c["base"] ** c["b"]  # its ACOS at its base spend
            for day in self.days:
                level = max(15.0, level * rng.uniform(0.9, 1.1))
                spend = level
                sales = k * spend ** c["b"] * rng.uniform(0.92, 1.08)
                clicks = spend / 1.1
                self.daily.append({
                    "start_date": day, "campaign_id": c["campaign_id"], "cost": spend,
                    "ad_sales": sales, "conversion": sales / 30, "clicks": clicks,
                    "impressions": clicks * 300,
                    "serving_hours": 13.0 if c["capped"] else 23.5,
                    "top_of_search_impression_share": c["share"], **BAIT})
        self.search_terms = self._terms(rng, healthy)
        self.placements = self._placements(healthy)
        self.products = [
            {"product_id": 1, "asin": "B0AAAAAAA1", "sku": "S1", "product_title": "Widget",
             "total_sales": 90_000, "total_units": 3000, "ad_sales": 30_000, "cost": 6_000,
             "sessions": 40_000, "buy_box_percentage": 99.0, "product_quantity": 900,
             "rating": 4.6, "ratings_total": 2100, "product_price": 29.99, **BAIT},
            {"product_id": 2, "asin": "B0AAAAAAA2", "sku": "S2", "product_title": "Gadget",
             "total_sales": 40_000, "total_units": 1000, "ad_sales": 12_000, "cost": 3_000,
             "sessions": 20_000, "buy_box_percentage": 99.0 if healthy else 68.0,
             "product_quantity": 400, "rating": 4.4, "ratings_total": 900,
             "product_price": 39.99, **BAIT}]
        self.targets = [{"adv_keyword_id": 10 + i, "ad_group_id": f"ag{c['campaign_id']}",
                         "text": f"widget {i}", "match_type": "exact", "state": "enabled",
                         "bid": 1.2, "impressions": 40_000, "clicks": 300, "cost": 330,
                         "attributed_sales_7d": 1500, "attributed_conversions_7d": 50,
                         "top_of_search_impression_share": c["share"], **BAIT}
                        for i, c in enumerate(self.campaigns)]
        self.calls: list[tuple[str, dict]] = []

    def _terms(self, rng, healthy):
        rows = []
        for i, c in enumerate(self.campaigns):
            for j in range(8):  # healthy terms: ~22% ACOS, plenty of orders
                rows.append(self._term(f"widget {i} {j}", c, clicks=120, spend=130,
                                       sales=600, orders=20, match="exact",
                                       matched=f"widget {i} {j}"))
        if not healthy:
            for j in range(6):  # waste: 60 clicks, nothing sold
                rows.append(self._term(f"free widget {j}", self.campaigns[0], clicks=60,
                                       spend=70, sales=0, orders=0, match="broad",
                                       matched="widget"))
            for j in range(4):  # converting, only through broad
                rows.append(self._term(f"blue widget pro {j}", self.campaigns[6], clicks=80,
                                       spend=60, sales=500, orders=12, match="broad",
                                       matched="widget"))
            # The same query bought twice: the weaker campaign at 4x the ACOS.
            rows.append(self._term("widget set", self.campaigns[7], clicks=900, spend=900,
                                   sales=4500, orders=150, match="exact", matched="widget set"))
            rows.append(self._term("widget set", self.campaigns[8], clicks=900, spend=900,
                                   sales=1100, orders=40, match="broad", matched="widget"))
        return rows

    def _term(self, query, c, *, clicks, spend, sales, orders, match, matched):
        return {"query": query, "campaign_id": c["campaign_id"],
                "ad_group_id": f"ag{c['campaign_id']}", "keyword_text": matched,
                "target_name": None, "match_type": match, "search_term_type": "keyword",
                "impressions": clicks * 250, "clicks": clicks, "cost": spend,
                "attributed_sales_7d": sales, "attributed_conversions_7d": orders,
                "sales": 0, "conversions_14d": 0, **BAIT}

    def _placements(self, healthy):
        rows = []
        for c in self.campaigns:
            for name, share in (("Top of Search on-Amazon", 0.5), ("Detail Page on-Amazon", 0.3),
                                ("Other on-Amazon", 0.2)):
                spend = 1000 * share
                acos = 0.22
                if name.startswith("Detail") and c["campaign_id"] == "1003" and not healthy:
                    acos = 0.9
                rows.append({"campaign_id": c["campaign_id"], "placementClassification": name,
                             "impressions": spend * 300, "clicks": spend / 1.1, "cost": spend,
                             "attributed_sales_7d": spend / acos,
                             "attributed_conversions_7d": spend / acos / 30, **BAIT})
        return rows

    # ── the MCP surface ─────────────────────────────────────────────────────────

    def __call__(self, tool: str, args: dict) -> dict:
        self.calls.append((tool, args))
        if tool == "get_timeseries":
            lo, hi = args["start_date"], args["end_date"]
            rows = [r for r in self.daily if lo <= r["start_date"] <= hi]
            if args["table_name"] == "org":
                by_day: dict[str, dict] = {}
                for r in rows:
                    d = by_day.setdefault(r["start_date"], {"start_date": r["start_date"],
                                                             "cost": 0.0, "ad_sales": 0.0,
                                                             "conversion": 0.0, **BAIT})
                    d["cost"] += r["cost"]
                    d["ad_sales"] += r["ad_sales"]
                    d["conversion"] += r["conversion"]
                for d in by_day.values():
                    d["total_sales"] = d["ad_sales"] * 3
                rows = list(by_day.values())
            return self._page(rows, args)
        if tool == "get_stats":
            table = {"keyword": self.targets, "target": [], "product": self.products}
            return self._page(table[args["table_name"]], args)
        if tool == "query_dataset":
            q = args["query"]
            source = {"api_gvcampaignch": self.campaigns,
                      "api_gvadgroupch": [{"ad_group_id": f"ag{c['campaign_id']}",
                                           "campaign_id": c["campaign_id"]}
                                          for c in self.campaigns],
                      "api_gvsearchtermreportch": self.search_terms,
                      "api_campaignplacementreportch": self.placements}[q["from"]]
            if q.get("page", 1) > 1:
                return _wrap({"data": []})
            return _wrap({"data": [dict(r) for r in source]})
        raise AssertionError(f"the adapter called {tool}, which this fake does not serve")

    def _page(self, rows, args):
        start = int(args.get("cursor") or 0)
        size = args.get("limit") or 1000
        page = rows[start:start + size]
        nxt = start + size if start + size < len(rows) else None
        return _wrap({"results": page, "cursor": str(nxt) if nxt else None})


def _wrap(body: dict) -> dict:
    return {"content": [{"type": "text", "text": "Answered by connector \"Acme\"."},
                        {"type": "text", "text": json.dumps(body)}]}
