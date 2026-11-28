"""ppckit — the arithmetic every seat of an Amazon PPC audit computes the same way.

This file is the whole kit. It is pure standard-library Python (3.11+) so the
platform can hand it to a sandbox as one file (the `ppc_kit` tool writes it to
`ppckit.py` in the workspace) and import it itself — the reviewer re-runs a
script against the same functions the analyst used, and the platform checks
the same formulas it tested.

It decides nothing about what is a finding. It answers questions an analyst
asks: is this zero-order streak luck? did conversion really change between two
periods? what does a customer earn after the first order? what bid hits this
cost per order? how do sales respond to spend here, and how well would that
have predicted this account's own past?

    import ppckit as k
    k.stats.zero_order_probability(clicks=30, cvr=0.10)      # 0.042
    k.economics.first_order_break_even(price=30, referral_fee=4.5, fulfilment_fee=5.2)
    k.bids.target_bid(order_value=34, cvr=0.15, target_acos=0.28)
    k.forecast.allocate(pools, baseline, window_days=60)
    k.forecast.backtest(campaign_daily)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import SimpleNamespace

VERSION = "2026.11.1"
#: The tiers of extra monthly spend an audit prices, in dollars.
SCALE_TIERS = (10_000, 25_000, 50_000, 100_000)


# ── statistics ──────────────────────────────────────────────────────────────────

def zero_order_probability(clicks: float, cvr: float) -> float:
    """Chance of `clicks` clicks and no order if the true conversion rate is `cvr`."""
    if clicks <= 0:
        return 1.0
    cvr = min(max(float(cvr), 0.0), 1.0)
    return (1.0 - cvr) ** float(clicks)


def clicks_needed(cvr: float, confidence: float = 0.95) -> int:
    """Clicks with no order before 'it converts at `cvr`' is unlikely at `confidence`."""
    cvr = min(max(float(cvr), 1e-9), 0.999999)
    return int(math.ceil(math.log(1 - confidence) / math.log(1 - cvr)))


def shrunk_rate(successes: float, trials: float, prior_rate: float,
                prior_strength: float = 20.0) -> float:
    """A small sample's rate pulled toward its group's: a beta prior worth
    `prior_strength` trials at `prior_rate`."""
    a = prior_rate * prior_strength + successes
    return a / (prior_strength + trials) if (prior_strength + trials) > 0 else prior_rate


def _betacf(a: float, b: float, x: float) -> float:
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1.0 + aa / c if abs(c) > 1e-300 else 1e300
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1.0 + aa / c if abs(c) > 1e-300 else 1e300
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-12:
            break
    return h


def beta_cdf(x: float, a: float, b: float) -> float:
    """Regularised incomplete beta I_x(a, b)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    ln = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
          + a * math.log(x) + b * math.log(1 - x))
    front = math.exp(ln)
    if x < (a + 1) / (a + b + 2):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1 - x) / b


def _beta_quantile(p: float, a: float, b: float) -> float:
    lo, hi = 0.0, 1.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if beta_cdf(mid, a, b) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def beta_interval(successes: float, trials: float, prior_rate: float | None = None,
                  prior_strength: float = 0.0, level: float = 0.9) -> tuple[float, float]:
    """Credible interval for a rate. With no prior, a flat Beta(1, 1)."""
    if prior_rate is None or prior_strength <= 0:
        a0, b0 = 1.0, 1.0
    else:
        a0, b0 = prior_rate * prior_strength, (1 - prior_rate) * prior_strength
    a, b = a0 + successes, b0 + max(trials - successes, 0)
    tail = (1 - level) / 2
    return _beta_quantile(tail, a, b), _beta_quantile(1 - tail, a, b)


def prob_rate_below(successes: float, trials: float, threshold: float,
                    prior_rate: float | None = None, prior_strength: float = 0.0) -> float:
    """Posterior probability that the true rate is below `threshold` — e.g. that a
    keyword's conversion rate is too low for its bid to meet its cost-per-order bar."""
    if prior_rate is None or prior_strength <= 0:
        a0, b0 = 1.0, 1.0
    else:
        a0, b0 = prior_rate * prior_strength, (1 - prior_rate) * prior_strength
    return beta_cdf(threshold, a0 + successes, b0 + max(trials - successes, 0))


def _normal_sf(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2))


def rate_change(successes_before: float, trials_before: float,
                successes_after: float, trials_after: float) -> dict:
    """Did a rate (orders per click, clicks per impression) change between two
    periods by more than chance? Two-proportion test, two-sided."""
    n1, n2 = float(trials_before), float(trials_after)
    if n1 <= 0 or n2 <= 0:
        return {"rate_before": None, "rate_after": None, "p_value": None,
                "significant": False, "note": "a period has no trials"}
    p1, p2 = successes_before / n1, successes_after / n2
    pooled = (successes_before + successes_after) / (n1 + n2)
    se = math.sqrt(max(pooled * (1 - pooled) * (1 / n1 + 1 / n2), 1e-18))
    z = (p2 - p1) / se
    p_value = min(1.0, 2 * _normal_sf(abs(z)))
    return {"rate_before": p1, "rate_after": p2,
            "relative_change": (p2 / p1 - 1) if p1 > 0 else None,
            "z": z, "p_value": p_value, "significant": p_value < 0.05,
            "note": ("small counts: treat as indicative"
                     if min(successes_before, successes_after) < 5 else "")}


def one_in(probability: float) -> str:
    """A probability in words a manager reads: 'about 1 in 20'."""
    if probability is None:
        return "unknown"
    if probability <= 0.001:
        return "under 1 in 1,000"
    if probability >= 0.5:
        return f"{probability:.0%}"
    return f"about 1 in {round(1 / probability):,}"


stats = SimpleNamespace(
    zero_order_probability=zero_order_probability, clicks_needed=clicks_needed,
    shrunk_rate=shrunk_rate, beta_cdf=beta_cdf, beta_interval=beta_interval,
    prob_rate_below=prob_rate_below, rate_change=rate_change, one_in=one_in)


# ── economics ───────────────────────────────────────────────────────────────────

COST_SHARES = (0.15, 0.25, 0.35)


def first_order_break_even(price: float, referral_fee: float, fulfilment_fee: float,
                           product_cost: float | None = None, storage: float = 0.0,
                           return_rate: float = 0.0,
                           return_cost_per_return: float | None = None) -> dict:
    """What one first order earns after Amazon, and the ACoS that spends all of it.

    Product cost is the owner's number, never Amazon's. Without it, the answer
    is a table across product costs at 15%, 25% and 35% of price."""
    price = float(price)
    lost_to_returns = float(return_rate) * (
        float(return_cost_per_return) if return_cost_per_return is not None else price * 0.3)
    after_amazon = price - referral_fee - fulfilment_fee - storage - lost_to_returns

    def at(cost: float) -> dict:
        contribution = after_amazon - cost
        return {"product_cost": round(cost, 2), "contribution": round(contribution, 2),
                "break_even_acos": round(contribution / price, 4) if price else None}

    out = {"price": price, "after_amazon": round(after_amazon, 2),
           "fee_margin": round(after_amazon / price, 4) if price else None}
    if product_cost is not None:
        out.update(at(float(product_cost)))
        out["basis"] = "owner-stated product cost"
    else:
        out["by_cost_share"] = [{"cost_share": s, **at(price * s)} for s in COST_SHARES]
        out["basis"] = "product cost unknown: break-even shown across cost shares"
    return out


def lifetime_value(contribution_first: float, contribution_repeat: float, *,
                   repeat_rate: float | None = None, monthly_churn: float | None = None,
                   orders_per_month: float = 1.0, horizon_months: int = 12) -> dict:
    """What a new customer earns over `horizon_months`.

    Two models, said in the answer: a repeat-rate model (each order is followed
    by another with probability `repeat_rate`) or a subscription survival model
    (a subscriber orders `orders_per_month` times a month and leaves with
    probability `monthly_churn` each month)."""
    if monthly_churn is not None:
        c = min(max(float(monthly_churn), 1e-6), 1.0)
        survival = sum((1 - c) ** m for m in range(1, horizon_months))
        repeats = survival * orders_per_month
        model = (f"subscription: {c:.1%} monthly churn, {orders_per_month:g} order(s) a "
                 f"month, {horizon_months} months")
    elif repeat_rate is not None:
        r = min(max(float(repeat_rate), 0.0), 0.99)
        cap = max(int(horizon_months * orders_per_month), 1)
        repeats = sum(r ** i for i in range(1, cap + 1))
        model = f"repeat rate {r:.0%} per order, at most {cap} repeat orders in {horizon_months} months"
    else:
        repeats, model = 0.0, "no repeat data: first order only"
    lifetime = contribution_first + repeats * contribution_repeat
    return {"expected_repeat_orders": round(repeats, 3),
            "lifetime_contribution": round(lifetime, 2), "model": model}


def ltv_acos_ceiling(lifetime_contribution: float, first_order_price: float) -> float:
    """The first-order ACoS at which acquiring a customer breaks even over its life."""
    return lifetime_contribution / first_order_price if first_order_price else 0.0


def payback_months(acquisition_cost: float, contribution_first: float,
                   monthly_contribution_after: float) -> float | None:
    """Months until a customer's contribution repays what it cost to acquire."""
    remaining = acquisition_cost - contribution_first
    if remaining <= 0:
        return 0.0
    if monthly_contribution_after <= 0:
        return None
    return remaining / monthly_contribution_after


def cost_per_new_customer(spend: float, new_to_brand_orders: float) -> float | None:
    return spend / new_to_brand_orders if new_to_brand_orders else None


economics = SimpleNamespace(
    first_order_break_even=first_order_break_even, lifetime_value=lifetime_value,
    ltv_acos_ceiling=ltv_acos_ceiling, payback_months=payback_months,
    cost_per_new_customer=cost_per_new_customer)


# ── bids and budgets ────────────────────────────────────────────────────────────

def target_bid(order_value: float, cvr: float, target_acos: float) -> float:
    """The highest CPC that still meets `target_acos`: order value × cvr × target."""
    return round(float(order_value) * float(cvr) * float(target_acos), 2)


def budget_for_full_day(daily_budget: float, serving_hours: float, cap: float = 3.0) -> float:
    """The daily budget that would have stayed in budget all day, at most `cap`×."""
    hours = max(float(serving_hours), 0.5)
    return round(float(daily_budget) * min(24.0 / hours, cap), 2)


def placement_multiplier(base_bid: float, wanted_bid: float) -> int:
    """The placement adjustment (0-900%) that lifts `base_bid` to `wanted_bid`."""
    if base_bid <= 0:
        return 0
    return int(min(max(round((wanted_bid / base_bid - 1) * 100), 0), 900))


bids = SimpleNamespace(target_bid=target_bid, budget_for_full_day=budget_for_full_day,
                       placement_multiplier=placement_multiplier)


# ── forecast ────────────────────────────────────────────────────────────────────
#
# A response curve with diminishing returns, sales = k · spend^b (0 < b ≤ 1),
# anchored on each pool's own spend and sales today. b is fitted by least
# squares on log sales against log spend when there are ≥ 21 days with real
# movement in spend, shrunk toward a prior by w = n / (n + 30); otherwise the
# prior is used and said. Priors are educated assumptions, overridable per pool:
#   impression_share 0.6 (auction maths: click elasticity 1.5 → spend^0.6),
#   budget_capped 0.9 (same auctions later in the day), new_targets 0.7,
#   share_of_search 0.6, acquisition 0.6.
# Ceilings come from the evidence: full-day serving (≤ 3×), 85% impression share
# (≤ 4×), 1.5× a search term's sales, +10 points of market share (≤ 3×), and any
# lower ceiling the analyst states (stock, search volume). Allocation is
# equimarginal: each dollar goes where the next is cheapest. TACoS counts only
# the incremental share of attributed sales by traffic.

PRIORS = {"impression_share": 0.6, "budget_capped": 0.9, "new_targets": 0.7,
          "share_of_search": 0.6, "acquisition": 0.6}
KINDS = tuple(PRIORS)
INCREMENTALITY = {"branded": 0.15, "non_branded": 0.5, "competitor": 0.6}
MIN_DAYS = 21
MIN_LOG_SPEND_SD = 0.15
SHRINK_DAYS = 30
IMPRESSION_SHARE_CAP = 0.85
MAX_SCALE = {"impression_share": 4.0, "budget_capped": 3.0, "new_targets": 1.5,
             "share_of_search": 3.0, "acquisition": 3.0}
SHARE_GAIN = 0.10


class PoolProblem(ValueError):
    pass


@dataclass
class Curve:
    name: str
    kind: str
    traffic: str
    s0: float
    q0: float
    b: float
    k: float
    s_max: float
    basis: str
    incrementality: float = 0.5

    def sales(self, s: float) -> float:
        return self.k * s ** self.b

    def spend_at_marginal(self, lam: float) -> float:
        """Spend where the next dollar returns 1/lam in sales (marginal ACoS lam)."""
        if self.b >= 0.999:
            return self.s_max if lam >= 1 / self.k else self.s0
        base = lam * self.k * self.b
        if base <= 0:
            return self.s0
        exponent = math.log(base) / (1 - self.b)
        if exponent > math.log(self.s_max):
            return self.s_max
        return min(max(math.exp(exponent), self.s0), self.s_max)


def fit_exponent(history, prior: float) -> tuple[float, str]:
    """b from daily [spend, sales] history, shrunk to the prior, and why."""
    points = [(float(s), float(q)) for s, q in (history or [])
              if isinstance(s, (int, float)) and isinstance(q, (int, float))
              and s > 0 and q > 0]
    n = len(points)
    if n < MIN_DAYS:
        return prior, f"assumed b={prior} ({n} usable days; {MIN_DAYS} needed to fit)"
    xs = [math.log(s) for s, _ in points]
    ys = [math.log(q) for _, q in points]
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sd = math.sqrt(sxx / n)
    if sd < MIN_LOG_SPEND_SD:
        return prior, (f"assumed b={prior} (spend barely moved over {n} days, so the "
                       "history cannot show how sales respond to it)")
    fitted = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sxx
    fitted = min(max(fitted, 0.1), 1.0)
    w = n / (n + SHRINK_DAYS)
    b = round(w * fitted + (1 - w) * prior, 3)
    return b, (f"fitted b={fitted:.2f} on {n} days, weighted {w:.0%} against the "
               f"prior {prior} → b={b}")


def curve(pool: dict, monthly: float, account_aov: float | None) -> Curve:
    """One pool's monthly response curve, with its ceiling."""
    kind = pool["kind"]
    if kind not in PRIORS:
        raise PoolProblem(f"pool {pool.get('name')!r}: kind must be one of {', '.join(KINDS)}")
    s0 = float(pool["window_spend"]) * monthly
    q0 = float(pool["window_sales"]) * monthly
    if s0 <= 0 or q0 <= 0:
        raise PoolProblem(f"pool {pool['name']!r} needs window_spend and window_sales "
                          "above zero: a curve is anchored on what the pool does today")
    b, basis = fit_exponent(pool.get("history"), float(pool.get("prior_b") or PRIORS[kind]))
    k = q0 / s0 ** b
    q_max = None
    s_max = None
    if kind == "budget_capped":
        hours = float(pool["serving_hours"])
        s_max = s0 * min(24 / max(hours, 0.5), MAX_SCALE[kind])
    elif kind == "impression_share" or (kind == "new_targets" and pool.get("impression_share")):
        share = float(pool["impression_share"])
        q_max = q0 * min(IMPRESSION_SHARE_CAP / max(share, 0.01), MAX_SCALE["impression_share"])
    elif kind in ("new_targets", "acquisition"):
        q_max = q0 * MAX_SCALE[kind]
    else:  # share_of_search
        market = float(pool["market_purchases"])
        share = float(pool["purchase_share"])
        aov = float(pool.get("order_value") or account_aov or 0)
        if aov <= 0:
            raise PoolProblem(f"pool {pool['name']!r} needs order_value (or a baseline "
                              "with orders) to turn market purchases into sales")
        target = min(share + SHARE_GAIN, share * 2)
        q_max = min(q0 + max(0.0, (target - share) * market * monthly * aov),
                    q0 * MAX_SCALE[kind])
    if pool.get("ceiling_sales_usd") is not None:
        lowered = float(pool["ceiling_sales_usd"]) * monthly
        q_max = lowered if q_max is None else min(q_max, lowered)
        if s_max is not None:
            s_max = min(s_max, (lowered / k) ** (1 / b))
    if s_max is None:
        s_max = (max(q_max, q0) / k) ** (1 / b)
    traffic = pool.get("traffic") or "non_branded"
    inc = float(pool["incrementality"]) if pool.get("incrementality") is not None \
        else INCREMENTALITY.get(traffic, 0.5)
    return Curve(pool["name"], kind, traffic, s0, q0, b, k, max(s_max, s0), basis, inc)


def _state(curves: list[Curve], lam: float) -> tuple[float, float, float, list[tuple]]:
    extra = d_sales = d_incremental = 0.0
    lines = []
    for c in curves:
        s = c.spend_at_marginal(lam)
        ds = c.sales(s) - c.q0
        extra += s - c.s0
        d_sales += ds
        d_incremental += ds * c.incrementality
        lines.append((c, s - c.s0, ds))
    return extra, d_sales, d_incremental, lines


def _search(ok, hi: float = 50.0) -> float:
    lo = 0.0
    if ok(hi):
        return hi
    for _ in range(80):
        mid = (lo + hi) / 2
        if ok(mid):
            lo = mid
        else:
            hi = mid
    return lo


def allocate(pools: list[dict], baseline: dict, window_days: int,
             tiers: tuple = SCALE_TIERS, band: float | None = None) -> dict:
    """Each tier answered at today's ACoS and, separately, today's TACoS.

    `baseline` is the account for the window: spend, ad_sales, total_sales
    (optional), ad_orders (optional). `band` (from `backtest`) puts a low/high
    range on every tier's sales."""
    monthly = 30.0 / window_days
    spend = float(baseline["spend"]) * monthly
    ad_sales = float(baseline["ad_sales"]) * monthly
    total = float(baseline["total_sales"]) * monthly if baseline.get("total_sales") else None
    orders = baseline.get("ad_orders")
    aov = (float(baseline["ad_sales"]) / float(orders)) if orders else None
    curves = [curve(p, monthly, aov) for p in pools]
    acos_now = spend / ad_sales
    tacos_now = spend / total if total else None

    def answer(tier: float, rule: str) -> dict:
        def ok(lam):
            extra, d_sales, d_inc, _ = _state(curves, lam)
            if extra > tier + 0.5:
                return False
            if extra <= 0:
                return True
            if rule == "acos":
                return (spend + extra) / (ad_sales + d_sales) <= acos_now + 1e-9
            return (spend + extra) / (total + d_inc) <= tacos_now + 1e-9
        lam = _search(ok)
        extra, d_sales, d_inc, lines = _state(curves, lam)
        out = {
            "absorbable_usd": round(extra, 2),
            "expected_sales_usd": round(d_sales, 2),
            "expected_acos": round(100 * (spend + extra) / (ad_sales + d_sales), 2),
            "expected_tacos": (round(100 * (spend + extra) / (total + d_inc), 2)
                               if total else None),
            "incremental_acos": round(100 * extra / d_sales, 2) if d_sales > 0 else None,
            "allocation": [{"pool": c.name, "usd": round(x, 2),
                            "expected_sales_usd": round(ds, 2),
                            "expected_acos": round(100 * x / ds, 2) if ds > 0 else None}
                           for c, x, ds in lines if x > 0.5],
        }
        if band is not None:
            out["sales_low"] = round(d_sales * (1 - band), 2)
            out["sales_high"] = round(d_sales * (1 + band), 2)
        return out

    answered = []
    for usd in tiers:
        at_acos = answer(usd, "acos")
        at_tacos = answer(usd, "tacos") if tacos_now is not None else None
        answered.append({"monthly_usd": usd, "at_acos": at_acos, "at_tacos": at_tacos,
                         "note": _note(usd, at_acos, curves)})
    return {
        "monthly_from_window": round(monthly, 4),
        "today": {"spend": round(spend, 2), "ad_sales": round(ad_sales, 2),
                  "acos": round(100 * acos_now, 2),
                  "tacos": round(100 * tacos_now, 2) if tacos_now else None},
        "tiers": answered,
        "assumptions": [{"pool": c.name, "kind": c.kind, "traffic": c.traffic,
                         "b": c.b, "basis": c.basis, "incrementality": c.incrementality,
                         "spend_now": round(c.s0, 2), "ceiling_spend": round(c.s_max, 2)}
                        for c in curves],
    }


def _note(usd: float, at_acos: dict, curves: list[Curve]) -> str | None:
    fits = at_acos["absorbable_usd"]
    if fits + 1 >= usd:
        return None
    room = sum(c.s_max - c.s0 for c in curves)
    if fits + 1 >= room:
        return (f"The evidence shows room for about ${room:,.0f} a month in total; the rest "
                "of this tier has nowhere proven to go.")
    return (f"About ${fits:,.0f} fits at today's ACoS. Past that, the next dollars cost "
            "more than today's ACoS returns.")


# The forecast's own test: fit each campaign on the first two thirds of its
# days, predict the last third from the spend it actually had, and compare with
# "the same ROAS as before". The 80th-percentile miss among campaigns whose
# spend moved ≥ 15% becomes the range on every tier (≥ 5 such campaigns), else
# the stated default.
BACKTEST_MIN_DAYS = 42
BACKTEST_MOVED = 0.15
BACKTEST_MIN_TESTED = 5
DEFAULT_BAND = 0.35
BACKTEST_PRIOR_B = 0.6


def _weighted(errors: list[tuple[float, float]]) -> float | None:
    total = sum(w for _, w in errors)
    return sum(abs(e) * w for e, w in errors) / total if total else None


def _p80(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(abs(v) for v in values)
    return ordered[min(len(ordered) - 1, int(0.8 * len(ordered)))]


def backtest(campaign_daily: list[dict] | None) -> dict:
    """`campaign_daily`: rows with campaign_id, date, spend, ad_sales."""
    per: dict[str, list[dict]] = {}
    for r in campaign_daily or []:
        per.setdefault(r["campaign_id"], []).append(r)
    model_err, naive_err, moved_model, moved_naive, tested = [], [], [], [], 0
    for rows in per.values():
        rows = sorted((r for r in rows if r["spend"] > 0), key=lambda r: r["date"])
        if len(rows) < BACKTEST_MIN_DAYS:
            continue
        cut = (2 * len(rows)) // 3
        train, test = rows[:cut], rows[cut:]
        q_train = sum(r["ad_sales"] for r in train)
        s_train = sum(r["spend"] for r in train)
        q_test = sum(r["ad_sales"] for r in test)
        if q_train <= 0 or q_test <= 0:
            continue
        b, _ = fit_exponent([[r["spend"], r["ad_sales"]] for r in train], BACKTEST_PRIOR_B)
        k = q_train / sum(r["spend"] ** b for r in train)
        predicted = sum(k * r["spend"] ** b for r in test)
        naive = sum(r["spend"] for r in test) * q_train / s_train
        weight = sum(r["spend"] for r in test)
        e_model, e_naive = predicted / q_test - 1, naive / q_test - 1
        model_err.append((e_model, weight))
        naive_err.append((e_naive, weight))
        tested += 1
        if abs((weight / len(test)) / (s_train / len(train)) - 1) >= BACKTEST_MOVED:
            moved_model.append((e_model, weight))
            moved_naive.append((e_naive, weight))
    measured = len(moved_model) >= BACKTEST_MIN_TESTED
    band = _p80([e for e, _ in moved_model]) if measured else None
    bias_total = sum(w for _, w in moved_model)
    return {
        "campaigns_tested": tested,
        "campaigns_with_spend_moves": len(moved_model),
        "model_error": _weighted(moved_model) if moved_model else _weighted(model_err),
        "naive_error": _weighted(moved_naive) if moved_naive else _weighted(naive_err),
        "model_bias": (sum(e * w for e, w in moved_model) / bias_total) if bias_total else None,
        "band": round(band, 4) if band is not None else DEFAULT_BAND,
        "band_measured": measured,
        "method": (f"fit on the first two thirds of each campaign's days ({BACKTEST_MIN_DAYS}+ "
                   "days with spend), predict the last third from its actual spend; errors "
                   f"weighted by spend, among campaigns whose daily spend moved "
                   f"{BACKTEST_MOVED:.0%} or more"),
    }


forecast = SimpleNamespace(
    PRIORS=PRIORS, INCREMENTALITY=INCREMENTALITY, fit_exponent=fit_exponent, curve=curve,
    allocate=allocate, backtest=backtest, SCALE_TIERS=SCALE_TIERS)
