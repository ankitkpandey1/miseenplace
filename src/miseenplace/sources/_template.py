"""Template for a new source. Copy to <service>.py, rename, fill in the TODOs.

Walkthrough: docs/writing-a-source.md. The finished source is registered in
sources/__init__.py and must pass tests/contract/test_source_contract.py.

What you write is only the service-specific part: fetching, filtering and posting. Cursors,
the baseline rule and the consent gate come from base.py and consent.py.
"""

from pathlib import Path

from miseenplace.chat import Message, Target
from miseenplace.consent import Draft, Outbox
from miseenplace.sources.base import Batch, Cursors, Person, advance_cursor, fetch_batch


class TemplateSource:
    name = "template"  # TODO 1: the name users pass to --source

    def __init__(self, root: Path, token: str) -> None:
        self.cursors = Cursors(root, self.name)
        self.outbox = Outbox(root, self.name, self._deliver)
        self.token = token  # never log or print it

    @classmethod
    def from_config(cls, root: Path, settings: dict[str, object]) -> "TemplateSource":
        # TODO 2: read credentials from the environment or a file the user owns, and
        # settings from [sources.template] in config.toml. Raise SourceError, naming the
        # missing KEY (never its value), when something is absent.
        raise NotImplementedError

    # ---- reading ----

    def _newest(self) -> str:
        """Position of the newest message on the service, as a string."""
        raise NotImplementedError  # TODO 3a

    def _after(self, position: str) -> Batch:
        """Messages after `position` that are for me and not mine, plus the newest position
        you looked at (even if you filtered every message out, so it is skipped for good)."""
        raise NotImplementedError  # TODO 3b

    def fetch(self, cursor: str, init: bool = False) -> Batch:
        return fetch_batch(self.cursors, cursor, init, self._newest, self._after)

    def advance(self, cursor: str, batch: Batch) -> None:
        advance_cursor(self.cursors, cursor, batch)

    def new(self, cursor: str, init: bool = False) -> list[Message]:
        batch = self.fetch(cursor, init)
        self.advance(cursor, batch)
        return batch.messages

    def thread(self, target: Target, limit: int = 40) -> list[Message]:
        raise NotImplementedError  # TODO 4: last `limit` messages, oldest first

    def users(self, query: str) -> list[Person]:
        raise NotImplementedError  # TODO 5: active people whose name or handle matches

    # ---- writing: always through the consent gate ----

    def draft(self, target: Target, text: str) -> Draft:
        # Resolve names to ids and fill target.label here so the human sees where it goes.
        # If the service needs a quote or prefix for replies, add it to `text` NOW: the
        # stored text must be exactly what gets posted.
        return self.outbox.draft(target, text)

    def send(self, draft_id: str) -> str:
        return self.outbox.send(draft_id)

    def _deliver(self, target: Target, text: str) -> str:
        """Post `text` to `target` and return the new message's id. Called only by send()."""
        raise NotImplementedError  # TODO 6 (the sixth and last)
