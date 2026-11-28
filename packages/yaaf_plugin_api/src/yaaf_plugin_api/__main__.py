"""`python -m yaaf_plugin_api check <repo>` — the static conformance report.

The same checks the install starts with, so an author learns of a missing hook
or fixture before pushing. It prints one line per requirement and exits 1 when
anything failed. `--json` prints the report as data, for an AI factory or a CI
job to read.
"""

from __future__ import annotations

import argparse
import json
import sys

from yaaf_plugin_api.report import check, full_check


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m yaaf_plugin_api")
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", help="check a plugin checkout against the spec")
    c.add_argument("path", nargs="?", default=".")
    c.add_argument("--json", action="store_true", help="print the report as JSON")
    c.add_argument("--no-import", action="store_true",
                   help="check the files only; do not import the plugin's module")
    c.add_argument("--full", action="store_true",
                   help="also run the conformance suite through a local plugin host, and the "
                        "plugin's own tests: the checks the install runs")
    sub.add_parser("spec", help="print the plugin spec as JSON")
    n = sub.add_parser("new", help="write a new plugin repo that already passes the spec")
    n.add_argument("path", help="a new (or empty) directory")
    n.add_argument("--id", dest="plugin_id", required=True, help="the plugin id, e.g. grant-writer")
    n.add_argument("--name", default="")
    n.add_argument("--kind", dest="kind_key", default="", help="the kind key (default: from the id)")
    n.add_argument("--artifact", action="append", default=[],
                   help="a gated artifact, such as report.json (repeatable)")
    for flag in ("review", "pages", "brief", "plan-gate", "completion-gate", "view", "lead",
                 "create-fields", "receipts"):
        n.add_argument(f"--{flag}", action="store_true")
    n.add_argument("--action", default=None, help="a data action's name")
    n.add_argument("--action-tool", default=None, help="the data-source tool the action calls")
    n.add_argument("--setup-step", default=None)
    args = parser.parse_args(argv)
    if args.command == "new":
        from yaaf_plugin_api import scaffold

        opts = scaffold.Options(
            args.plugin_id, name=args.name, kind_key=args.kind_key, artifacts=args.artifact,
            review=args.review, pages=args.pages, brief=args.brief, plan_gate=args.plan_gate,
            completion_gate=args.completion_gate, view=args.view, lead=args.lead,
            create_fields=args.create_fields, receipts=args.receipts, action=args.action,
            action_tool=args.action_tool, setup_step=args.setup_step)
        try:
            written = scaffold.write(args.path, opts)
        except scaffold.ScaffoldProblem as exc:
            print(f"not written: {exc}", file=sys.stderr)
            return 2
        print(f"wrote {len(written)} files to {args.path}")
        report = check(args.path)
        print(f"static check: {report.status}"
              + ("" if not report.failures() else
                 " — " + "; ".join(r.id for r in report.failures())))
        print(f"next: python -m yaaf_plugin_api check {args.path} --full")
        return 1 if report.failures() else 0
    if args.command == "spec":
        from yaaf_plugin_api.spec import spec

        print(json.dumps(spec(), indent=2))
        return 0
    report = full_check(args.path) if args.full else check(args.path,
                                                           load_module=not args.no_import)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(f"{report.plugin or '(no manifest)'} {report.version or ''} — api {report.api}: "
              f"{report.status}")
        for r in report.results:
            print(f"  {r.status:<7} {r.id:<11} {r.text}")
            for why in r.why:
                print(f"            ↳ {why}")
    return 1 if report.status == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
