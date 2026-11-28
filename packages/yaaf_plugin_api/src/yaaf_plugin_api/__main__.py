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

from yaaf_plugin_api.report import check


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m yaaf_plugin_api")
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", help="check a plugin checkout against the spec")
    c.add_argument("path", nargs="?", default=".")
    c.add_argument("--json", action="store_true", help="print the report as JSON")
    c.add_argument("--no-import", action="store_true",
                   help="check the files only; do not import the plugin's module")
    args = parser.parse_args(argv)
    report = check(args.path, load_module=not args.no_import)
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
