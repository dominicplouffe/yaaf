"""`yaaf spend`'s text: the install-wide rollup (`GET /observe/spend`) as a
terminal reads it.

The same answer Settings → Spend draws, in the same order — the totals, the
grouping, a bar per day, week or month, then every project, provider and model
— so a number read in one place can be found in the other. Terminals have no
colour to spare, so the bars are one series (the measure), and the biggest
groups in each bucket are named beside it in words.
"""

from __future__ import annotations

from datetime import date

BAR_WIDTH = 32
EIGHTHS = " ▏▎▍▌▋▊▉"
BY_HEAD = {"provider": "provider", "model": "model", "project": "project"}


def _n(value: float) -> str:
    """Compact past ten thousand, like the dashboard: token counts run to
    hundreds of millions and a column of nine-digit numbers is not read."""
    for size, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if value >= max(size, 10_000):
            return f"{value / size:.1f}".rstrip("0").rstrip(".") + suffix
    return f"{value:,.0f}"


def _usd(value: float) -> str:
    return f"${value:,.2f}"


def _value(row: dict, measure: str) -> float:
    return row["usd"] if measure == "usd" else row["tokens"]


def _show(value: float, measure: str) -> str:
    return _usd(value) if measure == "usd" else _n(value)


def _bar(fraction: float) -> str:
    eighths = round(max(0.0, min(fraction, 1.0)) * BAR_WIDTH * 8)
    full, rest = divmod(eighths, 8)
    return ("█" * full + (EIGHTHS[rest] if rest else "")).ljust(BAR_WIDTH)


def _period(start: str, every: str) -> str:
    day = date.fromisoformat(start)
    if every == "month":
        return day.strftime("%Y-%m")
    if every == "week":
        return f"wk {day:%Y-%m-%d}"
    return f"{day:%Y-%m-%d} {day:%a}"


def _table(headers: list[str], rows: list[list[str]], left: int = 1) -> list[str]:
    widths = [max(len(headers[i]), *(len(r[i]) for r in rows)) for i in range(len(headers))]

    def line(cells: list[str]) -> str:
        return "  " + "  ".join(c.ljust(widths[i]) if i < left else c.rjust(widths[i])
                                for i, c in enumerate(cells)).rstrip()

    return [line(headers), "  " + "  ".join("-" * w for w in widths), *map(line, rows)]


def _heading(title: str) -> list[str]:
    return ["", title, "─" * len(title)]


def choose_measure(data: dict, asked: str | None) -> str:
    """Dollars, unless nothing in the window was billed in dollars — then the
    tokens are the only figure there is."""
    if asked in ("usd", "tokens"):
        return asked
    totals = data["totals"]
    return "tokens" if totals["usd"] == 0 and totals["tokens"] > 0 else "usd"


def render(data: dict, measure: str) -> str:
    totals, every, by = data["totals"], data["every"], data["group_by"]
    spent = [p for p in data["options"]["projects"] if p["spent"]]
    # The projects in scope: the ones asked for, or every one the reader may open.
    scope = len(data["filters"]["project"]) or len(data["options"]["projects"])
    out = [
        f"Spend across {scope} project{'' if scope == 1 else 's'} · "
        f"{data['since']} → {data['until']} ({data['tz']}) · per {every}, by {BY_HEAD[by]}",
        f"Billed in dollars: {_usd(totals['usd'])} · tokens: {_n(totals['tokens'])}"
        f" ({_n(totals['plan_tokens'])} on subscriptions) · runs: {totals['runs']:,}"
        f" · {len(spent)} project{'' if len(spent) == 1 else 's'} spent",
        f"Note: {data['notes']['dollars']}",
    ]
    if not data["groups"]:
        out.append("\nNothing was spent in this window.")
        return "\n".join(out)

    unit = "dollars billed" if measure == "usd" else "tokens"
    whole = _value(totals, measure) or 1
    groups = sorted(data["groups"], key=lambda g: -_value(g, measure))
    out += _heading(f"By {BY_HEAD[by]} ({unit})")
    rows = [[g["label"] + (f" ({g['slug']})" if g.get("slug") else ""), f"{g['runs']:,}",
             _n(g["tokens_in"]), _n(g["cache_creation"]), _n(g["cache_read"]),
             _n(g["tokens_out"]), _n(g["tokens"]), _n(g["plan_tokens"]) if g["plan_tokens"] else "—",
             _usd(g["usd"]), f"{_value(g, measure) / whole:.0%}"] for g in groups]
    rows.append(["TOTAL", f"{totals['runs']:,}", _n(totals["tokens_in"]),
                 _n(totals["cache_creation"]), _n(totals["cache_read"]), _n(totals["tokens_out"]),
                 _n(totals["tokens"]), _n(totals["plan_tokens"]) if totals["plan_tokens"] else "—",
                 _usd(totals["usd"]), "100%"])
    out += _table([BY_HEAD[by], "runs", "input", "cache write", "cache read", "output", "tokens",
                   "on a plan", "billed", "share"], rows)

    labels = {g["key"]: g["label"] for g in data["groups"]}
    peak = max((_value(b, measure) for b in data["series"]), default=0) or 1
    out += _heading(f"Per {every} ({unit})")
    for bucket in data["series"]:
        value = _value(bucket, measure)
        top = sorted(((k, _value(v, measure)) for k, v in bucket["by"].items()),
                     key=lambda kv: -kv[1])
        named = [f"{labels.get(k, k)} {_show(v, measure)}" for k, v in top[:3] if v > 0]
        if len([v for _, v in top if v > 0]) > 3:
            named.append("…")
        out.append(f"  {_period(bucket['start'], every):<15} {_bar(value / peak)} "
                   f"{_show(value, measure):>10}  {' · '.join(named)}".rstrip())

    out += _heading("Every project, provider and model")
    out += _table(["project", "provider", "model", "runs", "tokens", "on a plan", "billed"],
                  [[r["slug"], r["provider_label"], r["model"], f"{r['runs']:,}", _n(r["tokens"]),
                    _n(r["plan_tokens"]) if r["plan_tokens"] else "—", _usd(r["usd"])]
                   for r in data["rows"]], left=3)
    out.append(f"\nNote: {data['notes']['tokens']}")
    return "\n".join(out)
