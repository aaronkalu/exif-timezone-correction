"""Compare the original timezone-correction.py with the current version on copies of real photos.

Two copies of --photos are made; the original script corrects one and the current version the other, both to the
same fixed --timezone offset. Every image is checked against the result the conversion must give: the same moment
in time, shown as clock time and offset in the new timezone, sub-seconds kept, and images already in that timezone
left alone. Every file is also checked for side effects: changes to other metadata, pixel data or file system
state, leftovers after --dry-run and --revert. The source folder is only read.

    uv run python scripts/compare.py --photos ~/Pictures/trip --timezone +09:00 --out /tmp/tz-compare
    uv run python scripts/compare.py ... -- --all-dates   # extra arguments for the current version
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
OLD_REF = "e7250a0"
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".tif", ".tiff", ".heic", ".heif", ".hif", ".avif", ".png", ".webp",
    ".raw", ".cr2", ".cr3", ".nef", ".nrw", ".arw", ".raf", ".rw2", ".orf", ".dng", ".pef", ".srw",
}
# (date tag, offset tag, sub-second tag) for each date --all-dates converts; the first is always converted.
DATES = (
    ("ExifIFD:DateTimeOriginal", "ExifIFD:OffsetTimeOriginal", "ExifIFD:SubSecTimeOriginal"),
    ("ExifIFD:CreateDate", "ExifIFD:OffsetTimeDigitized", "ExifIFD:SubSecTimeDigitized"),
    ("IFD0:ModifyDate", "ExifIFD:OffsetTime", "ExifIFD:SubSecTime"),
)
VOLATILE_GROUPS = {"System", "ExifTool", "Composite"}
LAYOUT_SUFFIXES = ("Offset", "Offsets", "Start", "IFDLength")
WRITER_STAMPS = {"XMP-x:XMPToolkit"}
_OFFSET = re.compile(r"^([+-])(\d{2}):(\d{2})$")


@dataclass
class Snapshot:
    files: dict[str, tuple[str, int, int]]
    tags: dict[str, dict[str, Any]]


@dataclass
class ToolRun:
    exit_code: int
    seconds: float
    output_tail: str


def main() -> int:
    args, tool_args = parse_args()
    out: Path = args.out
    work_old, work_new = out / "old", out / "new"

    source = read_tags(args.photos)
    images = sorted(name for name in walk(args.photos) if Path(name).suffix.lower() in IMAGE_EXTENSIONS
                    and not is_hidden(Path(name)))
    if not images:
        sys.exit(f"No images found in {args.photos}")
    for copy in (work_old, work_new):
        print(f"Copying to {copy}...")
        shutil.copytree(args.photos, copy, copy_function=shutil.copy2, symlinks=True)
    before = snapshot(work_new)
    before_old = snapshot(work_old)

    print("Dry run of the current version...")
    dry = run_new(args, work_new, [*tool_args, "--dry-run", "--no-undo-log"])
    after_dry = snapshot(work_new)

    print("Running the original script...")
    old = run_old(args, work_old)
    after_old = snapshot(work_old)

    print("Running the current version...")
    undo_log = out / "undo.jsonl"
    new = run_new(args, work_new, [*tool_args, "--undo-log", str(undo_log), "--report", str(out / "new-report.csv")])
    after_new = snapshot(work_new)

    print("Reverting the current version's run...")
    revert = run(
        [sys.executable, str(REPO / "timezone-correction.py"), "--revert", str(undo_log), "-q"], out
    ) if undo_log.is_file() else ToolRun(-1, 0.0, "no undo log written")
    after_revert = snapshot(work_new)

    target = parse_offset(args.timezone)
    assert target is not None
    rows = [check(name, source.get(name, {}), after_old.tags.get(name, {}), after_new.tags.get(name, {}), target)
            for name in images]
    write_rows(out / "files.csv", rows)
    report = render(
        args, tool_args, rows, old, new, dry, revert,
        side_effects("Original script", before_old, after_old),
        side_effects("Current version", before, after_new, ignore=frozenset({undo_log.name})),
        dry_run_check(before, after_dry),
        revert_check(before, after_revert),
    )
    (out / "report.md").write_text(report, encoding="utf-8")
    print(report)
    print(f"\nReport: {out / 'report.md'}\nPer file: {out / 'files.csv'}")
    return 0


def parse_args() -> tuple[argparse.Namespace, list[str]]:
    argv = sys.argv[1:]
    tool_args: list[str] = []
    if "--" in argv:
        split = argv.index("--")
        argv, tool_args = argv[:split], argv[split + 1:]
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--photos", type=Path, required=True)
    parser.add_argument("--timezone", required=True, help="Fixed target offset for both tools, e.g. +09:00.")
    parser.add_argument("--out", type=Path, required=True, help="New, empty working directory (needs 2x the space).")
    parser.add_argument("--old-ref", default=OLD_REF, help=f"Git revision of the original script (default {OLD_REF}).")
    args = parser.parse_args(argv)
    args.photos, args.out = args.photos.resolve(), args.out.resolve()
    if not _OFFSET.match(args.timezone):
        parser.error("--timezone must be a fixed offset like +09:00, which the original script also understands")
    if not args.photos.is_dir():
        parser.error(f"not a directory: {args.photos}")
    if args.out.is_relative_to(args.photos) or args.photos.is_relative_to(args.out):
        parser.error("--out must not overlap --photos")
    if args.out.exists() and any(args.out.iterdir()):
        parser.error(f"--out must be new or empty: {args.out}")
    args.out.mkdir(parents=True, exist_ok=True)
    return args, tool_args


def walk(directory: Path) -> list[str]:
    return [str(path.relative_to(directory)) for path in directory.rglob("*") if path.is_file()]


def is_hidden(relative: Path) -> bool:
    return any(part.startswith(".") for part in relative.parts)


def read_tags(directory: Path) -> dict[str, dict[str, Any]]:
    result = subprocess.run(
        ["exiftool", "-j", "-a", "-G1", "-s", "-r", "-q", "-q", "-charset", "filename=utf8", "-all",
         "-api", "ImageHashType=SHA256", "-ImageDataHash", str(directory)],
        capture_output=True, text=True, check=False,
    )
    records = json.loads(result.stdout) if result.stdout.strip() else []
    return {str(Path(record.pop("SourceFile")).relative_to(directory)): record for record in records}


def snapshot(directory: Path) -> Snapshot:
    files = {}
    for name in walk(directory):
        stat = (directory / name).stat()
        files[name] = (sha256(directory / name), stat.st_size, stat.st_mtime_ns)
    return Snapshot(files, read_tags(directory))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_old(args: argparse.Namespace, directory: Path) -> ToolRun:
    script = args.out / "old-tool" / "timezone-correction.py"
    script.parent.mkdir()
    script.write_text(subprocess.run(
        ["git", "-C", str(REPO), "show", f"{args.old_ref}:timezone-correction.py"],
        capture_output=True, text=True, check=True,
    ).stdout, encoding="utf-8")
    sign, offset = args.timezone[0], args.timezone[1:]
    command = [sys.executable, str(script), "-d", str(directory), "-r", "-t", offset, "-w", "4"]
    return run(command + (["-n"] if sign == "-" else []), script.parent)


def run_new(args: argparse.Namespace, directory: Path, extra: list[str]) -> ToolRun:
    return run([sys.executable, str(REPO / "timezone-correction.py"), "-d", str(directory), "-r",
                f"--timezone={args.timezone}", "-q", *extra], args.out)


def run(command: list[str], cwd: Path) -> ToolRun:
    start = time.monotonic()
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    lines = (result.stdout + result.stderr).strip().splitlines()
    return ToolRun(result.returncode, time.monotonic() - start, "\n".join(lines[-15:]))


def parse_offset(value: object) -> timedelta | None:
    match = _OFFSET.match(str(value)) if value is not None else None
    if not match:
        return None
    delta = timedelta(hours=int(match[2]), minutes=int(match[3]))
    return -delta if match[1] == "-" else delta


def format_offset(offset: timedelta) -> str:
    minutes = int(offset.total_seconds() // 60)
    return f"{'-' if minutes < 0 else '+'}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


def expected(tags: dict[str, Any], date_tag: str, offset_tag: str, target: timedelta) -> tuple[str, str] | None:
    """The converted (clock time, offset) for one date, or None if the source lacks it."""
    value, offset = tags.get(date_tag), parse_offset(tags.get(offset_tag))
    if not isinstance(value, str) or offset is None:
        return None
    local = datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
    moment = local.replace(tzinfo=timezone(offset))
    return moment.astimezone(timezone(target)).strftime("%Y:%m:%d %H:%M:%S"), format_offset(target)


def instant(tags: dict[str, Any]) -> str | None:
    value, offset = tags.get(DATES[0][0]), parse_offset(tags.get(DATES[0][1]))
    if not isinstance(value, str) or offset is None:
        return None
    moment = datetime.strptime(value, "%Y:%m:%d %H:%M:%S").replace(tzinfo=timezone(offset))
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def check(name: str, source: dict[str, Any], old: dict[str, Any], new: dict[str, Any], target: timedelta) -> dict[str, Any]:
    row: dict[str, Any] = {
        "file": name,
        "source_local": f"{source.get(DATES[0][0])} {source.get(DATES[0][1])}",
        "source_utc": instant(source),
    }
    for label, tags in (("old", old), ("new", new)):
        row[f"{label}_local"] = f"{tags.get(DATES[0][0])} {tags.get(DATES[0][1])}"
        row[f"{label}_utc"] = instant(tags)
        verdicts = []
        for date_tag, offset_tag, subsec_tag in DATES:
            want = expected(source, date_tag, offset_tag, target)
            got = (tags.get(date_tag), tags.get(offset_tag))
            unchanged = got == (source.get(date_tag), source.get(offset_tag))
            if want is None:
                verdicts.append("absent" if got == (None, None) else ("unchanged" if unchanged else "WRONG"))
            elif got == want and tags.get(subsec_tag) == source.get(subsec_tag):
                verdicts.append("unchanged" if unchanged else "correct")
            else:
                verdicts.append("unchanged" if unchanged else "WRONG")
        row[f"{label}_original"], row[f"{label}_create"], row[f"{label}_modify"] = verdicts
    return row


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def tag_category(key: str) -> str | None:
    group, _, name = key.rpartition(":")
    if group in VOLATILE_GROUPS:
        return None
    if "Date" in name or "Time" in name or name.startswith("Offset"):
        return "date"
    if key in WRITER_STAMPS:
        return "writer stamp"
    if name.endswith(LAYOUT_SUFFIXES):
        return "layout"
    return "unexpected"


def tag_changes(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[str]]:
    changes: dict[str, list[str]] = defaultdict(list)
    for key in before.keys() | after.keys():
        if before.get(key) != after.get(key) and (category := tag_category(key)) is not None:
            changes[category].append(key)
    return changes


def check_line(label: str, offenders: list[str], severity: str = "FAIL") -> list[str]:
    if not offenders:
        return [f"- PASS: {label}"]
    shown = ", ".join(f"`{name}`" for name in offenders[:5])
    more = f" and {len(offenders) - 5} more" if len(offenders) > 5 else ""
    return [f"- {severity}: {label}: {len(offenders)} files, e.g. {shown}{more}"]


def side_effects(label: str, before: Snapshot, after: Snapshot, ignore: frozenset[str] = frozenset()) -> str:
    added = sorted(after.files.keys() - before.files.keys() - ignore)
    removed = sorted(before.files.keys() - after.files.keys())
    pixel, hidden, mtime = [], [], []
    unexpected: Counter[str] = Counter()
    dates: Counter[str] = Counter()
    example: dict[str, str] = {}
    for name in sorted(before.files.keys() & after.files.keys()):
        if before.files[name][0] == after.files[name][0]:
            continue
        if is_hidden(Path(name)):
            hidden.append(name)
        # ExifTool -P restores whole seconds only.
        if before.files[name][2] // 10**9 != after.files[name][2] // 10**9:
            mtime.append(name)
        tags_before, tags_after = before.tags.get(name, {}), after.tags.get(name, {})
        if "File:ImageDataHash" in tags_before and tags_before["File:ImageDataHash"] != tags_after.get(
            "File:ImageDataHash"
        ):
            pixel.append(name)
        changes = tag_changes(tags_before, tags_after)
        dates.update(changes.get("date", []))
        for key in changes.get("unexpected", []):
            unexpected[key] += 1
            example.setdefault(key, f"{name}: {tags_before.get(key)!r} -> {tags_after.get(key)!r}")
    lines = [f"### {label}", ""]
    lines += check_line("Pixel data unchanged (ImageDataHash)", pixel)
    lines += check_line("Hidden files untouched", hidden)
    lines += check_line("No files removed", removed)
    lines += check_line("No unexpected new files (backups, temp files)", added)
    lines += check_line("File modification times kept", mtime, severity="WARN")
    if unexpected:
        lines += [f"- FAIL: Non-date metadata changed ({sum(unexpected.values())} tag changes)", "",
                  "  | Tag | Files | Example |", "  |---|---|---|"]
        lines += [f"  | `{key}` | {count} | {example[key][:120]} |" for key, count in unexpected.most_common(30)]
    else:
        lines.append("- PASS: No non-date metadata changed")
    if dates:
        lines.append("- INFO: Date tags changed: " + ", ".join(f"`{key}` ×{count}" for key, count in dates.most_common()))
    return "\n".join(lines)


def dry_run_check(before: Snapshot, after: Snapshot) -> str:
    changed = sorted(name for name in before.files if after.files.get(name, ("",))[0] != before.files[name][0])
    added = sorted(after.files.keys() - before.files.keys())
    return "\n".join(["### --dry-run", "", *check_line("No file changed", changed), *check_line("No file created", added)])


def revert_check(before: Snapshot, after: Snapshot) -> str:
    differing: Counter[str] = Counter()
    example: dict[str, str] = {}
    for name in before.files.keys() & after.files.keys():
        tags_before, tags_after = before.tags.get(name, {}), after.tags.get(name, {})
        for category, keys in tag_changes(tags_before, tags_after).items():
            if category in ("date", "unexpected"):
                for key in keys:
                    differing[key] += 1
                    example.setdefault(key, f"{name}: {tags_before.get(key)!r} -> {tags_after.get(key)!r}")
    lines = ["### --revert", ""]
    if not differing:
        lines.append("- PASS: Metadata after revert equals metadata before the run")
    else:
        lines += ["- FAIL: Metadata after revert differs from before the run", "",
                  "  | Tag | Files | Example |", "  |---|---|---|"]
        lines += [f"  | `{key}` | {count} | {example[key][:120]} |" for key, count in differing.most_common(30)]
    return "\n".join(lines)


def render(args: argparse.Namespace, tool_args: list[str], rows: list[dict[str, Any]], old: ToolRun, new: ToolRun,
           dry: ToolRun, revert: ToolRun, *sections: str) -> str:
    lines = [
        "# Timezone correction comparison", "",
        f"- Photos: `{args.photos}` ({len(rows)} images)",
        f"- Target: `{args.timezone}`; original `{args.old_ref}:timezone-correction.py`, current: working tree"
        + (f" with `{' '.join(tool_args)}`" if tool_args else ""),
        "", "## Runs", "", "| Run | Exit code | Time |", "|---|---|---|",
    ]
    for label, run_ in (("Original", old), ("Current", new), ("Current --dry-run", dry), ("Current --revert", revert)):
        lines.append(f"| {label} | {run_.exit_code} | {run_.seconds:.1f} s |")
    for label, run_ in (("Original", old), ("Current", new), ("Current --revert", revert)):
        if run_.exit_code != 0:
            lines += ["", f"{label} output (last lines):", "```", run_.output_tail, "```"]

    lines += ["", "## Correctness", "",
              ("*correct*: converted to the target with the same moment in time and sub-seconds; *unchanged*: left "
               "as it was (right for images already in the target timezone); *absent*: the image has no such date."),
              ""]
    needs_change = [row for row in rows if not row["source_local"].endswith(f" {args.timezone}")]
    already = [row for row in rows if row["source_local"].endswith(f" {args.timezone}")]
    lines += ["| Tool | Date | Needed conversion (" + str(len(needs_change)) + ") | Already in target ("
              + str(len(already)) + ") |", "|---|---|---|---|"]
    for tool in ("old", "new"):
        for date in ("original", "create", "modify"):
            counts = Counter(row[f"{tool}_{date}"] for row in needs_change)
            kept = Counter(row[f"{tool}_{date}"] for row in already)
            lines.append(
                f"| {'Original' if tool == 'old' else 'Current'} | {date.title()}Date | "
                + ", ".join(f"{verdict} {count}" for verdict, count in counts.most_common()) + " | "
                + ", ".join(f"{verdict} {count}" for verdict, count in kept.most_common()) + " |"
            )
    moved = [row for row in rows if row["source_utc"] and any(
        row[f"{tool}_utc"] not in (None, row["source_utc"]) for tool in ("old", "new"))]
    lines += ["", f"Moment in time changed (DateTimeOriginal in UTC): {len(moved)} files"]
    wrong = [row for row in rows if "WRONG" in (row["old_original"], row["new_original"])]
    if wrong:
        lines += ["", "| File | Source | Original script | Current |", "|---|---|---|---|"]
        lines += [f"| `{row['file']}` | {row['source_local']} | {row['old_local']} | {row['new_local']} |"
                  for row in wrong[:25]]
    lines += ["", "## Side effects", "", *(section + "\n" for section in sections)]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
