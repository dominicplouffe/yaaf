"""The audit as a workbook, and the change sheet a brand manager uploads.

The deck says what to change; this is where somebody goes to do it:

* **Summary** — the answer, the bands, the tiers.
* **Claims** — every claim, money first, with its test, confidence and evidence.
* **Plan** — every move with today's value, the new one, its week, its check
  and its stop rule; totals are formulas, so a deleted row moves them.
* **Change sheet** — the Amazon changes in bulk-operation vocabulary, IDs from
  the data. Also written alone as a CSV (`change_sheet_csv`).
* **One tab per claim that carries rows** — the search terms, keywords,
  placements or products behind it.
* **Limits** — not covered, assumptions, what the owner told us.

Written by the platform from the checked report, like the deck and the page.
"""

from __future__ import annotations

import csv
import io
import re

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from yaaf.ppc import AMAZON_ACTIONS, claims_by_id

FONT = "Arial"
INK = "18202B"
HEAD_FILL = PatternFill("solid", fgColor=INK)
DRAFT_FILL = PatternFill("solid", fgColor="FFF4E0")
MONEY = '$#,##0;($#,##0);"-"'
PCT = '0.0"%";(0.0"%");"-"'

#: Amazon's bulk-operation vocabulary for the changes an audit recommends.
CHANGE_COLUMNS = ("Product", "Entity", "Operation", "Campaign ID", "Ad Group ID",
                  "Keyword ID", "Product Targeting ID", "Campaign Name", "Ad Group Name",
                  "State", "Daily Budget", "Bid", "Keyword Text", "Match Type",
                  "Bidding Strategy", "Placement", "Percentage", "Move", "Week", "Why")


def _font(**kw) -> Font:
    return Font(name=FONT, size=kw.pop("size", 10), **kw)


def _header(ws, row: int, labels) -> None:
    for i, label in enumerate(labels, start=1):
        c = ws.cell(row=row, column=i, value=label)
        c.font = _font(bold=True, color="FFFFFF")
        c.fill = HEAD_FILL
        c.alignment = Alignment(vertical="center", wrap_text=True)
    ws.row_dimensions[row].height = 30


def _title(ws, title: str, subtitle: str, reviewed: bool) -> int:
    ws["A1"] = title
    ws["A1"].font = _font(size=14, bold=True, color=INK)
    ws["A2"] = subtitle
    ws["A2"].font = _font(color="5D6673", italic=True)
    if reviewed:
        return 4
    ws["A3"] = ("Draft — not yet reviewed. A separate reviewer has not yet re-derived these "
                "numbers; treat them as provisional.")
    ws["A3"].font = _font(bold=True, color="6B4300")
    ws["A3"].fill = DRAFT_FILL
    return 5


def _widths(ws, widths) -> None:
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _wrap(ws, row: int, cols: int) -> None:
    for col in range(1, cols + 1):
        cell = ws.cell(row=row, column=col)
        cell.font = _font()
        cell.alignment = Alignment(vertical="top", wrap_text=True)


def _tab_name(text: str, taken: set[str]) -> str:
    name = re.sub(r"[\[\]:*?/\\'“”]", "", text)[:31].rstrip() or "Rows"
    n = 2
    while name in taken:
        name = f"{name[:27]} {n}"
        n += 1
    taken.add(name)
    return name


def _value(v):
    if isinstance(v, (dict, list)):
        return str(v)
    return v


def change_rows(report: dict) -> list[dict]:
    """The moves an Amazon bulk upload can carry, one row each."""
    rows = []
    for m in sorted(report.get("moves") or [], key=lambda m: (m["week"], m["id"])):
        if m["action"] not in AMAZON_ACTIONS:
            continue
        e = m.get("entity") or {}
        etype = str(e.get("type") or "").lower()
        row = {k: "" for k in CHANGE_COLUMNS}
        row.update({"Product": e.get("product") or "Sponsored Products",
                    "Campaign ID": e.get("campaign_id") or "",
                    "Ad Group ID": e.get("ad_group_id") or "",
                    "Keyword ID": e.get("keyword_id") or "",
                    "Product Targeting ID": e.get("target_id") or "",
                    "Campaign Name": e.get("campaign_name") or (e.get("name") if etype ==
                                                                "campaign" else ""),
                    "Ad Group Name": e.get("ad_group_name") or "",
                    "Move": m["id"], "Week": m["week"], "Why": m["why"]})
        to = m.get("change_to")
        action = m["action"]
        if action == "budget":
            row.update({"Entity": "Campaign", "Operation": "Update", "Daily Budget": to})
        elif action == "bid":
            row.update({"Entity": "Product Targeting" if e.get("target_id") else "Keyword",
                        "Operation": "Update", "Bid": to})
        elif action == "placement":
            row.update({"Entity": "Bidding Adjustment", "Operation": "Update",
                        "Placement": e.get("placement") or "", "Percentage": to})
        elif action == "bidding_strategy":
            row.update({"Entity": "Campaign", "Operation": "Update", "Bidding Strategy": to})
        elif action == "keyword_add":
            row.update({"Entity": "Keyword", "Operation": "Create", "State": "enabled",
                        "Keyword Text": e.get("keyword_text") or e.get("name"),
                        "Match Type": e.get("match_type") or "exact", "Bid": to})
        elif action == "negative_add":
            row.update({"Entity": "Negative Keyword" if not e.get("asin") else
                        "Negative Product Targeting", "Operation": "Create", "State": "enabled",
                        "Keyword Text": e.get("keyword_text") or e.get("name"),
                        "Match Type": e.get("match_type") or "negativeExact"})
        elif action == "target_add":
            row.update({"Entity": "Product Targeting", "Operation": "Create",
                        "State": "enabled", "Bid": to})
        elif action == "pause":
            row.update({"Entity": etype.title() or "Keyword", "Operation": "Update",
                        "State": "paused"})
        elif action == "campaign_create":
            row.update({"Entity": "Campaign", "Operation": "Create", "State": "enabled",
                        "Campaign Name": e.get("name"), "Daily Budget": to})
        rows.append(row)
    return rows


def change_sheet_csv(report: dict) -> str:
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=list(CHANGE_COLUMNS))
    w.writeheader()
    for row in change_rows(report):
        w.writerow({k: _value(v) for k, v in row.items()})
    return out.getvalue()


def build(report: dict, reviewed: bool = False) -> bytes:
    by = claims_by_id(report)
    wb = Workbook()
    win = report["window"]
    sub = (f"{report.get('account') or 'Account'} · {win['start']} to {win['end']} · "
           f"{report['account_type']} account")

    ws = wb.active
    ws.title = "Summary"
    r = _title(ws, "Amazon advertising audit", sub, reviewed)
    ws.cell(row=r, column=1, value=report["answer"]["headline"]).font = _font(bold=True)
    ws.cell(row=r + 1, column=1, value="More money: " + report["answer"]["scale"]).font = _font()
    r += 3
    _header(ws, r, ["Campaign job", "ACoS today (%)", "Affordable (%)", "How we know"])
    for b in report["economics"]["bands"]:
        r += 1
        aff = b["affordable_acos"]
        ws.append([b["job"], b["today_acos"],
                   f"{aff[0]}–{aff[1]}" if isinstance(aff, list) else aff, b["basis"]])
        _wrap(ws, r, 4)
        ws.cell(row=r, column=2).number_format = PCT
    tiers = report.get("tiers") or []
    if tiers:
        r += 2
        _header(ws, r, ["Extra a month ($)", "Fits ($)", "Sales low ($)", "Sales mid ($)",
                        "Sales high ($)", "ACoS (%)", "TACoS (%)", "Held to", "What binds"])
        for t in tiers:
            r += 1
            ws.append([t["monthly_usd"], t["fits_usd"], t["sales_low"], t["sales_mid"],
                       t["sales_high"], t.get("acos"), t.get("tacos"), t["basis"],
                       t.get("binding") or ""])
            _wrap(ws, r, 9)
            for col in range(1, 6):
                ws.cell(row=r, column=col).number_format = MONEY
            for col in (6, 7):
                ws.cell(row=r, column=col).number_format = PCT
    _widths(ws, [22, 16, 16, 16, 16, 12, 12, 12, 50])

    ws = wb.create_sheet("Claims")
    r = _title(ws, "Every claim, money first", sub, reviewed)
    _header(ws, r, ["Claim", "Kind", "Area", "Statement", "$ a month", "Confidence",
                    "How it was checked", "Result", "Evidence", "Would change our mind"])
    ordered = sorted(by.values(), key=lambda c: -abs(c.get("monthly_usd") or 0))
    first = r + 1
    for c in ordered:
        r += 1
        t = c.get("test") or {}
        ev = "; ".join(
            (f"receipt {e['receipt']}" if e.get("receipt") else
             f"artifact {e['artifact']}" if e.get("artifact") else
             f"owner answer {e['owner']}" if e.get("owner") is not None else
             f"source {e.get('source')}")
            + (f" [{', '.join(e.get('fields') or [])}]" if e.get("fields") else "")
            + (f" via {e['script']}" if e.get("script") else "")
            for e in c.get("evidence") or [])
        ws.append([c["id"], c["kind"], c["area"], c["statement"], c.get("monthly_usd"),
                   c["confidence"], t.get("method", ""), t.get("result", ""), ev,
                   c.get("falsifier", "")])
        _wrap(ws, r, 10)
        ws.cell(row=r, column=5).number_format = MONEY
    if len(ordered):
        r += 1
        ws.cell(row=r, column=4, value="Total of claims with a monthly value").font = \
            _font(bold=True)
        ws.cell(row=r, column=5, value=f"=SUM(E{first}:E{r - 1})").number_format = MONEY
    _widths(ws, [8, 12, 14, 60, 12, 12, 40, 30, 50, 40])

    moves = sorted(report.get("moves") or [], key=lambda m: (m["week"], m["id"]))
    ws = wb.create_sheet("Plan")
    r = _title(ws, "The plan, week by week", sub, reviewed)
    _header(ws, r, ["Move", "Week", "Tier ($)", "Action", "What", "Today", "Set to",
                    "Why this number", "Extra spend ($/mo)", "Extra sales ($/mo)",
                    "Check on day", "Expect", "Stop if", "Claims"])
    first = r + 1
    for m in moves:
        r += 1
        e = m.get("entity") or {}
        exp = m.get("expected_monthly") or {}
        ws.append([m["id"], m["week"], m.get("tier") or 0, m["action"],
                   e.get("name") or m.get("what", ""), _value(m.get("today", "")),
                   _value(m.get("change_to", "")), m["why"], exp.get("spend"),
                   exp.get("sales"), m["check"]["day"], m["check"]["expect"],
                   m["stop_rule"], ", ".join(m.get("claims") or [])])
        _wrap(ws, r, 14)
        for col in (3, 9, 10):
            ws.cell(row=r, column=col).number_format = MONEY
    if moves:
        r += 1
        ws.cell(row=r, column=8, value="Total").font = _font(bold=True)
        for col in (9, 10):
            letter = get_column_letter(col)
            ws.cell(row=r, column=col, value=f"=SUM({letter}{first}:{letter}{r - 1})") \
                .number_format = MONEY
    _widths(ws, [7, 6, 10, 14, 34, 12, 12, 46, 14, 14, 10, 34, 34, 12])

    ws = wb.create_sheet("Change sheet")
    r = _title(ws, "Changes in Amazon bulk-operation columns",
               "Compare the headers with a bulk file downloaded from your own account before "
               "uploading. Rows without IDs are for changing by hand.", reviewed)
    _header(ws, r, CHANGE_COLUMNS)
    for row in change_rows(report):
        r += 1
        ws.append([_value(row[k]) for k in CHANGE_COLUMNS])
        _wrap(ws, r, len(CHANGE_COLUMNS))
    _widths(ws, [16, 18, 10, 16, 16, 16, 16, 30, 24, 10, 12, 8, 26, 14, 18, 20, 10, 7, 6, 50])

    taken = {"Summary", "Claims", "Plan", "Change sheet", "Limits"}
    for c in ordered:
        rows = [x for x in c.get("rows") or [] if isinstance(x, dict)]
        if not rows:
            continue
        ws = wb.create_sheet(_tab_name(f"{c['id']} {c['statement']}", taken))
        r = _title(ws, c["statement"][:200], f"Claim {c['id']} · {sub}", reviewed)
        cols = []
        for x in rows:
            for k in x:
                if k not in cols:
                    cols.append(k)
        _header(ws, r, [k.replace("_", " ") for k in cols])
        for x in rows:
            ws.append([_value(x.get(k)) for k in cols])
        _widths(ws, [max(10, min(40, len(k) + 6)) for k in cols])

    ws = wb.create_sheet("Limits")
    r = _title(ws, "What this audit could not see, and what it assumed", sub, reviewed)
    _header(ws, r, ["Kind", "What", "Detail"])
    for x in report.get("not_covered") or []:
        r += 1
        ws.append(["Not covered", x.get("title") or x["area"], x["why"]])
        _wrap(ws, r, 3)
    for x in report.get("assumptions") or []:
        r += 1
        ws.append(["Assumed", x["what"] + (f" = {x['value']}" if x.get("value") else ""),
                   x["why"]])
        _wrap(ws, r, 3)
    for x in report.get("owner_inputs") or []:
        r += 1
        ws.append(["You told us", x["question"], x["answer"]])
        _wrap(ws, r, 3)
    for x in report.get("leave_alone") or []:
        r += 1
        ws.append(["Leave alone", x["what"], x["checked"]])
        _wrap(ws, r, 3)
    _widths(ws, [14, 50, 80])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
