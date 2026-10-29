"""`status`: a live snapshot of what is open, who holds it, and where its PRs stand right now.

Items come from the journal; every PR ref is asked of `gh` at run time, in parallel, so the
answer cannot be older than the command. A PR `gh` cannot read gets a line saying so.
"""

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from miseenplace.inbox import compact
from miseenplace.model import Item, MiseError
from miseenplace.reconcile import GH_TIMEOUT, first_line, pr_urls
from miseenplace.today import plain

FIELDS = "state,reviewDecision,headRefOid,statusCheckRollup,mergedAt"
PR_PARTS_RE = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)")
FAILED = ("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE")
DONE_PRS = ("MERGED", "CLOSED")
NOTE_WIDTH = 60
NO_GH = "gh: not installed"

Fetch = Callable[[str], str]  # PR url -> `gh pr view --json` output; raises MiseError


@dataclass(frozen=True)
class Pr:
    """One PR as GitHub reports it now, or why we could not ask."""

    repo: str
    number: int
    state: str = ""
    review: str = ""
    ci: str = ""
    head: str = ""
    error: str = ""

    @property
    def ref(self) -> str:
        return f"{self.repo}#{self.number}"

    @property
    def closed(self) -> bool:
        return self.state in DONE_PRS


def split_url(url: str) -> tuple[str, int]:
    m = PR_PARTS_RE.fullmatch(url)
    if m is None:
        raise MiseError(f"not a pull request url: {url}")
    return m.group(1), int(m.group(2))


def gh_status_json(url: str) -> str:
    repo, number = split_url(url)
    done = subprocess.run(
        ["gh", "pr", "view", str(number), "-R", repo, "--json", FIELDS],
        capture_output=True,
        text=True,
        check=False,
        timeout=GH_TIMEOUT,
    )
    if done.returncode != 0:
        raise MiseError(first_line(done.stderr))
    return done.stdout


def status_fetcher() -> Fetch | None:
    return gh_status_json if shutil.which("gh") else None


def ci_of(rollup: object) -> str:
    """pass | fail | pending | none, from `statusCheckRollup` (check runs and commit statuses)."""
    checks = [c for c in rollup if isinstance(c, dict)] if isinstance(rollup, list) else []
    if not checks:
        return "none"
    verdicts = [str(c.get("conclusion") or c.get("state") or "").upper() for c in checks]
    if any(v in FAILED for v in verdicts):
        return "fail"
    if any(v in ("", "PENDING", "EXPECTED") for v in verdicts):
        return "pending"
    return "pass"


def parse_pr(url: str, raw: str) -> Pr:
    repo, number = split_url(url)
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return Pr(repo, number, error="gh: unreadable output")
    return Pr(
        repo,
        number,
        state=str(data.get("state", "")).upper(),
        review=str(data.get("reviewDecision") or "none"),
        ci=ci_of(data.get("statusCheckRollup")),
        head=str(data.get("headRefOid") or "")[:7],
    )


def fetch_pr(url: str, fetch: Fetch | None) -> Pr:
    repo, number = split_url(url)
    if fetch is None:
        return Pr(repo, number, error=NO_GH)
    try:
        return parse_pr(url, fetch(url))
    except (MiseError, OSError, subprocess.SubprocessError) as e:
        why = "timed out" if isinstance(e, subprocess.TimeoutExpired) else str(e)
        return Pr(repo, number, error=f"gh: {why}")


def fetch_all(items: list[Item], fetch: Fetch | None) -> dict[str, Pr]:
    """Each distinct PR url asked once, all at the same time."""
    urls = list(dict.fromkeys(u for i in items for u in pr_urls(i)))
    with ThreadPoolExecutor(max_workers=max(1, min(8, len(urls)))) as pool:
        return dict(zip(urls, pool.map(lambda u: fetch_pr(u, fetch), urls), strict=True))


def stale(item: Item, prs: dict[str, Pr]) -> bool:
    """Open, and every PR it refs is merged or closed: nothing is left to wait for."""
    urls = pr_urls(item)
    return bool(urls) and all(prs[u].closed for u in urls)


def latest_note(item: Item) -> str:
    return compact(plain(item["notes"][-1]), NOTE_WIDTH) if item["notes"] else ""


def item_text(item: Item) -> str:
    return compact(item["title"], NOTE_WIDTH)


def on_you(items: list[Item]) -> list[Item]:
    return [i for i in items if i["kind"] == "mine" and i["state"] != "waiting"]


def waiting(items: list[Item]) -> list[Item]:
    return [i for i in items if i["state"] == "waiting" or i["kind"] == "waiting"]


def item_line(item: Item) -> str:
    parts = [f"#{item['id']} {item_text(item)}", item["state"]]
    if item["who"]:
        parts.append(item["who"])
    if note := latest_note(item):
        parts.append(note)
    return "  " + " · ".join(parts)


def pr_line(item: Item, pr: Pr) -> str:
    what = pr.error or f"{pr.state} review={pr.review} ci={pr.ci} {pr.head}"
    return f"  {pr.ref} {what} · #{item['id']} {item_text(item)}"


def pr_lines(items: list[Item], prs: dict[str, Pr]) -> list[str]:
    lines: list[str] = []
    for item in items:
        lines.extend(pr_line(item, prs[u]) for u in pr_urls(item))
        if stale(item, prs):
            lines.append(f"  ! #{item['id']} is open but every PR is closed: mep done {item['id']}")
    return lines


def section(title: str, lines: list[str]) -> list[str]:
    return [f"{title} ({len(lines)})", *lines] if lines else [f"{title} (0)"]


def render(items: list[Item], prs: dict[str, Pr]) -> str:
    with_prs = [i for i in items if pr_urls(i)]
    return "\n".join(
        [
            *section("On you", [item_line(i) for i in on_you(items)]),
            *section("Waiting on others", [item_line(i) for i in waiting(items)]),
            *section("PRs", pr_lines(with_prs, prs)),
        ]
    )


def as_json(items: list[Item], prs: dict[str, Pr]) -> str:
    def entry(i: Item) -> dict[str, object]:
        return {
            "id": i["id"],
            "title": i["title"],
            "state": i["state"],
            "who": i["who"],
            "note": i["notes"][-1] if i["notes"] else None,
        }

    def pr_entry(i: Item, u: str) -> dict[str, object]:
        p = prs[u]
        fields = {"state": p.state, "review": p.review, "ci": p.ci, "head": p.head}
        return {"item": i["id"], "pr": p.ref, "url": u, **fields, "error": p.error or None}

    out = {
        "on_you": [entry(i) for i in on_you(items)],
        "waiting": [entry(i) for i in waiting(items)],
        "prs": [pr_entry(i, u) for i in items for u in pr_urls(i)],
        "stale": [i["id"] for i in items if stale(i, prs)],
    }
    return json.dumps(out, separators=(",", ":"))
