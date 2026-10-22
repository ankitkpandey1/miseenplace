"""`stats`: how long people wait on you, and whether that is getting better.

Chat gives reply latency (a message that mentions or DMs me, to my next message in that thread),
GitHub (`gh`) gives review turnaround and the review queue, the journal log gives items closed,
and one CSV row a day (`stats.csv`) holds what only I can say: focus, stress, the queue at the
end of the day. A metric with no data is `None` and prints as `n/a`; nothing is estimated.
"""

import csv
import datetime as dt
import io
import json
import math
import re
import shutil
import statistics
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, TypedDict, TypeVar, cast

from miseenplace.chat import Message
from miseenplace.model import Entry, MiseError
from miseenplace.storage.base import Storage

T = TypeVar("T")
CSV_NAME = "stats.csv"
FIELDS = ("date", "focus", "stress", "queue")
DAY = 86400.0
DELTA_RECENT, DELTA_BEFORE = 7, 14  # the last week against the two weeks before it
GH_TIMEOUT = 60
NO_GH = "skipped review rows: gh is not installed"
NA = "n/a"
QUEUE_QUERY = """query {
  queue: search(type: ISSUE, query: "is:pr is:open review-requested:@me") { issueCount }
}"""
REVIEWS_QUERY = """query {
  reviewed: search(type: ISSUE, first: %d, after: %s,
      query: "is:pr reviewed-by:@me updated:>=%s sort:updated-desc") {
    pageInfo { hasNextPage endCursor }
    nodes { ... on PullRequest {
      requests: timelineItems(itemTypes: [REVIEW_REQUESTED_EVENT], first: 30) {
        nodes { ... on ReviewRequestedEvent {
          createdAt requestedReviewer { ... on User { login } } } } }
      reviews(first: 50) { nodes { author { login } submittedAt } }
    } }
  }
  viewer { login }
}"""
PAGE_SIZE, MAX_PAGES, EMPTY_RETRIES = 50, 10, 2
MIN_P90 = 10  # below this many samples a 90th percentile is just the maximum


@dataclass(frozen=True)
class Reviews:
    turnarounds: list[tuple[float, float]]  # (when I reviewed, seconds since it was requested)
    queue: int  # open PRs waiting on my review right now
    truncated: bool = False  # more PRs matched than MAX_PAGES pages hold


@dataclass(frozen=True)
class Data:
    replies: "Waits | None"  # None: no chat
    reviews: Reviews | None  # None: no gh
    closed: list[float]  # when each item was closed
    rows: list[dict[str, str]]  # the daily CSV


@dataclass(frozen=True)
class Metrics:
    days: int
    reply_median: float | None
    reply_p90: float | None
    replied: int | None
    unanswered: int | None
    review_median: float | None
    reviewed: int | None
    closed: int
    closed_per_day: float
    pings_per_day: float | None
    focus: float | None
    stress: float | None
    logged: int


# ---- chat ------------------------------------------------------------------------


@dataclass(frozen=True)
class Waits:
    answered: list[tuple[float, float]]  # (when I replied, seconds waited)
    unanswered: list[float]  # when each still-unanswered ping arrived


def waits(messages: list[Message]) -> Waits:
    """Per thread: the first unanswered ping (a mention, group mentions too, or a DM) to my next
    message in it. A ping with no message of mine after it is unanswered."""
    threads: dict[str, list[Message]] = {}
    for m in sorted(messages, key=lambda m: m.ts):
        threads.setdefault(m.where, []).append(m)
    found: list[tuple[float, float]] = []
    open_: list[float] = []
    for where, rows in threads.items():
        asked: float | None = None
        for m in rows:
            if m.mine and asked is not None:
                found.append((m.ts, m.ts - asked))
                asked = None
            elif not m.mine and asked is None and (m.mentioned or where.startswith("DM:")):
                asked = m.ts
        if asked is not None:
            open_.append(asked)
    return Waits(found, open_)


# ---- GitHub ----------------------------------------------------------------------


class User(TypedDict):
    login: str


class Request(TypedDict):
    createdAt: str
    requestedReviewer: User | None  # a team request has no login


class Review(TypedDict):
    author: User | None
    submittedAt: str | None


class Nodes(TypedDict, Generic[T]):
    nodes: list[T]


class PullRequest(TypedDict):
    requests: Nodes[Request]
    reviews: Nodes[Review]


def graphql_data(raw: str) -> dict[str, object]:
    try:
        body = json.loads(raw)
        if body.get("errors"):  # a partial answer (a search that timed out) is not a count of 0
            raise MiseError(f"skipped review rows: gh reported {body['errors'][0]['message']}")
        return cast(dict[str, object], body["data"])
    except (ValueError, KeyError, TypeError, AttributeError):
        raise MiseError("gh gave unreadable output for the review query") from None


def parse_queue(raw: str) -> int:
    try:
        return int(cast(dict[str, dict[str, int]], graphql_data(raw))["queue"]["issueCount"])
    except (KeyError, TypeError, ValueError):
        raise MiseError("gh gave unreadable output for the review query") from None


@dataclass(frozen=True)
class Page:
    turnarounds: list[tuple[float, float]]
    matched: int  # PRs on this page, mine or not
    cursor: str | None  # where the next page starts; None on the last page


def parse_page(raw: str) -> Page:
    """One page of `REVIEWS_QUERY` output -> my turnarounds on it and where the next page is."""
    data = graphql_data(raw)
    try:
        search = cast(dict[str, Any], data["reviewed"])
        me = str(cast(dict[str, str], data["viewer"])["login"])
        prs = cast(list[PullRequest], search["nodes"])
        found = [t for pr in prs if (t := turnaround(pr, me))]
        info = search["pageInfo"]
        return Page(found, len(prs), str(info["endCursor"]) if info["hasNextPage"] else None)
    except (KeyError, TypeError, ValueError):
        raise MiseError("gh gave unreadable output for the review query") from None


def turnaround(pr: PullRequest, me: str) -> tuple[float, float] | None:
    """(when I first reviewed, wait) from the request made of me to that review."""
    asked = [
        at(e["createdAt"])
        for e in pr["requests"]["nodes"]
        if e["requestedReviewer"] and e["requestedReviewer"]["login"] == me
    ]
    mine = [
        at(r["submittedAt"])
        for r in pr["reviews"]["nodes"]
        if r["submittedAt"] and r["author"] and r["author"]["login"] == me
    ]
    after = [t for t in mine if asked and t >= min(asked)]
    return (min(after), min(after) - min(asked)) if after else None


def at(stamp: str) -> float:
    return dt.datetime.fromisoformat(stamp).timestamp()


def gh_graphql(query: str) -> str:
    if not shutil.which("gh"):
        raise MiseError(NO_GH)
    done = subprocess.run(
        ["gh", "api", "graphql", "-f", f"query={query}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=GH_TIMEOUT,
    )
    if done.returncode != 0:
        first = (done.stderr.strip().splitlines() or ["no output"])[0]
        raise MiseError(f"skipped review rows: gh failed: {first}")
    return done.stdout


def gh_queue() -> int:
    """Open PRs waiting on me. GitHub search sometimes answers 0 without an error: ask twice."""
    return parse_queue(gh_graphql(QUEUE_QUERY)) or parse_queue(gh_graphql(QUEUE_QUERY))


def first_page(query: str, first: bool) -> Page:
    """GitHub search often answers an empty first page without an error: ask up to 3 times."""
    page = parse_page(gh_graphql(query))
    for _ in range(EMPTY_RETRIES if first else 0):
        if page.matched:
            break
        page = parse_page(gh_graphql(query))
    return page


def gh_reviews(days: int) -> Reviews:
    since = day_of(time.time() - days * DAY)
    found: list[tuple[float, float]] = []
    cursor: str | None = None
    for _ in range(MAX_PAGES):
        after = json.dumps(cursor) if cursor else "null"
        page = first_page(REVIEWS_QUERY % (PAGE_SIZE, after, since), cursor is None)
        found += page.turnarounds
        cursor = page.cursor
        if cursor is None:
            break
    return Reviews(sorted(found), gh_queue(), cursor is not None)


# ---- journal and daily CSV ---------------------------------------------------------


def became_done(e: Entry) -> bool:
    change = e["changes"].get("state")  # [before, after]
    return isinstance(change, list) and change[-1:] == ["done"]


def closed_at(entries: list[Entry]) -> list[float]:
    """The first time each item was closed as done: `done` or a state change to done."""
    first: dict[int, float] = {}
    for e in entries:
        if e["op"] == "done" or became_done(e):
            first.setdefault(e["id"], time.mktime(time.strptime(e["ts"], "%Y-%m-%d %H:%M")))
    return list(first.values())


DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def bad_field(row: dict[str, str | None]) -> str | None:
    """What is wrong with a CSV row, or None."""
    vals = {f: row.get(f) for f in FIELDS}
    if any(v is None for v in vals.values()):
        return "too few fields"
    if not DATE_RE.fullmatch(str(vals["date"])):
        return f"date {vals['date']!r} is not YYYY-MM-DD"
    for f in ("focus", "stress"):
        if vals[f] not in ("", "1", "2", "3", "4", "5"):
            return f"{f} {vals[f]!r} is not 1 to 5"
    if not str(vals["queue"]).isdigit() and vals["queue"] != "":
        return f"queue {vals['queue']!r} is not a number"
    return None


def read_rows(storage: Storage, notes: list[str]) -> list[dict[str, str]]:
    """The valid rows; each bad row is skipped with a note naming its line."""
    text = storage.read_file(CSV_NAME) or ""
    if not text.strip():
        return []
    reader = csv.DictReader(io.StringIO(text))
    if not set(FIELDS) <= set(reader.fieldnames or ()):
        raise MiseError(f"{CSV_NAME} header must have {','.join(FIELDS)}; fix or delete the file")
    good: list[dict[str, str]] = []
    for row in reader:
        why = bad_field(row)
        if why:
            notes.append(f"{CSV_NAME}:{reader.line_num}: skipped, {why}")
        else:
            good.append({f: str(row[f]) for f in FIELDS})
    return good


def record(
    storage: Storage,
    date: str,
    ratings: tuple[int | None, int | None],
    queue: int | None,
    notes: list[str],
) -> str:
    """Write today's row; a rerun replaces it, keeping a rating not given again and a queue
    that a lone 0 (possibly a search hiccup) would wipe."""
    rows = read_rows(storage, notes)
    old = next((r for r in rows if r["date"] == date), {})
    keep_queue = queue is None or (queue == 0 and old.get("queue", "") not in ("", "0"))
    row = {
        "date": date,
        "focus": str(ratings[0]) if ratings[0] else old.get("focus", ""),
        "stress": str(ratings[1]) if ratings[1] else old.get("stress", ""),
        "queue": old.get("queue", "") if keep_queue else str(queue),
    }
    kept = [r for r in rows if r["date"] != date]
    out = io.StringIO()
    writer = csv.DictWriter(out, FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(sorted([*kept, row], key=lambda r: r["date"]))
    storage.write_file(CSV_NAME, out.getvalue())
    return ",".join(row[f] for f in FIELDS)


# ---- numbers ---------------------------------------------------------------------


def within(stamps: list[float], start: float, end: float) -> list[float]:
    return [t for t in stamps if start < t <= end]


def p90(values: list[float]) -> float | None:
    ordered = sorted(values)
    return ordered[math.ceil(0.9 * len(ordered)) - 1] if ordered else None


def median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def day_of(stamp: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(stamp))


def rows_in(rows: list[dict[str, str]], start: float, end: float) -> list[dict[str, str]]:
    return [r for r in rows if day_of(start) < r["date"] <= day_of(end)]


def felt(rows: list[dict[str, str]], field: str) -> list[float]:
    return [float(r[field]) for r in rows if r.get(field)]


def compute(data: Data, start: float, end: float) -> Metrics:
    days = round((end - start) / DAY)
    w = data.replies
    waited = [x for t, x in w.answered if start < t <= end] if w else None
    open_ = len([t for t in w.unanswered if start < t <= end]) if w else None
    turns = [x for t, x in data.reviews.turnarounds if start < t <= end] if data.reviews else None
    logged = rows_in(data.rows, start, end)
    closed = len(within(data.closed, start, end))
    return Metrics(
        days,
        median(waited) if waited else None,
        p90(waited) if waited and len(waited) >= MIN_P90 else None,
        None if waited is None else len(waited),
        open_,
        median(turns) if turns else None,
        None if turns is None else len(turns),
        closed,
        closed / days,
        None if waited is None else len(waited) / days,
        mean(felt(logged, "focus")),
        mean(felt(logged, "stress")),
        len(logged),
    )


# ---- output ----------------------------------------------------------------------


def span(seconds: float | None) -> str:
    if seconds is None:
        return NA
    if seconds < 60:
        return f"{round(seconds)}s"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h{minutes:02d}m" if minutes else f"{hours}h"
    return f"{hours // 24}d{hours % 24}h"


def num(value: float | None) -> str:
    return NA if value is None else f"{value:.1f}"


def count(n: int | None, noun: str) -> str:
    return "" if n is None else f"  ({n} {noun})"


def replies(m: Metrics) -> str:
    if m.replied is None or m.unanswered is None:
        return ""
    return f"  ({m.replied} answered, {m.unanswered} unanswered)"


def render(m: Metrics, queue: int | None) -> str:
    waits_ = f"median {span(m.reply_median)}  p90 {span(m.reply_p90)}"
    return "\n".join(
        [
            f"stats {m.days}d",
            f"reply latency   {waits_}{replies(m)}",
            f"review turnaround  median {span(m.review_median)}{count(m.reviewed, 'PRs')}",
            f"review queue    {NA if queue is None else queue} now",
            f"closed/day      {num(m.closed_per_day)}",
            f"pings answered/day  {num(m.pings_per_day)}",
            f"focus {num(m.focus)}  stress {num(m.stress)}{count(m.logged, 'days logged')}",
        ]
    )


def change(new: float | None, old: float | None) -> str:
    if new is None or old is None or old == 0:
        return NA
    return f"{(new - old) / old:+.0%}"


def render_delta(now: Metrics, before: Metrics) -> str:
    def row(
        name: str,
        fmt: Callable[[float | None], str],
        new: float | None,
        old: float | None,
        n: tuple[int | None, int | None],
    ) -> str:
        return f"{name}  {fmt(new)} vs {fmt(old)}  {change(new, old)}  n={n[0] or 0}/{n[1] or 0}"

    ping = (now.replied, before.replied)
    rows = [
        row("reply median", span, now.reply_median, before.reply_median, ping),
        row("review median", span, now.review_median, before.review_median,
            (now.reviewed, before.reviewed)),
        row("closed/day", num, now.closed_per_day, before.closed_per_day,
            (now.closed, before.closed)),
        row("pings answered/day", num, now.pings_per_day, before.pings_per_day, ping),
        row("focus", num, now.focus, before.focus, (now.logged, before.logged)),
        row("stress", num, now.stress, before.stress, (now.logged, before.logged)),
    ]  # fmt: skip
    head = f"last {now.days}d vs previous {before.days}d (lower is better for waits and stress)"
    return "\n".join([head, *rows])


# ---- putting it together ---------------------------------------------------------


def collect(
    entries: list[Entry],
    storage: Storage,
    digest: Callable[[float], list[Message]],
    days: int,
) -> tuple[Data, list[str]]:
    """Everything `days` back; a source that cannot answer leaves its rows `n/a` plus a note."""
    notes: list[str] = []
    replies: Waits | None = None
    reviews: Reviews | None = None
    try:
        replies = waits(digest(days))
    except MiseError as e:
        notes.append(f"skipped reply rows: {e}")
    try:
        reviews = gh_reviews(days)
    except MiseError as e:
        notes.append(str(e))
    if reviews and reviews.truncated:
        notes.append(f"review rows cover only the newest {PAGE_SIZE * MAX_PAGES} PRs")
    return Data(replies, reviews, closed_at(entries), read_rows(storage, notes)), notes


def report(data: Data, days: int, delta: bool, end: float) -> str:
    if delta:
        recent = compute(data, end - DELTA_RECENT * DAY, end)
        before = compute(data, end - (DELTA_RECENT + DELTA_BEFORE) * DAY, end - DELTA_RECENT * DAY)
        return render_delta(recent, before)
    queue = data.reviews.queue if data.reviews else None
    return render(compute(data, end - days * DAY, end), queue)
