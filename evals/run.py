"""Record or regression-check the golden transcripts.

    python -m evals.run                  # check every scenario against its golden
    python -m evals.run --record         # re-record every golden
    python -m evals.run -s NAME          # limit to one scenario (repeatable)
    python -m evals.run --list           # list scenarios

Check mode re-runs each scenario, normalizes the fresh transcript, and diffs
it against evals/golden/<name>.jsonl. Any diff — or any declarative
expectation failure — is a regression and exits nonzero with a readable
unified diff. Record mode refuses to write a golden whose run fails its own
expectations.
"""

from __future__ import annotations

import argparse
import sys

from evals import harness


def _run_one(path, record: bool) -> bool:
    """Run one scenario; print the outcome; return True on success."""
    scenario = harness.load_scenario(path)
    result = harness.run_scenario(scenario)

    if result.failures:
        print(f"[FAIL] {scenario.name}: {len(result.failures)} expectation failure(s)")
        for failure in result.failures:
            print(f"       - {failure}")
        if record:
            print(f"       golden NOT written for {scenario.name}")
        return False

    if record:
        golden = harness.write_golden(scenario, result.normalized)
        print(
            f"[OK]   {scenario.name}: expectations pass; "
            f"golden recorded ({len(result.normalized)} events) -> {golden}"
        )
        return True

    golden = harness.read_golden(scenario)
    if golden is None:
        print(
            f"[FAIL] {scenario.name}: no golden transcript at "
            f"{harness.golden_path(scenario)}; record it with: python -m evals.run --record"
        )
        return False
    if golden != result.normalized:
        print(f"[FAIL] {scenario.name}: transcript diverged from golden")
        for line in harness.unified_diff(golden, result.normalized, scenario.name):
            print(line)
        return False
    print(
        f"[OK]   {scenario.name}: expectations pass; "
        f"transcript matches golden ({len(result.normalized)} events)"
    )
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evals.run",
        description="Record or regression-check golden transcripts for the eval scenarios.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--record", action="store_true",
                      help="re-run scenarios and overwrite their golden transcripts")
    mode.add_argument("--check", action="store_true",
                      help="re-run scenarios and diff against the goldens (default)")
    parser.add_argument("--scenario", "-s", action="append", metavar="NAME",
                        help="limit to this scenario name (repeatable)")
    parser.add_argument("--list", action="store_true", help="list scenarios and exit")
    args = parser.parse_args(argv)

    paths = harness.list_scenarios()
    if args.list:
        for path in paths:
            print(path.stem)
        return 0
    if not paths:
        print("no scenarios found under evals/scenarios/", file=sys.stderr)
        return 2
    if args.scenario:
        by_name = {p.stem: p for p in paths}
        unknown = [n for n in args.scenario if n not in by_name]
        if unknown:
            print(
                f"unknown scenario(s): {', '.join(unknown)}; "
                f"available: {', '.join(sorted(by_name))}",
                file=sys.stderr,
            )
            return 2
        paths = [by_name[n] for n in args.scenario]

    ok = all([_run_one(path, record=args.record) for path in paths])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
