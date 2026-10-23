"""Recipes: named procedures (description + steps with {placeholders}), proposed then approved.

recipes.jsonl   one version of a recipe per line: name, desc, steps, tags, status, created,
                ran (uses), last_ran ("" until used), retracted (why, when it was forgotten)

status is proposed -> approved -> superseded (a newer version was approved) or retired (forgotten).
Adding an existing name never touches the approved version: the new one waits as proposed until
`approve`. mep only prints steps; it never runs them. Steps and descriptions that look like
secrets are refused.
"""

import datetime as dt
import json
import re
import time
from dataclasses import asdict, dataclass, replace

from miseenplace.facts import looks_secret
from miseenplace.jsonl import parsed, rows
from miseenplace.model import MiseError
from miseenplace.storage.base import Storage

NAME = "recipes.jsonl"
STATUSES = ("proposed", "approved", "superseded", "retired")
# {name} is a placeholder, {{ and }} are literal braces, ${name} is shell text and stays as written.
PLACEHOLDER = re.compile(r"\{\{|\}\}|(?<!\$)\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True)
class Recipe:
    name: str
    desc: str
    steps: list[str]
    tags: list[str]
    status: str
    created: str
    ran: int = 0
    last_ran: str = ""
    retracted: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, line: str) -> "Recipe":
        r = cls(**json.loads(line))
        texts = [r.name, r.desc, r.status, r.created, r.last_ran, r.retracted]
        if not all(isinstance(x, str) for x in texts) or r.status not in STATUSES:
            raise TypeError("recipe fields must be text with a known status")
        if not isinstance(r.steps, list) or not all(isinstance(s, str) for s in r.steps):
            raise TypeError("steps must be a list of text")
        if not isinstance(r.tags, list) or not all(isinstance(t, str) for t in r.tags):
            raise TypeError("tags must be a list of text")
        if not isinstance(r.ran, int) or isinstance(r.ran, bool):
            raise TypeError("ran must be a number")
        for day in (r.created, r.last_ran or r.created):
            dt.date.fromisoformat(day)
        return r

    def line(self, width: int) -> str:
        if self.status == "proposed":
            tail = "(proposed)"
        elif self.ran:
            tail = f"(ran {self.ran}, last {self.last_ran[5:]})"
        else:
            tail = "(never run)"
        text = f"{self.name}  {self.desc}  {tail}"
        return text if len(text) <= width else text[: width - 1] + "…"


def today() -> str:
    return time.strftime("%Y-%m-%d")


def read_recipes(storage: Storage) -> list[Recipe]:
    text = storage.read_file(NAME) or ""
    found = [(n, parsed(NAME, n, Recipe.from_json, line)) for n, line in rows(text)]
    seen: set[str] = set()
    for n, r in found:
        if r.status == "approved" and r.name in seen:
            raise MiseError(f"{NAME} line {n} is a second approved {r.name}; fix or delete it")
        seen.add(r.name if r.status == "approved" else "")
    return [r for _, r in found]


def write_recipes(storage: Storage, recipes: list[Recipe]) -> None:
    storage.write_file(NAME, "".join(r.to_json() + "\n" for r in recipes))


def find_status(recipes: list[Recipe], name: str, status: str) -> Recipe | None:
    return next((r for r in recipes if r.name == name and r.status == status), None)


def replaced(recipes: list[Recipe], old: Recipe, new: Recipe) -> list[Recipe]:
    return [new if r is old else r for r in recipes]


def check_known(recipes: list[Recipe], name: str) -> None:
    """Raises unless the name has a live (proposed or approved) version; says why if retired."""
    if any(r.name == name and r.status in ("proposed", "approved") for r in recipes):
        return
    last = next((r for r in reversed(recipes) if r.name == name and r.retracted), None)
    if last is not None:
        raise MiseError(f"{name} retired: {last.retracted}")
    raise MiseError(f"no recipe {name!r}")


def add_recipe(
    storage: Storage, name: str, desc: str, steps: list[str], tags: tuple[str, ...] = ()
) -> str:
    name, desc = name.strip(), " ".join(desc.split())
    steps = [" ".join(s.split()) for s in steps]
    if not name or not desc or not all(steps) or not steps:
        raise MiseError("a recipe needs a name, a description and at least one non-empty step")
    if ":" in name or len(name.split()) > 1:
        raise MiseError("a name has no spaces or colons: use something like stop-server")
    for label, text in (
        ("description", desc),
        ("step", "\n".join(steps)),
        ("tag", " ".join(tags)),
    ):
        if looks_secret(text):
            raise MiseError(f"the {label} looks like a secret; say where it lives, not the secret")
    recipes = read_recipes(storage)
    new = Recipe(name, desc, steps, sorted(set(tags)), "proposed", today())
    # An edit of a proposal replaces it; an approved version stays until `approve`.
    kept = [r for r in recipes if not (r.name == name and r.status == "proposed")]
    write_recipes(storage, [*kept, new])
    return f"added {name} (proposed)"


def approve(storage: Storage, name: str) -> str:
    recipes = read_recipes(storage)
    check_known(recipes, name)
    proposed = find_status(recipes, name, "proposed")
    if proposed is None:
        raise MiseError(f"{name} has no proposed version to approve")
    new = replace(proposed, status="approved")
    old = find_status(recipes, name, "approved")
    if old is not None:
        new = replace(new, ran=old.ran, last_ran=old.last_ran)
        recipes = replaced(recipes, old, replace(old, status="superseded"))
    write_recipes(storage, replaced(recipes, proposed, new))
    return f"{name} approved"


def get_version(recipes: list[Recipe], name: str, proposed: bool) -> Recipe:
    check_known(recipes, name)
    found = find_status(recipes, name, "proposed" if proposed else "approved")
    if found is None:
        hint = "no approved version yet; `recipe approve` it or show --proposed"
        raise MiseError(f"{name} has no proposed version" if proposed else f"{name}: {hint}")
    return found


def fill(steps: list[str], values: dict[str, str]) -> tuple[list[str], list[str], list[str]]:
    """Returns (numbered steps with known placeholders filled, unfilled names, unused keys)."""
    missing: list[str] = []
    used: set[str] = set()

    def sub(m: re.Match[str]) -> str:
        name = m.group(1)
        if name is None:
            return m.group(0)[0]  # {{ -> {  and  }} -> }
        used.add(name)
        if name in values:
            return values[name]
        if name not in missing:
            missing.append(name)
        return m.group(0)

    lines = [f"{i}. {PLACEHOLDER.sub(sub, s)}" for i, s in enumerate(steps, 1)]
    return lines, missing, [k for k in values if k not in used]


def ran(storage: Storage, name: str) -> Recipe:
    recipes = read_recipes(storage)
    old = get_version(recipes, name, False)
    new = replace(old, ran=old.ran + 1, last_ran=today())
    write_recipes(storage, replaced(recipes, old, new))
    return new


def forget(storage: Storage, name: str, why: str) -> None:
    recipes = read_recipes(storage)
    check_known(recipes, name)
    reason = why or "no reason given"
    write_recipes(
        storage,
        [
            (
                replace(r, status="retired", retracted=reason)
                if r.name == name and r.status in ("proposed", "approved")
                else r
            )
            for r in recipes
        ],
    )


def listed(recipes: list[Recipe], show_all: bool, tag: str) -> list[Recipe]:
    """Approved by last use (never run last), then, with show_all, proposed by name."""
    keep = [r for r in recipes if not tag or tag in r.tags]
    used = sorted((r for r in keep if r.status == "approved"), key=lambda r: r.name)
    used.sort(key=lambda r: r.last_ran, reverse=True)  # stable: ties stay by name, "" sinks
    waiting = sorted((r for r in keep if r.status == "proposed"), key=lambda r: r.name)
    return [*used, *waiting] if show_all else used
