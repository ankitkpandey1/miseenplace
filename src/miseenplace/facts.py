"""Facts: what is true about your world, with history, source and forget.

facts.jsonl   one version of a fact per line: key, value, source, tags, valid_from, verified,
              invalid_at ("" while current), retracted (why, when it was forgotten)

Adding a key that already has a current value closes that value (`invalid_at`) and keeps it;
nothing is deleted. Forgetting closes the fact and records a retraction, so adding the same
value again later warns. Only an explicit add or forget changes a fact; nothing is resolved
automatically. Values that look like secrets are refused: store where a secret lives.
"""

import datetime as dt
import json
import math
import re
import time
from dataclasses import asdict, dataclass, replace

from miseenplace.jsonl import parsed, rows
from miseenplace.model import MiseError
from miseenplace.storage.base import Storage

NAME = "facts.jsonl"
UNKNOWN = "unknown"
STALE_DAYS = 30
CHAT_SOURCE = re.compile(r"\b(zulip|slack|chat|dm|message\s+(id|\d+))\b", re.IGNORECASE)
KEYWORD_VALUE = re.compile(
    r"(token|passw(or)?d|secret|api[_-]?key|private[_-]?key)\s*[:=]\s*[\"']?([^\s\"']+)",
    re.IGNORECASE,
)
SECRET = re.compile(
    r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"
    r"|://[^/\s:@]+:[^/\s@]+@"
    r"|(?<![A-Za-z0-9])(gh[pousr]_[A-Za-z0-9]{8,}|github_pat_[A-Za-z0-9_]{8,}|sk-[A-Za-z0-9_-]{8,}"
    r"|AKIA[0-9A-Z]{12,}|xox[abp]-[A-Za-z0-9-]{8,})"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY",
    re.IGNORECASE,
)
MIN_RANDOM = 32
MIN_ENTROPY = 3.5  # bits per character; words and repeated text score well below it


@dataclass(frozen=True)
class Fact:
    key: str
    value: str
    source: str
    tags: list[str]
    valid_from: str
    verified: str
    invalid_at: str = ""
    retracted: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, line: str) -> "Fact":
        fact = cls(**json.loads(line))
        texts = [fact.key, fact.value, fact.source, fact.retracted]
        if not all(isinstance(x, str) for x in texts):
            raise TypeError("fact fields must be text")
        if not isinstance(fact.tags, list) or not all(isinstance(t, str) for t in fact.tags):
            raise TypeError("tags must be a list of text")
        for day in (fact.valid_from, fact.verified, fact.invalid_at or fact.valid_from):
            dt.date.fromisoformat(day)
        return fact

    @property
    def current(self) -> bool:
        return not self.invalid_at

    @property
    def untrusted(self) -> bool:
        return CHAT_SOURCE.search(self.source) is not None

    def line(self, width: int, now: str) -> str:
        flag = " untrusted" if self.untrusted else ""
        verified = f"verified {age(self.verified, now)}d"
        text = f"{self.key}: {self.value}  ({self.source}, {verified}{flag})"
        return text if len(text) <= width else text[: width - 1] + "…"

    def history_line(self, width: int) -> str:
        text = f"{self.value}  valid {self.valid_from} ->"
        text = f"{text} {self.invalid_at}" if self.invalid_at else text
        text = f"{text}  retracted: {self.retracted}" if self.retracted else text
        return text if len(text) <= width else text[: width - 1] + "…"


def today() -> str:
    return time.strftime("%Y-%m-%d")


def age(then: str, now: str) -> int:
    return (dt.date.fromisoformat(now) - dt.date.fromisoformat(then)).days


def read_facts(storage: Storage) -> list[Fact]:
    text = storage.read_file(NAME) or ""
    return [parsed(NAME, n, Fact.from_json, line) for n, line in rows(text)]


def write_facts(storage: Storage, facts: list[Fact]) -> None:
    storage.write_file(NAME, "".join(f.to_json() + "\n" for f in facts))


def current(facts: list[Fact]) -> list[Fact]:
    return [f for f in facts if f.current]


def find_current(facts: list[Fact], key: str) -> Fact | None:
    return next((f for f in facts if f.key == key and f.current), None)


def get_current(facts: list[Fact], key: str) -> Fact:
    found = find_current(facts, key)
    if found is not None:
        return found
    last = next((f for f in reversed(facts) if f.key == key), None)
    if last is not None and last.retracted:
        raise MiseError(f"{key} retracted on {last.invalid_at}: {last.retracted}")
    raise MiseError(f"no current fact {key!r}")


def replaced(facts: list[Fact], old: Fact, new: Fact) -> list[Fact]:
    return [new if f is old else f for f in facts]


def entropy(token: str) -> float:
    counts = [token.count(c) for c in set(token)]
    return -sum(n / len(token) * math.log2(n / len(token)) for n in counts)


def looks_secret(text: str) -> bool:
    if SECRET.search(text):
        return True
    for m in KEYWORD_VALUE.finditer(text):
        word = m.group(3)
        if len(word) >= 6 and any(c.isdigit() for c in word) and any(c.isalpha() for c in word):
            return True
    return any(
        len(w) >= MIN_RANDOM
        and not set(w) & set("/-")
        and any(c.isdigit() for c in w)
        and entropy(w) >= MIN_ENTROPY
        for w in text.split()
    )


def add_fact(
    storage: Storage, key: str, value: str, source: str = "", tags: tuple[str, ...] = ()
) -> tuple[str, str]:
    """Returns (what happened, a warning); the fact is stored either way."""
    key, value, source = key.strip(), " ".join(value.split()), source.strip()
    if not key or not value:
        raise MiseError("a fact needs a non-empty key and value")
    if ":" in key or len(key.split()) > 1:
        raise MiseError("a key has no spaces or colons: use something like prod-runs-on")
    for label, text in (("value", value), ("source", source), ("tag", " ".join(tags))):
        if looks_secret(text):
            raise MiseError(
                f"the {label} looks like a secret; store where it lives, not the secret"
            )
    facts = read_facts(storage)
    day = today()
    old = find_current(facts, key)
    if old is not None and old.value == value:
        new = replace(old, verified=day, source=source or old.source, tags=sorted(tags) or old.tags)
        write_facts(storage, replaced(facts, old, new))
        return f"{key} already current; source and tags updated, verified today", ""
    warning = ""
    gone = next(
        (f for f in reversed(facts) if f.key == key and f.value == value and f.retracted), None
    )
    if gone is not None:
        warning = f"{key} was retracted on {gone.invalid_at}: {gone.retracted}"
    if old is not None:
        facts = replaced(facts, old, replace(old, invalid_at=day))
    new = Fact(key, value, source or UNKNOWN, sorted(set(tags)), day, day)
    write_facts(storage, [*facts, new])
    return f"{key} stored", warning


def verify(storage: Storage, key: str) -> Fact:
    facts = read_facts(storage)
    fact = replace(get_current(facts, key), verified=today())
    write_facts(storage, replaced(facts, get_current(facts, key), fact))
    return fact


def forget(storage: Storage, key: str, why: str) -> None:
    facts = read_facts(storage)
    old = get_current(facts, key)
    write_facts(
        storage,
        replaced(facts, old, replace(old, invalid_at=today(), retracted=why or "no reason given")),
    )


def history(facts: list[Fact], key: str) -> list[Fact]:
    found = [f for f in facts if f.key == key]
    if not found:
        raise MiseError(f"no fact {key!r}")
    return found[::-1]


def text(f: Fact) -> str:
    return " ".join([f.key, f.value, f.source, *f.tags]).lower()


def search(facts: list[Fact], words: str) -> list[Fact]:
    need = words.lower().split()
    return [f for f in current(facts) if all(w in text(f) for w in need)]


def stale(facts: list[Fact], days: int) -> list[Fact]:
    now = today()
    return [f for f in current(facts) if age(f.verified, now) >= days]
