from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import Enum
from functools import partial
from itertools import islice
from pathlib import Path
from typing import Callable, Iterator, List, Mapping, Protocol, Sequence, TypeVar, Union

from .domain import CaptureTime, DateChange, DateTag, FileChange, ImageMetadata, MetadataError, UtcOffset, Zone

READ_BATCH_SIZE = 25
_GPS_ROUNDING_MINUTES = 15

_Batch = TypeVar("_Batch")
_Job = Callable[[], List["CorrectionResult"]]
_Step = List[Union["CorrectionResult", _Job]]


class MetadataStore(Protocol):
    def read(self, paths: Sequence[Path]) -> Mapping[Path, ImageMetadata | Exception]: ...

    def write(self, change: FileChange, backup: bool = False) -> None: ...


class Mode(Enum):
    CONVERT = "convert"
    RELABEL = "relabel"


class Outcome(Enum):
    UPDATED = "updated"
    WOULD_UPDATE = "would update"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class Correction:
    target: Zone | None = None
    mode: Mode = Mode.CONVERT
    shift: timedelta = timedelta(0)
    assumed_source: Zone | None = None
    infer_from_gps: bool = False
    tags: tuple[DateTag, ...] = (DateTag.ORIGINAL,)


@dataclass(frozen=True)
class Selection:
    start: datetime | None = None  # inclusive
    end: datetime | None = None  # exclusive
    camera_model: str | None = None

    def rejects(self, capture: CaptureTime, metadata: ImageMetadata) -> str | None:
        if self.start is not None and capture.local_time < self.start:
            return "taken before the selected date range."
        if self.end is not None and capture.local_time >= self.end:
            return "taken after the selected date range."
        if self.camera_model is not None and self.camera_model.casefold() not in (metadata.camera_model or "").casefold():
            return f"camera model {metadata.camera_model or '(unknown)'!r} not selected."
        return None


@dataclass(frozen=True)
class CorrectionResult:
    path: Path
    outcome: Outcome
    detail: str
    changes: tuple[FileChange, ...] = ()

    def __str__(self) -> str:
        return f"{self.outcome.value.capitalize()} {self.path}: {self.detail}"


@dataclass(frozen=True)
class _Read:
    path: Path
    metadata: ImageMetadata | Exception
    sidecars: Mapping[Path, ImageMetadata | Exception] = field(default_factory=dict)


@dataclass(frozen=True)
class _Plan:
    path: Path
    detail: str
    changes: tuple[FileChange, ...]


def plan_correction(
    path: Path,
    metadata: ImageMetadata,
    correction: Correction,
    selection: Selection = Selection(),
    sidecars: Mapping[Path, ImageMetadata] | None = None,
) -> CorrectionResult | _Plan:
    sidecars = sidecars or {}
    original = metadata.date(DateTag.ORIGINAL).effective()
    if original is None:
        return CorrectionResult(path, Outcome.SKIPPED, "no original capture time recorded.")

    rejection = selection.rejects(original, metadata)
    if rejection is not None:
        return CorrectionResult(path, Outcome.SKIPPED, rejection)

    if correction.mode is Mode.RELABEL:
        source, source_note = None, None  # relabelling keeps the clock time, so the source offset is irrelevant
    else:
        source, source_note = _resolve_source(original, metadata, correction)
    if source is None and correction.target is not None and correction.mode is Mode.CONVERT:
        return CorrectionResult(
            path,
            Outcome.SKIPPED,
            f"no UTC offset recorded for {original}; set a source timezone (or --gps) to correct it.",
        )

    desired: dict[DateTag, CaptureTime] = {}
    for tag in correction.tags:
        capture = metadata.date(tag).effective()
        if capture is None:
            continue
        tag_source = capture.offset if capture.offset is not None else source
        desired[tag] = _transform(capture, tag_source, correction)

    changes = [_file_change(path, metadata, desired, with_exif=True)]
    changes += [_file_change(sidecar, data, desired, with_exif=False) for sidecar, data in sidecars.items()]
    changes = tuple(change for change in changes if change.dates)
    if not changes:
        return CorrectionResult(path, Outcome.SKIPPED, "already correct.")

    detail = f"{original} -> {desired[DateTag.ORIGINAL]}"
    notes = [source_note] if source_note else []
    other_tags = sorted({c.tag.tag_name for change in changes for c in change.dates} - {DateTag.ORIGINAL.tag_name})
    if other_tags:
        notes.append(f"also {', '.join(other_tags)}")
    sidecar_count = sum(change.path != path for change in changes)
    if sidecar_count:
        notes.append(f"{sidecar_count} sidecar(s)")
    if notes:
        detail += f" ({'; '.join(notes)})"
    return _Plan(path, detail, changes)


def _resolve_source(
    original: CaptureTime, metadata: ImageMetadata, correction: Correction
) -> tuple[UtcOffset | None, str | None]:
    if original.offset is not None:
        return original.offset, None
    # The offset belongs to the corrected clock time, which is what _transform converts.
    local_time = original.local_time + correction.shift
    if correction.infer_from_gps and metadata.gps_utc_time is not None:
        inferred = _offset_from_gps(local_time, metadata.gps_utc_time)
        if inferred is not None:
            return inferred, f"no offset was recorded, {inferred} inferred from GPS time"
    if correction.assumed_source is not None:
        assumed = correction.assumed_source.offset_at_local(local_time)
        return assumed, f"no offset was recorded, assumed {assumed}"
    return None, None


def _offset_from_gps(local_time: datetime, gps_utc_time: datetime) -> UtcOffset | None:
    # The camera clock and the GPS fix are never exactly in step; real offsets are multiples of 15 minutes.
    minutes = (local_time - gps_utc_time).total_seconds() / 60
    try:
        return UtcOffset(round(minutes / _GPS_ROUNDING_MINUTES) * _GPS_ROUNDING_MINUTES)
    except ValueError:  # a stale GPS timestamp from another day
        return None


def _transform(capture: CaptureTime, source: UtcOffset | None, correction: Correction) -> CaptureTime:
    local_time = capture.local_time + correction.shift
    if correction.target is None:
        return CaptureTime(local_time, source)
    if correction.mode is Mode.RELABEL:
        return CaptureTime(local_time, correction.target.offset_at_local(local_time))

    utc_time = local_time - source.as_timedelta()
    target = correction.target.offset_at_utc(utc_time)
    return CaptureTime(utc_time + target.as_timedelta(), target)


def _file_change(
    path: Path, metadata: ImageMetadata, desired: Mapping[DateTag, CaptureTime], with_exif: bool
) -> FileChange:
    dates = []
    for tag, capture in desired.items():
        before = metadata.date(tag)
        if not with_exif and before.xmp is None:
            continue
        # Only DateTimeOriginal is created in EXIF; other dates are corrected where they exist.
        write_exif = with_exif and (before.exif is not None or tag is DateTag.ORIGINAL)
        after = before.rewritten(capture, write_exif)
        if after != before:
            dates.append(DateChange(tag, before, after))
    return FileChange(path, tuple(dates))


def correct_all(
    images: Sequence[Path],
    correction: Correction,
    store: MetadataStore,
    *,
    selection: Selection = Selection(),
    sidecars: Mapping[Path, Sequence[Path]] | None = None,
    workers: int = 1,
    dry_run: bool = False,
    backup: bool = False,
    on_written: Callable[[FileChange], None] | None = None,
) -> Iterator[CorrectionResult]:
    # on_written runs in worker threads right after each write, including writes that finish after the caller
    # stopped iterating (e.g. on Ctrl+C), whose results are never yielded; an undo log must record from there.
    sidecars = sidecars or {}

    def read(batch: Sequence[Path]) -> _Step:
        steps: _Step = []
        for item in _read_batch(batch, store, sidecars):
            plan = item if isinstance(item, CorrectionResult) else _plan_read(item, correction, selection)
            if isinstance(plan, CorrectionResult):
                steps.append(plan)
            elif dry_run:
                steps.append(CorrectionResult(plan.path, Outcome.WOULD_UPDATE, plan.detail, plan.changes))
            else:
                steps.append(partial(_apply, plan, store, backup, on_written))
        return steps

    return _pipeline(_batched(images), read, workers)


def revert_all(
    changes: Sequence[FileChange], store: MetadataStore, *, workers: int = 1, dry_run: bool = False
) -> Iterator[CorrectionResult]:
    by_path: dict[Path, list[FileChange]] = {}
    for change in changes:
        by_path.setdefault(change.path, []).append(change)

    def read(batch: Sequence[tuple[Path, list[FileChange]]]) -> _Step:
        try:
            found = store.read([path for path, _ in batch])
        except Exception as error:
            return [_failure(change.path, error) for _, file_changes in batch for change in file_changes]
        missing = MetadataError("exiftool returned no metadata")
        # One job per file, so entries from several runs on the same file are never written concurrently.
        return [
            partial(_revert_file, file_changes, found.get(path, missing), store, dry_run)
            for path, file_changes in batch
        ]

    return _pipeline(_batched(list(by_path.items())), read, workers)


def _batched(items: Sequence[_Batch]) -> Iterator[Sequence[_Batch]]:
    return (items[i : i + READ_BATCH_SIZE] for i in range(0, len(items), READ_BATCH_SIZE))


def _pipeline(
    batches: Iterator[_Batch], read: Callable[[_Batch], _Step], workers: int
) -> Iterator[CorrectionResult]:
    # Only a few batches are read ahead of the writes, so writing starts early and memory stays bounded.
    # Results are yielded as they finish, so one slow file does not hold back the ones behind it.
    executor = ThreadPoolExecutor(workers)
    try:
        reads = {executor.submit(read, batch) for batch in islice(batches, workers)}
        pending: set[Future] = set(reads)
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                if future not in reads:
                    yield from future.result()
                    continue
                reads.remove(future)
                for step in future.result():
                    if isinstance(step, CorrectionResult):
                        yield step
                    else:
                        pending.add(executor.submit(step))
                for batch in islice(batches, 1):
                    next_read = executor.submit(read, batch)
                    reads.add(next_read)
                    pending.add(next_read)
    finally:
        # Runs when the caller closes the generator early (e.g. Ctrl+C): drop queued work, finish files in progress.
        executor.shutdown(wait=True, cancel_futures=True)


def _read_batch(
    batch: Sequence[Path], store: MetadataStore, sidecars: Mapping[Path, Sequence[Path]]
) -> list[_Read | CorrectionResult]:
    paths = [*batch, *(sidecar for image in batch for sidecar in sidecars.get(image, ()))]
    try:
        found = store.read(paths)
    except Exception as error:  # One unreadable batch must not stop the run.
        return [_failure(image, error) for image in batch]

    missing = MetadataError("exiftool returned no metadata")
    return [
        _Read(image, found.get(image, missing), {s: found.get(s, missing) for s in sidecars.get(image, ())})
        for image in batch
    ]


def _plan_read(item: _Read, correction: Correction, selection: Selection) -> CorrectionResult | _Plan:
    if isinstance(item.metadata, Exception):
        return _failure(item.path, item.metadata)
    for sidecar, metadata in item.sidecars.items():
        if isinstance(metadata, Exception):
            return _failure(item.path, metadata, f"sidecar {sidecar.name}: ")
    try:
        return plan_correction(item.path, item.metadata, correction, selection, item.sidecars)
    except Exception as error:  # e.g. a date shifted out of range must fail only this image
        return _failure(item.path, error)


def _apply(
    plan: _Plan, store: MetadataStore, backup: bool, on_written: Callable[[FileChange], None] | None
) -> list[CorrectionResult]:
    written: list[FileChange] = []
    for change in plan.changes:
        try:
            store.write(change, backup)
        except Exception as error:
            prefix = "" if change.path == plan.path else f"sidecar {change.path.name}: "
            return [CorrectionResult(plan.path, Outcome.FAILED, prefix + _describe(error), tuple(written))]
        written.append(change)
        if on_written is not None:
            on_written(change)
    return [CorrectionResult(plan.path, Outcome.UPDATED, plan.detail, tuple(written))]


def _revert_file(
    changes: Sequence[FileChange], current: ImageMetadata | Exception, store: MetadataStore, dry_run: bool
) -> list[CorrectionResult]:
    results = []
    # A log appended to by several runs is undone newest first, each entry starting from the state the next one left.
    for change in reversed(changes):
        if isinstance(current, Exception):
            results.append(_failure(change.path, current))
            continue
        if any(current.date(c.tag) != c.after for c in change.dates):
            results.append(CorrectionResult(change.path, Outcome.SKIPPED, "changed since the undo log was written."))
            continue
        restored = change.reversed()
        detail = ", ".join(f"{c.tag.tag_name} -> {c.after.effective() or '(removed)'}" for c in restored.dates)
        if not dry_run:
            try:
                store.write(restored)
            except Exception as error:
                results.append(_failure(change.path, error))
                current = error
                continue
        results.append(CorrectionResult(change.path, Outcome.WOULD_UPDATE if dry_run else Outcome.UPDATED, detail, (restored,)))
        current = replace(current, dates={**current.dates, **{c.tag: c.after for c in restored.dates}})
    return results


def _failure(path: Path, error: Exception, prefix: str = "") -> CorrectionResult:
    return CorrectionResult(path, Outcome.FAILED, prefix + _describe(error))


def _describe(error: Exception) -> str:
    return str(error) or type(error).__name__
