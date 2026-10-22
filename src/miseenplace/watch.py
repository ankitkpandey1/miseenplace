"""`miseenplace watch`: block until at least one new actionable item, print it, exit 0.

Exiting is the wake-up: an agent's background shell returns when this process ends, the agent
triages the printed lines and starts the watcher again. Errors (network, auth, storage) go to
<root>/state/watch.log and are retried next cycle; stdout and stderr stay silent, so a bad
network never wakes anyone.
"""

import datetime as dt
import time
from collections.abc import Callable
from pathlib import Path

from miseenplace import private
from miseenplace.inbox import Inbox, InboxItem, render_lines
from miseenplace.sources.base import Source

EXHAUSTED = 3  # --max-cycles ran out with nothing to report


def log_error(root: Path, error: Exception) -> None:
    stamp = dt.datetime.now().isoformat(timespec="seconds")
    private.append_text(root / "state" / "watch.log", f"{stamp} {type(error).__name__}: {error}\n")


def poll(
    root: Path, make_source: Callable[[], Source], cursor: str
) -> tuple[Inbox, list[InboxItem]] | None:
    """One cycle. Any failure is logged (never printed); items already opened still come back."""
    try:
        inbox = Inbox(root, make_source())
    except Exception as e:
        log_error(root, e)
        return None
    for step in (lambda: inbox.ingest(cursor), inbox.recheck):
        try:
            step()
        except Exception as e:
            log_error(root, e)
    return inbox, inbox.unseen(cursor)


def watch(
    root: Path,
    make_source: Callable[[], Source],
    cursor: str,
    interval: float,
    once: bool,
    max_cycles: int,
) -> int:
    cycles = 0
    while True:
        polled = poll(root, make_source, cursor)
        if polled and polled[1]:
            inbox, fresh = polled
            print(render_lines(fresh), flush=True)
            inbox.mark_shown(fresh, cursor)
            return 0
        cycles += 1
        if once:
            return 0
        if max_cycles and cycles >= max_cycles:
            return EXHAUSTED
        time.sleep(interval)
