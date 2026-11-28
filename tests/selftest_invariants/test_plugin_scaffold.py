"""`yaaf plugin new` writes a plugin that already passes the spec.

A scaffold that only looked right would teach authors the wrong shape, so the
promise is the strict one: every combination of flags writes a repo that passes
the full conformance suite, its own tests included, with only what an install
decides left pending. The everything-on scaffold also installs into yaaf and
becomes ready, so its lead role passes the library's real rules. Bad option
combinations are refused before a file is written.

Each combination runs the suite through a real host, about 2 s.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner
from yaaf_plugin_api import report, scaffold

from tests.conftest import ADMIN
from yaaf.cli import main as cli
from yaaf.core.config import settings
from yaaf.plugins import registry

INSTALL_DECIDES = {"MIN-02", "MIN-03", "MIN-07"}
EVERYTHING = dict(artifacts=["application.json"], review=True, pages=True, brief=True,
                  plan_gate=True, completion_gate=True, view=True, lead=True,
                  create_fields=True, receipts=True, action="pull", action_tool="get_call",
                  setup_step="source")
COMBOS = {
    "bare": {},
    "one-gate": {"artifacts": ["report.json"]},
    "two-gates-and-pages": {"artifacts": ["brief.json", "plan.json"], "pages": True},
    "reviewed": {"artifacts": ["report.json"], "review": True, "completion_gate": True},
    "data-only": {"receipts": True, "action": "pull", "action_tool": "get_rows",
                  "setup_step": "connect", "view": True},
    "everything": EVERYTHING,
}


@pytest.mark.parametrize("flags", COMBOS.values(), ids=COMBOS.keys())
def test_every_scaffold_passes_the_full_suite_out_of_the_box(tmp_path, flags):
    root = tmp_path / "plugin"
    scaffold.write(root, scaffold.Options("grant-writer", **flags))
    full = report.full_check(root)
    failed = {r.id: r.why for r in full.results if r.status == "fail"}
    assert failed == {}
    assert {r.id for r in full.results if r.status == "pending"} == INSTALL_DECIDES
    assert next(r for r in full.results if r.id == "MIN-16").status == "pass"


def test_the_everything_scaffold_installs_and_becomes_ready(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings(), "plugins_root", str(tmp_path / "installed"))
    monkeypatch.setattr(settings(), "allow_local_plugins", True)
    root = tmp_path / "plugin"
    scaffold.write(root, scaffold.Options("grant-writer", **EVERYTHING))
    body = client.post("/admin/plugins/local", json={"path": str(root)}, headers=ADMIN).json()
    try:
        assert body["status"] == "ready", [(r["id"], r["why"]) for r in body["report"]["results"]
                                           if r["status"] not in ("pass", "n/a")]
        kinds = client.get("/admin/kinds", headers=ADMIN).json()["kinds"]
        [kind] = [k for k in kinds if k["kind"] == "plugin:grant_writer"]
        assert kind["lead"] == "grant-writer-lead"
        assert [f["key"] for f in kind["fields"]] == ["audience"]
    finally:
        registry.close_all()


@pytest.mark.parametrize(("flags", "why"), [
    ({"review": True}, "need an --artifact"),
    ({"pages": True}, "need an --artifact"),
    ({"action": "pull"}, "needs --action-tool"),
    ({"artifacts": ["../escape.json"]}, "plain .json file name"),
    ({"artifacts": ["a.json", "a.json"]}, "named twice"),
    ({"kind_key": "deliver"}, "built-in mode"),
    ({"setup_step": "Not A Slug"}, "--setup-step must match"),
], ids=["review", "pages", "action", "path", "twice", "core-kind", "step"])
def test_a_combination_that_cannot_work_is_refused_before_a_file_is_written(tmp_path, flags,
                                                                           why):
    with pytest.raises(scaffold.ScaffoldProblem, match=why):
        scaffold.write(tmp_path / "plugin", scaffold.Options("grant-writer", **flags))
    assert not (tmp_path / "plugin").exists()


def test_a_scaffold_never_writes_over_something(tmp_path):
    (tmp_path / "plugin").mkdir()
    (tmp_path / "plugin" / "mine.txt").write_text("keep")
    with pytest.raises(scaffold.ScaffoldProblem, match="not empty"):
        scaffold.write(tmp_path / "plugin", scaffold.Options("grant-writer"))
    with pytest.raises(scaffold.ScaffoldProblem, match="plugin id"):
        scaffold.write(tmp_path / "other", scaffold.Options("Bad Id"))


def test_yaaf_plugin_new_writes_one_and_checks_it(tmp_path):
    runner = CliRunner()
    out = runner.invoke(cli.app, ["plugin", "new", str(tmp_path / "p"), "--id", "grant-writer",
                                  "--artifact", "report.json", "--review", "--view"])
    assert out.exit_code == 0, out.output
    assert "static check: pending" in out.output
    assert (tmp_path / "p" / "fixtures/artifacts/refuse/report-review.json").is_dir()
    out = runner.invoke(cli.app, ["plugin", "new", str(tmp_path / "p"), "--id", "grant-writer"])
    assert out.exit_code == 2 and "not empty" in out.output


def test_two_checkouts_with_one_package_name_are_each_checked_as_themselves(tmp_path):
    """Two versions of one plugin share a package name. The check imported the
    first one's code for the second, and found the second's hooks missing."""
    first, second = tmp_path / "v1", tmp_path / "v2"
    scaffold.write(first, scaffold.Options("grant-writer"))
    scaffold.write(second, scaffold.Options("grant-writer", artifacts=["report.json"],
                                            brief=True))
    for root in (first, second, first):
        assert report.check(root).failures() == [], root
