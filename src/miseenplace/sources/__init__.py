"""Source registry. A new source is one module here plus one line in REGISTRY."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from miseenplace.model import MiseError
from miseenplace.sources import demo, zulip
from miseenplace.sources.base import Source

if TYPE_CHECKING:
    from miseenplace.config import Config

Factory = Callable[[Path, dict[str, object]], Source]

REGISTRY: dict[str, Factory] = {
    "zulip": zulip.ZulipSource.from_config,
    "demo": demo.DemoSource.from_config,
}


def open_source(name: str | None, root: Path, config: Config) -> Source:
    chosen = name or config.default_source
    if not chosen:
        raise MiseError(
            f"no source chosen; pass --source, set MISEENPLACE_SOURCE or default_source in "
            f"config.toml (available: {', '.join(sorted(REGISTRY))})"
        )
    if chosen not in REGISTRY:
        raise MiseError(f"unknown source {chosen!r}; available: {', '.join(sorted(REGISTRY))}")
    return REGISTRY[chosen](root, config.sources.get(chosen, {}))
