"""What the Founder is doing right now, in-memory only.

A drafting turn takes minutes — a measured one took 319 seconds — and for all
of that time the only thing the chairman could see was a spinner. His question
was "looks to me like we are stuck", and nothing in the product could answer
it. That is the whole reason this module exists:

    "I don't mind how long it takes as long as it is accurate. the user needs
     to get feedback that something is happening and what we are doing."

So: no cap on how long a turn may run, and a running commentary while it does.

Ephemeral on purpose, the way `yaaf.server.typing_state` is. Progress means
nothing once the turn ends — the transcript and the draft are the record — so
it never touches the database. The API process is one process
(`uvicorn.run(create_app(), …)`), the SPA polls it, and losing the process
loses nothing but an animation.

Steps are labelled in the chairman's language rather than the tool's. He is
being told what his project's designer is doing, not read a call log.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

#: How long a finished turn's steps stay readable. Long enough that the last
#: poll after a turn ends still finds them; short enough to be forgotten.
_KEEP_SECONDS = 120.0

#: Nothing at all for this long means the turn is wedged rather than working.
#: It has to clear the fire drill's own 180s ceiling, because a drill that
#: takes its full time is working perfectly.
STALL_SECONDS = 300.0

#: What each tool is doing, said in the chairman's terms.
LABELS = {
    "search_roles": "Reading the role library",
    "search_crews": "Reading the crew library",
    "read_source": "Reading a source you pointed me at",
    "grade_goal": "Grading the goal against the grammar",
    "list_credentials": "Checking what can pay for this project",
    "list_integrations": "Checking which of your tools it can use",
    "propose_bundle": "Writing the draft",
    "rehearse": "Running the fire drill on the draft",
    "ask": "Writing your next questions",
}


@dataclass
class Step:
    label: str
    state: str = "running"  # running | done | failed
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "state": self.state,
            "seconds": round((self.finished_at or time.time()) - self.started_at, 1),
        }


@dataclass
class Turn:
    steps: list[Step] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    running: bool = True
    ended_at: float | None = None
    # Carried here as well as on the row: the page sees a failure the moment it
    # happens rather than waiting for the next full fetch.
    error: str | None = None
    kind: str | None = None
    # The round of questions, the moment `ask` is called rather than when the
    # turn commits: the draft takes minutes more, and that is reading time.
    round: dict | None = None


_lock = threading.Lock()
_turns: dict[str, Turn] = {}


def _reap(now: float) -> None:
    """Called under the lock, on every write. No janitor for two dictionaries."""
    for key, turn in list(_turns.items()):
        if not turn.running and turn.ended_at and now - turn.ended_at > _KEEP_SECONDS:
            del _turns[key]


def begin(thread_id: str) -> None:
    """Start a turn, discarding whatever the previous one left."""
    with _lock:
        _reap(time.time())
        _turns[thread_id] = Turn(steps=[Step("Thinking about what you asked for")])


def start_step(thread_id: str, label: str) -> None:
    """Open a step, closing whichever one was open."""
    with _lock:
        turn = _turns.get(thread_id)
        if turn is None:
            return
        now = time.time()
        turn.last_activity = now
        for step in turn.steps:
            if step.state == "running":
                step.state, step.finished_at = "done", now
        turn.steps.append(Step(label))


def finish_step(thread_id: str, *, ok: bool = True) -> None:
    with _lock:
        turn = _turns.get(thread_id)
        if turn is None:
            return
        now = time.time()
        turn.last_activity = now
        for step in reversed(turn.steps):
            if step.state == "running":
                step.state = "done" if ok else "failed"
                step.finished_at = now
                break


def note(thread_id: str, text: str) -> None:
    """The Founder's own words, mid-turn.

    The most honest progress signal there is: when the model says what it is
    about to do, that sentence beats any label this module could invent.
    """
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if first:
        start_step(thread_id, first[:160])


def set_round(thread_id: str, asked: dict) -> None:
    with _lock:
        turn = _turns.get(thread_id)
        if turn is not None:
            turn.round = asked


def touch(thread_id: str) -> None:
    """Something happened. Only silence counts against the stall watchdog."""
    with _lock:
        turn = _turns.get(thread_id)
        if turn is not None:
            turn.last_activity = time.time()


def end(thread_id: str, *, ok: bool = True, error: str | None = None,
        kind: str | None = None) -> None:
    with _lock:
        turn = _turns.get(thread_id)
        if turn is None:
            return
        turn.error, turn.kind = error, kind
        now = time.time()
        for step in turn.steps:
            if step.state == "running":
                step.state = "done" if ok else "failed"
                step.finished_at = now
        turn.running = False
        turn.ended_at = now


def is_running(thread_id: str) -> bool:
    """Whether a turn is in flight — the guard against two at once."""
    with _lock:
        turn = _turns.get(thread_id)
        return turn is not None and turn.running


def stalled_for(thread_id: str) -> float:
    """Seconds since anything last happened. 0.0 when no turn is running."""
    with _lock:
        turn = _turns.get(thread_id)
        if turn is None or not turn.running:
            return 0.0
        return time.time() - turn.last_activity


def current_label(thread_id: str) -> str:
    """What it was doing — so a stall says which step it died on."""
    with _lock:
        turn = _turns.get(thread_id)
        if turn is None or not turn.steps:
            return "starting up"
        for step in reversed(turn.steps):
            if step.state == "running":
                return step.label
        return turn.steps[-1].label


def read(thread_id: str) -> dict:
    """What the page polls for."""
    with _lock:
        turn = _turns.get(thread_id)
        if turn is None:
            return {"running": False, "steps": [], "seconds": 0.0,
                    "error": None, "kind": None, "round": None}
        return {
            "running": turn.running,
            "seconds": round((turn.ended_at or time.time()) - turn.started_at, 1),
            "steps": [step.as_dict() for step in turn.steps],
            "error": turn.error,
            "kind": turn.kind,
            "round": turn.round,
        }


def clear() -> None:
    """Tests only — one process, one dictionary, no leaking between cases."""
    with _lock:
        _turns.clear()
