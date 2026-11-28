"""The audit as a presentation a brand manager reads in five minutes.

Written by the platform from the checked report, so it cannot say anything the
report does not. The story is the crew's — the answer, the portrait, what the
business can afford, the chapters, the plan — and the shape is the same for
every account:

* **The answer first**: the claims worth the most money and the scale answer.
* **What good looks like for this business** before any campaign: the ACoS
  it can afford by campaign job, beside today.
* **One chapter a slide**, its takeaway as the headline, each claim with its
  number, how it was checked and how sure we are, and the rows that prove it.
* **The plan**, week by week: every change with today's value, the new one,
  when to look and what turns it off.
* **What to leave alone, what was not covered, what was assumed.**
* **Draft until reviewed**: until a separate seat's review passes, every slide
  says so.

One self-contained HTML file: no fonts, scripts or images fetched from
anywhere, so it opens the same from an email attachment as from the
Deliverables page, and prints one slide a page.
"""

from __future__ import annotations

import html
from datetime import date

from yaaf.ppc import CRITERIA_TITLES, claims_by_id

#: Rows of proof shown on a slide; the workbook carries all of them.
PROOF_ROWS = 10
PROOF_COLUMNS = 7
MAX_PLAN_ROWS = 14


def _e(text) -> str:
    return html.escape(str(text if text is not None else ""), quote=True)


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _money(x) -> str:
    if not _num(x):
        return "—"
    sign = "−" if x < 0 else ""
    x = abs(float(x))
    if x >= 10_000:
        return f"{sign}${x / 1000:,.0f}k"
    return f"{sign}${x:,.0f}"


def _pct(x) -> str:
    if isinstance(x, list) and len(x) == 2 and all(_num(v) for v in x):
        return f"{x[0]:.0f}–{x[1]:.0f}%"
    return f"{x:.1f}%" if _num(x) else "—"


def _day(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def _cell(value) -> str:
    if _num(value):
        if isinstance(value, float) and abs(value) < 1 and value != 0:
            return f"{value:.3g}"
        return f"{value:,.2f}".rstrip("0").rstrip(".") if isinstance(value, float) \
            else f"{value:,}"
    return _e(value)


def _proof(rows) -> str:
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    if not rows:
        return ""
    cols = []
    for r in rows[:PROOF_ROWS]:
        for k in r:
            if k not in cols:
                cols.append(k)
    cols = cols[:PROOF_COLUMNS]
    head = "".join(f"<th>{_e(c.replace('_', ' '))}</th>" for c in cols)
    body = "".join("<tr>" + "".join(f"<td>{_cell(r.get(c))}</td>" for c in cols) + "</tr>"
                   for r in rows[:PROOF_ROWS])
    more = (f'<p class="source">{len(rows) - PROOF_ROWS} more rows in the workbook.</p>'
            if len(rows) > PROOF_ROWS else "")
    return f'<table class="proof"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>{more}'


SURE = {"high": "Very likely", "medium": "Likely", "low": "Worth testing"}
BASIS = {"acos": "today's ACoS", "tacos": "today's TACoS", "payback": "payback"}


def _claim_li(c: dict) -> str:
    n = _money(c["monthly_usd"]) + "/mo" if _num(c.get("monthly_usd")) else \
        {"works": "Working", "risk": "Risk", "context": "Context",
         "forecast": "Forecast"}.get(c["kind"], "")
    t = c.get("test") or {}
    how = []
    if t.get("method"):
        how.append(f"How we checked: {t['method']} → {t['result']}")
    how.append(SURE[c["confidence"]])
    if c.get("falsifier"):
        how.append(f"Would change our mind: {c['falsifier']}")
    return (f'<li class="{_e(c["kind"])}"><span class="n">{_e(n)}</span>'
            f'<span class="s">{_e(c["statement"])}'
            f'<span class="how">{_e(" · ".join(how))}</span></span></li>')


def _change(m: dict) -> str:
    today, to = m.get("today"), m.get("change_to")
    if today in (None, "") and to in (None, ""):
        return "—"
    if today in (None, ""):
        return f"new: {_e(to)}"
    return f"{_e(today)} → {_e(to if to not in (None, '') else 'paused')}"


def _slide(body: str, cls: str = "") -> str:
    return f'<section class="slide {cls}">{body}</section>'


def render(report: dict, reviewed: bool = False) -> str:
    by = claims_by_id(report)
    w = report["window"]
    account = report.get("account") or "Your account"
    draft = ("" if reviewed else
             '<p class="draft">Draft — not yet reviewed. A separate reviewer has not yet '
             "re-derived these numbers; treat them as provisional.</p>")
    src = (f"Amazon {report['account_type']} account · {_day(w['start'])} to {_day(w['end'])}"
           + (f" · {report['marketplace']}" if report.get("marketplace") else ""))
    slides = []
    a = report["answer"]
    slides.append(_slide(
        f'<p class="kicker">Amazon advertising audit</p><h1>{_e(account)}</h1>'
        f'<p class="lede">{_e(a["headline"])}</p>{draft}<p class="source">{_e(src)}</p>',
        "cover"))
    top = "".join(_claim_li(by[c]) for c in a["top"])
    slides.append(_slide(
        f'<p class="kicker">The short version</p><h2>What is worth the most</h2>'
        f'<ul class="claims">{top}</ul>'
        f'<p class="note"><strong>More money:</strong> {_e(a["scale"])}</p>'
        f'<p class="source">{_e(src)}</p>'))
    biz = report["business"]
    biz_claims = "".join(_claim_li(by[c]) for c in biz.get("claims") or [] if c in by)
    slides.append(_slide(
        f'<p class="kicker">Your business, as the data shows it</p>'
        f'<h2>What you sell and who buys it</h2><p class="lede">{_e(biz["portrait"])}</p>'
        + (f'<ul class="claims">{biz_claims}</ul>' if biz_claims else "")))
    econ = report["economics"]
    rows = "".join(
        f'<tr><td>{_e(b["job"])}</td><td class="v">{_pct(b["today_acos"])}</td>'
        f'<td class="v">{_pct(b["affordable_acos"])}</td>'
        f'<td class="basis">{_e(b["basis"])}</td></tr>' for b in econ["bands"])
    slides.append(_slide(
        '<p class="kicker">What good looks like for you</p>'
        '<h2>What you can afford to pay for a sale</h2>'
        '<table class="bands"><thead><tr><th>Campaign job</th><th>ACoS today</th>'
        f'<th>You can afford</th><th>How we know</th></tr></thead><tbody>{rows}</tbody></table>'
        + (f'<p class="note">{_e(econ["note"])}</p>' if econ.get("note") else "")))
    for ch in report["chapters"]:
        claims = [by[c] for c in ch["claims"]]
        proof = next((c.get("rows") for c in claims if c.get("rows")), None)
        slides.append(_slide(
            f'<p class="kicker">{_e(ch["title"])}</p><h2>{_e(ch["takeaway"])}</h2>'
            f'<ul class="claims">{"".join(_claim_li(c) for c in claims)}</ul>'
            + _proof(proof)))
    tiers = report.get("tiers") or []
    if tiers:
        biggest = max(t["monthly_usd"] for t in tiers)
        bars = []
        for t in tiers:
            share = 100 * t["fits_usd"] / biggest if biggest else 0
            bars.append(
                f'<div class="tier"><strong>{_money(t["monthly_usd"])} more a month</strong>'
                f'<div class="bar"><div class="fill" style="width:{share:.1f}%"></div></div>'
                f'<span class="fits">{_money(t["fits_usd"])} fits · sales '
                f'{_money(t["sales_low"])}–{_money(t["sales_high"])} · ACoS '
                f'{_pct(t.get("acos"))} · held to {_e(BASIS[t["basis"]])}</span></div>')
        binding = "".join(f"<li><strong>{_money(t['monthly_usd'])}:</strong> "
                          f"{_e(t['binding'])}</li>" for t in tiers if t.get("binding"))
        fc = by.get(tiers[0].get("forecast_claim")) or {}
        how = (fc.get("test") or {})
        slides.append(_slide(
            '<p class="kicker">If you spend more</p>'
            f'<h2>{_e(a["scale"])}</h2><div class="tiers">{"".join(bars)}</div>'
            + (f'<ul class="plain">{binding}</ul>' if binding else "")
            + (f'<p class="source">How sure: {_e(how.get("method"))} → '
               f'{_e(how.get("result"))}</p>' if how.get("method") else "")))
    moves = sorted(report.get("moves") or [], key=lambda m: (m["week"], m["id"]))
    if moves:
        rows = []
        for m in moves[:MAX_PLAN_ROWS]:
            ent = (m.get("entity") or {}).get("name") or m.get("what") or ""
            tier = m.get("tier")
            when = "now" if not tier else f"with {_money(tier)}"
            rows.append(
                f'<tr><td class="wk">{m["week"]}</td>'
                f'<td><strong>{_e(m["action"].replace("_", " "))}</strong> {_e(ent)}'
                f'<br><span class="source">{_e(m["why"])} ({when})</span></td>'
                f'<td>{_change(m)}</td>'
                f'<td>Day {m["check"]["day"]}: {_e(m["check"]["expect"])}</td>'
                f'<td>{_e(m["stop_rule"])}</td></tr>')
        more = (f'<p class="source">{len(moves) - MAX_PLAN_ROWS} more changes in the workbook '
                "and the change sheet.</p>" if len(moves) > MAX_PLAN_ROWS else "")
        slides.append(_slide(
            '<p class="kicker">The plan</p><h2>What to change, week by week</h2>'
            '<table class="plan"><thead><tr><th>Week</th><th>Change</th><th>Today → set to</th>'
            f'<th>Check</th><th>Stop if</th></tr></thead><tbody>{"".join(rows)}</tbody></table>'
            + more))
    if report.get("leave_alone"):
        items = "".join(f"<li><strong>{_e(x['what'])}</strong> {_e(x['checked'])}</li>"
                        for x in report["leave_alone"])
        slides.append(_slide('<p class="kicker">What is working</p>'
                             f'<h2>Leave these alone</h2><ul class="plain">{items}</ul>'))
    gaps = report.get("not_covered") or []
    assumed = report.get("assumptions") or []
    told = report.get("owner_inputs") or []
    if gaps or assumed or told:
        parts = []
        if gaps:
            parts.append("<h3>Not covered</h3><ul class=\"plain\">" + "".join(
                f"<li><strong>{_e(x.get('title') or x['area'])}.</strong> {_e(x['why'])}</li>"
                for x in gaps) + "</ul>")
        if assumed:
            parts.append("<h3>What we assumed</h3><ul class=\"plain\">" + "".join(
                f"<li>{_e(x['what'])}{(' = ' + _e(x['value'])) if x.get('value') else ''}. "
                f"{_e(x['why'])}</li>" for x in assumed) + "</ul>")
        if told:
            parts.append("<h3>What you told us</h3><ul class=\"plain\">" + "".join(
                f"<li>{_e(x['question'])} — {_e(x['answer'])}</li>" for x in told) + "</ul>")
        slides.append(_slide('<p class="kicker">The limits of this audit</p>'
                             '<h2>What it could not see, and what it assumed</h2>'
                             + "".join(parts)))
    checked = ("Every number came from your account's Amazon data, pulled through the "
               "platform and recorded; nothing typed into an advertising tool was used. "
               "Each finding was tested against the alternatives before it was written. ")
    review = ("A separate reviewer re-derived every number worth $1,000 a month or more and "
              "graded the audit on " + ", ".join(CRITERIA_TITLES.values()).lower() + "."
              if reviewed else "This draft has not been reviewed yet.")
    slides.append(_slide('<p class="kicker">How this audit was made</p>'
                         f'<h2>Where the numbers come from</h2><p class="lede">{_e(checked)}'
                         f'</p><p class="note">{_e(review)}</p>', "method"))
    return (_PAGE.replace("{title}", _e(f"Amazon advertising audit — {account}"))
            .replace("{slides}", "\n".join(slides))
            .replace("{count}", str(len(slides))))


_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {
  --paper: #ffffff;
  --ink: #18202b;
  --muted: #5d6673;
  --rule: #dfe3e8;
  --save: #0e7a55;
  --grow: #1f5fbf;
  --risk: #a9661a;
  --wash: #f4f6f8;
  --serif: Charter, "Bitstream Charter", "Sitka Text", Cambria, Georgia, serif;
  --sans: -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
}
* { box-sizing: border-box; }
html { scroll-snap-type: y mandatory; }
body { margin: 0; background: var(--paper); color: var(--ink); font: 18px/1.5 var(--sans);
  font-variant-numeric: tabular-nums; }
.slide { min-height: 100vh; scroll-snap-align: start; padding: 7vh 8vw 6vh;
  display: flex; flex-direction: column; border-bottom: 1px solid var(--rule); }
h1, h2 { font-family: var(--serif); font-weight: 700; letter-spacing: -0.01em; margin: 0; }
h1 { font-size: clamp(44px, 6.4vw, 88px); line-height: 1.02; max-width: 14ch; }
h2 { font-size: clamp(30px, 3.6vw, 50px); line-height: 1.1; max-width: 22ch; }
.kicker { color: var(--muted); margin: 0 0 18px; font-size: 20px; }
.lede { font-size: clamp(20px, 1.8vw, 26px); max-width: 44ch; margin: 28px 0 0; }
.note { color: var(--muted); max-width: 70ch; margin-top: 28px; }
.source { margin-top: auto; padding-top: 32px; color: var(--muted); font-size: 14px;
  max-width: 90ch; }
.cover { justify-content: center; }
.cover .source { margin-top: 64px; }
.headline-numbers { list-style: none; padding: 0; margin: 40px 0 0; display: grid; gap: 26px; }
.headline-numbers li { display: grid; grid-template-columns: minmax(180px, 16vw) 1fr;
  gap: 28px; align-items: baseline; border-top: 1px solid var(--rule); padding-top: 18px; }
.headline-numbers .n { font-family: var(--serif); font-size: clamp(34px, 3.8vw, 56px);
  font-weight: 700; line-height: 1; }
.headline-numbers .t { max-width: 52ch; font-size: 20px; }
.save .n, .finding.save .big .n { color: var(--save); }
.grow .n, .finding.grow .big .n { color: var(--grow); }
.risk .n, .finding.risk .big .n { color: var(--risk); }
.keep .n { color: var(--ink); }
.today { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 0; margin: 44px 0 0; border-top: 2px solid var(--ink); }
.today div { padding: 18px 20px 0 0; }
.today dt { color: var(--muted); font-size: 16px; }
.today dd { margin: 6px 0 0; font-family: var(--serif); font-size: clamp(28px, 2.8vw, 42px);
  font-weight: 700; }
.area { color: var(--muted); margin: 0 0 14px; font-size: 18px; }
.split { display: grid; grid-template-columns: 1.05fr 1fr; gap: 5vw; align-items: start; }
.big { margin: 30px 0 0; }
.big .n { font-family: var(--serif); font-size: clamp(56px, 7vw, 104px); font-weight: 700;
  line-height: 0.95; display: block; }
.big .t { display: block; margin-top: 8px; font-size: 20px; color: var(--muted); }
.do { margin-top: 30px; padding: 16px 18px; background: var(--wash); max-width: 52ch;
  border-left: 4px solid currentColor; }
.finding.save .do { color: var(--ink); border-left-color: var(--save); }
.finding.grow .do { border-left-color: var(--grow); }
.finding.risk .do { border-left-color: var(--risk); }
.evidence p { margin-top: 0; max-width: 60ch; }
.proof { width: 100%; border-collapse: collapse; font-size: 16px; margin-top: 18px; }
.proof th { text-align: left; font-weight: 600; color: var(--muted); padding: 8px 10px 8px 0;
  border-bottom: 2px solid var(--ink); }
.proof td { padding: 9px 10px 9px 0; border-bottom: 1px solid var(--rule); vertical-align: top; }
.proof td.money, .proof td.int, .proof td.pct, .proof td.hours,
.proof th.money, .proof th.int, .proof th.pct, .proof th.hours { text-align: right; }
.plain { padding-left: 1.1em; max-width: 70ch; margin: 34px 0 0; }
.plain li { margin: 0 0 14px; }
.tiers { margin: 40px 0 0; display: grid; gap: 18px; max-width: 980px; }
.tier { display: grid; grid-template-columns: 210px 1fr 260px; gap: 18px; align-items: center; }
.tier .bar { height: 22px; background: var(--wash); position: relative; }
.tier .fill { position: absolute; inset: 0 auto 0 0; background: var(--grow); }
.tier .fits { font-size: 16px; }
.where { margin-top: 30px; }
.monday { list-style: none; padding: 0; margin: 36px 0 0; max-width: 1000px;
  counter-reset: step; }
.monday li { display: grid; grid-template-columns: 40px 1fr 170px; gap: 18px;
  padding: 14px 0; border-bottom: 1px solid var(--rule); align-items: baseline; }
.monday li::before { counter-increment: step; content: counter(step);
  font-family: var(--serif); font-weight: 700; font-size: 22px; }
.monday li .worth { text-align: right; font-weight: 600; white-space: nowrap; }
.method p { max-width: 64ch; }
.method h2 + p { margin-top: 34px; }
.draft { background: #fff4e0; color: #6b4300; padding: 10px 14px; max-width: 70ch;
  border-left: 4px solid var(--risk); margin: 24px 0 0; font-size: 16px; }
.claims { list-style: none; padding: 0; margin: 34px 0 0; display: grid; gap: 22px;
  max-width: 1100px; }
.claims li { display: grid; grid-template-columns: minmax(230px, 18vw) 1fr; gap: 26px;
  border-top: 1px solid var(--rule); padding-top: 16px; align-items: baseline; }
.claims .n { font-family: var(--serif); font-size: clamp(26px, 2.6vw, 40px); font-weight: 700;
  line-height: 1; white-space: nowrap; }
.claims .fix .n, .claims .opportunity .n { color: var(--grow); }
.claims .risk .n { color: var(--risk); }
.claims .works .n { color: var(--save); }
.claims .s { font-size: 19px; max-width: 64ch; }
.claims .how { display: block; color: var(--muted); font-size: 15px; margin-top: 6px;
  max-width: 80ch; }
.bands { width: 100%; max-width: 1100px; border-collapse: collapse; margin-top: 34px; }
.bands th { text-align: left; color: var(--muted); font-weight: 600; padding: 8px 12px 8px 0;
  border-bottom: 2px solid var(--ink); }
.bands td { padding: 12px 12px 12px 0; border-bottom: 1px solid var(--rule);
  vertical-align: top; }
.bands td.v { font-family: var(--serif); font-size: 28px; font-weight: 700; white-space: nowrap; }
.bands td.basis { color: var(--muted); font-size: 15px; max-width: 60ch; }
.plan { width: 100%; border-collapse: collapse; font-size: 15px; margin-top: 26px; }
.plan th { text-align: left; color: var(--muted); font-weight: 600; padding: 6px 10px 6px 0;
  border-bottom: 2px solid var(--ink); }
.plan td { padding: 8px 10px 8px 0; border-bottom: 1px solid var(--rule); vertical-align: top; }
.plan td.wk { font-family: var(--serif); font-weight: 700; font-size: 20px; }
.nav { position: fixed; right: 20px; bottom: 16px; color: var(--muted); font-size: 13px; }
:focus-visible { outline: 3px solid var(--grow); outline-offset: 3px; }
@media (max-width: 860px) {
  .split { grid-template-columns: 1fr; }
  .headline-numbers li { grid-template-columns: 1fr; gap: 6px; }
  .tier { grid-template-columns: 1fr; gap: 6px; }
  .monday li { grid-template-columns: 32px 1fr; }
  .monday li .worth { grid-column: 2; text-align: left; }
}
@media print {
  @page { size: landscape; margin: 0; }
  html { scroll-snap-type: none; }
  .slide { min-height: 100vh; page-break-after: always; border: 0; }
  .nav { display: none; }
}
@media (prefers-reduced-motion: reduce) { html { scroll-behavior: auto; } }
</style>
</head>
<body>
<main>{slides}</main>
<p class="nav">Use the arrow keys to move between {count} slides.</p>
<script>
(function () {
  var slides = Array.prototype.slice.call(document.querySelectorAll('.slide'));
  function current() {
    var y = window.scrollY + window.innerHeight / 3, i = 0;
    slides.forEach(function (s, j) { if (s.offsetTop <= y) { i = j; } });
    return i;
  }
  document.addEventListener('keydown', function (e) {
    var next = { ArrowDown: 1, ArrowRight: 1, PageDown: 1, ' ': 1,
                 ArrowUp: -1, ArrowLeft: -1, PageUp: -1 }[e.key];
    if (!next) { return; }
    e.preventDefault();
    var i = Math.max(0, Math.min(slides.length - 1, current() + next));
    slides[i].scrollIntoView({ behavior: 'smooth' });
  });
}());
</script>
</body>
</html>
"""
