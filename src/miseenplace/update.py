"""Daily update check. It runs in a detached process; a new version applies on the next run."""

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
CHECK_EVERY = 24 * 3600.0
FETCH_TIMEOUT = 3.0
UPGRADE_TIMEOUT = 300.0
ENV_DISABLE = "MISEENPLACE_DISABLE_AUTOUPDATE"
STATE_NAME = "update.json"
MANUAL_UPGRADE = f"pip install --upgrade {PACKAGE}"


@dataclass
class State:
    checked: float = 0.0  # last time a check was started
    notified: float = 0.0  # last time the "is available" line was printed
    available: str = ""  # newest version seen, "" when current or unknown
    skill_dirs: list[str] = field(default_factory=list)


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


def auto_enabled(env: Mapping[str, str], tty: bool, config_flag: bool) -> bool:
    """False when opted out by env or config, in CI, or when nobody is watching a terminal."""
    if env.get(ENV_DISABLE, "") not in ("", "0") or env.get("CI"):
        return False
    return tty and config_flag


def is_due(state: State, now: float) -> bool:
    return now - state.checked >= CHECK_EVERY


def detect_installer(prefix: str, editable: bool) -> Installer | None:
    """Only uv tool and pipx installs are upgraded for you; the path tells them apart."""
    if editable:
        return None
    parts = Path(prefix).parts
    pairs = set(itertools.pairwise(parts))
    if ("uv", "tools") in pairs and (uv := shutil.which("uv")):
        return Installer("uv", (uv, "tool", "upgrade", PACKAGE))
    if ("pipx", "venvs") in pairs and (pipx := shutil.which("pipx")):
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
    """The once-a-day line for installs we cannot upgrade ourselves."""
    state = load_state(cache)
    if installer or not is_newer(state.available, current) or now - state.notified < CHECK_EVERY:
        return None
    state.notified = now
    save_state(cache, state)
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


def apply_upgrade(installer: Installer) -> bool:
    """Upgrade, then refresh skills from the NEW code in a fresh interpreter."""
    try:
        done = subprocess.run(
            installer.command, capture_output=True, timeout=UPGRADE_TIMEOUT, check=False
        )
        if done.returncode != 0:
            return False
        subprocess.run(
            [sys.executable, "-m", "miseenplace", "skills", "--refresh"],
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def update_now(cache: Path, installer: Installer | None, fetch: Callable[[], str | None]) -> str:
    """`mep update`: check and upgrade now, say what happened."""
    verdict = check(cache, time.time(), __version__, fetch, installer)
    if verdict.latest is None:
        raise MiseError("could not reach PyPI to look for a newer version")
    if verdict.action == "none":
        return f"miseenplace {__version__} is up to date"
    if verdict.action == "notify" or installer is None:
        return f"miseenplace {verdict.latest} is available: {MANUAL_UPGRADE}"
    if not apply_upgrade(installer):
        raise MiseError(f"upgrade failed; run: {' '.join(installer.command)}")
    return f"upgraded miseenplace {__version__} -> {verdict.latest}; it applies on the next run"


def start_background_check(cache: Path, now: float) -> None:
    """Mark the check as started (so parallel runs do not pile up) and detach the worker."""
    state = load_state(cache)
    if not is_due(state, now):
        return
    state.checked = now
    try:
        save_state(cache, state)
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


def background() -> None:
    """The detached worker: check, and upgrade if we know how. Silent on every failure."""
    cache, installer = cache_dir(), current_installer()
    try:
        verdict = check(cache, time.time(), __version__, fetch_latest, installer)
        if verdict.action == "upgrade" and installer:
            apply_upgrade(installer)
    except (OSError, MiseError):
        return


if __name__ == "__main__":
    background()
