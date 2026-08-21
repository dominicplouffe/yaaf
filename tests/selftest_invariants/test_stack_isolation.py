"""Two stacks on one box must not touch each other's containers.

The scenario this exists for: a git worktree runs a branch beside the live
stack, and its database is a restored dump of the live one — so the project
ids and slugs are IDENTICAL. Every lookup that keys on a project is therefore
ambiguous across stacks, and the failure is not a crash but a live chat
session being read as a zombie and removed.

The rest is a regression guard on the compose file: each of those values was
hardcoded once, and re-hardcoding any of them silently restores the collision.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from yaaf.core.config import settings
from yaaf.runner import sandbox

COMPOSE = Path(__file__).resolve().parents[2] / "deploy" / "docker-compose.yml"


@pytest.fixture
def stack(monkeypatch):
    """Point the process at a named stack, restored after the test.

    Deliberately NOT settings.cache_clear(): the cached Settings holds this
    run's database URL and temp workspace roots, handed to it by conftest
    before any yaaf import resolved. Dropping the cache mid-suite rebuilds it
    from a bare environment, and later tests get a different database — which
    surfaced as an unrelated websocket test failing about half the time."""

    def _set(name: str) -> str:
        monkeypatch.setattr(settings(), "stack", name)
        return name

    return _set


def _uncommented(text: str) -> str:
    """Compose comments carry example values on purpose; only the live YAML
    is being asserted on here."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _literals(text: str) -> str:
    """The YAML with every ``${VAR:-default}`` removed, so a name that survives
    is one nothing can override. The defaults are supposed to be today's
    values — finding `yaaf-sandbox:latest` inside a substitution is the fix
    working, and finding it outside one is the bug coming back."""
    return re.sub(r"\$\{[^}]*\}", "", _uncommented(text))


# ── the container boundary ──────────────────────────────────────────────────


def test_default_stack_keeps_todays_chat_container_name(stack):
    """A session running across this upgrade is adopted by name, so the name
    on the default stack may not move."""
    stack("deploy")
    assert sandbox.chat_container_name("fpl-manager") == "yaaf-chat-fpl-manager"


def test_second_stack_gets_its_own_chat_container_name(stack):
    """Docker names are unique per daemon: same slug, two stacks, one name is
    a fight over a handle rather than isolation."""
    stack("modes")
    assert sandbox.chat_container_name("fpl-manager") == "yaaf-chat-modes-fpl-manager"


def test_every_container_carries_the_stack_label(stack):
    stack("modes")
    assert sandbox._stack_labels()[sandbox.STACK_LABEL] == "modes"


def test_stack_label_does_not_displace_the_chat_labels(stack):
    """The chat labels bind a container to its run; the stack label is added
    beside them, never instead of them."""
    stack("modes")
    labels = sandbox._stack_labels(
        {sandbox.CHAT_LABEL: "proj_abc", sandbox.CHAT_RUN_LABEL: "run_1"}
    )
    assert labels == {
        sandbox.STACK_LABEL: "modes",
        sandbox.CHAT_LABEL: "proj_abc",
        sandbox.CHAT_RUN_LABEL: "run_1",
    }


def test_chat_lookup_is_scoped_to_this_stack(stack):
    """The whole point: a restored dump shares project ids, so filtering on
    the project alone finds the OTHER stack's live session."""
    stack("modes")
    filters = sandbox._chat_filters("proj_abc")
    assert filters == {"label": ["yaaf.chat.project=proj_abc", "yaaf.stack=modes"]}


def test_two_stacks_never_produce_the_same_chat_filter(stack):
    """Same project id, different stack — the filters must differ, or one
    supervisor reaps the other's container."""
    stack("deploy")
    live = sandbox._chat_filters("proj_abc")
    stack("modes")
    twin = sandbox._chat_filters("proj_abc")
    assert live != twin


# ── the compose file ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "hardcoded, why",
    [
        ("yaaf-sandbox:latest", "a rebuild would change the other stack's next run"),
    ],
)
def test_compose_does_not_hardcode_stack_owned_names(hardcoded, why):
    assert hardcoded not in _literals(COMPOSE.read_text()), (
        f"{hardcoded!r} is hardcoded again: {why}"
    )


def test_compose_state_paths_are_all_parameterised():
    """Bare /var/lib/yaaf paths are host bind mounts two runners would share.
    The container side counts too — the runner hands these to the Docker
    daemon as HOST paths, so a mismatch mounts the other stack's workspace."""
    bare = re.findall(r"/var/lib/yaaf\b\S*", _literals(COMPOSE.read_text()))
    assert not bare, f"unparameterised state paths: {bare}"


def test_compose_sandbox_network_is_parameterised():
    """Declared with an explicit name, so compose does not prefix it per
    project — shared, it is one network and one `compose down` away from
    taking the live stack's sandboxes with it."""
    yaml = _uncommented(COMPOSE.read_text())
    assert "${YAAF_SANDBOX_NETWORK:-yaaf_sandbox}" in yaml
    assert "\n    name: yaaf_sandbox\n" not in yaml


def test_compose_project_name_defaults_to_todays_implicit_one():
    """`deploy` is the directory basename, which is what compose has been
    using all along — so an existing box keeps its containers and its
    Postgres volume rather than silently starting empty."""
    yaml = _uncommented(COMPOSE.read_text())
    assert "name: ${YAAF_STACK:-deploy}" in yaml
