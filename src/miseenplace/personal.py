"""The user's own words, kept as data in the data root (never in the code repository).

style/<source>.md            how the user writes (learn-style writes it; drafting reads it)
corpus/<source>/sent.jsonl   the user's own sent messages, deduplicated by message id
requests.jsonl               what the user asked their agent to do, verbatim, with a timestamp

Why a verbatim request log: a summary drifts every time it is rewritten, and a later session
works from the summary. The user's exact words do not drift.
"""

import datetime as dt
import json
from dataclasses import asdict, dataclass

from miseenplace.model import MiseError
from miseenplace.sources.base import Digestible, Source
from miseenplace.storage.base import Storage

REQUESTS = "requests.jsonl"


def style_name(source: str) -> str:
    return f"style/{source}.md"


def corpus_name(source: str) -> str:
    return f"corpus/{source}/sent.jsonl"


def read_style(storage: Storage, source: str) -> str | None:
    return storage.read_file(style_name(source))


def write_style(storage: Storage, source: str, text: str) -> None:
    if not text.strip():
        raise MiseError("empty style file; write the style text first")
    storage.write_file(style_name(source), text.rstrip() + "\n")


# ---- corpus ----


@dataclass(frozen=True)
class Sent:
    id: str
    ts: float
    where: str
    text: str


def read_corpus(storage: Storage, source: str) -> list[Sent]:
    text = storage.read_file(corpus_name(source)) or ""
    return [Sent(**json.loads(line)) for line in text.splitlines() if line.strip()]


def learn(storage: Storage, source: Source, days: float) -> tuple[int, int]:
    """Fetch my own messages of the last `days` and append the new ones. Returns (added, total)."""
    if not isinstance(source, Digestible):
        raise MiseError(f"source {source.name!r} cannot list history; style learn needs digest")
    known = {s.id for s in read_corpus(storage, source.name)}
    fresh = [
        Sent(m.id, m.ts, m.where, m.text)
        for m in source.digest(days, include_noise=True, only_mine=True)
        if m.mine and m.id not in known and m.text.strip()
    ]
    if fresh:
        storage.append_file(
            corpus_name(source.name), "".join(json.dumps(asdict(s)) + "\n" for s in fresh)
        )
    return len(fresh), len(known) + len(fresh)


def sample(storage: Storage, source: str, n: int) -> list[Sent]:
    """The most recent `n` sent messages, oldest first."""
    return sorted(read_corpus(storage, source), key=lambda s: s.ts)[-n:]


# ---- request log ----


@dataclass(frozen=True)
class Request:
    ts: str
    source: str | None
    text: str


def note_request(storage: Storage, text: str, source: str | None) -> Request:
    if not text.strip():
        raise MiseError("empty request; pass the user's exact words")
    r = Request(dt.datetime.now().isoformat(timespec="seconds"), source, text)
    storage.append_file(REQUESTS, json.dumps(asdict(r)) + "\n")
    return r


def read_requests(storage: Storage, n: int) -> list[Request]:
    text = storage.read_file(REQUESTS) or ""
    rows = [Request(**json.loads(x)) for x in text.splitlines() if x]
    return rows[-n:]
