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

from yaaf.core.models import FactoryPhase, PpcMode, ProjectMode

#: Every shape a project may be created as, in the order the form offers them.
MODES = (ProjectMode.EXPLORE, ProjectMode.DELIVER, ProjectMode.OPERATE,
         ProjectMode.CODE_FACTORY, ProjectMode.PPC_FACTORY)
MODE_PATTERN = "^(" + "|".join(m.value for m in MODES) + ")$"


def _mode_phase(project: Any, phase: str | None = None) -> tuple[str, str | None]:
    """The mode and the second word that decides its shape: a Code Factory's
    phase, or an Amazon PPC Factory's mode (audit or operate). A project has
    at most one of the two, so one slot carries whichever it has."""
    if isinstance(project, str):
        return project, phase
    if isinstance(project, dict):
        mode = str(project.get("mode") or ProjectMode.EXPLORE)
        key = "ppc_mode" if mode == ProjectMode.PPC_FACTORY else "factory_phase"
        return mode, project.get(key, phase)
    mode = str(project.mode)
    if mode == ProjectMode.PPC_FACTORY:
        return mode, getattr(project, "ppc_mode", None)
    return mode, getattr(project, "factory_phase", None)


#: How a Code Factory may start. `repo_build` adopts a repo the way `repo`
#: does — same intake, same crew's eyes on code that already has users — but
#: owes a build first: a feature, a rewrite, a launch, paced by a budget for
#: that job, and handed over to upkeep the way a new build is. Without it the
#: only way to ask a kept repo for something big was a second project on the
#: same repo, two crews and two code-health ratchets fighting over one main.
FACTORY_STARTS = ("new", "repo", "repo_build")
ADOPTING_STARTS = ("repo", "repo_build")


def adopts_repo(factory_start: str | None) -> bool:
    return factory_start in ADOPTING_STARTS


def initial_phase(mode: str, factory_start: str | None) -> str | None:
    """A factory that starts from nothing is built first, and so is one that
    adopts a repo to build something on it; one that adopts a repo to keep it
    already has users, so it starts in upkeep. Other modes have none."""
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
    is the hand-over to upkeep rather than the end of the project; an Amazon
    PPC audit, whose finish line is its report."""
    mode, phase = _mode_phase(project, phase)
    if mode == ProjectMode.DELIVER:
        return True
    if mode == ProjectMode.PPC_FACTORY:
        return phase != PpcMode.OPERATE
    return mode == ProjectMode.CODE_FACTORY and phase != FactoryPhase.UPKEEP


def runs_on_cycles(project: Any, phase: str | None = None) -> bool:
    """Turns on a rhythm, with standing obligations judged at each roll.
    Operate always; a Code Factory once it is in upkeep; an Amazon PPC
    Factory operating an account to its goals."""
    mode, phase = _mode_phase(project, phase)
    if mode == ProjectMode.OPERATE:
        return True
    if mode == ProjectMode.PPC_FACTORY:
        return phase == PpcMode.OPERATE
    return mode == ProjectMode.CODE_FACTORY and phase == FactoryPhase.UPKEEP


#: The one role that leads a Code Factory, and leads nothing else. The kind
#: answers who runs it, so the server fills it and refuses any other.
FACTORY_LEAD_ROLE = "tech-lead"


def is_factory(project: Any) -> bool:
    mode, _ = _mode_phase(project)
    return mode == ProjectMode.CODE_FACTORY


#: How an Amazon PPC Factory may run, in the order the form offers them, and
#: the ones a project may be created as today. Operate is answered everywhere
#: (shape, form, founder) so nothing has to be found again when it opens, and
#: refused at creation until its goals are boundaries the server enforces —
#: an account run to a target ACOS by a prompt is the thing this platform
#: exists not to ship.
PPC_MODES = (PpcMode.AUDIT, PpcMode.OPERATE)
PPC_MODES_OPEN = (PpcMode.AUDIT,)

#: The one role that leads an Amazon PPC Factory, and leads nothing else, for
#: the reason FACTORY_LEAD_ROLE is the Code Factory's.
PPC_LEAD_ROLE = "ppc-lead"


def is_ppc_factory(project: Any) -> bool:
    mode, _ = _mode_phase(project)
    return mode == ProjectMode.PPC_FACTORY


def ppc_mode(project: Any) -> str | None:
    """audit | operate for an Amazon PPC Factory, None for anything else."""
    mode, phase = _mode_phase(project)
    if mode != ProjectMode.PPC_FACTORY:
        return None
    return phase or PpcMode.AUDIT


def initial_ppc_mode(mode: str, requested: str | None) -> str | None:
    """An Amazon PPC Factory that names no mode is an audit: it is the one
    that is open, and the one every account should have before it is run."""
    if str(mode) != ProjectMode.PPC_FACTORY:
        return None
    return requested or PpcMode.AUDIT


def lead_role(mode: str) -> str | None:
    """The role a kind of project is always led by, or None when the chairman
    picks. Asked by the server, the form and the founder alike."""
    return {ProjectMode.CODE_FACTORY: FACTORY_LEAD_ROLE,
            ProjectMode.PPC_FACTORY: PPC_LEAD_ROLE}.get(str(mode))


def label(mode: str) -> str:
    return {"code_factory": "Code Factory",
            "ppc_factory": "Amazon PPC Factory"}.get(str(mode), str(mode).title())


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
