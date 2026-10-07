from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Iterable, Iterator, Protocol

from .domain import CaptureTime, UtcOffset


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


def correct_timezone(
    path: Path,
    target: UtcOffset,
    store: MetadataStore,
    assumed_source: UtcOffset | None = None,
) -> CorrectionResult:
    try:
        original = store.read_capture_time(path)
        if original is None:
            return CorrectionResult(path, Outcome.SKIPPED, "no original capture time recorded.")
        if original.offset == target:
            return CorrectionResult(path, Outcome.SKIPPED, "timezone already set.")

        source = original.offset if original.offset is not None else assumed_source
        if source is None:
            return CorrectionResult(
                path, Outcome.SKIPPED, f"no UTC offset recorded for {original}; set a source timezone to correct it."
            )

        corrected = replace(original, offset=source).in_timezone(target)
        store.write_capture_time(path, corrected)
    except Exception as error:  # One unreadable or out-of-range image must not stop the batch.
        return CorrectionResult(path, Outcome.FAILED, str(error) or type(error).__name__)

    detail = f"{original} -> {corrected}"
    if original.offset is None:
        detail += f" (no offset was recorded, assumed {source})"
    return CorrectionResult(path, Outcome.UPDATED, detail)


def correct_all(
    paths: Iterable[Path],
    target: UtcOffset,
    store: MetadataStore,
    workers: int = 1,
    assumed_source: UtcOffset | None = None,
) -> Iterator[CorrectionResult]:
    executor = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = [executor.submit(correct_timezone, path, target, store, assumed_source) for path in paths]
        for future in futures:
            yield future.result()
    finally:
        # Runs when the caller closes the generator early (e.g. Ctrl+C): drop queued work, finish files in progress.
        executor.shutdown(wait=True, cancel_futures=True)
