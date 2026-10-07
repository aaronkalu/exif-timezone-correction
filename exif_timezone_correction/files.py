from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

IMAGE_EXTENSIONS = frozenset(
    {
        ".jpg", ".jpeg", ".tif", ".tiff", ".heic",
        ".raw", ".arw", ".raf", ".nef", ".orf", ".rw2", ".cr2", ".cr3",
    }
)

# macOS writes "._<name>" metadata files on non-Apple filesystems; they are not images.
_APPLE_DOUBLE_PREFIX = "._"


def find_images(directory: Path, recursive: bool = False) -> list[Path]:
    # Symlinks can reach one file through several paths; processing it twice in parallel would race.
    images: dict[Path, Path] = {}
    for path in sorted(_iter_files(directory, recursive)):
        if _is_image(path):
            images.setdefault(path.resolve(), path)
    return list(images.values())


def _iter_files(directory: Path, recursive: bool) -> Iterator[Path]:
    if not recursive:
        yield from (path for path in directory.iterdir() if path.is_file())
        return

    visited: set[str] = set()
    for root, dirnames, filenames in os.walk(directory, followlinks=True):
        real_root = os.path.realpath(root)
        if real_root in visited:  # a symlink loop or a second link to the same folder
            dirnames.clear()
            continue
        visited.add(real_root)
        yield from (path for path in (Path(root, name) for name in filenames) if path.is_file())


def _is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTENSIONS and not path.name.startswith(_APPLE_DOUBLE_PREFIX)
