"""The standard checks, computed by the platform from the pulled account.

These are the parts of an audit that are arithmetic, not judgement: spend on
search terms that have had enough clicks to judge and sold nothing, converting
terms nobody bids on exactly, the same query bought twice, placements and
targets running far hotter than the account, profitable campaigns that run
out of money. They run on Amazon's data alone (`yaaf.ppc.data`), the same way
for every account, and each finding carries the rows behind it so a reader can
check it line by line.

**One yardstick for "costs too much": excess spend.** A row's excess is what
it spent beyond what the account's own ACOS would have paid for the same
sales: spend − sales × account ACOS. It is zero for a row at or under the
account's efficiency, and for a row that sold nothing it is all of its spend.
It never invents a target; the account is the benchmark. A row is only judged
once it has enough clicks to be judged (`click_floor`).

**What a check does not decide** is whether to act. A check can say a term
spent $1,900 without an order on 140 clicks; only the crew, reading the term,
can say it is a launch keyword the owner wants kept. Every platform finding
must be either carried into the report or dismissed there with a reason.
"""

from __future__ import annotations

import re
from collections import defaultdict

from yaaf.ppc import data as D

#: Rows kept under each finding, most money first: all of them, so the
#: workbook's totals are the finding's totals, up to a size a sheet can hold.
ROWS = 1000
#: Below this much monthly money a finding is not worth the owner's attention.
MIN_EFFECT_USD = 50.0
#: A row is judged only past this many clicks: two and a half times the clicks
#: one order takes at the account's conversion rate, and never fewer than 10.
CLICKS_PER_ORDER_MULTIPLE = 2.5
MIN_CLICKS = 10
#: "Far hotter than the account": ACOS above this multiple of the account's.
HOT_MULTIPLE = 1.5
#: "A winner": ACOS at or under this multiple of the account's.
WINNER_MULTIPLE = 0.8
#: Serving fewer hours than this a day means the budget ran out.
CAPPED_HOURS = 20.0
#: Low top-of-search impression share: room to buy more of the same traffic.
LOW_SHARE = 0.5
AUTO_GROUPS = {"close-match": "close match", "loose-match": "loose match",
               "substitutes": "substitutes", "complements": "complements",
               "queryhighrelmatches": "close match", "querybroadrelmatches": "loose match",
               "asinsubstituterelated": "substitutes", "asinaccessoryrelated": "complements"}


def _acos(spend: float, sales: float) -> float | None:
    return spend / sales if sales > 0 else None


def _pct(x: float | None) -> str:
    return "no sales" if x is None else f"{100 * x:.1f}%"


def _money(x: float) -> str:
    return f"${x:,.0f}"


def _n(count: int, one: str, many: str | None = None) -> str:
    return f"{count:,} {one if count == 1 else (many or one + 's')}"


class Context:
    """The account's own yardsticks, computed once."""

    def __init__(self, bundle: dict):
        self.bundle = bundle
        self.days = D.window_days(bundle)
        self.monthly = 30.0 / self.days
        rows = D.in_window(bundle.get("campaign_daily"), bundle) or \
            D.in_window(bundle.get("account_daily"), bundle)
        t = D.totals(rows, ("spend", "ad_sales", "ad_orders"))
        acct = D.in_window(bundle.get("account_daily"), bundle)
        clicks = sum(r["clicks"] for r in D.in_window(bundle.get("campaign_daily"), bundle))
        if not clicks:
            clicks = sum(r["clicks"] for r in bundle.get("search_terms") or [])
        self.spend, self.sales, self.orders = t["spend"], t["ad_sales"], t["ad_orders"]
        self.clicks = clicks
        self.total_sales = (sum(r["total_sales"] or 0 for r in acct)
                            if acct and all(r.get("total_sales") is not None for r in acct)
                            else None)
        self.acos = _acos(self.spend, self.sales)
        self.cvr = self.orders / clicks if clicks else None
        floor = CLICKS_PER_ORDER_MULTIPLE / self.cvr if self.cvr else MIN_CLICKS * 3
        self.click_floor = max(MIN_CLICKS, round(floor))
        self.campaigns = {c["campaign_id"]: c for c in bundle.get("campaigns") or []}

    def excess(self, spend: float, sales: float) -> float:
        if self.acos is None:
            return 0.0
        return max(0.0, spend - sales * self.acos)

    def name(self, campaign_id: str | None) -> str:
        c = self.campaigns.get(str(campaign_id)) if campaign_id is not None else None
        return c["name"] if c else f"campaign {campaign_id}"


def _finding(key: str, n: int, *, what, evidence, figure, count, action, effect,
             effect_kind, confidence, would_change, tables, rows) -> dict:
    fields = sorted({f for t in tables for f in D.SOURCE_FIELDS[t]})
    return {"platform_id": f"P-{key}-{n}", "what": what, "evidence": evidence,
            "figure": round(figure, 4), "count": int(max(1, count)), "action": action,
            "expected_monthly_effect_usd": round(effect, 2), "effect_kind": effect_kind,
            "confidence": confidence, "would_change_our_mind": would_change,
            "fields": fields, "rows": rows[:ROWS]}


# ── the checks ──────────────────────────────────────────────────────────────────


def baseline(ctx: Context) -> dict:
    b = ctx.bundle
    out = {"spend": round(ctx.spend, 2), "ad_sales": round(ctx.sales, 2),
           "ad_orders": round(ctx.orders), "clicks": round(ctx.clicks),
           "acos": round(100 * ctx.acos, 2) if ctx.acos else None,
           "cvr": round(100 * ctx.cvr, 2) if ctx.cvr else None,
           "click_floor": ctx.click_floor, "days": ctx.days,
           "fields": sorted(set(D.SOURCE_FIELDS["campaign_daily"])
                            | set(D.SOURCE_FIELDS["account_daily"]))}
    if ctx.total_sales:
        out["total_sales"] = round(ctx.total_sales, 2)
        out["tacos"] = round(100 * ctx.spend / ctx.total_sales, 2)
    by_type: dict[str, dict] = defaultdict(lambda: {"spend": 0.0, "ad_sales": 0.0})
    for r in D.in_window(b.get("campaign_daily"), b):
        c = ctx.campaigns.get(r["campaign_id"])
        t = by_type[c["ad_type"] if c else "unknown"]
        t["spend"] += r["spend"]
        t["ad_sales"] += r["ad_sales"]
    out["by_ad_type"] = {k: {"spend": round(v["spend"], 2), "ad_sales": round(v["ad_sales"], 2),
                             "acos": round(100 * v["spend"] / v["ad_sales"], 2)
                             if v["ad_sales"] else None} for k, v in by_type.items()}
    # Trend: the window's second half against its first, in ACOS and spend.
    rows = D.in_window(b.get("campaign_daily") or b.get("account_daily"), b)
    days = sorted({r["date"] for r in rows})
    if len(days) >= 28:
        mid = days[len(days) // 2]
        first = D.totals([r for r in rows if r["date"] < mid], ("spend", "ad_sales"))
        second = D.totals([r for r in rows if r["date"] >= mid], ("spend", "ad_sales"))
        out["trend"] = {"first_half_acos": round(100 * _acos(first["spend"], first["ad_sales"]), 2)
                        if first["ad_sales"] else None,
                        "second_half_acos": round(100 * _acos(second["spend"], second["ad_sales"]), 2)
                        if second["ad_sales"] else None,
                        "spend_change": round(second["spend"] / first["spend"] - 1, 4)
                        if first["spend"] else None}
    return out


def search_term_waste(ctx: Context) -> list[dict]:
    rows = []
    for r in ctx.bundle.get("search_terms") or []:
        if r["clicks"] >= ctx.click_floor and r["ad_orders"] == 0 and r["spend"] > 0:
            rows.append({"query": r["query"], "campaign": ctx.name(r["campaign_id"]),
                         "ad_group_id": r["ad_group_id"], "clicks": int(r["clicks"]),
                         "spend": round(r["spend"], 2)})
    if not rows:
        return []
    rows.sort(key=lambda x: -x["spend"])
    spend = sum(x["spend"] for x in rows)
    clicks = sum(x["clicks"] for x in rows)
    monthly = spend * ctx.monthly
    return [_finding(
        "search_term_waste", 1,
        what=f"Negate {_n(len(rows), 'search term')} that {'has' if len(rows) == 1 else 'have'} had enough clicks and sold nothing.",
        evidence=(f"{_n(len(rows), 'search term')} spent {_money(spend)} on {clicks:,} clicks with no "
                  f"orders over {ctx.days} days. Each had at least {ctx.click_floor} clicks — "
                  f"{CLICKS_PER_ORDER_MULTIPLE}× the clicks one order takes at this account's "
                  f"{_pct(ctx.cvr)} conversion rate."),
        figure=spend, count=clicks,
        action="Add each as a negative exact in the ad group that bought it; review any that "
               "are launch or brand terms before negating.",
        effect=monthly, effect_kind="saving",
        confidence="high" if min(x["clicks"] for x in rows) >= 2 * ctx.click_floor else "medium",
        would_change="An order on any of these terms in the next 30 days at their click volume.",
        tables=("search_terms",), rows=rows)]


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def harvesting(ctx: Context) -> list[dict]:
    rows = _harvest_rows(ctx)
    if not rows:
        return []
    sales = sum(x["sales"] for x in rows)
    spend = sum(x["spend"] for x in rows)
    orders = sum(x["orders"] for x in rows)
    return [_finding(
        "harvesting", 1,
        what=f"Give {_n(len(rows), 'converting search term')} {'its' if len(rows) == 1 else 'their'} own exact keyword{'' if len(rows) == 1 else 's'}.",
        evidence=(f"{_n(len(rows), 'search term')} sold {_money(sales)} on {_n(orders, 'order')} at "
                  f"{_pct(spend / sales)} ACOS — at or under the account's {_pct(ctx.acos)} — "
                  "but are bought only through broad, phrase or automatic matching."),
        figure=sales, count=orders,
        action="Add each as an exact keyword at a bid near its current cost per click, and "
               "as a negative exact where it was found, so the two do not bid against each "
               "other.",
        effect=0.0, effect_kind="sales",
        confidence="medium",
        would_change="The exact keywords converting below these terms' current rate after "
                     "30 days.",
        tables=("search_terms", "targets"), rows=rows)]


def _harvest_rows(ctx: Context) -> list[dict]:
    exact = {_norm(t["text"]) for t in ctx.bundle.get("targets") or []
             if (t["match_type"] or "").lower() == "exact"
             and (t.get("state") or "enabled").lower() == "enabled"}
    # A query already matched by an exact keyword of the same words is bid on
    # exactly, whether or not the keyword list came with the pull.
    exact |= {_norm(r["query"]) for r in ctx.bundle.get("search_terms") or []
              if (r.get("match_type") or "").lower() == "exact"
              and _norm(r.get("matched")) == _norm(r["query"])}
    per_query: dict[str, dict] = defaultdict(lambda: {"spend": 0.0, "ad_sales": 0.0,
                                                      "ad_orders": 0.0, "clicks": 0.0})
    for r in ctx.bundle.get("search_terms") or []:
        q = _norm(r["query"])
        if not q or q in exact or re.fullmatch(r"b0[0-9a-z]{8}", q):
            continue  # already an exact keyword, or an ASIN (a product target's job)
        p = per_query[q]
        for k in ("spend", "ad_sales", "ad_orders", "clicks"):
            p[k] += r[k]
    rows = []
    for q, p in per_query.items():
        a = _acos(p["spend"], p["ad_sales"])
        if p["ad_orders"] >= 2 and a is not None and ctx.acos and a <= ctx.acos:
            rows.append({"query": q, "orders": int(p["ad_orders"]), "spend": round(p["spend"], 2),
                         "sales": round(p["ad_sales"], 2), "acos": round(100 * a, 2)})
    rows.sort(key=lambda x: -x["sales"])
    return rows


def duplicates(ctx: Context) -> list[dict]:
    """The same query bought by two or more campaigns: the weaker pays more."""
    per: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(
        lambda: {"spend": 0.0, "ad_sales": 0.0, "clicks": 0.0}))
    for r in ctx.bundle.get("search_terms") or []:
        cell = per[_norm(r["query"])][r["campaign_id"]]
        for k in ("spend", "ad_sales", "clicks"):
            cell[k] += r[k]
    rows = []
    for q, camps in per.items():
        judged = {c: v for c, v in camps.items() if v["clicks"] >= ctx.click_floor}
        if len(judged) < 2:
            continue
        ranked = sorted(judged.items(), key=lambda kv: (_acos(kv[1]["spend"], kv[1]["ad_sales"])
                                                        or float("inf")))
        best_acos = _acos(ranked[0][1]["spend"], ranked[0][1]["ad_sales"])
        if best_acos is None:
            continue
        for c, v in ranked[1:]:
            lost = max(0.0, v["spend"] - v["ad_sales"] * best_acos)
            if lost > 0:
                rows.append({"query": q, "weaker_campaign": ctx.name(c),
                             "best_campaign": ctx.name(ranked[0][0]),
                             "spend": round(v["spend"], 2), "excess": round(lost, 2)})
    if not rows:
        return []
    rows.sort(key=lambda x: -x["excess"])
    excess = sum(x["excess"] for x in rows)
    queries = len({x["query"] for x in rows})
    return [_finding(
        "structure", 1,
        what=(f"Give \u201c{rows[0]['query']}\u201d one campaign, not two." if queries == 1
              else f"Give {_n(queries, 'search term')} one campaign each."),
        evidence=(f"{_n(queries, 'search term is', 'search terms are')} bought by two or more campaigns, each with "
                  f"at least {ctx.click_floor} clicks. The weaker campaigns spent "
                  f"{_money(excess)} more than the best one would have for the same sales."),
        figure=excess, count=len(rows),
        action="Keep each term in the campaign that converts it best and negate it exact in "
               "the others.",
        effect=excess * ctx.monthly, effect_kind="saving", confidence="medium",
        would_change="The surviving campaign's ACOS on these terms rising above the weaker "
                     "one's once it carries all the traffic.",
        tables=("search_terms",), rows=rows)]


def _hot_rows(ctx: Context, items, label) -> list[dict]:
    rows = []
    for name, extra, v in items:
        a = _acos(v["spend"], v["ad_sales"])
        hot = a is None or (ctx.acos and a > HOT_MULTIPLE * ctx.acos)
        if v["clicks"] >= ctx.click_floor and hot:
            ex = ctx.excess(v["spend"], v["ad_sales"])
            if ex > 0:
                rows.append({label: name, **extra, "clicks": int(v["clicks"]),
                             "spend": round(v["spend"], 2), "acos": round(100 * a, 2) if a else None,
                             "excess": round(ex, 2)})
    rows.sort(key=lambda x: -x["excess"])
    return rows


def placements(ctx: Context) -> list[dict]:
    items = []
    off = {"spend": 0.0, "ad_sales": 0.0, "clicks": 0.0}
    for r in ctx.bundle.get("placements") or []:
        # Off-Amazon spend is its own finding (below): multipliers do not reach
        # it, so it is not counted twice under a fix that cannot touch it.
        if "off" in r["placement"].lower():
            for k in off:
                off[k] += r[k]
        else:
            items.append((r["placement"], {"campaign": ctx.name(r["campaign_id"])}, r))
    out = []
    rows = _hot_rows(ctx, items, "placement")
    if rows:
        excess = sum(x["excess"] for x in rows)
        out.append(_finding(
            "placements", 1,
            what="Lower the multipliers on placements running far hotter than the account.",
            evidence=(f"{_n(len(rows), 'campaign placement')} ran above {HOT_MULTIPLE}× the account's "
                      f"{_pct(ctx.acos)} ACOS with at least {ctx.click_floor} clicks each, "
                      f"spending {_money(excess)} beyond what the account's own ACOS would "
                      "have paid for their sales."),
            figure=excess, count=sum(x["clicks"] for x in rows),
            action="Cut the bid adjustment on each listed placement (to 0% where it has one); "
                   "leave the base bids alone.",
            effect=excess * ctx.monthly, effect_kind="saving", confidence="medium",
            would_change="Those placements' ACOS falling under the account's after the change "
                         "with no fall in their sales.",
            tables=("placements", "campaigns"), rows=rows))
    if off["clicks"] >= ctx.click_floor and ctx.acos:
        a = _acos(off["spend"], off["ad_sales"])
        ex = ctx.excess(off["spend"], off["ad_sales"])
        if ex > 0:
            out.append(_finding(
                "platform_changes", 1,
                what="Check the off-Amazon placements the account was enrolled in.",
                evidence=(f"Off-Amazon placements spent {_money(off['spend'])} on "
                          f"{int(off['clicks']):,} clicks at {_pct(a)} ACOS against the "
                          f"account's {_pct(ctx.acos)}. Placement bid adjustments do not apply "
                          "there."),
                figure=off["spend"], count=off["clicks"],
                action="Limit reach to Amazon on the campaigns carrying this spend, or keep it "
                       "and watch it as its own line.",
                effect=ex * ctx.monthly, effect_kind="saving", confidence="medium",
                would_change="Off-Amazon ACOS settling under the account's over the next 30 days.",
                tables=("placements",), rows=[]))
    return out


def bids(ctx: Context) -> list[dict]:
    items = [(t["text"], {"match_type": t["match_type"], "campaign": ctx.name(t.get("campaign_id"))},
              t) for t in ctx.bundle.get("targets") or []
             if not _auto_group(t["text"]) and (t.get("state") or "enabled").lower() == "enabled"]
    rows = _hot_rows(ctx, items, "target")
    if not rows:
        return []
    excess = sum(x["excess"] for x in rows)
    return [_finding(
        "bids", 1,
        what=f"Bid down {_n(len(rows), 'keyword or target', 'keywords and targets')} running far hotter than the account.",
        evidence=(f"{_n(len(rows), 'keyword or target', 'keywords and targets')} ran above {HOT_MULTIPLE}× the account's "
                  f"{_pct(ctx.acos)} ACOS with at least {ctx.click_floor} clicks each, spending "
                  f"{_money(excess)} beyond what the account's own ACOS would have paid for "
                  "their sales."),
        figure=excess, count=sum(x["clicks"] for x in rows),
        action="Lower each bid toward the account's ACOS (bid × account ACOS ÷ its ACOS), or "
               "pause those with no sales.",
        effect=excess * ctx.monthly, effect_kind="saving", confidence="medium",
        would_change="These targets holding their sales at the lower bids would mean the "
                     "saving is larger; losing most of them would mean the bids were the price "
                     "of their volume.",
        tables=("targets",), rows=rows)]


def _auto_group(text: str | None) -> str | None:
    return AUTO_GROUPS.get(_norm(text).replace(" ", "-").replace("_", "-")) or \
        AUTO_GROUPS.get(_norm(text).replace(" ", "").replace("-", ""))


def auto_targeting(ctx: Context) -> list[dict]:
    groups: dict[str, dict] = defaultdict(lambda: {"spend": 0.0, "ad_sales": 0.0, "clicks": 0.0})
    for t in ctx.bundle.get("targets") or []:
        g = _auto_group(t["text"])
        if g:
            for k in ("spend", "ad_sales", "clicks"):
                groups[g][k] += t[k]
    rows = _hot_rows(ctx, [(g, {}, v) for g, v in groups.items()], "group")
    if not rows:
        return []
    excess = sum(x["excess"] for x in rows)
    return [_finding(
        "auto_targeting", 1,
        what="Bid the automatic targeting groups for what each returns.",
        evidence=(f"In automatic campaigns, {', '.join(x['group'] for x in rows)} ran above "
                  f"{HOT_MULTIPLE}× the account's {_pct(ctx.acos)} ACOS, spending "
                  f"{_money(excess)} beyond what the account's own ACOS would have paid."),
        figure=excess, count=sum(x["clicks"] for x in rows),
        action="Set each group's bid separately, lowering the listed ones toward the "
               "account's ACOS.",
        effect=excess * ctx.monthly, effect_kind="saving", confidence="medium",
        would_change="The groups' sales falling more than their spend after the change.",
        tables=("targets",), rows=rows)]


def _serving(ctx: Context) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for cid, rows in D.by(D.in_window(ctx.bundle.get("campaign_daily"), ctx.bundle),
                          "campaign_id").items():
        t = D.totals(rows, ("spend", "ad_sales", "ad_orders", "clicks"))
        hours = [r["serving_hours"] for r in rows if r.get("serving_hours") is not None]
        share = [r["tos_impression_share"] for r in rows
                 if r.get("tos_impression_share") is not None]
        out[cid] = {**t, "rows": rows, "hours": sum(hours) / len(hours) if hours else None,
                    "share": sum(share) / len(share) if share else None}
    return out


def budgets(ctx: Context) -> list[dict]:
    rows = []
    for cid, v in _serving(ctx).items():
        a = _acos(v["spend"], v["ad_sales"])
        if v["hours"] is None or v["hours"] >= CAPPED_HOURS or a is None or not ctx.acos:
            continue
        if a <= ctx.acos and v["clicks"] >= ctx.click_floor:
            missed = v["spend"] * (min(24 / max(v["hours"], 0.5), 3.0) - 1)
            rows.append({"campaign": ctx.name(cid), "serving_hours": round(v["hours"], 1),
                         "acos": round(100 * a, 2), "spend": round(v["spend"], 2),
                         "missed_spend": round(missed, 2)})
    if not rows:
        return []
    rows.sort(key=lambda x: -x["missed_spend"])
    missed = sum(x["missed_spend"] for x in rows)
    return [_finding(
        "budgets", 1,
        what=f"Fund {_n(len(rows), 'campaign that runs', 'campaigns that run')} out of budget while beating the account.",
        evidence=(f"{_n(len(rows), 'campaign')} at or under the account's {_pct(ctx.acos)} ACOS served "
                  f"on average fewer than {CAPPED_HOURS:.0f} hours a day. Kept in budget all day "
                  f"they would have spent about {_money(missed)} more over {ctx.days} days."),
        figure=missed, count=len(rows),
        action="Raise each daily budget so it lasts the day — the scale tiers below price "
               "what that buys.",
        effect=0.0, effect_kind="sales", confidence="medium",
        would_change="Their ACOS rising above the account's once they serve all day.",
        tables=("campaign_daily", "campaigns"), rows=rows)]


def retail_readiness(ctx: Context) -> list[dict]:
    rows = []
    for p in ctx.bundle.get("products") or []:
        spend = p.get("spend") or 0
        if spend <= 0:
            continue
        reasons = []
        if p.get("buy_box_pct") is not None and p["buy_box_pct"] < 90:
            reasons.append(f"buy box {p['buy_box_pct']:.0f}%")
        if p.get("stock") is not None and p["stock"] <= 0:
            reasons.append("out of stock")
        if p.get("rating") is not None and p["rating"] < 3.8:
            reasons.append(f"rated {p['rating']:.1f}")
        if reasons:
            rows.append({"asin": p["asin"], "title": (p.get("title") or "")[:80],
                         "spend": round(spend, 2), "why": ", ".join(reasons),
                         "_reasons": reasons})
    if not rows:
        return []
    rows.sort(key=lambda x: -x["spend"])
    spend = sum(x["spend"] for x in rows)
    if len(rows) == 1:
        r = rows[0]
        said = (f"{r['title'] or r['asin']} ({r['asin']}) took {_money(spend)} of ad spend over "
                f"{ctx.days} days with {r['why']}.")
    else:
        tally = defaultdict(int)
        for r in rows:
            for why in r["_reasons"]:
                tally[why.split(" ")[0] if why.startswith(("buy", "rated")) else why] += 1
        parts = [f"{n} {('lost the buy box often' if k == 'buy' else 'rated under 3.8' if k == 'rated' else k)}"
                 for k, n in tally.items()]
        said = (f"{_n(len(rows), 'advertised product')} took {_money(spend)} of ad spend over "
                f"{ctx.days} days: {', '.join(parts)}.")
    for r in rows:
        r.pop("_reasons", None)
    return [_finding(
        "retail_readiness", 1,
        what=(f"Fix the listing for {rows[0]['title'] or rows[0]['asin']} before spending more "
              "on its ads." if len(rows) == 1 else
              f"Fix the listing before the ads on {_n(len(rows), 'advertised product')}."),
        evidence=(f"{said} Sponsored Products stop serving whenever the featured offer is lost "
                  "or stock runs out, and a weak listing caps what every click can convert."),
        figure=spend, count=len(rows),
        action="Restore the featured offer and stock, or address the rating, before adding "
               "spend to these products.",
        effect=0.0, effect_kind="risk", confidence="high",
        would_change="Buy-box share above 95% and stock in place on all of them.",
        tables=("products",), rows=rows)]


def branded(ctx: Context) -> list[dict]:
    brands = {_norm(c["brand_name"]) for c in ctx.campaigns.values() if c.get("brand_name")}
    brands = {b for b in brands if len(b) >= 3}
    if not brands:
        return []
    t = {"spend": 0.0, "ad_sales": 0.0, "clicks": 0.0}
    for r in ctx.bundle.get("search_terms") or []:
        if any(b in _norm(r["query"]) for b in brands):
            for k in t:
                t[k] += r[k]
    if not ctx.spend or t["spend"] / ctx.spend < 0.10:
        return []
    share = t["spend"] / ctx.spend
    a = _acos(t["spend"], t["ad_sales"])
    return [_finding(
        "branded", 1,
        what="Test how much of the branded spend is buying sales the brand would get anyway.",
        evidence=(f"{share:.0%} of ad spend ({_money(t['spend'])}) went to searches for the "
                  f"brand's own name, at {_pct(a)} ACOS on {int(t['clicks']):,} clicks. Branded "
                  "searches convert well and are usually the least incremental spend."),
        figure=t["spend"], count=t["clicks"],
        action="Run a holdout: pause branded Sponsored Products on a few brand terms for two "
               "weeks where no competitor bids, and compare total sales.",
        effect=0.0, effect_kind="test", confidence="low",
        would_change="Total sales falling by more than the paused ad sales during the test.",
        tables=("search_terms", "campaigns"), rows=[])]


CHECKS = (search_term_waste, harvesting, duplicates, placements, bids, auto_targeting,
          budgets, retail_readiness, branded)


def run(bundle: dict) -> dict:
    """Every check over a validated bundle: the baseline, and the findings by lever."""
    ctx = Context(bundle)
    findings: dict[str, list[dict]] = defaultdict(list)
    for check in CHECKS:
        for f in check(ctx):
            key = f["platform_id"].split("-")[1]
            # A finding worth less than the owner's attention is not raised —
            # unless its value is not counted in dollars (a risk, a test, a
            # growth lever the forecast prices).
            if f["effect_kind"] == "saving" and f["expected_monthly_effect_usd"] < MIN_EFFECT_USD:
                continue
            findings[key].append(f)
    return {"baseline": baseline(ctx), "findings": dict(findings),
            "looked_at": {
                "search_term_waste": f"{len(bundle.get('search_terms') or [])} search-term rows "
                                     f"at a {ctx.click_floor}-click floor",
                "harvesting": "every converting search term not yet an exact keyword",
                "structure": "every search term bought by more than one campaign",
                "placements": f"{len(bundle.get('placements') or [])} campaign placements",
                "bids": f"{len(bundle.get('targets') or [])} keywords and targets",
                "auto_targeting": "the four automatic targeting groups",
                "budgets": "serving hours on every campaign",
                "retail_readiness": f"{len(bundle.get('products') or [])} products",
                "branded": "search terms containing the brand's name",
            },
            "yardsticks": {"account_acos": ctx.acos, "account_cvr": ctx.cvr,
                           "click_floor": ctx.click_floor, "monthly_factor": ctx.monthly}}


def pools(bundle: dict) -> list[dict]:
    """Where the account could take more money, from the same numbers the checks
    used, each with its daily history for the forecast to fit."""
    ctx = Context(bundle)
    if not ctx.acos:
        return []
    w = bundle["window"]
    out = []
    for cid, v in _serving(ctx).items():
        a = _acos(v["spend"], v["ad_sales"])
        if a is None or v["clicks"] < ctx.click_floor or v["ad_sales"] <= 0:
            continue
        history = [[s, q] for _, s, q in D.daily_series(v["rows"], w["start"], w["end"])]
        c = ctx.campaigns.get(cid) or {}
        base = {"traffic": "non_branded", "window_spend": round(v["spend"], 2),
                "window_sales": round(v["ad_sales"], 2), "history": history,
                "fields": sorted(set(D.SOURCE_FIELDS["campaign_daily"]))}
        if v["hours"] is not None and v["hours"] < CAPPED_HOURS and a <= ctx.acos:
            out.append({**base, "name": f"{ctx.name(cid)} (out of budget)",
                        "kind": "budget_capped", "serving_hours": round(v["hours"], 2),
                        "evidence": f"{_pct(a)} ACOS, serving {v['hours']:.1f} hours a day."})
        elif (v["share"] is not None and v["share"] < LOW_SHARE
              and a <= WINNER_MULTIPLE * ctx.acos and c.get("ad_type", "").lower()
              .startswith("sponsoredproducts")):
            out.append({**base, "name": f"{ctx.name(cid)} (low top-of-search share)",
                        "kind": "impression_share", "impression_share": round(v["share"], 4),
                        "evidence": f"{_pct(a)} ACOS with {v['share']:.0%} top-of-search "
                                    "impression share."})
    harvest = _harvest_rows(ctx)
    spend = sum(x["spend"] for x in harvest)
    sales = sum(x["sales"] for x in harvest)
    if spend > 0 and sales > 0:
        out.append({"name": "Converting search terms moved to exact", "kind": "new_targets",
                    "traffic": "non_branded", "window_spend": round(spend, 2),
                    "window_sales": round(sales, 2),
                    "fields": sorted(set(D.SOURCE_FIELDS["search_terms"])),
                    "evidence": f"{len(harvest)} harvest candidates: {_money(sales)} in sales "
                                f"at {_pct(spend / sales)} ACOS."})
    return out
