from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Sequence

from tqdm import tqdm

from . import exiftool
from .correction import Outcome, correct_all
from .domain import UtcOffset
from .files import find_images


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        target = resolve_target_offset(args.timezone, args.negative)
    except ValueError as error:
        parser.error(str(error))
    if not args.dir.is_dir():
        parser.error(f"Directory not found: {args.dir}")

    if not exiftool.is_installed():
        print("ExifTool is not installed. Please install it before running this script.", file=sys.stderr)
        return 1

    images = find_images(args.dir, args.recursive)
    if not images:
        print("No images found.")
        return 0

    outcomes: Counter[Outcome] = Counter()
    results = correct_all(images, target, exiftool.ExifTool(), args.workers)
    with tqdm(total=len(images), desc="Processing Images", unit="img") as progress:
        for result in results:
            tqdm.write(str(result))
            outcomes[result.outcome] += 1
            progress.update()

    print(
        f"Done: {outcomes[Outcome.UPDATED]} updated, "
        f"{outcomes[Outcome.SKIPPED]} skipped, "
        f"{outcomes[Outcome.FAILED]} failed."
    )
    return 1 if outcomes[Outcome.FAILED] else 0


def resolve_target_offset(timezone: str, negative: bool) -> UtcOffset:
    offset = UtcOffset.parse(timezone)
    if not negative:
        return offset
    if timezone.strip().startswith(("+", "-")):
        raise ValueError("Use either a signed --timezone or --negative, not both.")
    return offset.negated()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Adjust image EXIF timestamps based on a new timezone.")
    parser.add_argument("-d", "--dir", required=True, type=Path, help="Path to the folder containing images.")
    parser.add_argument(
        "-t",
        "--timezone",
        required=True,
        help='New timezone as "HH:MM" (e.g. 02:00 for UTC+2). A signed value such as '
        '"--timezone=-06:00" is also accepted.',
    )
    parser.add_argument(
        "-n", "--negative", action="store_true", help="The new timezone is negative (default is positive)."
    )
    parser.add_argument("-r", "--recursive", action="store_true", help="Process images in subdirectories recursively.")
    parser.add_argument("-w", "--workers", type=_positive_int, default=1, help="Number of threads to run in parallel.")
    return parser


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        value = 0
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {text!r}")
    return value
