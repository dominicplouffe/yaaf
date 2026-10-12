"""The Deliverables index: every file a project has, walked once and asked many times.

The Deliverables screen used to be one endpoint that walked the whole
workspace, listed the whole artifact store, and shipped all of it to the
browser, which then searched, sorted and foldered it by day. Every fifteen
seconds, per open tab, with nothing to stop a slow walk from overlapping the
next one. Measured on a project with twenty thousand working files it took a
second and a half and two thirds of a megabyte per call, most of it spent
descending into `.git` only to throw what it found there away. On a busy disk
or an S3 bucket the calls overlapped, the threadpool filled, and the tab simply
stopped loading — which is the screen a chairman opens to find the work.

So the walk happens here, at most once per FRESH_S per project however many
screens are asking, and the questions the screen asks — search, filter, sort,
which day — are answered against what this holds. The browser receives the
folder headers and the rows it is actually showing, not the project.

A stale index is served rather than waited on, and refreshed behind the
request: a reader gets an answer at once and the age of it is stated
(`as_of`), which is the trade every polled screen here already makes.
"""

from __future__ import annotations

import heapq
import logging
import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from yaaf.core import artifacts

log = logging.getLogger("yaaf.file_index")

#: Working files held per project, newest kept. Past this the screen says how
#: many older ones it is not showing rather than implying they do not exist.
MAX_INDEXED_FILES = 50_000
#: Where the walk itself stops. A tree big enough to hit this is a runaway, not
#: a project; stopping beats enumerating an agent's node_modules on every read.
MAX_SCANNED_FILES = 200_000
#: Platform plumbing, not agent work: staging (tokens, prompts, traces) and git
#: internals never show in the chairman's view, and are never descended into.
SKIPPED_DIRS = frozenset({".git", ".yaaf"})

#: Younger than this, an index is served as it is.
FRESH_S = 10.0
#: ...or younger than its own walk divided by this. A walk that takes four
#: seconds is not started every ten while someone has the tab open: the index
#: stays fresh long enough that walking is at most this share of the time.
MAX_WALK_SHARE = 0.2
#: Older, a rebuild starts and the reader waits this long for it — enough for
#: an ordinary project's walk, so it reads as live — before being handed the
#: one already held.
PATIENCE_S = 0.5
#: Older than this, the held index is too old to show and the reader waits.
STALE_S = 300.0
#: Nobody has looked for this long: drop it, and walk again if they come back.
IDLE_S = 900.0

#: Days back from the start of today, per window. -1 is all time.
WINDOWS = {"today": 0, "7d": 6, "30d": 29, "all": -1}
STATES = ("all", "published", "working")
SORTS = ("name", "state", "size", "at", "type")
#: Rows a folder shows before "show more".
ROWS_PER_DAY = 150
MAX_ROWS_PER_DAY = 10_000
#: How many rows a filter may open folders for on its own. Opening every
#: folder a search touched was the right instinct and the wrong ceiling: a
#: two-letter search over a year of files opened three hundred of them.
FILTERED_OPEN_ROWS = 300
UNKNOWN_DAY = "unknown"


@dataclass(frozen=True, slots=True)
class Item:
    """One row of the screen. A published name republished by later runs is one
    item with its earlier copies in `older`, newest first — five copies of
    three files are three deliverables, not five."""

    id: str
    name: str
    dir: str
    state: str
    ext: str
    size: int
    ts: float | None
    at: str | None
    path: str | None
    key: str | None
    run_id: str | None
    version: int
    older: tuple[Item, ...]
    hay: str


@dataclass(slots=True)
class Snapshot:
    items: list[Item]
    #: Working files the walk saw, and how many of them are held. When
    #: `stopped`, the walk hit MAX_SCANNED_FILES and `found` is a floor.
    found: int
    kept: int
    stopped: bool
    as_of: datetime
    #: Seconds the walk and the store listing took.
    cost: float = 0.0
    built: float = field(default_factory=time.monotonic)
    read_at: float = field(default_factory=time.monotonic)
    views: dict = field(default_factory=dict)
    by_id: dict[str, Item] | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def find(self, item_id: str) -> Item | None:
        if self.by_id is None:
            index: dict[str, Item] = {}
            for item in self.items:
                index[item.id] = item
                for old in item.older:
                    index[old.id] = old
            self.by_id = index
        return self.by_id.get(item_id)


def ext_of(name: str) -> str:
    base = name.rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[-1].lower() if "." in base else ""


# ── building ────────────────────────────────────────────────────────────────────


def _walk(root: Path) -> tuple[list[tuple[float, str, int]], bool]:
    """(mtime, relative path, size) for every regular file under root.

    os.scandir rather than Path.rglob: the type of an entry comes free with the
    listing, and a skipped directory is never entered at all. rglob walked all
    of `.git` and filtered it out afterwards, and spent most of its time
    building Path objects for files it was about to discard.
    """
    found: list[tuple[float, str, int]] = []
    stack = [""]
    base = str(root)
    while stack:
        rel_dir = stack.pop()
        try:
            listing = os.scandir(os.path.join(base, rel_dir) if rel_dir else base)
        except OSError:
            continue
        with listing:
            for entry in listing:
                rel = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name not in SKIPPED_DIRS:
                            stack.append(rel)
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    stat = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                found.append((stat.st_mtime, rel, stat.st_size))
                if len(found) >= MAX_SCANNED_FILES:
                    return found, True
    return found, False


def _timestamp(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return None


def _published(rows: list[dict]) -> list[Item]:
    by_name: dict[str, list[dict]] = {}
    for row in rows:
        parts = row["key"].split("/")
        by_name.setdefault(parts[-1], []).append(
            {**row, "run_id": parts[2] if len(parts) > 3 else None})
    items = []
    for name, copies in by_name.items():
        copies.sort(key=lambda r: r["modified"] or "", reverse=True)

        def make(row: dict, version: int, older: tuple[Item, ...] = (), name=name) -> Item:
            return Item(
                id=row["key"], name=name, dir="", state="published", ext=ext_of(name),
                size=row["size"], ts=_timestamp(row["modified"]), at=row["modified"],
                path=None, key=row["key"], run_id=row["run_id"], version=version,
                older=older, hay=name.lower(),
            )

        total = len(copies)
        older = tuple(make(row, total - 1 - i) for i, row in enumerate(copies[1:]))
        items.append(make(copies[0], total, older))
    return items


def build(root: Path, prefix: str) -> Snapshot:
    as_of = datetime.now(UTC)
    began = time.monotonic()
    found, stopped = _walk(root) if root.is_dir() else ([], False)
    # Newest first, then cut: the screen's first question is "what changed
    # today", and a cap that kept the alphabetically first would answer it by
    # dropping this morning's work.
    newest = (heapq.nlargest(MAX_INDEXED_FILES, found) if len(found) > MAX_INDEXED_FILES
              else sorted(found, reverse=True))
    items = _published(artifacts.store().list_keys(prefix))
    for mtime, rel, size in newest:
        name = rel.rsplit("/", 1)[-1]
        items.append(Item(
            id=f"w:{rel}", name=name, dir=rel[: max(len(rel) - len(name) - 1, 0)],
            state="working", ext=ext_of(name), size=size, ts=mtime,
            at=datetime.fromtimestamp(mtime, tz=UTC).isoformat(), path=rel, key=None,
            run_id=None, version=1, older=(), hay=rel.lower(),
        ))
    return Snapshot(items=items, found=len(found), kept=len(newest), stopped=stopped,
                    as_of=as_of, cost=time.monotonic() - began)


# ── holding ─────────────────────────────────────────────────────────────────────

_lock = threading.Lock()
_held: dict[str, Snapshot] = {}
_inflight: dict[str, Future] = {}
# Two, not one per request: the point is that a slow disk costs one walk per
# project, not one per tab, and never a threadpool's worth of them.
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="file-index")


def _rebuild(project_id: str, root: Path, prefix: str) -> Snapshot:
    try:
        snap = build(root, prefix)
        with _lock:
            _held[project_id] = snap
        return snap
    finally:
        with _lock:
            _inflight.pop(project_id, None)


def snapshot(project_id: str, root: Path, prefix: str) -> Snapshot:
    """The project's index — fresh, or as fresh as it can be without making
    the reader wait on a walk that is already known to be slow."""
    now = time.monotonic()
    with _lock:
        for pid in [p for p, s in _held.items() if now - s.read_at > IDLE_S]:
            del _held[pid]
        held = _held.get(project_id)
        if held is not None:
            held.read_at = now
            if now - held.built <= max(FRESH_S, held.cost / MAX_WALK_SHARE):
                return held
        pending = _inflight.get(project_id)
        if pending is None:
            pending = _pool.submit(_rebuild, project_id, root, prefix)
            _inflight[project_id] = pending
    if held is None or now - held.built > STALE_S:
        return pending.result()
    try:
        return pending.result(timeout=PATIENCE_S)
    except TimeoutError:
        return held
    except Exception:
        # The last good index, labelled with its age, beats an error page on a
        # screen that polls; the next read tries again.
        log.exception("file index rebuild failed for %s; serving the one held", project_id)
        return held


# ── asking ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Query:
    q: str = ""
    state: str = "all"
    type: str = ""
    since: str = "all"
    tz: str = "UTC"
    sort: str = "at"
    desc: bool = True

    @classmethod
    def of(cls, q: str = "", state: str = "all", type: str = "", since: str = "all",
           tz: str = "UTC", sort: str = "at", desc: bool = True) -> Query:
        """Anything the screen does not offer reads as its default, the way the
        page itself reads an unknown ?state= from a pasted link."""
        return cls(
            q=" ".join(q.lower().split()),
            state=state if state in STATES else "all",
            type=type.lower(),
            since=since if since in WINDOWS else "all",
            tz=_zone(tz).key,
            sort=sort if sort in SORTS else "at",
            desc=desc,
        )

    @property
    def terms(self) -> list[str]:
        return self.q.split()

    @property
    def filtering(self) -> bool:
        return bool(self.q) or self.state != "all" or bool(self.type) or self.since != "all"


@dataclass(slots=True)
class Day:
    key: str
    items: list[Item]
    published: int
    #: Position of this day's first row in the whole filtered list, so a row
    #: can say where it sits and the reader can walk on from it.
    start: int


@dataclass(slots=True)
class View:
    days: list[Day]
    matched: int
    states: dict[str, int]
    types: list[tuple[str, int]]
    total: int
    published_total: int
    today_total: int
    today: str
    _flat: list[Item] | None = None

    @property
    def flat(self) -> list[Item]:
        if self._flat is None:
            self._flat = [item for day in self.days for item in day.items]
        return self._flat


@lru_cache(maxsize=64)
def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name) if name else ZoneInfo("UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


@lru_cache(maxsize=1 << 16)
def _day_of_bucket(tz: str, bucket: int) -> str:
    return datetime.fromtimestamp(bucket * 900, _zone(tz)).date().isoformat()


def day_of(ts: float | None, tz: str) -> str:
    """The reader's local day, not UTC's: a file written at 6pm in California
    belongs in this evening's folder. Every zone's offset is a whole number of
    quarter hours, so local midnight always falls on a quarter-hour boundary
    and the answer can be kept per quarter hour rather than worked out per file."""
    if ts is None:
        return UNKNOWN_DAY
    return _day_of_bucket(tz, int(ts // 900))


def _order(hits: list[tuple[str, Item]], query: Query) -> list[tuple[str, Item]]:
    # Name sorts on the path as shown, folder and all, and every other column
    # falls back to it, so two files of the same size or the same minute keep
    # one order instead of trading places on each poll. Python's sort is stable
    # in both directions, so the fallback stays ascending under a descending
    # primary — which is what the screen always did.
    if query.sort == "name":
        return sorted(hits, key=lambda h: (h[1].hay, h[1].id), reverse=query.desc)
    by_name = sorted(hits, key=lambda h: (h[1].hay, h[1].id))
    primary = {
        "size": lambda h: h[1].size,
        "at": lambda h: h[1].ts if h[1].ts is not None else float("-inf"),
        "state": lambda h: h[1].state,
        "type": lambda h: h[1].ext,
    }[query.sort]
    return sorted(by_name, key=primary, reverse=query.desc)


def view(snap: Snapshot, query: Query) -> View:
    zone = _zone(query.tz)
    now = datetime.now(zone)
    today = now.date().isoformat()
    # "today" is in the key because a screen open since yesterday afternoon
    # has to mean today after midnight.
    cache_key = (query, today)
    with snap.lock:
        held = snap.views.get(cache_key)
    if held is not None:
        return held

    terms = query.terms
    back = WINDOWS[query.since]
    floor = (None if back < 0 else
             (now.replace(hour=0, minute=0, second=0, microsecond=0)
              - timedelta(days=back)).timestamp())
    states = {"all": 0, "published": 0, "working": 0}
    types: dict[str, int] = {}
    hits: list[tuple[str, Item]] = []
    published_total = today_total = 0
    for item in snap.items:
        day = day_of(item.ts, query.tz)
        if item.state == "published":
            published_total += 1
        if day == today:
            today_total += 1
        if floor is not None and (item.ts is None or item.ts < floor):
            continue
        if terms and not all(t in item.hay for t in terms):
            continue
        # Each filter counts what it WOULD show, leaving itself out: a type
        # list reading "md (0)" because you are on Published is a broken list.
        state_ok = query.state == "all" or item.state == query.state
        type_ok = not query.type or item.ext == query.type
        if type_ok:
            states["all"] += 1
            states[item.state] += 1
        if state_ok:
            types[item.ext] = types.get(item.ext, 0) + 1
        if state_ok and type_ok:
            hits.append((day, item))

    grouped: dict[str, list[Item]] = {}
    for day, item in _order(hits, query):
        grouped.setdefault(day, []).append(item)
    keys = sorted((k for k in grouped if k != UNKNOWN_DAY), reverse=True)
    if UNKNOWN_DAY in grouped:
        keys.append(UNKNOWN_DAY)
    days, start = [], 0
    for key in keys:
        items = grouped[key]
        days.append(Day(key=key, items=items, start=start,
                        published=sum(1 for i in items if i.state == "published")))
        start += len(items)

    result = View(
        days=days, matched=len(hits), states=states,
        types=sorted(types.items(), key=lambda kv: (-kv[1], kv[0])),
        total=len(snap.items), published_total=published_total,
        today_total=today_total, today=today,
    )
    with snap.lock:
        if len(snap.views) >= 16:
            snap.views.pop(next(iter(snap.views)))
        snap.views[cache_key] = result
    return result


def folders(result: View, query: Query, expand: set[str], collapse: set[str],
            caps: dict[str, int]) -> list[tuple[Day, bool, int]]:
    """Which folders are open, and how far down each one shows.

    Today's folder and the newest one open by themselves; a filter opens more,
    in order, until FILTERED_OPEN_ROWS rows are showing. Anything the reader
    toggled is honoured over all of that.
    """
    out = []
    shown = 0
    for i, day in enumerate(result.days):
        cap = max(1, min(caps.get(day.key, ROWS_PER_DAY), MAX_ROWS_PER_DAY))
        if day.key in expand:
            is_open = True
        elif day.key in collapse:
            is_open = False
        else:
            is_open = (day.key == result.today or i == 0
                       or (query.filtering and shown < FILTERED_OPEN_ROWS))
        if is_open:
            shown += min(len(day.items), cap)
        out.append((day, is_open, cap))
    return out
