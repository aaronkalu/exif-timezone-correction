from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Iterator, Protocol

from .domain import CaptureTime, MetadataError, UtcOffset


class MetadataStore(Protocol):
    def read_capture_time(self, path: Path) -> CaptureTime | None: ...

    def write_capture_time(self, path: Path, capture: CaptureTime) -> None: ...


class Outcome(Enum):
    UPDATED = "updated"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class CorrectionResult:
    path: Path
    outcome: Outcome
    detail: str

    def __str__(self) -> str:
        return f"{self.outcome.value.capitalize()} {self.path}: {self.detail}"


def correct_timezone(path: Path, target: UtcOffset, store: MetadataStore) -> CorrectionResult:
    try:
        original = store.read_capture_time(path)
        if original is None:
            return CorrectionResult(path, Outcome.SKIPPED, "no original capture time recorded.")
        if original.offset == target:
            return CorrectionResult(path, Outcome.SKIPPED, "timezone already set.")

        corrected = original.in_timezone(target)
        store.write_capture_time(path, corrected)
    except MetadataError as error:
        return CorrectionResult(path, Outcome.FAILED, str(error))

    detail = f"{original} -> {corrected}"
    if original.offset is None:
        detail += " (no offset was recorded, assumed UTC)"
    return CorrectionResult(path, Outcome.UPDATED, detail)


def correct_all(
    paths: Iterable[Path],
    target: UtcOffset,
    store: MetadataStore,
    workers: int = 1,
) -> Iterator[CorrectionResult]:
    executor = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = [executor.submit(correct_timezone, path, target, store) for path in paths]
        for future in as_completed(futures):
            yield future.result()
    finally:
        # Drop queued work if the caller stops early (e.g. Ctrl+C); finish files in progress.
        executor.shutdown(wait=True, cancel_futures=True)
