from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import IO, Any

from .domain import CaptureTime, DateChange, DateTag, FileChange, StoredDate, UtcOffset

# One JSON object per line, flushed as each file is written, so an interrupted run still leaves a usable log.


class UndoLog:
    def __init__(self, stream: IO[str]) -> None:
        self._stream = stream
        self._lock = threading.Lock()

    def record(self, change: FileChange) -> None:
        line = json.dumps(_dump_change(change)) + "\n"
        with self._lock:  # called from the worker threads
            self._stream.write(line)
            self._stream.flush()


def load(path: Path) -> list[FileChange]:
    changes = []
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                changes.append(_load_change(json.loads(line)))
            except (ValueError, KeyError, TypeError) as error:
                raise ValueError(f"{path}:{number}: not a valid undo log entry ({error})") from error
    return changes


def _dump_change(change: FileChange) -> dict[str, Any]:
    return {
        "path": str(change.path.absolute()),
        "dates": [
            {"tag": date.tag.name, "before": _dump_stored(date.before), "after": _dump_stored(date.after)}
            for date in change.dates
        ],
    }


def _load_change(data: dict[str, Any]) -> FileChange:
    dates = tuple(
        DateChange(DateTag[date["tag"]], _load_stored(date["before"]), _load_stored(date["after"]))
        for date in data["dates"]
    )
    return FileChange(Path(data["path"]), dates)


def _dump_stored(stored: StoredDate) -> dict[str, Any]:
    return {"exif": _dump_capture(stored.exif), "xmp": _dump_capture(stored.xmp)}


def _load_stored(data: dict[str, Any]) -> StoredDate:
    return StoredDate(_load_capture(data["exif"]), _load_capture(data["xmp"]))


def _dump_capture(capture: CaptureTime | None) -> dict[str, Any] | None:
    if capture is None:
        return None
    return {"local_time": capture.local_time.isoformat(), "offset": None if capture.offset is None else str(capture.offset)}


def _load_capture(data: dict[str, Any] | None) -> CaptureTime | None:
    if data is None:
        return None
    offset = None if data["offset"] is None else UtcOffset.parse(data["offset"])
    return CaptureTime(datetime.fromisoformat(data["local_time"]), offset)
