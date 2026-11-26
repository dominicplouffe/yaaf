"""The three questions every mode branch asks, answered in one place.

The code never switched over modes. It asked three yes/no questions — does this
project have scope, does it ship once to a date, does it run on cycles — and
answered each with a mode comparison at the call site. That held while each
mode answered all three the same way for its whole life. Code Factory does not:
it ships to a date while it is being built and runs on cycles once it is kept,
so `mode == OPERATE` asked at forty sites would have been forty places to
forget the second half.

Pure functions over a mode string and a phase, so the constitution, the
founder and the rehearsal — none of which hold a Project row — ask the same
question the server does.
"""
from __future__ import annotations

from typing import Any

from yaaf.core.models import FactoryPhase, ProjectMode

#: Every shape a project may be created as, in the order the form offers them.
MODES = (ProjectMode.EXPLORE, ProjectMode.DELIVER, ProjectMode.OPERATE,
         ProjectMode.CODE_FACTORY)
MODE_PATTERN = "^(" + "|".join(m.value for m in MODES) + ")$"


def _mode_phase(project: Any, phase: str | None = None) -> tuple[str, str | None]:
    if isinstance(project, str):
        return project, phase
    if isinstance(project, dict):
        return str(project.get("mode") or ProjectMode.EXPLORE), \
            project.get("factory_phase", phase)
    return str(project.mode), getattr(project, "factory_phase", None)


def initial_phase(mode: str, factory_start: str | None) -> str | None:
    """A factory that starts from nothing is built first; one that adopts a
    repo already has users, so it starts in upkeep. Other modes have none."""
    if str(mode) != ProjectMode.CODE_FACTORY:
        return None
    return FactoryPhase.UPKEEP if factory_start == "repo" else FactoryPhase.BUILD


def has_scope(project: Any) -> bool:
    """Explore is the open-ended arm; everything else owes a plan."""
    mode, _ = _mode_phase(project)
    return mode != ProjectMode.EXPLORE


def ships_once(project: Any, phase: str | None = None) -> bool:
    """Has one finish line a date can promise and a completion can close.
    Deliver always; a Code Factory while it is being built, whose finish line
    is the hand-over to upkeep rather than the end of the project."""
    mode, phase = _mode_phase(project, phase)
    if mode == ProjectMode.DELIVER:
        return True
    return mode == ProjectMode.CODE_FACTORY and phase != FactoryPhase.UPKEEP


def runs_on_cycles(project: Any, phase: str | None = None) -> bool:
    """Turns on a rhythm, with standing obligations judged at each roll.
    Operate always; a Code Factory once it is in upkeep."""
    mode, phase = _mode_phase(project, phase)
    if mode == ProjectMode.OPERATE:
        return True
    return mode == ProjectMode.CODE_FACTORY and phase == FactoryPhase.UPKEEP


def is_factory(project: Any) -> bool:
    mode, _ = _mode_phase(project)
    return mode == ProjectMode.CODE_FACTORY


def label(mode: str) -> str:
    return {"code_factory": "Code Factory"}.get(str(mode), str(mode).title())


def behaves_as(project: Any, phase: str | None = None) -> str:
    """The older mode this project reads as right now, for the readers that
    render one of three shapes — the review headline and board above all. A
    factory being built reads as Deliver; one being kept reads as Operate."""
    mode, phase = _mode_phase(project, phase)
    if runs_on_cycles(mode, phase):
        return ProjectMode.OPERATE
    if ships_once(mode, phase):
        return ProjectMode.DELIVER
    return mode
