"""config.toml in the data root: parsed once, validated, never a traceback."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from miseenplace.model import DEFAULT_ACTORS, MiseError

ENV_DIR = "MISEENPLACE_DIR"
CONFIG_NAME = "config.toml"
DEFAULT_DRAFT_TTL_HOURS = 24.0
SourceTable = dict[str, object]


@dataclass(frozen=True)
class Config:
    actors: tuple[str, ...] = DEFAULT_ACTORS
    default_actor: str = DEFAULT_ACTORS[1]
    default_source: str | None = None
    storage: dict[str, object] = field(default_factory=dict)
    sources: dict[str, SourceTable] = field(default_factory=dict)
    draft_ttl_hours: float = DEFAULT_DRAFT_TTL_HOURS
    auto_update: bool = True


def default_root() -> Path:
    return Path.home() / ".local" / "share" / "miseenplace"


def resolve_root(override: str | None = None, env: dict[str, str] | None = None) -> Path:
    """--dir, else $MISEENPLACE_DIR, else ~/.local/share/miseenplace."""
    chosen = override or (env if env is not None else os.environ).get(ENV_DIR)
    return Path(chosen).expanduser() if chosen else default_root()


def _table(cfg: dict[str, object], key: str, path: Path) -> dict[str, object]:
    value = cfg.get(key, {})
    if not isinstance(value, dict):
        raise MiseError(f"{path}: [{key}] must be a table, e.g. [{key}] followed by key = value")
    return cast(dict[str, object], value)


def _actors(cfg: dict[str, object], path: Path) -> tuple[str, ...]:
    actors = cfg.get("actors", list(DEFAULT_ACTORS))
    if (
        not isinstance(actors, list)
        or not actors
        or not all(isinstance(a, str) and a for a in actors)
    ):
        raise MiseError(f'{path}: actors must be a non-empty list of names, e.g. ["me", "agent"]')
    return tuple(cast(list[str], actors))


def _optional_str(cfg: dict[str, object], key: str, path: Path) -> str | None:
    value = cfg.get(key)
    if value is not None and not (isinstance(value, str) and value):
        raise MiseError(f"{path}: {key} must be a non-empty string")
    return value


def _ttl(consent: dict[str, object], path: Path) -> float:
    value = consent.get("draft_ttl_hours", DEFAULT_DRAFT_TTL_HOURS)
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise MiseError(f"{path}: [consent] draft_ttl_hours must be a number above 0")
    return float(value)


def _auto_update(cfg: dict[str, object], path: Path) -> bool:
    value = cfg.get("auto_update", True)
    if not isinstance(value, bool):
        raise MiseError(f"{path}: auto_update must be true or false")
    return value


def load_config(path: Path) -> Config:
    if not path.exists():
        return Config()
    try:
        cfg = cast(dict[str, object], tomllib.loads(path.read_text(encoding="utf-8")))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise MiseError(f"{path}: not valid TOML ({e}); fix the file or delete it") from None
    actors = _actors(cfg, path)
    default = _optional_str(cfg, "default_actor", path) or actors[0]
    if default not in actors:
        raise MiseError(f"{path}: default_actor {default!r} is not in actors {list(actors)}")
    sources = _table(cfg, "sources", path)
    for name, table in sources.items():
        if not isinstance(table, dict):
            raise MiseError(f"{path}: [sources.{name}] must be a table")
    return Config(
        actors=actors,
        default_actor=default,
        default_source=_optional_str(cfg, "default_source", path),
        storage=_table(cfg, "storage", path),
        sources=cast(dict[str, SourceTable], sources),
        draft_ttl_hours=_ttl(_table(cfg, "consent", path), path),
        auto_update=_auto_update(cfg, path),
    )
