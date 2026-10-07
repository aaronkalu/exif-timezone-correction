from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

RAW_EXTENSIONS = frozenset(
    {".raw", ".arw", ".raf", ".nef", ".nrw", ".orf", ".rw2", ".cr2", ".cr3", ".dng", ".pef", ".srw"}
)
IMAGE_EXTENSIONS = RAW_EXTENSIONS | {
    ".jpg", ".jpeg", ".tif", ".tiff", ".heic", ".heif", ".hif", ".avif", ".png", ".webp",
}
_SIDECAR_EXTENSION = ".xmp"

# macOS writes "._<name>" metadata files on non-Apple filesystems; they are not images.
_APPLE_DOUBLE_PREFIX = "._"


def find_images(directory: Path, recursive: bool = False) -> list[Path]:
    # Symlinks can reach one file through several paths; processing it twice in parallel would race.
    images: dict[Path, Path] = {}
    for path in sorted(_iter_files(directory, recursive)):
        if _is_image(path):
            images.setdefault(path.resolve(), path)
    return list(images.values())


def find_sidecars(images: list[Path]) -> dict[Path, list[Path]]:
    """Maps each image to its XMP sidecars: "photo.cr2.xmp" (darktable) and, for RAW files, "photo.xmp" (Lightroom).

    A sidecar is given to one image only, so a RAW+JPEG pair never writes the same sidecar twice.
    """
    listings: dict[Path, dict[str, Path]] = {}
    claimed: set[Path] = set()
    sidecars: dict[Path, list[Path]] = {}
    for image in images:
        if image.parent not in listings:
            listings[image.parent] = {
                path.name.casefold(): path
                for path in image.parent.iterdir()
                if path.suffix.casefold() == _SIDECAR_EXTENSION and path.is_file() and not _is_apple_double(path)
            }
        names = [image.name + _SIDECAR_EXTENSION]
        if image.suffix.lower() in RAW_EXTENSIONS:
            names.append(image.stem + _SIDECAR_EXTENSION)
        for name in names:
            sidecar = listings[image.parent].get(name.casefold())
            if sidecar is not None and sidecar.resolve() not in claimed:
                claimed.add(sidecar.resolve())
                sidecars.setdefault(image, []).append(sidecar)
    return sidecars


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
    return path.suffix.lower() in IMAGE_EXTENSIONS and not _is_apple_double(path)


def _is_apple_double(path: Path) -> bool:
    return path.name.startswith(_APPLE_DOUBLE_PREFIX)
