"""Lessons learned from real corrections, and the weekly replay that keeps them honest.

lessons.jsonl        one lesson per line: when (trigger), do (action), not (the wrong action),
                     evidence, votes, pattern (optional regex that spots the wrong action)
corrections.jsonl    past corrections: `wrong` (what was done) and `fine` (what was wanted)
lessons.good.jsonl   the lesson set that last scored at least as well as the one before it

A new lesson starts at 2 votes; a vote up or down moves it by one; at 0 it is deleted.
`check` replays every correction against the lessons that have a pattern: a correction passes
when some pattern matches its `wrong` text and no pattern matches its `fine` text. If the
current set passes fewer than the last good set did, the last good set is restored.
"""

import json
import re
from dataclasses import dataclass, replace

from miseenplace.jsonl import parsed, rows
from miseenplace.model import MiseError
from miseenplace.storage.base import Storage

LESSONS = "lessons.jsonl"
CASES = "corrections.jsonl"
GOOD = "lessons.good.jsonl"
START_VOTES = 2


@dataclass(frozen=True)
class Lesson:
    id: int
    when: str
    do: str
    not_: str
    evidence: str
    votes: int = START_VOTES
    pattern: str = ""

    def line(self, width: int, full: bool = False) -> str:
        text = f"L{self.id} v{self.votes} when {self.when} -> do {self.do}, not {self.not_}"
        if full:
            return f"{text} | evidence: {self.evidence}"
        return text if len(text) <= width else text[: width - 1] + "…"

    def to_json(self) -> str:
        return json.dumps(
            {
                "id": self.id,
                "when": self.when,
                "do": self.do,
                "not": self.not_,
                "evidence": self.evidence,
                "votes": self.votes,
                "pattern": self.pattern,
            }
        )

    @classmethod
    def from_json(cls, line: str) -> "Lesson":
        raw = json.loads(line)
        return cls(
            int(raw["id"]),
            str(raw["when"]),
            str(raw["do"]),
            str(raw["not"]),
            str(raw["evidence"]),
            int(raw["votes"]),
            str(raw.get("pattern", "")),
        )


@dataclass(frozen=True)
class Case:
    wrong: str
    fine: str

    @classmethod
    def from_json(cls, line: str) -> "Case":
        raw = json.loads(line)
        return cls(str(raw["wrong"]), str(raw["fine"]))


def read_lessons(storage: Storage, name: str = LESSONS) -> list[Lesson]:
    text = storage.read_file(name) or ""
    return [parsed(name, n, Lesson.from_json, line) for n, line in rows(text)]


def write_lessons(storage: Storage, lessons: list[Lesson], name: str = LESSONS) -> None:
    storage.write_file(name, "".join(x.to_json() + "\n" for x in lessons))


def read_cases(storage: Storage) -> list[Case]:
    text = storage.read_file(CASES) or ""
    return [parsed(CASES, n, Case.from_json, line) for n, line in rows(text)]


def add_case(storage: Storage, wrong: str, fine: str) -> None:
    if not wrong.strip() or not fine.strip():
        raise MiseError("a correction needs both the wrong text and the text that was wanted")
    storage.append_file(CASES, json.dumps({"wrong": wrong, "fine": fine}) + "\n")


def add_lesson(
    storage: Storage, when: str, do: str, not_: str, evidence: str, pattern: str
) -> Lesson:
    if not all(s.strip() for s in (when, do, not_, evidence)):
        raise MiseError("a lesson needs when, do, not and evidence, none empty")
    try:
        matches_nothing_specific = bool(pattern) and re.compile(pattern).search("") is not None
    except re.error as e:
        raise MiseError(f"pattern is not a valid regex: {e}") from None
    if matches_nothing_specific:
        raise MiseError("pattern matches the empty string, so it would flag everything")
    seed_baseline(storage)
    lessons = read_lessons(storage)
    next_id = max((x.id for x in lessons), default=0) + 1
    new = Lesson(next_id, when, do, not_, evidence, pattern=pattern)
    write_lessons(storage, [*lessons, new])
    return new


def seed_baseline(storage: Storage) -> None:
    """Without a good set the first worsening change would be accepted: save one before mutating."""
    if storage.read_file(GOOD) is None:
        write_lessons(storage, read_lessons(storage), GOOD)


def vote(storage: Storage, lesson_id: int, delta: int) -> str:
    seed_baseline(storage)
    lessons = read_lessons(storage)
    found = next((x for x in lessons if x.id == lesson_id), None)
    if found is None:
        raise MiseError(f"no lesson L{lesson_id}")
    votes = found.votes + delta
    if votes <= 0:
        write_lessons(storage, [x for x in lessons if x.id != lesson_id])
        return f"L{lesson_id} deleted"
    write_lessons(storage, [replace(x, votes=votes) if x.id == lesson_id else x for x in lessons])
    return f"L{lesson_id} v{votes}"


def passed(lessons: list[Lesson], cases: list[Case]) -> int:
    """How many corrections the lesson patterns catch without flagging the wanted text."""
    patterns = [re.compile(x.pattern) for x in lessons if x.pattern]
    return sum(
        any(p.search(c.wrong) for p in patterns) and not any(p.search(c.fine) for p in patterns)
        for c in cases
    )


def differences(now: list[Lesson], good: list[Lesson]) -> str:
    """What a restore undoes, by lesson id."""
    have, was = {x.id: x for x in now}, {x.id: x for x in good}
    parts = {
        "restored": [i for i in was if i not in have],
        "removed": [i for i in have if i not in was],
        "changed": [i for i in have if i in was and have[i] != was[i]],
    }
    return "; ".join(f"{k} " + " ".join(f"L{i}" for i in v) for k, v in parts.items() if v)


def check(storage: Storage) -> str:
    """Replay the corrections; keep the lessons if no worse than the last good set, else revert."""
    cases = read_cases(storage)
    now = read_lessons(storage)
    score = passed(now, cases)
    if storage.read_file(GOOD) is not None:
        good = read_lessons(storage, GOOD)
        before = passed(good, cases)
        if score < before:
            what = differences(now, good)
            write_lessons(storage, good)
            return f"reverted: {score}/{len(cases)} < {before}/{len(cases)}; {what}"
    if storage.read_file(GOOD) != "".join(x.to_json() + "\n" for x in now):
        write_lessons(storage, now, GOOD)
    unchecked = sum(1 for x in now if not x.pattern)
    return f"ok: {score}/{len(cases)} corrections caught, {unchecked} lessons unchecked"
