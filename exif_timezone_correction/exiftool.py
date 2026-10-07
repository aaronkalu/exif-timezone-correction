from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

from .domain import CaptureTime, MetadataError, UtcOffset

_EXECUTABLE = "exiftool"
_EXIF_DATETIME_FORMAT = "%Y:%m:%d %H:%M:%S"
_MISSING_VALUE = "-"  # Placeholder exiftool prints for an absent tag in table (-T) output.

# Read and write the same EXIF tags, so a second run sees what the first one wrote.
_DATETIME_TAG = "EXIF:DateTimeOriginal"
_OFFSET_TAG = "EXIF:OffsetTimeOriginal"


def is_installed() -> bool:
    try:
        _run("-ver")
    except MetadataError:
        return False
    return True


class ExifTool:
    def read_capture_time(self, path: Path) -> CaptureTime | None:
        output = _run("-T", f"-{_DATETIME_TAG}", f"-{_OFFSET_TAG}", _as_argument(path))
        fields = output.rstrip("\n").split("\t")
        if len(fields) != 2:
            raise MetadataError(f"Unexpected exiftool output: {output!r}")

        datetime_text, offset_text = fields
        if datetime_text == _MISSING_VALUE:
            return None

        try:
            local_time = datetime.strptime(datetime_text, _EXIF_DATETIME_FORMAT)
            offset = None if offset_text == _MISSING_VALUE else UtcOffset.parse(offset_text)
        except ValueError as error:
            raise MetadataError(f"Unreadable timestamp: {error}") from error

        return CaptureTime(local_time=local_time, offset=offset)

    def write_capture_time(self, path: Path, capture: CaptureTime) -> None:
        # SubSecTimeOriginal is deliberately not written: a whole-minute shift leaves it unchanged.
        if capture.offset is None:
            raise ValueError("Refusing to write a capture time without a UTC offset.")

        _run(
            "-overwrite_original",
            f"-{_DATETIME_TAG}={capture.local_time.strftime(_EXIF_DATETIME_FORMAT)}",
            f"-{_OFFSET_TAG}={capture.offset}",
            _as_argument(path),
        )


def _run(*args: str) -> str:
    try:
        result = subprocess.run(
            [_EXECUTABLE, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as error:
        raise MetadataError(f"Could not run {_EXECUTABLE}: {error}") from error

    if result.returncode != 0:
        message = result.stderr.strip() or f"{_EXECUTABLE} exited with code {result.returncode}"
        raise MetadataError(message)
    return result.stdout


def _as_argument(path: Path) -> str:
    # An absolute path never starts with "-", so exiftool cannot mistake it for an option.
    return str(path.absolute())
