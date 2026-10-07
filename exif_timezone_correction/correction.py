from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Iterator, Mapping, Protocol, Sequence

from .domain import CaptureTime, DateChange, DateTag, FileChange, ImageMetadata, MetadataError, UtcOffset, Zone

READ_BATCH_SIZE = 25
_GPS_ROUNDING_MINUTES = 15


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
    if correction.infer_from_gps and metadata.gps_utc_time is not None:
        inferred = _offset_from_gps(original.local_time, metadata.gps_utc_time)
        if inferred is not None:
            return inferred, f"no offset was recorded, {inferred} inferred from GPS time"
    if correction.assumed_source is not None:
        assumed = correction.assumed_source.offset_at_local(original.local_time)
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
        if with_exif and before.exif is None and tag is not DateTag.ORIGINAL:
            continue  # only DateTimeOriginal is created; other dates are corrected where they exist
        after = before.rewritten(capture, with_exif)
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
) -> Iterator[CorrectionResult]:
    sidecars = sidecars or {}
    batches = [images[i : i + READ_BATCH_SIZE] for i in range(0, len(images), READ_BATCH_SIZE)]

    def handle(item: _Read | CorrectionResult) -> Iterator[CorrectionResult | Future]:
        if isinstance(item, CorrectionResult):
            yield item
            return
        plan = _plan_read(item, correction, selection)
        if isinstance(plan, CorrectionResult):
            yield plan
        elif dry_run:
            yield CorrectionResult(plan.path, Outcome.WOULD_UPDATE, plan.detail, plan.changes)
        else:
            yield executor.submit(_apply, plan, store, backup)

    with _Pool(workers) as executor:
        initial = [executor.submit(_read_batch, batch, store, sidecars) for batch in batches]
        yield from executor.drain(initial, handle)


def revert_all(
    changes: Sequence[FileChange], store: MetadataStore, *, workers: int = 1, dry_run: bool = False
) -> Iterator[CorrectionResult]:
    def handle(item: CorrectionResult) -> Iterator[CorrectionResult]:
        yield item

    with _Pool(workers) as executor:
        initial = [executor.submit(_revert, change, store, dry_run) for change in changes]
        yield from executor.drain(initial, handle)


class _Pool(ThreadPoolExecutor):
    def drain(self, initial: Sequence[Future], handle) -> Iterator[CorrectionResult]:
        # Results are yielded as they finish, so one slow file does not hold back the ones behind it.
        pending = set(initial)
        try:
            while pending:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    items = future.result()
                    for item in items if isinstance(items, list) else [items]:
                        for output in handle(item):
                            if isinstance(output, Future):
                                pending.add(output)
                            else:
                                yield output
        finally:
            # Runs when the caller closes the generator early (e.g. Ctrl+C): drop queued work, finish files in progress.
            self.shutdown(wait=True, cancel_futures=True)


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


def _apply(plan: _Plan, store: MetadataStore, backup: bool) -> CorrectionResult:
    written: list[FileChange] = []
    for change in plan.changes:
        try:
            store.write(change, backup)
        except Exception as error:
            prefix = "" if change.path == plan.path else f"sidecar {change.path.name}: "
            return CorrectionResult(plan.path, Outcome.FAILED, prefix + _describe(error), tuple(written))
        written.append(change)
    return CorrectionResult(plan.path, Outcome.UPDATED, plan.detail, tuple(written))


def _revert(change: FileChange, store: MetadataStore, dry_run: bool) -> CorrectionResult:
    try:
        current = store.read([change.path])[change.path]
        if isinstance(current, Exception):
            raise current
        if any(current.date(c.tag) != c.after for c in change.dates):
            return CorrectionResult(change.path, Outcome.SKIPPED, "changed since the undo log was written.")
        restored = change.reversed()
        detail = ", ".join(f"{c.tag.tag_name} -> {c.after.effective() or '(removed)'}" for c in restored.dates)
        if dry_run:
            return CorrectionResult(change.path, Outcome.WOULD_UPDATE, detail, (restored,))
        store.write(restored)
    except Exception as error:
        return _failure(change.path, error)
    return CorrectionResult(change.path, Outcome.UPDATED, detail, (restored,))


def _failure(path: Path, error: Exception, prefix: str = "") -> CorrectionResult:
    return CorrectionResult(path, Outcome.FAILED, prefix + _describe(error))


def _describe(error: Exception) -> str:
    return str(error) or type(error).__name__
