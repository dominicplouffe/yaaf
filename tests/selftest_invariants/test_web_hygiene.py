"""Rules about the cockpit's source that nothing else can catch.

TypeScript compiles a React component declared inside another component
happily, the suite stays green, and the screen misbehaves in a way that only
shows up under a human's hands. So the rule is a refusal, in the same spirit as
`test_suite_hygiene.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

WEB_SRC = Path(__file__).resolve().parents[2] / "web" / "src"

#: An indented declaration of a capitalised name — `function Foo(` or
#: `const Foo = (`. Indented means "inside something", and something, in a
#: .tsx file, is almost always another component's render body.
NESTED_DECL = re.compile(
    r"^[ \t]+(?:function|const)\s+([A-Z][A-Za-z0-9]*)\s*(?:=\s*)?[({<]",
    re.MULTILINE,
)


def _tsx_files() -> list[Path]:
    return sorted(WEB_SRC.rglob("*.tsx"))


def test_the_web_source_is_where_we_think_it_is():
    """A rule that silently stops scanning anything is not a rule."""
    assert WEB_SRC.is_dir(), f"{WEB_SRC} is missing — this file's rules scan nothing"
    assert len(_tsx_files()) > 10, "too few .tsx files found; the glob is wrong"


def test_no_component_is_declared_inside_another_component():
    """A component declared in a render body is a *new type* on every render,
    so React unmounts and rebuilds its DOM subtree rather than updating it.
    Anything the browser keeps in the DOM instead of in React state is thrown
    away with it: `<details open>`, an uncontrolled input's text, a scroll
    offset, focus, a running CSS transition.

    This shipped once. `TaskDetails` lived inside `Work`, which polls every six
    seconds; every tick snapped all forty task drawers shut, the page lost two
    thirds of its height, and the browser clamped the reader to the bottom of
    what was left. Nothing failed — not tsc, not this suite. The chairman found
    it by being thrown to the end of the page every few seconds.

    Hoist it to module scope. If it needs the parent's state, take it as props.
    """
    offenders: list[str] = []
    for path in _tsx_files():
        source = path.read_text()
        for match in NESTED_DECL.finditer(source):
            name = match.group(1)
            # Rendered as an element somewhere in this file is what makes it a
            # component rather than a capitalised constant or a local type.
            if re.search(rf"<{name}[\s/>]", source):
                line = source[: match.start()].count("\n") + 1
                offenders.append(
                    f"{path.relative_to(WEB_SRC.parents[1])}:{line} — <{name}>"
                )

    assert not offenders, (
        "these components are declared inside another component, so React "
        "rebuilds their DOM on every parent render:\n  "
        + "\n  ".join(offenders)
        + "\nMove them to module scope and pass what they need as props."
    )
