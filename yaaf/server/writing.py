"""What agents write, measured by who it was written for — and the opening line
that lets a reader stop there.

The guidance on how to write lives in the prompt
(`contextpack.WRITING_FOR_THE_READER`) and is deliberately only guidance: the
chairman ruled out word caps. A rule without a ceiling needs a way to see
whether it is being followed, so this module gives the CEO's review the facts
a manager would coach from — how long each reader's files run, whether a
person opened the ones written for him, and which writer is far from the
team's norm. Nothing here refuses anything.

`opening` is the other half: the first lines of a file, lifted at publish, so
the announcement of a deliverable can say what it found. A reader who gets the
answer in the notification only opens the file when he wants more.
"""

from __future__ import annotations

import re
import statistics
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core.models import Agent, Artifact, Project, ResultFor, Run, Task
from yaaf.server import plain

#: Files that are prose, read rather than processed.
PROSE_EXTENSIONS = (".md", ".markdown", ".txt", ".text", ".rst", ".html", ".htm")

#: How much of a file's opening an announcement quotes. About two sentences —
#: enough for an answer and its number, short enough to read in a notification.
OPENING_CHARS = 280

#: A writer is shown to the CEO when their files run this many times the
#: project's typical length. Relative on purpose: what is long depends on the
#: work, and the team's own norm is the fairest measure of it.
OUTLIER_RATIO = 2.5
#: …and only with enough files to call it a habit rather than one long piece.
OUTLIER_MIN_FILES = 3

#: Bigger than any prose file worth reading; a file past it is not read here.
_READ_LIMIT = 4 * 1024 * 1024

_FRONT_MATTER = re.compile(r"\A---\n.*?\n---\n", re.S)
_TAG = re.compile(r"<[^>]+>")
_MD_DECOR = re.compile(r"(\*\*|__|`|\*|_)(?=\S)|(?<=\S)(\*\*|__|`|\*|_)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_SPACE = re.compile(r"\s+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def is_prose(name: str) -> bool:
    return name.lower().endswith(PROSE_EXTENSIONS)


def _text(name: str, data: bytes) -> str | None:
    if not is_prose(name) or len(data) > _READ_LIMIT:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if name.lower().endswith((".html", ".htm")):
        text = _TAG.sub("\n", text)
    return text


def words(name: str, data: bytes) -> int | None:
    """Words in a prose file, counted as the chairman's messages are; None for
    anything that is not prose."""
    text = _text(name, data)
    return None if text is None else plain.words(text)


def opening(name: str, data: bytes) -> str | None:
    """The first real paragraph of a prose file — past any title, front
    matter, table or code — cut at a sentence end near `OPENING_CHARS`."""
    text = _text(name, data)
    if not text:
        return None
    text = _FRONT_MATTER.sub("", text.replace("\r\n", "\n"))
    paragraph: list[str] = []
    in_code = False
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if not stripped:
            if paragraph:
                break
            continue
        # Titles, rules, tables and front-matter-ish key lines are not an answer.
        if stripped.startswith(("#", "|", "---", "===", "<!--")):
            if paragraph:
                break
            continue
        paragraph.append(stripped.lstrip("-*> ").strip())
    if not paragraph:
        return None
    flat = _SPACE.sub(" ", _MD_DECOR.sub("", _LINK.sub(r"\1", " ".join(paragraph)))).strip()
    if len(flat) <= OPENING_CHARS:
        return flat
    cut = ""
    for sentence in _SENTENCE_END.split(flat):
        if cut and len(cut) + 1 + len(sentence) > OPENING_CHARS:
            break
        cut = f"{cut} {sentence}".strip()
    if not cut or len(cut) > OPENING_CHARS:
        cut = flat[:OPENING_CHARS].rsplit(" ", 1)[0] + " …"
    return cut


def compose(session: Session, project: Project, since: datetime, until: datetime) -> dict:
    """The writing record for one review window: prose files published, by
    who they were written for, how long they ran, whether a person opened the
    ones written for one, and the writers far from the team's norm.

    Who a file was for comes from its task (`Task.result_for`); a file whose
    run held no task counts as the team's, the default reader."""
    from yaaf.core.artifacts import store

    rows = list(session.execute(
        select(Artifact).where(Artifact.project_id == project.id,
                               Artifact.published_at > since,
                               Artifact.published_at <= until)
        .order_by(Artifact.published_at)
    ).scalars())
    rows = [a for a in rows if is_prose(a.name)]
    run_ids = {a.run_id for a in rows if a.run_id}
    task_of_run: dict[str, str] = {}
    if run_ids:
        for run_id, task_id in session.execute(
            select(Run.id, Run.task_id).where(Run.id.in_(run_ids), Run.task_id.is_not(None))
        ).all():
            task_of_run[run_id] = task_id
        for task_id, run_id in session.execute(
            select(Task.id, Task.claim_run_id).where(Task.claim_run_id.in_(run_ids))
        ).all():
            task_of_run.setdefault(run_id, task_id)
    reader_of_task = dict(session.execute(
        select(Task.id, Task.result_for).where(Task.id.in_(set(task_of_run.values())))
    ).all()) if task_of_run else {}
    names = dict(session.execute(
        select(Agent.id, Agent.name).where(Agent.project_id == project.id)).all())

    files = []
    for a in rows:
        try:
            n = words(a.name, store().get_bytes(a.storage_key))
        except (FileNotFoundError, ValueError, OSError):
            n = None
        if n is None:
            continue
        reader = reader_of_task.get(task_of_run.get(a.run_id or "")) or ResultFor.TEAM
        files.append({"name": a.name, "words": n, "reader": reader, "opens": a.opens or 0,
                      "by": a.published_by_agent_id})

    def summary(group: list[dict]) -> dict:
        return {"files": len(group),
                "median_words": int(statistics.median(f["words"] for f in group)) if group else 0}

    for_team = [f for f in files if f["reader"] == ResultFor.TEAM]
    for_people = [f for f in files if f["reader"] != ResultFor.TEAM]
    people = summary(for_people)
    people["opened"] = sum(1 for f in for_people if f["opens"])
    people["not_opened"] = [f["name"] for f in for_people if not f["opens"]][:10]

    outliers = []
    if files:
        norm = statistics.median(f["words"] for f in files)
        by_writer: dict[str, list[dict]] = {}
        for f in files:
            if f["by"]:
                by_writer.setdefault(f["by"], []).append(f)
        for agent_id, theirs in by_writer.items():
            if len(theirs) < OUTLIER_MIN_FILES:
                continue
            typical = statistics.median(f["words"] for f in theirs)
            if norm and typical >= OUTLIER_RATIO * norm:
                outliers.append({
                    "agent": names.get(agent_id, agent_id), "files": len(theirs),
                    "median_words": int(typical), "team_median_words": int(norm),
                    "opened_by_a_person": sum(1 for f in theirs if f["opens"]),
                })
        outliers.sort(key=lambda o: -o["median_words"])

    return {
        "for_team": summary(for_team),
        "for_people": people,
        "long_writers": outliers,
        "note": ("Prose files only. Opens count a person opening the file in the app; "
                 "one read in Slack or email is not counted."),
    }
