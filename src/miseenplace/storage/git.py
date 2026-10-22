"""Local files kept in a git repository: pull before writing and reading, commit and push after.

    [storage]
    backend = "git"
    remote = "git@example.com:me/my-miseenplace-data.git"   # optional; no remote = commit only
    branch = "main"

The data root becomes a repo. `state/`, the lock and the snapshot cache are per machine: they are
excluded through .git/info/exclude (never an untracked file that could collide with a pull).

Two machines that wrote since their last sync diverge. `refresh` merges them: files are merged
by git (`-X ours`, JSON-lines files by union) and then the audit log is rebuilt as the remote's
entries followed by this machine's unpushed entries, renumbered (see logmerge.py). If git cannot
merge, the merge is aborted, the repository is left as it was, and StorageError says what to do.
An unreachable or refusing remote raises StorageUnreachable; local data is untouched.
"""

import json
import subprocess
from pathlib import Path

from miseenplace.model import Entry
from miseenplace.storage.base import StorageError, StorageUnreachable
from miseenplace.storage.local import LOG_NAME, SNAP_NAME, LocalStorage, parse_log
from miseenplace.storage.logmerge import reappend

TRACKED = ("data", "style", "corpus", "requests.jsonl", "Action Items.md")
IGNORED = ("state/", SNAP_NAME, ".lock")
ATTRIBUTES = "*.jsonl merge=union"


class GitStorage(LocalStorage):
    def __init__(self, root: Path, settings: dict[str, object] | None = None) -> None:
        super().__init__(root, settings)
        settings = settings or {}
        self.branch = str(settings.get("branch", "main"))
        self.root.mkdir(parents=True, exist_ok=True)
        if self.git("rev-parse", "--git-dir", check=False).returncode:
            self.git("init", "-q", "-b", self.branch)
        self.use_branch()
        self.write_local_rules()
        remote = settings.get("remote")
        if remote:
            self.use_remote(str(remote))

    # ---- setup ----

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        done = subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if check and done.returncode:
            raise StorageError(f"git {args[0]} failed: {(done.stderr or done.stdout).strip()}")
        return done

    def has_commits(self) -> bool:
        return self.git("rev-parse", "--verify", "-q", "HEAD", check=False).returncode == 0

    def use_branch(self) -> None:
        """Work on the configured branch even if the repository was left on another one."""
        current = self.git("symbolic-ref", "--short", "-q", "HEAD", check=False).stdout.strip()
        if current == self.branch:
            return
        if not self.has_commits():
            self.git("symbolic-ref", "HEAD", f"refs/heads/{self.branch}")
        elif self.git("rev-parse", "--verify", "-q", self.branch, check=False).returncode == 0:
            self.git("checkout", "-q", self.branch)
        else:
            self.git("checkout", "-q", "-b", self.branch)

    def write_local_rules(self) -> None:
        info = self.root / ".git" / "info"
        info.mkdir(parents=True, exist_ok=True)
        for name, lines in (("exclude", IGNORED), ("attributes", (ATTRIBUTES,))):
            path = info / name
            have = path.read_text(encoding="utf-8").split("\n") if path.exists() else []
            missing = [x for x in lines if x not in have]
            if missing:
                with path.open("a", encoding="utf-8") as f:
                    f.write("\n".join(missing) + "\n")

    def use_remote(self, url: str) -> None:
        current = self.git("remote", "get-url", "origin", check=False)
        if current.returncode:
            self.git("remote", "add", "origin", url)
        elif current.stdout.strip() != url:
            self.git("remote", "set-url", "origin", url)

    # ---- remote state ----

    def has_remote(self) -> bool:
        return bool(self.git("remote").stdout.strip())

    def remote_has_branch(self) -> bool:
        """True if the remote has the branch, False if it is empty; unknown raises."""
        found = self.git("ls-remote", "--exit-code", "--heads", "origin", self.branch, check=False)
        if found.returncode == 0:
            return True
        if found.returncode == 2:  # reachable, but the branch does not exist yet
            return False
        raise StorageUnreachable(
            f"cannot reach the git remote ({found.stderr.strip() or 'unknown error'}); "
            "your data is safe locally"
        )

    # ---- pull ----

    def refresh(self) -> None:
        """Bring in the remote's changes, merging divergent work from another machine."""
        if not self.has_remote() or not self.remote_has_branch():
            return
        fetched = self.git("fetch", "-q", "origin", self.branch, check=False)
        if fetched.returncode:
            raise StorageUnreachable(f"git fetch failed: {fetched.stderr.strip()}")
        self.commit("miseenplace: local changes")
        theirs = f"origin/{self.branch}"
        if not self.has_commits():
            self.git("merge", "-q", "--ff-only", theirs)
        elif self.is_ancestor(theirs, "HEAD"):
            return
        elif self.is_ancestor("HEAD", theirs):
            self.git("merge", "-q", "--ff-only", theirs)
        else:
            self.merge_diverged(theirs)

    def is_ancestor(self, older: str, newer: str) -> bool:
        return self.git("merge-base", "--is-ancestor", older, newer, check=False).returncode == 0

    def log_at(self, rev: str) -> list[Entry]:
        shown = self.git("show", f"{rev}:{LOG_NAME}", check=False)
        return parse_log(shown.stdout, f"{rev}:{LOG_NAME}") if shown.returncode == 0 else []

    def merge_diverged(self, theirs: str) -> None:
        base = self.git("merge-base", "HEAD", theirs).stdout.strip()
        base_log, remote_log = self.log_at(base), self.log_at(theirs)
        local_log = parse_log(self.read_file(LOG_NAME) or "", LOG_NAME)
        if local_log[: len(base_log)] != base_log:
            raise StorageError(
                "the local audit log no longer starts with the shared history; restore "
                f"{LOG_NAME} from git (`git checkout HEAD -- {LOG_NAME}`) and retry"
            )
        merged = self.git(
            "merge", "-q", "--no-edit", "-X", "ours", "-m", "miseenplace: merge", theirs,
            check=False,
        )  # fmt: skip
        if merged.returncode:
            self.git("merge", "--abort", check=False)
            raise StorageError(
                f"git could not merge the remote changes ({merged.stderr.strip()}). Nothing was "
                f"changed locally. Run `git -C {self.root} pull` by hand, resolve the conflict, "
                "and retry."
            )
        rebuilt = reappend(remote_log, local_log[len(base_log) :])
        self.write_file(LOG_NAME, "".join(json.dumps(e) + "\n" for e in rebuilt))
        self.commit("miseenplace: re-append local entries after the remote's")

    # ---- push ----

    def commit(self, message: str) -> None:
        """Commit what changed under the tracked paths; stop tracking per-machine files."""
        self.git(
            "rm",
            "-r",
            "--cached",
            "-q",
            "--ignore-unmatch",
            "--",
            *IGNORED,
            check=False,
        )
        present = [p for p in TRACKED if (self.root / p).exists()]
        if present:
            self.git("add", "--", *present)
        if self.git("diff", "--cached", "--quiet", check=False).returncode:
            self.git("commit", "-qm", message)

    def sync(self, message: str) -> None:
        """Commit, then push. A failed push is an error."""
        self.commit(message)
        if self.has_remote() and self.has_commits():
            pushed = self.git("push", "-q", "-u", "origin", self.branch, check=False)
            if pushed.returncode:
                raise StorageUnreachable(
                    f"git push failed: {pushed.stderr.strip()}. Your data is committed locally; "
                    "run `miseenplace sync` again when the remote works."
                )
