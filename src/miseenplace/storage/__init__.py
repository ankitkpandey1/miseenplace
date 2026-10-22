"""Storage backends and the loader that picks one from config.

    [storage]
    backend = "local"                      # default
    backend = "git"                        # local files + pull/commit/push
    backend = "my_package.module:Class"    # your own, implementing storage.base.Storage
    backend = "name"                       # an entry point in the `miseenplace.storage` group

The whole [storage] table is passed to the backend as its settings.
"""

import importlib
from collections.abc import Callable
from importlib.metadata import entry_points
from pathlib import Path
from typing import cast

from miseenplace.storage.base import Storage, StorageError
from miseenplace.storage.git import GitStorage
from miseenplace.storage.local import LocalStorage

ENTRY_POINT_GROUP = "miseenplace.storage"
Factory = Callable[[Path, dict[str, object]], Storage]
BUILTIN: dict[str, Factory] = {"local": LocalStorage, "git": GitStorage}


def resolve_class(spec: str) -> Factory:
    if spec in BUILTIN:
        return BUILTIN[spec]
    if ":" in spec:
        module, _, name = spec.partition(":")
        try:
            return cast(Factory, getattr(importlib.import_module(module), name))
        except (ImportError, AttributeError) as e:
            raise StorageError(f"cannot load storage backend {spec!r}: {e}") from None
    found = entry_points(group=ENTRY_POINT_GROUP)
    if spec in found.names:
        return cast(Factory, found[spec].load())
    known = sorted({*BUILTIN, *found.names})
    raise StorageError(f"unknown storage backend {spec!r}; available: {', '.join(known)}")


def open_storage(root: Path, settings: dict[str, object]) -> Storage:
    cls = resolve_class(str(settings.get("backend", "local")))
    backend = cls(root, settings)
    if not isinstance(backend, Storage):
        raise StorageError(f"{backend!r} does not implement the Storage protocol")
    return backend
