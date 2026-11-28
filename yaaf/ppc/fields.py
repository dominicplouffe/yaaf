"""Which data an Amazon PPC audit may rest on: Amazon's, or computed from it.

The chairman's rule: a field a platform lets somebody type in — a target ACOS,
a max ACOS, a tag, a bid preview, a cost of goods — is never evidence. Nobody
outside that platform can say what it was set for, by whom, or whether it was
ever true, so an audit built on it inherits a guess it cannot see. What counts
is what Amazon recorded (impressions, clicks, spend, attributed sales, the
search terms, the placements, the buy box, the stock) and what is computed
from those records alone (serving hours from budget events, organic sales as
total less ad sales, TACOS).

So this is an allowlist, and it fails closed. A field it does not recognise is
refused, with the sentence that says how to add it: by naming here where in
Amazon's data it comes from. A short list of fields known to be entered by a
person is kept only so the refusal can say so plainly.
"""

from __future__ import annotations

import re

#: Amazon's own records — Ads API reports, Selling Partner API reports, Brand
#: Analytics, Marketing Stream and AMC — under the names MCP servers commonly
#: give them. Matched against the lower-cased field with any `alias.` prefix
#: removed.
AMAZON = tuple(re.compile(p) for p in (
    # Delivery and cost
    r"^(impressions?|view_impressions|viewable_impressions|clicks|cost|spend|ad_spend)$",
    r"^(ctr|click_thru_rate|click_through_rate|cpc|cost_per_click|cpm|vcpm)$",
    # Attributed outcomes, any window, same-SKU or not
    r"^(ad_)?sales(_same_sku|_halo)?$", r"^ad_sales_same_sku$",
    r"^(attributed_)?(sales|conversions|purchases|units(_ordered|_sold)?|orders)"
    r"(_\d+d)?(_same_sku|_other_sku)?$",
    r"^(acos|roas|acos_same_sku|roas_same_sku|conv_rate|cvr|conversion(_rate)?|"
    r"conversion_same_sku)$",
    r"^(other_sku|halo)_(sales|units|orders)(_\d+d)?$",
    r"^(new_to_brand|ntb)_\w+$", r"^brand_store_page_views$", r"^detail_page_views$",
    # Share and position, as Amazon reports them
    r"^(top_of_search_impression_share|search_term_impression_share|"
    r"search_term_impression_rank|impression_share)$",
    # Structure and settings as Amazon stores them
    r"^(portfolio|campaign|ad_group|keyword|target|ad|advertised|purchased)_"
    r"(id|name|asin|sku)$",
    r"^(campaign_(type|state|status|serving_status|daily_budget|bid_strategy|"
    r"targeting_type|tactic|budget|budget_type|start_date|end_date))$",
    r"^(daily_budget|budget|bid|default_bid|bidding_strategy|bid_strategy|state|"
    r"status|serving_status|match_type|keyword_text|keyword_type|text|targeting|"
    r"targeting_type|targeting_expression|expression|ad_type|ad_product|"
    r"placement|placement_classification|placementclassification|"
    r"placement_bid_adjustment|bid_adjustment|percentage|site|"
    r"query|search_term|search_query|asin|sku|parent_asin|marketplace|"
    r"market_place|currency|date|start_date|end_date|hour|time_window_start)$",
    # Brand Analytics search query performance
    r"^(search_query_(volume|score)|(total|market|brand|asin)_(query_)?"
    r"(impressions?|clicks|cart_adds|basket_adds|purchases)(_count|_share|_rate|"
    r"_price_median)?)$",
    r"^(click|purchase|impression|cart_add)_share$",
    # Retail: Business Reports, inventory, catalogue, fees
    r"^(sessions|page_views|buy_box_percentage|featured_offer_percentage|"
    r"units_ordered|ordered_product_sales|total_order_items|total_sales|"
    r"total_units|total_order_count)$",
    r"^(product_quantity(_fba|_on_date)?|fulfillable_quantity|available|inbound_\w+|"
    r"fbc_inbound_\w+|reserved_quantity|days_of_supply|stock_status)$",
    r"^(rating|ratings_total|review_count|bsr_rank|sales_rank|product_price|"
    r"listing_price|your_price|price|landed_price|item_name|product_title|title)$",
    r"^(referral_fee|fba_fee|fulfillment_fee|estimated_fee(_total)?|"
    r"estimated_referral_fee_per_unit|expected_fulfillment_fee_per_unit)$",
    # Brand Analytics repeat purchase behaviour
    r"^(orders|unique_customers|repeat_customers(_pct_total)?|repeat_purchase_revenue"
    r"(_pct_total)?|repeat_customers_pct|repeat_orders|repeat_revenue|"
    r"(customer|order)_count)$",
    # Subscribe & Save, as Amazon reports it
    r"^(subscribe_and_save|sns|s_and_s)_\w+$",
    r"^(active_subscriptions|new_subscriptions|cancelled_subscriptions|"
    r"subscription_(units|revenue|share)|retention_(30|90)_days?|"
    r"(30|90)_day_retention|subscriber_count)$",
    # Returns (FBA customer returns, returns insights)
    r"^(return_date|quantity|returned_quantity|return_quantity|reason|return_reason|"
    r"detailed_disposition|disposition|customer_comments|order_id|fnsku|"
    r"license_plate_number|return_rate|returns|returned_units|refund_amount)$",
    # Inventory planning and restock
    r"^(units_shipped_t\d+|sales_shipped_last_\d+_days|units_shipped_last_\d+_days|"
    r"inv_age_\w+|inventory_supply_at_fba|recommended_replenishment_qty|"
    r"recommended_ship_date|days_of_supply_at_amazon_fulfillment_network|"
    r"total_days_of_supply|weeks_of_cover_t\d+|sell_through|alert|"
    r"afn_\w+|mfn_\w+|(fulfillable|unfulfillable|reserved|inbound)_quantity|"
    r"historical_days_of_supply|estimated_storage_cost_next_month)$",
    # DSP (incl. brand halo), as Amazon DSP reports it
    r"^(total_)?(dpv|atc|atcr|dpvr|purchases|units_sold|sales|roas|ecpp|ecpatc|"
    r"ecpdpv|reach|frequency|viewability)(_views|_clicks|_bh|_14d|_rate)?$",
    r"^(order|line_item|creative|advertiser|audience|supply_source)_(id|name|type)$",
    r"^(impressions_views|click_throughs)$",
    # Search query performance, the names reports commonly use
    r"^(search_query|search_query_volume|search_query_score|reporting_date|week|"
    r"year|month|start_date|end_date)$",
    r"^(impressions|clicks|cart_adds|purchases)_(total_count|brand_count|asin_count|"
    r"brand_share|asin_share|price_median|brand_price_median|"
    r"same_day_shipping_speed|1d_shipping_speed|2d_shipping_speed|rate)$",
    # Catalog and offers
    r"^(brand|manufacturer|product_type|item_type|browse_node(_id|_name)?|"
    r"category|sub_category|classification|display_group|size|color|pack_size|"
    r"item_package_quantity|number_of_items|unit_count|release_date|"
    r"is_prime|is_fulfilled_by_amazon|is_buy_box_winner|buy_box_price|"
    r"offer_count|number_of_offers|seller_id|seller_name|condition|"
    r"shipping|fulfillment_channel|feedback_count|feedback_rating)$",
))

#: Computed from the records above and nothing else. Each with where it comes
#: from, because "derived" is a claim the next reader should be able to check.
DERIVED = {
    "serving_hours": "hours a campaign served each day, from Amazon's budget events",
    "time_in_budget": "share of the day a campaign was in budget, from budget events",
    "organic_sales": "total sales less ad-attributed sales",
    "organic_units": "total units less ad-attributed units",
    "organic_page_views": "page views less ad clicks",
    "org_conv_rate": "organic units over organic page views",
    "tacos": "ad spend over total sales",
    "total_acos": "ad spend over total sales",
    "total_roas": "total sales over ad spend",
    "total_conv_rate": "total units over sessions",
    "average_daily_spend": "spend over days in the window",
    "average_daily_sales": "sales over days in the window",
    "day_range": "days in the window",
    "taov": "total sales over total orders", "tauv": "total sales over total units",
    "aaov": "ad sales over ad orders", "aauv": "ad sales over ad units",
    "ssaov": "same-SKU ad sales over same-SKU orders",
    "ssauv": "same-SKU ad sales over same-SKU units",
    "fee_margin": "price less Amazon's referral and fulfilment fees, over price",
    "return_rate": "returned units over units shipped, from the returns report",
    "returns_adjusted_acos": "ad spend over ad sales net of returns",
    "days_of_cover": "available plus inbound units over recent daily units sold",
    "repeat_rate": "repeat customers over unique customers, from repeat purchase",
    "orders_per_customer": "orders over unique customers",
    "ntb_share": "new-to-brand orders over orders",
    "cost_per_new_customer": "ad spend over new-to-brand orders",
    "capture_gap": "purchase share less impression share, from SQP",
    "conversion_share_gap": "purchase share less click share, from SQP",
    "after_amazon": "price less Amazon's fees and the cost of returns",
    "market_purchases": "the market's purchases for a query, from SQP",
    "order_value": "sales over orders",
    "price_per_unit": "price over the units in the pack, from the catalog",
}

#: Typed in by a person on some platform, or computed from something that was.
#: Not the rule — the rule is the allowlist above — only a plainer refusal.
KNOWN_ENTERED = re.compile(
    r"^(target_acos|max_acos|min_acos|target_roas|acos_target|goal_\w*|ad_goal|"
    r"tags?(_enhanced)?|preview_bid|floor_bid|ceil_bid|manual_bid|has_manual_bid|"
    r"bid_(stats|slope|resolved|trend_\w+|avg_\w+|last_\w+|optimization|multiplier)|"
    r"lockout|locked|target_bid_state|trellis_\w+|ntm_\w+|\w*_managed|"
    r"campaign_net_bid_adjust|budget_allocation|scheduled_actions|seo_score|"
    r"buyability_score|landed_cost|total_cogs|cogs|unit_cost|shipping_cost|margin\w*|"
    r"profit\w*|roms|min_price|max_price|pricing_plan_\w+|product_pricing_plan_\w+|"
    r"price_override_end|enroll_date|sales_state|inventory_state\w*|source|"
    r"eligibility_status|daily_sales_goal|daily_ad_spend_goal|days_with_goal|"
    r"sales_with_goal|ad_spend_with_goal|campaign_goal|campaign_gv_type|"
    r"optimization_rule_id|campaign_daily_bid_adjust|campaign_weekly_bid_adjust|"
    r"total_cost|adv_(keyword|target)_(source|tags)|managed|ad_name|advertisement_id|"
    r"\w*_score)$")


def normalise(field: str) -> str:
    name = str(field or "").strip().lower()
    if ":" in name:
        name = name.split(":", 1)[1]
    return name.rsplit(".", 1)[-1]


def classify(field: str) -> str:
    """amazon | derived | entered | unknown. Only the first two may be used.

    `amc:` marks a column of an Amazon Marketing Cloud query: AMC is Amazon's
    own event data, so what a query computes from it is Amazon's."""
    raw = str(field or "").strip().lower()
    if raw.startswith("amc:") and len(raw) > 4:
        return "amazon"
    name = normalise(field)
    if not name:
        return "unknown"
    if KNOWN_ENTERED.match(name):
        return "entered"
    if name in DERIVED:
        return "derived"
    if any(p.match(name) for p in AMAZON):
        return "amazon"
    return "unknown"


def refusals(fields, where: str) -> list[str]:
    """Why `fields` cannot carry `where`, one sentence each; empty if they can."""
    if not isinstance(fields, list) or not fields or \
            not all(isinstance(f, str) and f.strip() for f in fields):
        return [f"{where}.fields is required: the data fields the claim was computed "
                "from, so a reader can check it rests on Amazon's records"]
    out = []
    for f in fields:
        kind = classify(f)
        if kind == "entered":
            out.append(f"{where} uses {f!r}, a value somebody entered on a platform, not "
                       "Amazon data; no part of an audit may rest on it")
        elif kind == "unknown":
            out.append(f"{where} uses {f!r}, which is not recognised as Amazon data or "
                       "computed from it. If it is, it is added to yaaf/ppc/fields.py with "
                       "where it comes from; until then it is not evidence")
    return out
