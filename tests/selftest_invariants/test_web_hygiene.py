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


#: A colour written as a hex literal has one value in both themes, and one of
#: the two will be wrong.
HEX_IN_PAINT = re.compile(
    r"(?:fill|stroke|stopColor|color|background|borderColor|borderTopColor)"
    r"\s*[:=]\s*[\"'`{]?\s*#[0-9a-fA-F]{3,8}\b"
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


def test_no_chart_paints_with_a_hardcoded_colour():
    """Every colour in the cockpit comes from a semantic custom property, so
    light and dark are one decision made in `styles.css` rather than a decision
    re-made in every SVG.

    The burn-up is why this is a rule rather than a habit. It draws nine
    strokes and a fill, and a single `#8ca0f0` among them would be invisible in
    the light theme, correct in the dark one, and unreviewable in a diff — the
    failure only shows up under a reader's eyes, on one of the two themes,
    which is exactly the class of bug this file exists to refuse.
    """
    offenders: list[str] = []
    for path in _tsx_files():
        source = path.read_text()
        for match in HEX_IN_PAINT.finditer(source):
            line = source[: match.start()].count("\n") + 1
            offenders.append(f"{path.relative_to(WEB_SRC.parents[1])}:{line}")

    assert not offenders, (
        "these paint a colour from a hex literal instead of a semantic custom "
        "property, so one of the two themes is wrong:\n  " + "\n  ".join(offenders)
    )


def test_the_burn_up_is_drawn_for_deliver_projects_only():
    """A burn-up of work that recurs is a flat line — `velocity.standing_record`
    says so in its own docstring, and an Operate project already has the record
    that carries the information drawn above this.

    Worse than useless, it was wrong: an Operate project's ceiling is the open
    cycle's scope while its met-events span every cycle it has run, so the line
    climbed through the top of its own axis every turn of the rhythm. The
    server no longer feeds it that, but the gate is one clause in one JSX
    expression and a later refactor would drop it with nothing to notice.
    """
    source = (WEB_SRC / "pages" / "project" / "Plan.tsx").read_text()
    call = re.search(r"\{[^{}]*<Burnup\b", source, re.DOTALL)
    assert call, "<Burnup> is no longer rendered from a guarded expression"
    assert 'mode === "deliver"' in call.group(0), (
        "the burn-up is rendered without the Deliver gate — see "
        "velocity.standing_record for why a recurring project must not get one"
    )


def test_nothing_that_only_wants_reading_sits_under_the_decision_heading():
    """The desk's decide zone holds only what decides itself if ignored.

    An approval denies itself in hours; a question falls back to the CEO's
    stated default. A raised flag does neither — it proceeds on the CEO's
    proposal whether or not anybody answers, which `policy.awaiting_chairman`
    says in as many words: every member of it is "a decision the dial left with
    the chairman", and one member exempt from that "would make the emptiness
    unreadable for all of them".

    Flags were pushed into that zone anyway, under a heading claiming they were
    waiting on the reader, with a sentence beneath them admitting they were
    not. On an autonomous project that was the entire desk: a count of one and
    nothing the reader could do about it. The rule is mechanical because the
    argument for breaking it is always available and always wrong.
    """
    source = (WEB_SRC / "pages" / "project" / "Overview.tsx").read_text()
    start = source.index("const needs: React.ReactNode[] = []")
    end = source.index("const news: React.ReactNode[] = []")
    assert start < end, "the desk's two zones are no longer in the expected order"
    decide_zone = source[start:end]
    assert "rawFlags" not in decide_zone, (
        "a flag is being pushed into the zone that needs a decision. A flag "
        "does not block and does not expire — it belongs with the news, which "
        "is the `news` array below it."
    )
