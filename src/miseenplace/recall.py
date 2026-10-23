"""Recall: one relevance-ranked read across lessons, facts and approved recipes.

BM25 (k1=1.2, b=0.75) over every current lesson, current fact and approved recipe, in pure
Python. A name/key or tag token counts twice, a body token once (lesson: when, do, not; fact:
value, source; recipe: description, steps). Tokens are lowercase runs of letters and digits, so
`8260`, `koyal` and `pr7101` survive. A query term with no exact vocabulary hit is replaced by the
closest vocabulary term by character-trigram Jaccard similarity (at least 0.5), else dropped.
A term containing a digit is never fuzzy-matched: `826000` must not find `82600`.

Recency is a multiplier on the BM25 score, never more than 1.2: a recipe's last_ran and a fact's
verified date give up to +0.2, halving every 30 days; a lesson gets +0.01 per vote, at most +0.1.

Every query is appended to recall.jsonl (ts, query, kind, returned keys) so a later check can
tell whether the needed item came back. Nothing leaves the machine.
"""

import json
import math
import re
import time
from collections import Counter
from dataclasses import dataclass

from miseenplace import facts, lessons, recipes
from miseenplace.storage.base import Storage

LOG = "recall.jsonl"
DEFAULT_MAX_CHARS = 1200
K1, B = 1.2, 0.75
NAME_WEIGHT = 2
FUZZY_MIN = 0.5
MAX_BOOST = 0.2
HALF_LIFE_DAYS = 30
VOTE_BOOST, MAX_VOTE_BOOST = 0.01, 0.1
TOKEN = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Item:
    kind: str
    key: str
    line: str
    weights: Counter[str]
    boost: float


def tokens(text: str) -> list[str]:
    return TOKEN.findall(text.lower())


def weighted(names: list[str], body: list[str]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for text in names:
        for t in tokens(text):
            counts[t] += NAME_WEIGHT
    for text in body:
        counts.update(tokens(text))
    return counts


def recency(day: str, today: str) -> float:
    if not day:
        return 1.0
    halvings = max(facts.age(day, today), 0) / HALF_LIFE_DAYS
    return 1.0 + MAX_BOOST * math.pow(0.5, halvings)


def lesson_item(x: lessons.Lesson) -> Item:
    boost = 1.0 + min(x.votes * VOTE_BOOST, MAX_VOTE_BOOST)
    weights = weighted([], [x.when, x.do, x.not_])
    return Item("lesson", f"L{x.id}", f"lesson when {x.when} -> do {x.do}", weights, boost)


def fact_item(f: facts.Fact, today: str) -> Item:
    line = f"fact {f.key}: {f.value} ({f.source}, verified {facts.age(f.verified, today)}d)"
    weights = weighted([f.key, *f.tags], [f.value, f.source])
    return Item("fact", f.key, line, weights, recency(f.verified, today))


def recipe_item(r: recipes.Recipe, today: str) -> Item:
    weights = weighted([r.name, *r.tags], [r.desc, *r.steps])
    return Item("recipe", r.name, f"recipe {r.name}: {r.desc}", weights, recency(r.last_ran, today))


def load(storage: Storage, kind: str, today: str) -> list[Item]:
    items: list[Item] = []
    if kind in ("", "lesson"):
        items += [lesson_item(x) for x in lessons.read_lessons(storage)]
    if kind in ("", "fact"):
        items += [fact_item(f, today) for f in facts.current(facts.read_facts(storage))]
    if kind in ("", "recipe"):
        approved = [r for r in recipes.read_recipes(storage) if r.status == "approved"]
        items += [recipe_item(r, today) for r in approved]
    return items


def trigrams(word: str) -> set[str]:
    return {word[i : i + 3] for i in range(len(word) - 2)} or {word}


def closest(term: str, vocabulary: set[str]) -> str | None:
    """The vocabulary term most like `term` (trigram Jaccard >= FUZZY_MIN); ties go alphabetical."""
    mine = trigrams(term)
    best, best_score = None, 0.0
    for word in sorted(vocabulary):
        theirs = trigrams(word)
        score = len(mine & theirs) / len(mine | theirs)
        if score >= FUZZY_MIN and score > best_score:
            best, best_score = word, score
    return best


def query_terms(query: str, vocabulary: set[str]) -> list[str]:
    found: list[str] = []
    for term in tokens(query):
        if term in vocabulary:
            hit: str | None = term
        else:
            hit = None if any(c.isdigit() for c in term) else closest(term, vocabulary)
        if hit is not None and hit not in found:
            found.append(hit)
    return found


def bm25(item: Item, terms: list[str], docs: list[Item], average: float) -> float:
    length = sum(item.weights.values())
    score = 0.0
    for term in terms:
        tf = item.weights[term]
        if not tf:
            continue
        holding = sum(1 for d in docs if term in d.weights)
        idf = math.log(1 + (len(docs) - holding + 0.5) / (holding + 0.5))
        score += idf * tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / average))
    return score


def rank(items: list[Item], query: str) -> list[Item]:
    """Items with a positive score, best first; equal scores keep their stored order."""
    vocabulary = {t for item in items for t in item.weights}
    terms = query_terms(query, vocabulary)
    average = sum(sum(i.weights.values()) for i in items) / len(items) if items else 1.0
    scored = [(bm25(item, terms, items, average) * item.boost, n) for n, item in enumerate(items)]
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [items[n] for score, n in scored if score > 0]


def cut(matched: list[Item], max_chars: int) -> list[Item]:
    """The best items whose lines (one newline each) fit; the rest are left out."""
    shown: list[Item] = []
    used = 0
    for item in matched:
        if used + len(item.line) + 1 > max_chars:
            break
        shown.append(item)
        used += len(item.line) + 1
    return shown


def footer(shown: list[Item], matched: int, total: int) -> str:
    chars = sum(len(x.line) + 1 for x in shown)
    text = f"{len(shown)} of {total} items" + (f", {chars} chars" if shown else "")
    return f"{text}, {matched - len(shown)} left out" if matched > len(shown) else text


def log_query(storage: Storage, query: str, kind: str, shown: list[Item]) -> None:
    row = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "query": query,
        "kind": kind,
        "returned": [f"{x.kind}:{x.key}" for x in shown],
    }
    storage.append_file(LOG, json.dumps(row) + "\n")


def recall(storage: Storage, query: str, max_chars: int, kind: str) -> str:
    items = load(storage, kind, facts.today())
    matched = rank(items, query)
    shown = cut(matched, max_chars)
    log_query(storage, query, kind, shown)
    return "\n".join([*(x.line for x in shown), footer(shown, len(matched), len(items))])
