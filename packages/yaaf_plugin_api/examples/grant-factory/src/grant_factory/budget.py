"""Served verbatim to seats as budget.py: every seat totals a budget the same way."""


def total(lines):
    return round(sum(float(line["amount"]) for line in lines), 2)
