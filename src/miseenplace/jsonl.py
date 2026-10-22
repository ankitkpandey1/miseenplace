"""Reading a jsonl store: numbered non-blank lines, and a parse that names the bad line."""

from collections.abc import Callable
from typing import TypeVar

from miseenplace.model import MiseError

T = TypeVar("T")


def rows(text: str) -> list[tuple[int, str]]:
    return [(n, line) for n, line in enumerate(text.splitlines(), 1) if line.strip()]


def parsed(name: str, number: int, parse: Callable[[str], T], line: str) -> T:
    try:
        return parse(line)
    except (ValueError, KeyError, TypeError, AttributeError):
        raise MiseError(f"{name} line {number} is not a valid entry; fix or delete it") from None
