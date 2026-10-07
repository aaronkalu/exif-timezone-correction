from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import Counter
from contextlib import ExitStack, closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, Sequence

from tqdm import tqdm

from . import __version__, exiftool, undo
from .correction import Correction, CorrectionResult, Mode, Outcome, Selection, correct_all, revert_all
from .domain import DateTag, FileChange, NamedZone, Zone, parse_duration, parse_zone
from .files import find_images, find_sidecars

DEFAULT_WORKERS = min(4, os.cpu_count() or 1)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.revert is not None:
            changes = undo.load(args.revert)
        else:
            correction, selection = _build_request(args)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    if args.revert is None and not args.dir.is_dir():
        parser.error(f"Directory not found: {args.dir}")

    installed = exiftool.version()
    if installed is None:
        print("ExifTool is not installed. Please install it before running this script.", file=sys.stderr)
        return 1
    if installed < exiftool.MINIMUM_VERSION:
        required = ".".join(map(str, exiftool.MINIMUM_VERSION))
        print(f"ExifTool {required} or newer is required, found {'.'.join(map(str, installed))}.", file=sys.stderr)
        return 1

    with ExitStack() as stack:
        store = stack.enter_context(exiftool.ExifTool())
        if args.revert is not None:
            total = len(changes)
            results = revert_all(changes, store, workers=args.workers, dry_run=args.dry_run)
            log = None
        else:
            images = find_images(args.dir, args.recursive)
            if not images:
                print("No images found.")
                return 0
            total = len(images)
            sidecars = {} if args.no_sidecars else find_sidecars(images)
            log = None if args.dry_run or args.no_undo_log else stack.enter_context(_UndoLogFile(_undo_log_path(args)))
            results = correct_all(
                images,
                correction,
                store,
                selection=selection,
                sidecars=sidecars,
                workers=args.workers,
                dry_run=args.dry_run,
                backup=args.backup,
                on_written=None if log is None else log.record,
            )
        report = stack.enter_context(_Report(args.report)) if args.report else None
        # closing() shuts the worker pool down on Ctrl+C, so queued images are not processed after the interrupt.
        outcomes = _run(stack.enter_context(closing(results)), total, args.quiet, report)

    _print_summary(outcomes, args.dry_run)
    if log is not None and log.used:
        print(f"Undo log: {log.path} (restore with --revert)")
    return 1 if outcomes[Outcome.FAILED] else 0


def _run(results: Iterator[CorrectionResult], total: int, quiet: bool, report: _Report | None) -> Counter[Outcome]:
    outcomes: Counter[Outcome] = Counter()
    with tqdm(total=total, desc="Processing", unit="file", disable=quiet) as progress:
        for result in results:
            if report is not None:
                report.record(result)
            if not quiet or result.outcome is Outcome.FAILED:
                tqdm.write(str(result), file=sys.stderr if result.outcome is Outcome.FAILED else sys.stdout)
            outcomes[result.outcome] += 1
            progress.update()
    return outcomes


def _print_summary(outcomes: Counter[Outcome], dry_run: bool) -> None:
    if dry_run:
        print(
            f"Dry run: {outcomes[Outcome.WOULD_UPDATE]} would be updated, "
            f"{outcomes[Outcome.SKIPPED]} skipped, {outcomes[Outcome.FAILED]} failed. No files were changed."
        )
    else:
        print(
            f"Done: {outcomes[Outcome.UPDATED]} updated, "
            f"{outcomes[Outcome.SKIPPED]} skipped, "
            f"{outcomes[Outcome.FAILED]} failed."
        )


def _build_request(args: argparse.Namespace) -> tuple[Correction, Selection]:
    if args.dir is None:
        raise ValueError("--dir is required unless --revert is given.")
    target = None if args.timezone is None else resolve_target_zone(args.timezone, args.negative)
    if args.negative and target is None:
        raise ValueError("--negative needs --timezone.")
    source = None if args.source_timezone is None else parse_zone(args.source_timezone)
    shift = timedelta(0) if args.shift is None else parse_duration(args.shift)
    if target is None and not shift and source is None and not args.gps:
        raise ValueError("Nothing to do: give --timezone, --shift, --source-timezone or --gps.")
    if args.mode == Mode.RELABEL.value and target is None:
        raise ValueError("--mode relabel needs --timezone.")

    start = None if args.date_from is None else _parse_date(args.date_from, end_of_day=False)
    end = None if args.date_until is None else _parse_date(args.date_until, end_of_day=True)
    if start is not None and end is not None and start >= end:
        raise ValueError("--from must be before --until.")

    correction = Correction(
        target=target,
        mode=Mode(args.mode),
        shift=shift,
        assumed_source=source,
        infer_from_gps=args.gps,
        tags=tuple(DateTag) if args.all_dates else (DateTag.ORIGINAL,),
    )
    return correction, Selection(start, end, args.camera)


def resolve_target_zone(timezone: str, negative: bool) -> Zone:
    zone = parse_zone(timezone)
    if not negative:
        return zone
    if isinstance(zone, NamedZone):
        raise ValueError("--negative only applies to HH:MM offsets.")
    if timezone.strip().startswith(("+", "-")):
        raise ValueError("Use either a signed --timezone or --negative, not both.")
    print("Warning: --negative is deprecated; write the sign instead, e.g. --timezone=-06:00.", file=sys.stderr)
    return zone.negated()


def _parse_date(text: str, end_of_day: bool) -> datetime:
    try:
        moment = datetime.fromisoformat(text.strip().replace("T", " "))
    except ValueError:
        raise ValueError(f"Invalid date {text!r}. Use YYYY-MM-DD or 'YYYY-MM-DD HH:MM[:SS]'.") from None
    if end_of_day and len(text.strip()) == len("YYYY-MM-DD"):
        return moment + timedelta(days=1)  # a bare --until date includes that whole day
    return moment


def _undo_log_path(args: argparse.Namespace) -> Path:
    if args.undo_log is not None:
        return args.undo_log
    return args.dir / f"exif-timezone-correction-undo-{datetime.now():%Y%m%d-%H%M%S}.jsonl"


class _UndoLogFile:
    """Opened before any file is touched, so an unwritable log location stops the run instead of losing undo data."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.used = False

    def __enter__(self) -> _UndoLogFile:
        try:
            self._stream = self.path.open("a", encoding="utf-8")
        except OSError as error:
            raise SystemExit(f"Cannot write the undo log {self.path}: {error}. Use --undo-log or --no-undo-log.")
        self._log = undo.UndoLog(self._stream)
        return self

    def record(self, change: FileChange) -> None:
        self._log.record(change)
        self.used = True

    def __exit__(self, *exc_info: object) -> None:
        self._stream.close()
        if not self.used and self.path.stat().st_size == 0:
            self.path.unlink()


class _Report:
    def __init__(self, path: Path) -> None:
        self._path = path

    def __enter__(self) -> _Report:
        try:
            self._stream = self._path.open("w", newline="", encoding="utf-8")
        except OSError as error:
            raise SystemExit(f"Cannot write the report {self._path}: {error}.")
        self._writer = csv.writer(self._stream)
        self._writer.writerow(["path", "outcome", "detail"])
        return self

    def record(self, result: CorrectionResult) -> None:
        self._writer.writerow([str(result.path), result.outcome.value, result.detail])

    def __exit__(self, *exc_info: object) -> None:
        self._stream.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Correct image capture timestamps: move them into another timezone, fix a wrong camera "
        "clock, or fill in missing UTC offsets. Negative values must be written with '=', e.g. --timezone=-06:00."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-d", "--dir", type=Path, help="Path to the folder containing images.")
    parser.add_argument("-r", "--recursive", action="store_true", help="Process images in subdirectories recursively.")

    what = parser.add_argument_group("what to change")
    what.add_argument(
        "-t",
        "--timezone",
        help='New timezone: an offset such as "+02:00" or "-06:00", or an IANA name such as "Europe/Berlin" '
        "(the offset is then chosen per photo, following daylight saving time).",
    )
    what.add_argument("-n", "--negative", action="store_true", help="Deprecated: the HH:MM --timezone is negative.")
    what.add_argument(
        "--mode",
        choices=[mode.value for mode in Mode],
        default=Mode.CONVERT.value,
        help="convert (default): keep the moment, change clock time and offset. "
        "relabel: keep the clock time, only replace the offset (the camera clock was right, the offset wrong).",
    )
    what.add_argument(
        "--shift",
        help="Move the clock time by [+|-]HH:MM[:SS] first, to fix a camera clock that was wrong "
        "(e.g. --shift=-1:00:30). Not idempotent: each run shifts again.",
    )
    what.add_argument(
        "-s",
        "--source-timezone",
        help="Timezone the camera clock was set to (offset or IANA name), for images that recorded no UTC offset. "
        "Without it (or --gps) those images are skipped.",
    )
    what.add_argument(
        "--gps",
        action="store_true",
        help="For images without a recorded offset, infer it from the GPS timestamp (which is UTC).",
    )
    what.add_argument(
        "--all-dates",
        action="store_true",
        help="Also correct CreateDate and ModifyDate (with their offsets) where present, like ExifTool's AllDates.",
    )

    which = parser.add_argument_group("which images")
    which.add_argument("--from", dest="date_from", help="Only images taken at or after this local time (YYYY-MM-DD[ HH:MM]).")
    which.add_argument("--until", dest="date_until", help="Only images taken before this local time; a bare date includes that day.")
    which.add_argument("--camera", help="Only images whose camera model contains this text (case-insensitive).")
    which.add_argument("--no-sidecars", action="store_true", help="Do not update .xmp sidecar files next to images.")

    safety = parser.add_argument_group("safety and output")
    safety.add_argument("--dry-run", action="store_true", help="Show what would change without writing anything.")
    safety.add_argument("--backup", action="store_true", help="Keep ExifTool's <file>_original backup copies.")
    safety.add_argument("--undo-log", type=Path, help="Where to write the undo log (default: in --dir).")
    safety.add_argument("--no-undo-log", action="store_true", help="Do not write an undo log.")
    safety.add_argument("--revert", type=Path, metavar="UNDO_LOG", help="Restore the values recorded in an undo log.")
    safety.add_argument("--report", type=Path, help="Write a CSV with the outcome for every file.")
    safety.add_argument("-q", "--quiet", action="store_true", help="Print only failures and the summary.")
    safety.add_argument(
        "-w", "--workers", type=_positive_int, default=DEFAULT_WORKERS,
        help=f"Number of files processed in parallel (default: {DEFAULT_WORKERS}).",
    )
    return parser


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        value = 0
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {text!r}")
    return value
