"""Repository hygiene: skills follow the standard, packaging is real, no personal data."""

import json
import re
import runpy
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from tests.conftest import REPO

SKILLS = sorted(p.parent for p in (REPO / "skills").glob("*/SKILL.md"))
EXPECTED_SKILLS = {"journal", "inbox-triage", "inbox-watch", "learn-style", "setup", "eod-review"}
FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def test_all_skills_exist() -> None:
    assert {p.name for p in SKILLS} == EXPECTED_SKILLS


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.name)
def test_skill_follows_the_agent_skills_standard(skill: Path) -> None:
    text = (skill / "SKILL.md").read_text()
    match = FRONT.match(text)
    assert match, "SKILL.md needs YAML frontmatter"
    fields = dict(line.split(": ", 1) for line in match.group(1).splitlines() if ": " in line)
    assert fields["name"] == skill.name
    assert re.fullmatch(r"[a-z0-9-]{1,64}", fields["name"])
    assert 40 <= len(fields["description"]) <= 1024
    assert text[match.end() :].strip().startswith("# ")


def test_skills_state_the_two_safety_rules() -> None:
    triage = (REPO / "skills" / "inbox-triage" / "SKILL.md").read_text().lower()
    assert "re-read the thread before calling anything pending" in triage
    assert "explicit yes on that exact text" in triage


def test_plugin_manifests_point_at_the_single_skills_copy() -> None:
    plugin = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
    market = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text())
    assert (REPO / plugin["skills"]).is_dir()
    assert market["plugins"][0]["source"] == "./"
    assert market["plugins"][0]["name"] == plugin["name"]
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text())
    assert plugin["version"] == market["plugins"][0]["version"] == pyproject["project"]["version"]


def test_both_script_names_point_at_one_entry() -> None:
    scripts = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts["miseenplace"] == scripts["mep"] == "miseenplace.cli:main"


def test_runtime_has_no_dependencies() -> None:
    assert tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["dependencies"] == []


def test_doc_links_resolve() -> None:
    link = re.compile(r"\]\(((?!https?:|#|mailto:)[^)#\s]+)")
    pages = [*REPO.glob("*.md"), *(REPO / "docs").glob("*.md"), *(REPO / "skills").rglob("*.md")]
    broken = [
        f"{p.relative_to(REPO)} -> {m}"
        for p in pages
        for m in link.findall(p.read_text())
        if not (p.parent / m).resolve().exists()
    ]
    assert broken == []


def test_no_personal_names_or_domains_in_the_repo() -> None:
    done = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "check_no_personal.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout


def test_the_personal_check_can_fail(tmp_path: Path) -> None:
    bad = tmp_path / "x.txt"
    word = runpy.run_path(str(REPO / "scripts" / "check_no_personal.py"))["DENYLIST"][0]
    bad.write_text(f"contact someone at {word}\n")
    done = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "check_no_personal.py"), str(bad)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 1


def test_render_does_not_depend_on_any_source() -> None:
    text = (REPO / "src" / "miseenplace" / "render.py").read_text()
    assert "miseenplace.sources" not in text


def test_the_tour_speaks_only_through_say(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from miseenplace.tour import run_tour

    lines: list[str] = []
    run_tour(tmp_path, say=lines.append)
    assert len(lines) > 5
    assert capsys.readouterr().out == ""  # nothing bypassed `say`
