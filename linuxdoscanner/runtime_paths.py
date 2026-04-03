from __future__ import annotations

import shutil
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def source_root() -> Path:
    return Path(__file__).resolve().parent.parent


def app_root() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return source_root()


def bundle_root() -> Path:
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            return Path(meipass).resolve()
        internal_dir = current_executable().parent / "_internal"
        if internal_dir.exists():
            return internal_dir.resolve()
    return source_root()


def current_executable() -> Path:
    return Path(sys.executable).resolve()


def bootstrap_bundled_directory(name: str) -> Path:
    destination = app_root() / name
    source = bundle_root() / name
    if destination.exists() or not source.exists():
        return destination
    if source.resolve(strict=False) == destination.resolve(strict=False):
        return destination
    shutil.copytree(source, destination, dirs_exist_ok=True)
    return destination
