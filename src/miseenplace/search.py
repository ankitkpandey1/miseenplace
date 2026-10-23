"""Search: one query across everything mep keeps, plus the chat source.

Stores (`--in KIND`): item (title, context, next, notes, decisions, topic, tags), log (diary),
draft, request (the request log), lesson, fact (current), recipe (not retracted), ask, chat.

Matching is case-insensitive and every query word must match. A word matches a document when it
is a substring (an exact hit) or, unless `exact`, when it is close to one of the document's words:
`difflib` similarity of at least FUZZY_MIN, or an abbreviation (`mgmt` for `management`: same first
letter, letters in order). Words under FUZZY_WORD letters or containing a digit are never fuzzy.
Ranking: documents where every word is an exact hit first, then by mean word score, then newest.

Chat is searched by the source (Zulip's `search` narrow). If that finds nothing, the query is
retried once per word and the union is fuzzy-ranked here. Kinds without a date drop out under
`--days`; kinds without a counterparty drop out under `--from`.
"""

import datetime as dt
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher

from miseenplace import asks, consent, facts, lessons, personal, recipes
from miseenplace.chat import Message
from miseenplace.inbox import compact
from miseenplace.model import MiseError, now
from miseenplace.sources.base import Searchable, Source, SourceError
from miseenplace.store import Store

KINDS = ("item", "log", "draft", "request", "lesson", "fact", "recipe", "ask", "chat")
DEFAULT_LIMIT = 10
FUZZY_MIN = 0.75
FUZZY_WORD = 4
EXACT_SCORE = 1.0
SUBSTRING_SCORE = 0.95
ABBREVIATION_SCORE = 0.8
TEXT_WIDTH = 90
WHERE_WIDTH = 28
WORD = re.compile(r"\w+")
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
ALL_ROWS = sys.maxsize
SECONDS_PER_DAY = 86400

Rank = tuple[int, float, float]  # exact tier, mean word score, time (all higher is better)


@dataclass(frozen=True)
class Query:
    text: str
    kinds: tuple[str, ...] = ()
    who: str | None = None
    days: float | None = None
    exact: bool = False
    limit: int = DEFAULT_LIMIT


@dataclass(frozen=True)
class Doc:
    kind: str
    line: str
    text: str
    who: str = ""
    ts: float | None = None


@dataclass(frozen=True)
class Hit:
    kind: str
    line: str
    rank: Rank


# ---- matching ----


def tokens(text: str) -> list[str]:
    return WORD.findall(text.lower())


def in_order(word: str, token: str) -> bool:
    """`word` is an abbreviation of `token`: same first letter, the rest in order."""
    if word[0] != token[0]:
        return False
    letters = iter(token)
    return all(c in letters for c in word)


def fuzzy_score(word: str, vocabulary: set[str]) -> float:
    """Best similarity of `word` to a vocabulary word; cheap upper bounds skip most of them."""
    if len(word) < FUZZY_WORD or any(c.isdigit() for c in word):
        return 0.0
    matcher = SequenceMatcher(None)
    matcher.set_seq2(word)  # the word is the cached side; only the token changes
    best = 0.0
    for token in vocabulary:
        matcher.set_seq1(token)
        if matcher.real_quick_ratio() >= FUZZY_MIN and matcher.quick_ratio() >= FUZZY_MIN:
            ratio = matcher.ratio()
            if ratio >= FUZZY_MIN:
                best = max(best, ratio)
                continue
        if len(token) > len(word) and in_order(word, token):
            best = max(best, ABBREVIATION_SCORE)
    return best


def word_score(word: str, haystack: str, vocabulary: set[str], exact: bool) -> float:
    if word in vocabulary:
        return EXACT_SCORE
    if word in haystack:
        return SUBSTRING_SCORE
    return 0.0 if exact else fuzzy_score(word, vocabulary)


def match(words: list[str], text: str, exact: bool) -> tuple[int, float] | None:
    """(1 if every word is an exact hit else 0, mean word score), or None unless all words match."""
    haystack = text.lower()
    vocabulary = set(tokens(haystack))
    scores: list[float] = []
    for w in words:
        score = word_score(w, haystack, vocabulary, exact)
        if score <= 0.0:
            return None  # every word must match: no need to score the rest
        scores.append(score)
    if not scores:
        return None
    return int(min(scores) >= SUBSTRING_SCORE), sum(scores) / len(scores)


def ranked(docs: list[Doc], q: Query) -> list[Hit]:
    words = tokens(q.text)
    cutoff = None if q.days is None else time.time() - q.days * SECONDS_PER_DAY
    hits: list[Hit] = []
    for d in docs:
        if cutoff is not None and (d.ts is None or d.ts < cutoff):
            continue
        if q.who and q.who.lower() not in d.who.lower():
            continue
        m = match(words, d.text, q.exact)
        if m:
            hits.append(Hit(d.kind, d.line, (m[0], m[1], d.ts or 0.0)))
    return hits


# ---- documents, one builder per store ----


def stamp(text: str) -> float:
    return dt.datetime.fromisoformat(text).timestamp()


def item_docs(store: Store) -> list[Doc]:
    docs = []
    for i in store.load()[1]:
        decisions = [f"{d['what']} {d['why']}" for d in i["decisions"]]
        fields = [i["title"], i["context"], i["next"], i["topic"], *i["notes"], *decisions]
        text = " ".join([f for f in fields if f] + i["tags"])
        tail = " · ".join(x for x in (i["state"], i["who"]) if x)
        line = f"item · #{i['id']} · {compact(i['title'], TEXT_WIDTH)} · {tail}"
        docs.append(Doc("item", line, text, i["who"] or "", stamp(i["updated"])))
    return docs


def log_docs(store: Store) -> list[Doc]:
    docs = []
    for e in store.read_log():
        if e["op"] == "log":
            c = e["changes"]
            line = f"log · {e['ts'][5:10]} · {compact(str(c['text']), TEXT_WIDTH)}"
            docs.append(Doc("log", line, f"{c['text']} {c['topic'] or ''}", "", stamp(e["ts"])))
    return docs


def draft_docs(store: Store) -> list[Doc]:
    docs = []
    for d in consent.all_drafts(store.root):
        dest = d.target.label or d.target.to
        line = f"draft · {d.id} · {d.status} · {compact(dest, WHERE_WIDTH)} · "
        docs.append(
            Doc("draft", line + compact(d.text, TEXT_WIDTH), f"{dest} {d.text}", d.target.to,
                d.created_ts)
        )  # fmt: skip
    return docs


def request_docs(store: Store) -> list[Doc]:
    return [
        Doc("request", f"request · {r.ts[:16]} · {compact(r.text, TEXT_WIDTH)}", r.text, "",
            stamp(r.ts))
        for r in personal.read_requests(store.storage, ALL_ROWS)
    ]  # fmt: skip


def lesson_docs(store: Store) -> list[Doc]:
    return [
        Doc("lesson", "lesson · " + x.line(TEXT_WIDTH), f"{x.when} {x.do} {x.not_} {x.evidence}")
        for x in lessons.read_lessons(store.storage)
    ]


def fact_docs(store: Store) -> list[Doc]:
    return [
        Doc("fact", f"fact · {f.key}: {compact(f.value, TEXT_WIDTH)}",
            " ".join([f.key, f.value, f.source, *f.tags]), "", stamp(f.verified))
        for f in facts.current(facts.read_facts(store.storage))
    ]  # fmt: skip


def recipe_docs(store: Store) -> list[Doc]:
    return [
        Doc("recipe", "recipe · " + r.line(TEXT_WIDTH),
            " ".join([r.name, r.desc, *r.steps, *r.tags]), "", stamp(r.last_ran or r.created))
        for r in recipes.read_recipes(store.storage)
        if not r.retracted
    ]  # fmt: skip


def ask_docs(store: Store) -> list[Doc]:
    return [
        Doc("ask", "ask · " + a.line(TEXT_WIDTH, now()),
            f"{a.decision} {a.recommend} {a.default}", "", stamp(a.created))
        for a in asks.read_asks(store.storage)
    ]  # fmt: skip


BUILDERS: dict[str, Callable[[Store], list[Doc]]] = {
    "item": item_docs,
    "log": log_docs,
    "draft": draft_docs,
    "request": request_docs,
    "lesson": lesson_docs,
    "fact": fact_docs,
    "recipe": recipe_docs,
    "ask": ask_docs,
}


# ---- chat ----


def clean(text: str) -> str:
    """Chat text is untrusted: control characters (terminal escapes, CR) become spaces."""
    return CONTROL.sub(" ", text)


def chat_line(m: Message) -> str:
    where, text = compact(clean(m.where), WHERE_WIDTH), compact(clean(m.text), TEXT_WIDTH)
    parts = ["chat", clean(m.id), clean(m.sender), where, text]
    return " · ".join(parts + ([clean(m.url)] if m.url else []))


def chat_docs(messages: list[Message]) -> list[Doc]:
    return [Doc("chat", chat_line(m), m.text + " " + m.where, m.sender, m.ts) for m in messages]


def chat_hits(src: Source, q: Query) -> list[Hit]:
    """Ask the source; if it finds nothing, ask once per word and fuzzy-rank the union here."""
    if not isinstance(src, Searchable):
        raise MiseError(f"source {src.name!r} cannot search; use --in with another kind")
    found = src.search(q.text, q.who, q.days, q.limit)
    if found:
        return [Hit("chat", d.line, (1, EXACT_SCORE, d.ts or 0.0)) for d in chat_docs(found)]
    words = list(dict.fromkeys(tokens(q.text)))
    if q.exact or len(words) < 2:
        return []
    union = {m.id: m for w in words for m in src.search(w, q.who, q.days, q.limit)}
    return ranked(chat_docs(list(union.values())), q)


def chat_part(open_src: Callable[[], Source], q: Query, strict: bool) -> list[Hit]:
    """Strict (`--in chat`): any problem is an error. Global: a source that cannot search, or
    that is not configured, is skipped silently; an unreachable one gets a stderr note."""
    try:
        src = open_src()
        if not strict and not isinstance(src, Searchable):
            return []
        return chat_hits(src, q)
    except SourceError as e:
        if strict:
            raise
        print(f"miseenplace: chat not searched ({e})", file=sys.stderr)
    except MiseError:
        if strict:
            raise
    return []


# ---- entry ----


def search_all(store: Store, q: Query, open_src: Callable[[], Source]) -> list[str]:
    """Lines for the best `q.limit` hits, grouped by kind in KINDS order, best first in a group."""
    if not tokens(q.text):
        raise MiseError(f"nothing to search for in {q.text!r}; use words or numbers")
    wanted = q.kinds or KINDS
    hits: list[Hit] = []
    for kind, build in BUILDERS.items():
        if kind in wanted:
            hits += ranked(build(store), q)
    if "chat" in wanted:
        hits += chat_part(open_src, q, strict="chat" in q.kinds)
    best = sorted(hits, key=lambda h: h.rank, reverse=True)[: q.limit]
    return [h.line for h in sorted(best, key=lambda h: KINDS.index(h.kind))]
