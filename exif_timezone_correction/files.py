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
    return sorted(path for path in _iter_files(directory, recursive) if _is_image(path))


def _iter_files(directory: Path, recursive: bool) -> Iterator[Path]:
    if not recursive:
        yield from (path for path in directory.iterdir() if path.is_file())
        return
    for root, _, filenames in os.walk(directory):
        yield from (Path(root, name) for name in filenames)


def _is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTENSIONS and not path.name.startswith(_APPLE_DOUBLE_PREFIX)
