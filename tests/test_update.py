"""The auto-update decisions, on real temporary directories. No network, no upgrades run."""

import fcntl
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from miseenplace import update
from miseenplace.model import MiseError
from miseenplace.update import Installer, State

UV_PREFIX = "/h/.local/share/uv/tools/miseenplace-cli"
UV = Installer("uv", ("uv", "tool", "upgrade", "miseenplace-cli"))
HOUR = update.CHECK_EVERY
DAY = update.NOTICE_EVERY
T0 = 1_700_000_000.0


def test_check_runs_once_an_hour() -> None:
    assert update.is_due(State(), now=T0)
    assert not update.is_due(State(checked=T0), now=T0 + HOUR - 1)
    assert update.is_due(State(checked=T0), now=T0 + HOUR)


def test_opt_outs_disable_auto_update_but_a_missing_terminal_does_not() -> None:
    assert update.auto_enabled({}, config_flag=True)  # no terminal needed: the worker is silent
    assert not update.auto_enabled({"MISEENPLACE_DISABLE_AUTOUPDATE": "1"}, True)
    assert not update.auto_enabled({"CI": "true"}, True)
    assert not update.auto_enabled({}, config_flag=False)


def test_newer_version_upgrades_only_when_the_installer_is_known() -> None:
    assert update.decide("0.2.0", "0.10.0", UV).action == "upgrade"  # numeric, not text, order
    assert update.decide("0.2.0", "0.10.0", None).action == "notify"
    assert update.decide("0.2.0", "0.2.0", UV).action == "none"
    assert update.decide("0.2.0", None, UV).action == "none"


def test_check_records_the_answer_and_notice_prints_once_a_day(tmp_path: Path) -> None:
    verdict = update.check(tmp_path, T0, "0.2.0", lambda: "0.3.0", None)
    assert verdict.action == "notify"
    state = update.load_state(tmp_path)
    assert (state.checked, state.available) == (T0, "0.3.0")
    first = update.pending_notice(tmp_path, T0 + 1, "0.2.0", None)
    assert first == f"miseenplace 0.3.0 is available: {update.MANUAL_UPGRADE}"
    assert update.pending_notice(tmp_path, T0 + 2, "0.2.0", None) is None
    assert update.pending_notice(tmp_path, T0 + 1 + DAY, "0.2.0", None) == first
    assert update.pending_notice(tmp_path, T0 + 1 + 2 * DAY, "0.3.0", None) is None


def test_an_unreachable_index_is_remembered_but_changes_nothing(tmp_path: Path) -> None:
    verdict = update.check(tmp_path, T0, "0.2.0", lambda: None, UV)
    assert verdict == update.Verdict("none", None)
    assert update.load_state(tmp_path) == State(checked=T0)


def test_only_uv_tool_and_pipx_installs_are_upgraded_for_you() -> None:
    uv = update.detect_installer("/h/.local/share/uv/tools/miseenplace-cli", False)
    pipx = update.detect_installer("/h/.local/pipx/venvs/miseenplace-cli", False)
    assert uv is None or uv.name == "uv"  # None only when uv is not on this PATH
    assert pipx is None or pipx.name == "pipx"
    assert update.detect_installer("/proj/.venv", editable=False) is None
    assert update.detect_installer("/usr", editable=False) is None
    assert update.detect_installer("/h/.local/share/uv/tools/x", editable=True) is None


def fake_tool(home: Path, rel: str, name: str) -> Path:
    path = home / rel / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def test_the_uv_command_makes_uv_re_read_the_index_for_this_package(tmp_path: Path) -> None:
    uv_bin = fake_tool(tmp_path, ".local/bin", "uv")
    uv = update.detect_installer(UV_PREFIX, False, tmp_path, {"PATH": ""})
    assert uv is not None and uv.command[0] == str(uv_bin)
    cmd = list(uv.command)
    assert cmd[cmd.index("--reinstall-package") + 1] == update.PACKAGE


def test_the_real_uv_accepts_every_flag_of_the_upgrade_command() -> None:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not installed")
    installer = update.detect_installer(UV_PREFIX, False)
    assert installer is not None
    flags = [a for a in installer.command if a.startswith("--")]
    done = subprocess.run([uv, "tool", "upgrade", *flags, update.PACKAGE, "--help"],
                          capture_output=True, text=True, check=False)  # fmt: skip
    assert done.returncode == 0, done.stderr


def test_uv_and_pipx_are_found_off_a_minimal_path(tmp_path: Path) -> None:
    env = {"PATH": ""}
    assert update.detect_installer(UV_PREFIX, False, tmp_path, env) is None
    cargo = fake_tool(tmp_path, ".cargo/bin", "uv")
    found = update.detect_installer(UV_PREFIX, False, tmp_path, env)
    assert found is not None and found.command[0] == str(cargo)
    custom = fake_tool(tmp_path, "custom", "uv")
    env = {"PATH": "", "UV_INSTALL_DIR": str(custom.parent)}
    found = update.detect_installer(UV_PREFIX, False, tmp_path, env)
    assert found is not None and found.command[0] == str(custom)
    pipx = fake_tool(tmp_path, ".local/bin", "pipx")
    found = update.detect_installer("/h/.local/pipx/venvs/miseenplace-cli", False, tmp_path, env)
    assert found is not None and found.command[0] == str(pipx)


def test_an_upgrade_counts_only_when_the_installed_version_is_the_latest_or_newer() -> None:
    assert update.upgrade_confirmed("miseenplace 0.4.0\n", "0.4.0")
    assert update.upgrade_confirmed("miseenplace 0.5.0\n", "0.4.0")
    assert not update.upgrade_confirmed("miseenplace 0.3.0\n", "0.4.0")
    assert not update.upgrade_confirmed("Traceback: boom", "0.4.0")
    assert not update.upgrade_confirmed("", "0.4.0")


def test_a_failed_fetch_keeps_the_known_newer_version(tmp_path: Path) -> None:
    update.check(tmp_path, T0, "0.2.0", lambda: "0.3.0", None)
    update.check(tmp_path, T0 + DAY, "0.2.0", lambda: None, None)
    assert update.load_state(tmp_path).available == "0.3.0"


def test_skills_are_copied_and_refreshed_into_recorded_dirs(tmp_path: Path) -> None:
    src, cache, dest = tmp_path / "src", tmp_path / "cache", tmp_path / "skills"
    (src / "setup").mkdir(parents=True)
    (src / "setup" / "SKILL.md").write_text("v1")
    assert update.install_skills(dest, cache, src) == 1
    assert update.install_skills(dest, cache, src) == 1
    assert update.load_state(cache).skill_dirs == [str(dest.resolve())]
    (src / "setup" / "SKILL.md").write_text("v2")
    assert update.refresh_skills(cache, src) == 1
    assert (dest / "setup" / "SKILL.md").read_text() == "v2"


def test_a_failed_upgrade_is_not_retried_for_a_day_and_a_newer_version_resets_it() -> None:
    state = State(failed_version="0.4.0", failed_at=T0, failed_reason="uv exited 1")
    assert update.retry_blocked(state, "0.4.0", T0 + DAY - 1)
    assert not update.retry_blocked(state, "0.4.0", T0 + DAY)
    assert not update.retry_blocked(state, "0.4.1", T0 + 1)


def test_a_failure_is_recorded_with_version_and_reason_and_survives_a_reload(
    tmp_path: Path,
) -> None:
    broken = Installer("uv", ("false",))
    with pytest.raises(MiseError, match="exited 1"):
        update.try_upgrade(tmp_path, broken, "0.4.0", T0)
    state = update.load_state(tmp_path)
    assert (state.failed_version, state.failed_at) == ("0.4.0", T0)
    assert "exited 1" in state.failed_reason


def test_the_notice_waits_while_an_automatic_upgrade_is_pending(tmp_path: Path) -> None:
    update.check(tmp_path, T0, "0.2.0", lambda: "0.3.0", UV)
    assert update.pending_notice(tmp_path, T0 + 1, "0.2.0", UV) is None
    state = update.load_state(tmp_path)
    state.failed_version, state.failed_at, state.failed_reason = "0.3.0", T0, "uv exited 1"
    update.save_state(tmp_path, state)
    notice = update.pending_notice(tmp_path, T0 + 2, "0.2.0", UV)
    assert notice is not None
    assert "uv exited 1" in notice and " ".join(UV.command) in notice
    assert update.MANUAL_UPGRADE not in notice


def test_a_second_worker_exits_at_once_while_the_lock_is_held(tmp_path: Path) -> None:
    seen: list[int] = []

    def fetch() -> str | None:
        seen.append(1)
        return "0.3.0"

    with open(tmp_path / update.LOCK_NAME, "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert not update.run_worker(tmp_path, T0, None, fetch)
        assert seen == []
    assert update.run_worker(tmp_path, T0, None, fetch)
    assert seen == [1]
    assert not update.run_worker(tmp_path, T0 + 1, None, fetch)  # no longer due under the lock


def test_an_installer_that_still_sees_the_old_version_is_retried_then_fails_after_6h(
    tmp_path: Path,
) -> None:
    if not (Path(sys.executable).parent / "miseenplace").is_file():
        pytest.skip("no miseenplace entry point next to this interpreter")
    lagging = Installer("uv", ("true",))  # exits 0 and installs nothing, like a lagging mirror
    for now in (1000.0, 1000.0 + 3600):
        with pytest.raises(update.NotYetAvailable):
            update.try_upgrade(tmp_path, lagging, "999.0.0", now)
        state = update.load_state(tmp_path)
        assert state.failed_version == "" and state.lagging_since == 1000.0
    with pytest.raises(update.NotYetAvailable):
        update.try_upgrade(tmp_path, lagging, "999.0.0", 1000.0 + update.MIRROR_LAG_LIMIT)
    assert update.load_state(tmp_path).failed_version == "999.0.0"  # now the notice can show it
