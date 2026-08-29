"""Invariant selftests: the suite's own speed.

Everything else in this directory attacks a rule the product enforces. This one
attacks a rule the *test suite* enforces, for the same reason and in the same
shape: a convention that lives only in a document is a convention that erodes,
and the erosion here is measured in minutes of everybody's day.

Three patterns carry the suite's speed, and each was worth real time when it was
put in:

  * the app is built once per worker, not once per test  (~22s)
  * the schema is built once per worker, not once per test  (~130s on Postgres)
  * a fire drill runs once for the tests that only read its report  (~26s)

Each is easy to undo by accident, and undoing one is invisible — the suite still
passes, it just gets slower, and nobody notices until it is slow enough to
complain about. So they are checked here, statically, by reading the test tree
rather than by timing it. Timing assertions go flaky on a loaded CI runner and a
flaky gate is worse than no gate.

Adding a genuinely slow test is fine and always allowed. What is not allowed is
adding one *by accident*, which is what these catch.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

TESTS_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _test_modules() -> list[pathlib.Path]:
    return sorted(p for p in TESTS_ROOT.rglob("test_*.py"))


def _calls(tree: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)]


def _name_of(call: ast.Call) -> str:
    """`create_app(...)` -> "create_app"; `Base.metadata.create_all(...)` ->
    "create_all". The attribute chain is irrelevant to what we are asking."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _offenders(forbidden: str) -> list[str]:
    found = []
    for path in _test_modules():
        tree = ast.parse(path.read_text())
        for call in _calls(tree):
            if _name_of(call) == forbidden:
                found.append(f"{path.relative_to(TESTS_ROOT.parent)}:{call.lineno}")
    return found


def test_no_test_builds_its_own_app():
    """The app is a session cost, and `create_app()` measured at 27.7ms — call
    it per test and it is twenty-odd seconds of the suite spent registering the
    same routes onto a fresh object. The `client` fixture hands you the shared
    one; it holds no request state, so there is nothing to gain by rebuilding."""
    offenders = _offenders("create_app")
    assert not offenders, (
        "these call create_app() directly — use the `client` fixture instead, "
        f"which shares one app per worker: {offenders}")


@pytest.mark.parametrize("builder", ["create_all", "drop_all", "reset_for_tests"])
def test_no_test_builds_its_own_schema(builder):
    """Building 47 tables per test cost ~130 seconds on Postgres before the
    schema became a session cost. The `client` fixture gives you an empty
    database; if you need a *different* database, the fire drill's `swap_engine`
    is the supported way to get one and it gives the caller's engine back."""
    allowed = {
        # The drill's own harness: it deliberately builds a caller database to
        # prove the drill leaves it alone, and the migration tests exist
        # precisely to check what create_all does and does not cover.
        "tests/selftest_invariants/test_rehearsal.py",
        "tests/selftest_invariants/test_migration_chain.py",
        "tests/selftest_invariants/test_migration_ops.py",
    }
    offenders = [o for o in _offenders(builder)
                 if o.rsplit(":", 1)[0] not in allowed]
    assert not offenders, (
        f"these build a schema inside a test ({builder}) — the `client` fixture "
        f"already gives you an empty one: {offenders}")


def test_a_drill_is_not_re_run_for_a_report_that_is_only_read():
    """A fire drill is a whole project lifecycle, about three seconds. Seven
    tests once ran the identical default spec to produce seven identical
    dictionaries, which made this the slowest file in the suite and, under
    xdist, the floor for the whole run.

    A test asserting on a drill's SIDE EFFECT must still run its own — the
    effect happens once, and the shared fixture would hand it a report from a
    drill that already finished. Those are the ones listed here. Everything else
    reading a default-spec report should take the `report` fixture.
    """
    path = TESTS_ROOT / "selftest_invariants" / "test_rehearsal.py"
    tree = ast.parse(path.read_text())

    side_effect_tests = {
        "test_an_autonomous_dial_waits_for_nobody",          # compares two drills
        "test_it_runs_no_command_out_of_a_spec_file",         # watches for execution
        "test_the_drill_never_touches_the_caller_s_data",     # inspects the caller db
        "test_the_drill_builds_no_workspace_in_the_live_root",
        "test_it_refuses_to_run_against_a_database_it_did_not_create",
        "test_a_state_root_it_cannot_write_never_stops_it",   # needs its monkeypatch live
        "test_a_drill_never_delivers_a_notice_to_the_chairman_it_invented",
        "test_it_gives_the_notification_channels_back_including_the_ones_that_were_unset",
        "test_it_gives_the_state_roots_back_including_the_ones_that_were_unset",
    }

    offenders = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name.startswith("test_")):
            continue
        if node.name in side_effect_tests:
            continue
        for call in _calls(node):
            # rehearse(spec()) with no overrides is the shared drill exactly.
            if _name_of(call) != "rehearse" or len(call.args) != 1:
                continue
            inner = call.args[0]
            if (isinstance(inner, ast.Call) and _name_of(inner) == "spec"
                    and not inner.args and not inner.keywords):
                offenders.append(f"{node.name}:{call.lineno}")

    assert not offenders, (
        "these run the default fire drill for themselves — take the `report` "
        "fixture instead, or add the test to `side_effect_tests` above with a "
        f"note saying which side effect it needs: {offenders}")
