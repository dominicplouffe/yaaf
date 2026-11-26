"""Score a reader of the chairman's lines against his own answer key.

    python -m evals.read_as KEY.json --reader rules
    python -m evals.read_as KEY.json --reader model --project SLUG
    python -m evals.read_as KEY.json --predictions PRED.json   # any outside reader

KEY.json is a list of {id, body, ceo_before, label}: his real boardroom lines,
each with the label he says it should get (yaaf.server.intent.LABELS). It holds
his own words, so it lives outside the repository. `--reader model` reads with
the project's Fast model on its own key — real calls, a fraction of a cent each,
and nothing written to the project. `--predictions` scores a reader that ran
somewhere else (a local model) from a list of {id, label, confidence}.

What is scored is the mistake that matters, not the label: a line the CEO could
act on that he meant as talk ("wrongly acts"), and an order it would only talk
about ("wrongly holds"). The first changes his site; the second costs a reply.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path


def _acts(label: str, confidence: float, by: str) -> bool:
    from yaaf.server.intent import Reading

    return Reading(label, confidence, by).acts


def read_with_rules(key: list[dict]) -> list[dict]:
    from yaaf.server import intent

    out = []
    for row in key:
        started = time.monotonic()
        reading = intent.rules(row["body"])
        out.append({"id": row["id"], "label": reading.label, "confidence": reading.confidence,
                    "by": reading.by, "ms": int((time.monotonic() - started) * 1000)})
    return out


def read_with_model(key: list[dict], slug: str) -> list[dict]:
    from sqlalchemy import select

    from yaaf.core.db import db_session
    from yaaf.core.models import Project
    from yaaf.server import intent

    out = []
    with db_session() as session:
        project = session.execute(select(Project).where(Project.slug == slug)).scalar_one()
        for row in key:
            started = time.monotonic()
            try:
                reading, usage = intent.ask_model(session, project, row["body"],
                                                  row.get("ceo_before"))
                cost = getattr(usage, "cost_usd", None)
                tokens = (getattr(usage, "input", 0), getattr(usage, "output", 0))
            except Exception as exc:  # noqa: BLE001 — a failed read is scored as one
                reading, cost, tokens = intent.Reading("question", 0.0, f"failed: {exc!r}"[:80]), \
                    None, (0, 0)
            out.append({"id": row["id"], "label": reading.label,
                        "confidence": reading.confidence, "by": reading.by,
                        "ms": int((time.monotonic() - started) * 1000),
                        "usd": cost, "tokens": tokens})
        session.rollback()  # nothing read here is kept
    return out


def score(key: list[dict], predictions: list[dict]) -> dict:
    by_id = {p["id"]: p for p in predictions}
    rows, wrong = [], []
    for row in key:
        p = by_id.get(row["id"])
        if p is None:
            continue
        acts = _acts(p["label"], float(p.get("confidence") or 0), p.get("by") or "")
        should = row["label"] in ("order", "approval")
        rows.append((row, p, acts, should))
        if p["label"] != row["label"]:
            wrong.append({"n": row.get("n"), "his": row["label"], "read": p["label"],
                          "confidence": p.get("confidence"), "body": row["body"][:160]})
    n = len(rows) or 1
    ms = sorted(p.get("ms") or 0 for _, p, _, _ in rows)
    usd = [p.get("usd") for _, p, _, _ in rows if p.get("usd") is not None]
    return {
        "lines": len(rows),
        "label_right": sum(1 for r, p, _, _ in rows if p["label"] == r["label"]) / n,
        "act_right": sum(1 for _, _, a, s in rows if a == s) / n,
        "wrongly_acts": [r.get("n") for r, _, a, s in rows if a and not s],
        "wrongly_holds": [r.get("n") for r, _, a, s in rows if s and not a],
        "median_ms": ms[len(ms) // 2] if ms else None,
        "p95_ms": ms[int(len(ms) * 0.95) - 1] if ms else None,
        "usd_total": round(sum(usd), 6) if usd else None,
        "confusion": Counter(f"{r['label']}->{p['label']}" for r, p, _, _ in rows
                             if p["label"] != r["label"]).most_common(),
        "wrong": wrong,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("key", type=Path)
    parser.add_argument("--reader", choices=("rules", "model"))
    parser.add_argument("--project", help="the project whose Fast model and key read it")
    parser.add_argument("--predictions", type=Path, help="score an outside reader's output")
    parser.add_argument("--out", type=Path, help="write the predictions here")
    args = parser.parse_args(argv)

    key = json.loads(args.key.read_text())
    if args.predictions:
        predictions = json.loads(args.predictions.read_text())
    elif args.reader == "rules":
        predictions = read_with_rules(key)
    elif args.reader == "model":
        if not args.project:
            parser.error("--reader model needs --project")
        predictions = read_with_model(key, args.project)
    else:
        parser.error("name a --reader or --predictions")
    if args.out:
        args.out.write_text(json.dumps(predictions, indent=1))
    json.dump(score(key, predictions), sys.stdout, indent=1, default=str)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
