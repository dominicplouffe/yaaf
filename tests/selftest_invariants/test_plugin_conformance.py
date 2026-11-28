"""The conformance suite catches what it says it catches (build step 6).

Each case takes the example plugin, breaks it one way, and runs the suite
(`yaaf_plugin_api.conformance`) through a real host. It asserts that the
requirement the break violates is the one that fails, so a suite that went
quiet on a whole class of defect turns a test red. One case runs both runners
(an author's `LocalHost`, and core's sandboxed pool as the install uses it) on
the same broken plugin and expects the same verdict: a plugin green locally and
red at install is a bug in yaaf.

Each case starts a host and asks a few hundred questions, about 1 to 2 s; the
hang case waits out a one-second timeout. That cost is deliberate: a fake
host could not prove what the real one does.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from yaaf_plugin_api import client, conformance
from yaaf_plugin_api import report as api_report
from yaaf_plugin_api.manifest import load_path

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "packages/yaaf_plugin_api/examples/grant-factory"
INIT = "src/grant_factory/__init__.py"


def broken(tmp_path: Path, old: str, new: str, *, file: str = INIT,
           toml: tuple[str, str] | None = None) -> Path:
    dest = tmp_path / "plugin"
    shutil.copytree(EXAMPLE, dest, ignore=shutil.ignore_patterns("__pycache__"))
    path = dest / file
    text = path.read_text()
    assert old in text, f"the example no longer contains {old!r}"
    path.write_text(text.replace(old, new, 1))
    if toml:
        manifest = dest / "yaaf-plugin.toml"
        manifest.write_text(manifest.read_text().replace(*toml, 1))
    return dest


def suite(root: Path, **kw) -> dict:
    manifest = load_path(root)
    host = client.LocalHost(root, manifest)
    try:
        return conformance.run(root, manifest, host, **kw)
    finally:
        host.stop()


def failed(results: dict) -> dict[str, list[str]]:
    return {rid: why for rid, (status, why) in results.items() if status == "fail"}


def test_the_example_passes_everything_the_suite_decides():
    results = suite(EXAMPLE)
    assert failed(results) == {}
    assert {rid for rid, (s, _) in results.items() if s == "pass"} >= {
        "MIN-08", "MIN-09", "MIN-10", "MIN-11", "MIN-12", "MIN-13", "MIN-14", "MIN-15",
        "MIN-17", "EXT-ART-1", "EXT-ART-2", "EXT-PAGE-1", "EXT-PAGE-2", "EXT-REV-2",
        "EXT-RCT-1", "EXT-ACT-1", "EXT-ACT-2", "EXT-ACT-3", "EXT-GATE-1", "EXT-BRF-1",
        "EXT-STP-1", "EXT-VIEW-1", "EXT-CRT-1"}


BREAKS = [
    # (what breaks, old text, new text, the requirement that must fail)
    ("a gate that raises on hostile input",
     "        if not isinstance(doc, dict):\n            return Refuse([\"application.json must",
     "        doc['title'].strip()\n        if not isinstance(doc, dict):\n"
     "            return Refuse([\"application.json must", "MIN-09"),
    ("a brief that differs from call to call",
     'return Brief("Draft application.json',
     'import itertools as _it\n        global _N\n        _N = globals().get("_N", 0) + 1\n'
     '        return Brief(str(_N) + "Draft application.json', "MIN-11"),
    ("a brief that reaches the network",
     'return Brief("Draft application.json',
     'import socket\n        try:\n            socket.create_connection(("127.0.0.1", 9), 1)\n'
     '        except Exception:\n            pass\n        return Brief("Draft application.json',
     "MIN-12"),
    ("a refusal that is a stack trace",
     'return Refuse(["application.json is not valid JSON"])',
     'return Refuse(["Traceback (most recent call last):\\n  File \\"x.py\\""])', "MIN-17"),
    ("a data action that lets the source's failure escape",
     "        except ToolError as exc:\n            return ActionResult",
     "        except KeyError as exc:\n            return ActionResult", "EXT-ACT-2"),
    ("a data action that calls a tool it does not declare",
     '            result = gateway.call("get_call", {})',
     '            gateway.call("delete_everything", {})\n'
     '            result = gateway.call("get_call", {})', "EXT-ACT-3"),
    ("view data that does not fit its view",
     '"text": "Drafted" if latest else "Not drafted yet"}',
     '"text": "Drafted" if latest else "Not drafted yet", "colour": "red"}', "EXT-VIEW-1"),
    ("a final render that drops a page",
     '        return [Page("application.md", "text/markdown", body.encode())]',
     '        return [] if reviewed else [Page("application.md", "text/markdown", '
     'body.encode())]', "EXT-PAGE-2"),
    ("a read whose fields cannot be told, said as an empty list",
     "        return ReadFields.unknown()", "        return ReadFields(())", "EXT-RCT-1"),
    ("a completion gate that never clears",
     '            return [Owed("application", "no application.json has been accepted yet")]\n'
     '        return []',
     '            return [Owed("application", "no application.json has been accepted yet")]\n'
     '        return [Owed("forever", "always one more thing")]', "EXT-GATE-1"),
    ("a create check that accepts nothing",
     "        return [] if isinstance(amount, (int, float)) and amount > 0 else \\",
     "        return [\"no\"] if True else \\", "EXT-CRT-1"),
]


@pytest.mark.parametrize(("old", "new", "rid"), [b[1:] for b in BREAKS],
                         ids=[b[0] for b in BREAKS])
def test_each_break_fails_the_requirement_it_violates(tmp_path, old, new, rid):
    if rid == "EXT-CRT-1":
        create = tmp_path / "plugin" / "fixtures" / "create" / "accept"
        root = broken(tmp_path, old, new)
        create.mkdir(parents=True)
        (create / "ok.json").write_text('{"funder": "F", "amount": 5}')
    else:
        root = broken(tmp_path, old, new)
    problems = failed(suite(root))
    assert rid in problems, problems


def test_a_gate_that_hangs_on_hostile_input_fails_min_10(tmp_path):
    root = broken(tmp_path, "        try:\n            doc = json.loads(upload.data)",
                  "        if not upload.data:\n            while True:\n                pass\n"
                  "        try:\n            doc = json.loads(upload.data)",
                  toml=("[capabilities]", "[limits]\nhook_timeout_s = 1\n\n[capabilities]"))
    problems = failed(suite(root))
    assert "MIN-10" in problems and "timeout" in problems["MIN-10"][0]


def test_a_plugin_whose_own_tests_fail_fails_min_16(tmp_path):
    root = broken(tmp_path, "    assert isinstance(verdict, Refuse)\n",
                  "    assert not isinstance(verdict, Refuse)\n",
                  file="tests/test_application_gate.py")
    status, why = client.run_tests(root)
    assert status == "fail" and "failed" in why[0]
    assert failed(suite(root, run_tests=lambda: client.run_tests(root)))["MIN-16"]


def test_a_gate_no_test_names_fails_min_16(tmp_path):
    root = broken(tmp_path, "", "", file="tests/test_application_gate.py")
    tests = root / "tests" / "test_application_gate.py"
    tests.write_text(tests.read_text().replace("application-review.json", "the-review"))
    assert "names application-review.json" in failed(suite(root))["MIN-16"][0]


def test_an_open_cycling_mode_cannot_pass_until_core_can_hold_it(tmp_path):
    root = broken(tmp_path, '  key   = "draft"', '  key   = "draft"', file="yaaf-plugin.toml",
                  toml=('  open  = true\n', '  open  = true\n\n  [[kind.modes]]\n'
                                            '  key   = "keep"\n  shape = "runs_on_cycles"\n'
                                            '  open  = true\n'))
    assert "no cycle model" in failed(suite(root))["EXT-CYC-1"][0]


def test_the_author_and_the_install_reach_the_same_verdict(tmp_path):
    """The same broken plugin through LocalHost and through core's sandboxed
    pool (what the install uses): the same requirements fail."""
    from yaaf.plugins import host as H
    from yaaf.plugins.registry import conformance_results

    root = broken(tmp_path, *BREAKS[4][1:3])  # the action that lets a failure escape
    local = set(failed(suite(root)))
    installed = conformance_results(H.HostSpec.from_checkout(root))
    assert set(failed(installed)) == local and "EXT-ACT-2" in local


def test_the_full_local_check_leaves_only_what_an_install_decides_pending():
    report = api_report.full_check(EXAMPLE)
    pending = {r.id for r in report.results if r.status == "pending"}
    assert report.failures() == [] and pending == {"MIN-02", "MIN-03", "MIN-07"}
