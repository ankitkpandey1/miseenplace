"""The source contract: what a chat service must offer, plus the shared cursor machinery.

A source answers five questions: what is new for me (`new`), what was said in this thread
(`thread`), store a reply for approval (`draft`), post an approved draft (`send`), and who is
this person (`users`). Everything personal (credentials, noise filters) lives in the user's
config, never in the source code.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from miseenplace import private
from miseenplace.chat import Message as Message
from miseenplace.chat import Target
from miseenplace.consent import Draft, NotDelivered
from miseenplace.model import MiseError

CURSOR_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class SourceError(MiseError):
    """The service could not be reached or refused the call. Callers retry next cycle."""


class SendRefused(SourceError, NotDelivered):
    """A send that DEFINITELY did not post (nothing connected, request rejected before posting)."""


@dataclass(frozen=True)
class Person:
    id: str
    name: str
    handle: str  # email or username, whatever the service shows
    bot: bool = False


@runtime_checkable
class Source(Protocol):
    name: str

    def fetch(self, cursor: str, init: bool = False) -> "Batch":
        """Messages for me since `cursor`'s position WITHOUT moving it (see `advance`).

        An unknown cursor (or `init=True`) yields no messages and a baseline position of now.
        """
        ...

    def advance(self, cursor: str, batch: "Batch") -> None:
        """Move `cursor` past `batch`. Call it only after the messages are durably handled."""
        ...

    def new(self, cursor: str, init: bool = False) -> list[Message]:
        """`fetch` then `advance` in one step, for callers that cannot lose anything anyway.

        Every consumer passes its own cursor name, so two consumers never swallow each other's
        messages. An unknown cursor (or `init=True`) sets the baseline to now and returns [].
        """
        ...

    def thread(self, target: Target, limit: int = 40) -> list[Message]:
        """The last `limit` messages of a thread or direct conversation, oldest first."""
        ...

    def draft(self, target: Target, text: str) -> Draft:
        """Store the exact text that `send` will post and return it with its draft id."""
        ...

    def send(self, draft_id: str) -> str:
        """Post a stored draft, once. Takes only a draft id, never text. Returns the posted id."""
        ...

    def users(self, query: str) -> list[Person]:
        """Active people whose name or handle contains `query`."""
        ...


@runtime_checkable
class Digestible(Protocol):
    """Optional: a grouped history of the last days (triage, learning the user's style)."""

    def digest(
        self, days: float, include_noise: bool = False, only_mine: bool = False
    ) -> list[Message]: ...


@runtime_checkable
class Lookup(Protocol):
    """Optional: find one message by id (reconcile follows a journal ref to its thread)."""

    name: str

    def message(self, message_id: str) -> Message: ...

    def thread(self, target: Target, limit: int = 40) -> list[Message]: ...


class Cursors:
    """One tiny file per consumer: <root>/state/cursors/<source>/<name>.

    Separate files mean two consumers never read-modify-write the same bytes, so neither can
    overwrite the other's position.
    """

    def __init__(self, root: Path, source: str) -> None:
        self.dir = root / "state" / "cursors" / source

    def _path(self, name: str) -> Path:
        if not CURSOR_RE.match(name):
            raise MiseError(f"bad cursor name {name!r}; use letters, digits, - and _")
        return self.dir / name

    def get(self, name: str) -> str | None:
        path = self._path(name)
        return path.read_text(encoding="utf-8").strip() if path.exists() else None

    def set(self, name: str, position: str) -> None:
        private.write_text(self._path(name), position)


@dataclass(frozen=True)
class Batch:
    """Messages for me after a position, and the newest position the fetch passed.

    The position can be ahead of the last returned message: filtered-out messages are skipped
    for good.
    """

    messages: list[Message]
    position: str


def fetch_batch(
    cursors: Cursors,
    name: str,
    init: bool,
    newest: Callable[[], str],
    after: Callable[[str], Batch],
) -> Batch:
    """The shared `fetch` logic: baseline on first use, otherwise everything after the position."""
    position = cursors.get(name)
    if init or position is None:
        return Batch([], newest())
    return after(position)


def advance_cursor(cursors: Cursors, name: str, batch: Batch) -> None:
    if cursors.get(name) != batch.position:
        cursors.set(name, batch.position)
