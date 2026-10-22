"""The auto-update decisions, on real temporary directories. No network, no upgrades run."""

from pathlib import Path

from miseenplace import update
from miseenplace.update import Installer, State

UV = Installer("uv", ("uv", "tool", "upgrade", "miseenplace-cli"))
DAY = update.CHECK_EVERY
T0 = 1_700_000_000.0


def test_check_runs_once_a_day() -> None:
    assert update.is_due(State(), now=T0)
    assert not update.is_due(State(checked=T0), now=T0 + DAY - 1)
    assert update.is_due(State(checked=T0), now=T0 + DAY)


def test_opt_outs_and_non_interactive_runs_never_auto_update() -> None:
    assert update.auto_enabled({}, tty=True, config_flag=True)
    assert not update.auto_enabled({"MISEENPLACE_DISABLE_AUTOUPDATE": "1"}, True, True)
    assert not update.auto_enabled({"CI": "true"}, True, True)
    assert not update.auto_enabled({}, tty=False, config_flag=True)
    assert not update.auto_enabled({}, tty=True, config_flag=False)


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
