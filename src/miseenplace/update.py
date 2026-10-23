"""Hourly update check. It runs in a detached process; a new version applies on the next run."""

import fcntl
import http.client
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from miseenplace import __version__
from miseenplace.assets import CHECKOUT, skills_dir
from miseenplace.model import MiseError

PACKAGE = "miseenplace-cli"
PYPI_URL = f"https://pypi.org/pypi/{PACKAGE}/json"
CHECK_EVERY = 3600.0
NOTICE_EVERY = 24 * 3600.0
FETCH_TIMEOUT = 3.0
UPGRADE_TIMEOUT = 300.0
RETRY_FAILED_AFTER = 24 * 3600.0
LOCK_NAME = "update.lock"
ENV_DISABLE = "MISEENPLACE_DISABLE_AUTOUPDATE"
STATE_NAME = "update.json"
MIRROR_LAG_LIMIT = 6 * 3600.0  # still "not there" after this long is a real failure
MANUAL_UPGRADE = f"pip install --upgrade {PACKAGE}"


@dataclass
class State:
    checked: float = 0.0  # last time a check was started
    notified: float = 0.0  # last time the "is available" line was printed
    available: str = ""  # newest version seen, "" when current or unknown
    skill_dirs: list[str] = field(default_factory=list)
    failed_version: str = ""  # the version whose automatic upgrade last failed
    failed_at: float = 0.0
    failed_reason: str = ""
    lagging_version: str = ""  # installer ran cleanly but still saw the old version
    lagging_since: float = 0.0


@dataclass(frozen=True)
class Installer:
    name: str
    command: tuple[str, ...]


@dataclass(frozen=True)
class Verdict:
    action: str  # "none" | "upgrade" | "notify"
    latest: str | None  # None when PyPI could not be reached


# ---- state ----------------------------------------------------------------------


def cache_dir(env: Mapping[str, str] | None = None) -> Path:
    base = (env if env is not None else os.environ).get("XDG_CACHE_HOME")
    return (Path(base) if base else Path.home() / ".cache") / "miseenplace"


def load_state(cache: Path) -> State:
    try:
        raw = json.loads((cache / STATE_NAME).read_text(encoding="utf-8"))
        dirs = [d for d in raw.get("skill_dirs", []) if isinstance(d, str)]
        return State(
            checked=float(raw.get("checked", 0)),
            notified=float(raw.get("notified", 0)),
            available=str(raw.get("available", "")),
            skill_dirs=dirs,
            failed_version=str(raw.get("failed_version", "")),
            failed_at=float(raw.get("failed_at", 0)),
            failed_reason=str(raw.get("failed_reason", "")),
            lagging_version=str(raw.get("lagging_version", "")),
            lagging_since=float(raw.get("lagging_since", 0)),
        )
    except (OSError, ValueError, AttributeError, TypeError):
        return State()


def save_state(cache: Path, state: State) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    tmp = cache / f"{STATE_NAME}.{os.getpid()}"
    tmp.write_text(json.dumps(state.__dict__), encoding="utf-8")
    tmp.replace(cache / STATE_NAME)


# ---- decisions ------------------------------------------------------------------


def parse_version(text: str) -> tuple[int, ...] | None:
    found = re.match(r"\d+(\.\d+)*", text.strip())
    return tuple(int(p) for p in found.group().split(".")) if found else None


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    return a is not None and b is not None and a > b


def auto_enabled(env: Mapping[str, str], config_flag: bool) -> bool:
    """False when opted out by env or config, or in CI. The worker is detached and silent."""
    if env.get(ENV_DISABLE, "") not in ("", "0") or env.get("CI"):
        return False
    return config_flag


def is_due(state: State, now: float) -> bool:
    return now - state.checked >= CHECK_EVERY


def find_tool(name: str, home: Path, env: Mapping[str, str]) -> str | None:
    """PATH first, then where installers put their binaries (cron has a minimal PATH)."""
    if found := shutil.which(name, path=env.get("PATH", "")):
        return found
    dirs = [home / ".local" / "bin", home / ".cargo" / "bin"]
    if install_dir := env.get("UV_INSTALL_DIR"):
        dirs.insert(0, Path(install_dir))
    for d in dirs:
        if (candidate := d / name).is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def detect_installer(
    prefix: str, editable: bool, home: Path | None = None, env: Mapping[str, str] | None = None
) -> Installer | None:
    """Only uv tool and pipx installs are upgraded for you; the path tells them apart."""
    if editable:
        return None
    home, env = home or Path.home(), env if env is not None else os.environ
    pairs = set(itertools.pairwise(Path(prefix).parts))
    if ("uv", "tools") in pairs and (uv := find_tool("uv", home, env)):
        return Installer(
            "uv", (uv, "tool", "upgrade", "--reinstall-package", PACKAGE, PACKAGE)
        )  # implies --refresh-package, which `tool upgrade` itself does not accept
    if ("pipx", "venvs") in pairs and (pipx := find_tool("pipx", home, env)):
        return Installer("pipx", (pipx, "upgrade", PACKAGE))
    return None


def current_installer() -> Installer | None:
    editable = (CHECKOUT / "pyproject.toml").is_file()
    return detect_installer(sys.prefix, editable)


def decide(current: str, latest: str | None, installer: Installer | None) -> Verdict:
    if latest is None or not is_newer(latest, current):
        return Verdict("none", latest)
    return Verdict("upgrade" if installer else "notify", latest)


def fetch_latest() -> str | None:
    """The newest release on PyPI, or None on any failure."""
    try:
        with urllib.request.urlopen(PYPI_URL, timeout=FETCH_TIMEOUT) as resp:
            version = json.load(resp)["info"]["version"]
    except (OSError, http.client.HTTPException, ValueError, KeyError, TypeError):
        return None
    return version if isinstance(version, str) else None


def check(
    cache: Path,
    now: float,
    current: str,
    fetch: Callable[[], str | None],
    installer: Installer | None,
) -> Verdict:
    """Ask for the newest version and remember the answer. Upgrading is the caller's job."""
    state = load_state(cache)
    verdict = decide(current, fetch(), installer)
    state.checked = now
    if verdict.latest is not None:  # a failed fetch keeps what we knew
        state.available = verdict.latest if verdict.action != "none" else ""
    save_state(cache, state)
    return verdict


def pending_notice(
    cache: Path, now: float, current: str, installer: Installer | None
) -> str | None:
    """The once-a-day line for a known newer version we cannot or did not manage to install."""
    state = load_state(cache)
    if not is_newer(state.available, current) or now - state.notified < NOTICE_EVERY:
        return None
    failed = state.failed_version == state.available
    if installer and not failed:
        return None  # the automatic upgrade is pending or running
    state.notified = now
    save_state(cache, state)
    if installer:
        return (
            f"miseenplace {state.available} is available, the automatic upgrade failed "
            f"({state.failed_reason}): {' '.join(installer.command)}"
        )
    return f"miseenplace {state.available} is available: {MANUAL_UPGRADE}"


# ---- skills ---------------------------------------------------------------------


def install_skills(dest: Path, cache: Path, src: Path | None = None) -> int:
    """Copy the bundled skills into dest and remember dest for later refreshes."""
    count = copy_skills(src or skills_dir(), dest)
    state = load_state(cache)
    recorded = str(dest.expanduser().resolve())
    if recorded not in state.skill_dirs:
        state.skill_dirs.append(recorded)
        save_state(cache, state)
    return count


def copy_skills(src: Path, dest: Path) -> int:
    dest = dest.expanduser()
    skills = [p for p in sorted(src.iterdir()) if p.is_dir() and p.name != "__pycache__"]
    for skill in skills:
        shutil.copytree(
            skill,
            dest / skill.name,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    return len(skills)


def refresh_skills(cache: Path, src: Path | None = None) -> int:
    """Re-copy into every recorded directory that still exists."""
    total = 0
    for recorded in load_state(cache).skill_dirs:
        if Path(recorded).is_dir():
            total += copy_skills(src or skills_dir(), Path(recorded))
    return total


# ---- doing it ---------------------------------------------------------------------


def upgrade_confirmed(version_output: str, latest: str) -> bool:
    """True when `miseenplace --version` output ("miseenplace 0.4.0") is the latest or newer."""
    installed, wanted = parse_version(version_output.strip().rpartition(" ")[2]), parse_version(
        latest
    )
    return installed is not None and wanted is not None and installed >= wanted


def installed_version_output() -> str:
    """What the entry point installed next to this interpreter prints for --version."""
    entry = Path(sys.executable).parent / "miseenplace"
    if not entry.is_file():
        raise MiseError(f"could not confirm the new version: {entry} not found")
    try:
        done = subprocess.run(
            [str(entry), "--version"], capture_output=True, text=True, timeout=60, check=False
        )
    except subprocess.TimeoutExpired:
        raise MiseError("could not confirm the new version: --version timed out") from None
    return done.stdout


class NotYetAvailable(MiseError):
    """The installer ran cleanly but still sees the old version: PyPI's mirrors lag a release."""


def apply_upgrade(installer: Installer, latest: str) -> None:
    """Upgrade, confirm the new version is installed, then refresh skills from the NEW code.

    Raises MiseError with the reason when the installer failed or the new version is not there.
    """
    try:
        done = subprocess.run(
            installer.command, capture_output=True, timeout=UPGRADE_TIMEOUT, check=False
        )
        if done.returncode != 0:
            raise MiseError(f"{installer.name} exited {done.returncode}")
        output = installed_version_output()
        if not upgrade_confirmed(output, latest):
            raise NotYetAvailable(
                f"{installer.name} ran but the installed version is still "
                f"{output.strip() or 'unknown'}, not {latest}"
            )
        subprocess.run(
            [sys.executable, "-m", "miseenplace", "skills", "--refresh"],
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise MiseError(f"{installer.name} could not run: {exc}") from exc


def retry_blocked(state: State, version: str, now: float) -> bool:
    """A failed upgrade of this exact version is not retried for a day; a newer one resets it."""
    return state.failed_version == version and now - state.failed_at < RETRY_FAILED_AFTER


def try_upgrade(cache: Path, installer: Installer, latest: str, now: float) -> None:
    """apply_upgrade, remembering a failure (version, time, reason) or clearing it on success."""
    try:
        apply_upgrade(installer, latest)
    except NotYetAvailable as exc:
        state = load_state(cache)
        if state.lagging_version != latest:
            state.lagging_version, state.lagging_since = latest, now
        if now - state.lagging_since < MIRROR_LAG_LIMIT:
            save_state(cache, state)
            raise  # mirrors still catching up: the next hourly check retries
        state.failed_version, state.failed_at, state.failed_reason = latest, now, str(exc)[:200]
        save_state(cache, state)
        raise
    except MiseError as exc:
        state = load_state(cache)
        state.failed_version, state.failed_at, state.failed_reason = latest, now, str(exc)[:200]
        save_state(cache, state)
        raise
    state = load_state(cache)
    state.failed_version, state.failed_at, state.failed_reason = "", 0.0, ""
    state.lagging_version, state.lagging_since = "", 0.0
    save_state(cache, state)


def update_now(cache: Path, installer: Installer | None, fetch: Callable[[], str | None]) -> str:
    """`mep update`: check and upgrade now, say what happened. Ignores the retry backoff."""
    verdict = check(cache, time.time(), __version__, fetch, installer)
    if verdict.latest is None:
        raise MiseError("could not reach PyPI to look for a newer version")
    if verdict.action == "none":
        return f"miseenplace {__version__} is up to date"
    if verdict.action == "notify" or installer is None:
        return f"miseenplace {verdict.latest} is available: {MANUAL_UPGRADE}"
    try:
        try_upgrade(cache, installer, verdict.latest, time.time())
    except NotYetAvailable as exc:
        if load_state(cache).failed_version == verdict.latest:  # lagging past the limit: real
            raise MiseError(f"upgrade failed: {exc}; run: {' '.join(installer.command)}") from exc
        raise MiseError(
            f"PyPI has not caught up with {verdict.latest} yet; try again in a few minutes"
        ) from exc
    except MiseError as exc:
        raise MiseError(f"upgrade failed: {exc}; run: {' '.join(installer.command)}") from exc
    return f"upgraded miseenplace {__version__} -> {verdict.latest}; it applies on the next run"


def start_background_check(cache: Path, now: float) -> None:
    """Detach the worker when a check is due; it takes the lock, so parallel runs do not pile up."""
    state = load_state(cache)
    if not is_due(state, now):
        return
    try:
        worker = subprocess.Popen(
            [sys.executable, "-I", "-m", "miseenplace.update"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        threading.Thread(target=worker.wait, daemon=True).start()  # reap it: no zombie in watch
    except OSError:
        return


def run_worker(
    cache: Path, now: float, installer: Installer | None, fetch: Callable[[], str | None]
) -> bool:
    """Check and upgrade under an exclusive lock. False when it is held or no check is due."""
    cache.mkdir(parents=True, exist_ok=True)
    with open(cache / LOCK_NAME, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        if not is_due(load_state(cache), now):
            return False
        verdict = check(cache, now, __version__, fetch, installer)
        wanted = verdict.latest if verdict.action == "upgrade" and installer else None
        if installer and wanted and not retry_blocked(load_state(cache), wanted, now):
            try_upgrade(cache, installer, wanted, now)
    return True


def background() -> None:
    """The detached worker: check, and upgrade if we know how. Silent on every failure."""
    try:
        run_worker(cache_dir(), time.time(), current_installer(), fetch_latest)
    except (OSError, MiseError):
        return


if __name__ == "__main__":
    background()
