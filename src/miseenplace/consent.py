"""The consent gate: nothing is posted that was not first stored as an exact draft.

`Outbox.draft` stores the exact text and destination and returns a draft id. `Outbox.send`
accepts ONLY that id, never free text. A draft is delivered at most once, and expires (default
24 hours, `[consent] draft_ttl_hours` in config.toml). The agent's job is to show the stored text
to the human and call `send` only after an explicit yes on that exact text.

Delivery can end three ways. Delivered: the draft is `sent`. Definitely not delivered (the source
raised NotDelivered: connection refused, request rejected): the draft stays sendable. Anything
else (a timeout after the request left, a cut response): nobody knows whether it was posted, so
the draft becomes `unknown` and is never retried automatically. The human checks the thread, then
runs `send DRAFT --mark-sent` or `send DRAFT --mark-unsent`.

This is a structural gate, not proof that a human said yes (see docs/security.md).
"""

import hashlib
import json
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from miseenplace import private
from miseenplace.chat import Target, load
from miseenplace.config import CONFIG_NAME, load_config
from miseenplace.model import MiseError, now

DRAFT_ID_RE = re.compile(r"^d[0-9a-f]{8}$")
SECONDS_PER_HOUR = 3600

__all__ = [
    "ConsentError",
    "Draft",
    "NotDelivered",
    "Outbox",
    "Target",
    "pending_drafts",
]


class ConsentError(MiseError):
    """The send was refused: unknown, modified, expired, unsure or already-sent draft."""


class NotDelivered(Exception):
    """Raised by a source's deliver when the message was DEFINITELY not posted."""


@dataclass(frozen=True)
class Draft:
    id: str
    source: str
    target: Target
    text: str
    sha256: str  # covers the destination and the text
    created: str
    created_ts: float = 0.0
    status: str = "drafted"  # drafted | sent | unknown
    sent_ref: str | None = None

    def preview(self) -> str:
        """What the human must see before saying yes: destination and the exact text."""
        t = self.target
        dest = t.label or (f"#{t.to} > {t.thread}" if t.thread else f"DM: {t.to}")
        if t.edit_of:
            dest = f"EDIT of message {t.edit_of} ({dest})"
        return f"draft {self.id} -> {dest}\n----- exact text -----\n{self.text}\n----- end -----"


def digest(target: Target, text: str) -> str:
    """Hash of the destination AND the text, so editing either invalidates the draft."""
    fields = asdict(target)
    if fields["edit_of"] is None:  # drafts stored before edits existed keep their hash
        del fields["edit_of"]
    canonical = json.dumps({"target": fields, "text": text}, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


Deliver = Callable[[Target, str], str]  # (target, exact text) -> id of the posted message


class Outbox:
    """Drafts of one source, one JSON file per draft under <root>/state/drafts/."""

    def __init__(self, root: Path, source: str, deliver: Deliver) -> None:
        self.root = root
        self.dir = root / "state" / "drafts"
        self.source = source
        self._deliver = deliver

    @property
    def ttl_seconds(self) -> float:
        return load_config(self.root / CONFIG_NAME).draft_ttl_hours * SECONDS_PER_HOUR

    def _path(self, draft_id: str) -> Path:
        if not DRAFT_ID_RE.match(draft_id):
            raise ConsentError(
                f"{draft_id!r} is not a draft id (looks like d1a2b3c4e); "
                "send takes only the id printed by `draft`, never message text"
            )
        return self.dir / f"{draft_id}.json"

    def _claim_path(self, draft_id: str) -> Path:
        return self._path(draft_id).with_suffix(".sending")

    def draft(self, target: Target, text: str) -> Draft:
        if not text.strip():
            raise ConsentError("empty message")
        d = Draft(
            id="d" + secrets.token_hex(4),
            source=self.source,
            target=target,
            text=text,
            sha256=digest(target, text),
            created=now(),
            created_ts=time.time(),
        )
        private.create_exclusive(self._path(d.id), json.dumps(asdict(d), indent=1))
        return d

    def get(self, draft_id: str) -> Draft:
        path = self._path(draft_id)
        if not path.exists():
            raise ConsentError(f"no draft {draft_id}; create one with `draft` first")
        d = self._read(path)
        if d.source != self.source:
            raise ConsentError(
                f"draft {draft_id} belongs to source {d.source!r}, not {self.source!r}"
            )
        return d

    def _read(self, path: Path) -> Draft:
        data = json.loads(path.read_text(encoding="utf-8"))
        return load(Draft, {**data, "target": load(Target, data["target"])})

    def _expired(self, d: Draft) -> bool:
        return time.time() - d.created_ts > self.ttl_seconds

    def pending(self) -> list[Draft]:
        """Unsent, unexpired drafts of this source; other sources' drafts are not our business."""
        if not self.dir.exists():
            return []
        found: list[Draft] = []
        for path in sorted(self.dir.glob("d*.json")):
            try:
                d = self._read(path)
            except (ValueError, KeyError, TypeError):
                continue  # not a draft we can read; `send` on its id reports it
            if d.source == self.source and d.status == "drafted" and not self._expired(d):
                found.append(d)
        return found

    def send(self, draft_id: str) -> str:
        """Deliver the stored text of `draft_id`, once. Returns the posted message id."""
        d = self.get(draft_id)
        self._check_sendable(d)
        claim = self._claim_path(draft_id)
        try:
            private.create_exclusive(claim, str(time.time()))
        except FileExistsError:
            raise ConsentError(
                f"draft {draft_id} is already being sent, or a previous send was cut off. "
                f"{self._recovery(draft_id)}"
            ) from None
        try:
            ref = self._deliver(d.target, d.text)
        except NotDelivered:
            claim.unlink()  # nothing was posted: the draft stays sendable
            raise
        except BaseException as e:
            self._write(Draft(**{**asdict(d), "target": d.target, "status": "unknown"}))
            claim.unlink()
            raise ConsentError(
                f"draft {draft_id}: could not confirm delivery ({type(e).__name__}). "
                f"{self._recovery(draft_id)}"
            ) from e
        self._write(Draft(**{**asdict(d), "target": d.target, "status": "sent", "sent_ref": ref}))
        claim.unlink()
        return ref

    def _check_sendable(self, d: Draft) -> None:
        if d.status == "sent":
            raise ConsentError(f"draft {d.id} was already sent (ref {d.sent_ref})")
        if d.status == "unknown":
            raise ConsentError(f"draft {d.id}: delivery was not confirmed. {self._recovery(d.id)}")
        if digest(d.target, d.text) != d.sha256:
            raise ConsentError(f"draft {d.id} was modified after it was drafted; draft again")
        if self._expired(d):
            raise ConsentError(
                f"draft {d.id} expired (older than {self.ttl_seconds / SECONDS_PER_HOUR:g} hours); "
                "draft again and show the human the new text"
            )

    def _recovery(self, draft_id: str) -> str:
        return (
            "It may or may not have been posted: look in the thread first. Then run "
            f"`miseenplace send {draft_id} --mark-sent` if it is there, or "
            f"`miseenplace send {draft_id} --mark-unsent` if it is not (that allows sending again)."
        )

    def mark(self, draft_id: str, sent: bool) -> Draft:
        """The human's verdict after checking the thread. Only for unsure or cut-off sends."""
        d = self.get(draft_id)
        claim = self._claim_path(draft_id)
        if d.status not in ("unknown",) and not claim.exists():
            raise ConsentError(f"draft {draft_id} is {d.status}; there is nothing to mark")
        verdict = (
            {"status": "sent", "sent_ref": "marked-sent-by-hand"}
            if sent
            else {"status": "drafted", "sent_ref": None}
        )
        updated = Draft(**{**asdict(d), "target": d.target, **verdict})
        self._write(updated)
        claim.unlink(missing_ok=True)
        return updated

    def _write(self, d: Draft) -> None:
        private.write_text(self._path(d.id), json.dumps(asdict(d), indent=1))


def _cannot_deliver(target: Target, text: str) -> str:
    raise ConsentError("this outbox is read-only; send through the source")


def pending_drafts(root: Path, source: str) -> list[Draft]:
    """Drafts of `source` not yet sent, oldest first, without needing a live source."""
    return Outbox(root, source, _cannot_deliver).pending()


def mark_draft(root: Path, source: str, draft_id: str, sent: bool) -> Draft:
    """Record the human's verdict on a draft whose delivery is unconfirmed."""
    return Outbox(root, source, _cannot_deliver).mark(draft_id, sent)
