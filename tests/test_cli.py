import csv
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from exif_timezone_correction.cli import main, resolve_target_zone
from exif_timezone_correction.domain import MetadataError, NamedZone, UtcOffset
from exif_timezone_correction.exiftool import ExifTool

BLANK_JPEG = Path(__file__).parent / "data" / "blank.jpg"
requires_exiftool = pytest.mark.skipif(shutil.which("exiftool") is None, reason="exiftool not installed")
running_as_root = hasattr(os, "geteuid") and os.geteuid() == 0


@pytest.mark.parametrize(
    ("timezone", "negative", "expected"),
    [("06:00", False, "+06:00"), ("06:00", True, "-06:00"), ("-06:30", False, "-06:30"), ("+02:00", False, "+02:00")],
)
def test_resolve_target_zone(timezone, negative, expected):
    assert resolve_target_zone(timezone, negative) == UtcOffset.parse(expected)


def test_resolve_named_target_zone():
    assert isinstance(resolve_target_zone("America/New_York", False), NamedZone)


@pytest.mark.parametrize(("timezone", "negative"), [("+06:00", True), ("Europe/Berlin", True), ("Mars/Olympus", False)])
def test_invalid_target_zones(timezone, negative):
    with pytest.raises(ValueError):
        resolve_target_zone(timezone, negative)


@pytest.mark.parametrize(
    "argv",
    [
        ["-t", "99:00"],
        ["-t", "02:00", "-w", "0"],
        ["-t", "02:00", "-s", "25:00"],
        ["-t", "02:00", "-d", "/does/not/exist"],
        [],
        ["--mode", "relabel", "--shift", "01:00"],
        ["--shift", "1h"],
        ["-t", "02:00", "--from", "2024-05-02", "--until", "2024-05-01"],
        ["-t", "02:00", "--from", "yesterday"],
    ],
)
def test_invalid_arguments_exit_with_usage_error(argv, tmp_path):
    if "-d" not in argv:
        argv = ["-d", str(tmp_path), *argv]
    with pytest.raises(SystemExit) as exit_info:
        main(argv)
    assert exit_info.value.code == 2


def _make_image(directory, name, **tags):
    path = directory / name
    shutil.copy(BLANK_JPEG, path)
    if tags:
        _set_tags(path, **tags)
    return path


def _set_tags(path, **tags):
    args = [f"-{tag}={value}" for tag, value in tags.items()]
    subprocess.run(["exiftool", "-q", "-overwrite_original", *args, str(path)], check=True)


def _read_tags(path, tags=("EXIF:DateTimeOriginal", "SubSecTimeOriginal", "OffsetTimeOriginal")):
    output = subprocess.run(
        ["exiftool", "-T", *(f"-{tag}" for tag in tags), str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    return tuple(output.rstrip("\n").split("\t"))


def _undo_logs(directory):
    return sorted(directory.glob("exif-timezone-correction-undo-*.jsonl"))


@requires_exiftool
def test_end_to_end(tmp_path):
    with_subsec = _make_image(
        tmp_path, "a.jpg",
        DateTimeOriginal="2024:05:01 10:00:00", SubSecTimeOriginal="045", OffsetTimeOriginal="+02:00",
    )
    no_offset = _make_image(tmp_path, "b.jpg", DateTimeOriginal="2024:05:01 10:00:00")
    no_date = _make_image(tmp_path, "c.jpg")

    exit_code = main(["-d", str(tmp_path), "--timezone=-06:30", "-w", "2"])

    assert exit_code == 0
    assert _read_tags(with_subsec) == ("2024:05:01 01:30:00", "045", "-06:30")
    assert _read_tags(no_offset) == ("2024:05:01 10:00:00", "-", "-")
    assert _read_tags(no_date) == ("-", "-", "-")


@requires_exiftool
def test_negative_flag_still_works(tmp_path):
    image = _make_image(tmp_path, "a.jpg", DateTimeOriginal="2024:05:01 10:00:00", OffsetTimeOriginal="+02:00")

    assert main(["-d", str(tmp_path), "-t", "06:30", "-n"]) == 0
    assert _read_tags(image) == ("2024:05:01 01:30:00", "-", "-06:30")


@requires_exiftool
def test_source_timezone_applies_to_images_without_offset(tmp_path):
    no_offset = _make_image(tmp_path, "a.jpg", DateTimeOriginal="2024:05:01 10:00:00")

    exit_code = main(["-d", str(tmp_path), "--timezone=-06:30", "--source-timezone=+02:00"])

    assert exit_code == 0
    assert _read_tags(no_offset) == ("2024:05:01 01:30:00", "-", "-06:30")


@requires_exiftool
def test_capture_time_recorded_only_in_xmp(tmp_path):
    image = _make_image(tmp_path, "a.jpg", **{"XMP:DateTimeOriginal": "2024:05:01 10:00:00.045+02:00"})

    exit_code = main(["-d", str(tmp_path), "--timezone=-06:30"])

    assert exit_code == 0
    tags = ("EXIF:DateTimeOriginal", "OffsetTimeOriginal", "XMP:DateTimeOriginal")
    assert _read_tags(image, tags) == ("2024:05:01 01:30:00", "-06:30", "2024:05:01 01:30:00.045-06:30")


@requires_exiftool
def test_missing_exif_offset_is_written_from_xmp_when_already_in_target_timezone(tmp_path):
    image = _make_image(
        tmp_path, "a.jpg",
        **{"EXIF:DateTimeOriginal": "2024:05:01 10:00:00", "XMP:DateTimeOriginal": "2024:05:01 10:00:00-06:00"},
    )

    assert main(["-d", str(tmp_path), "--timezone=-06:00"]) == 0
    assert _read_tags(image) == ("2024:05:01 10:00:00", "-", "-06:00")


@requires_exiftool
def test_running_twice_is_idempotent(tmp_path):
    image = _make_image(tmp_path, "a.jpg", DateTimeOriginal="2024:05:01 10:00:00", OffsetTimeOriginal="+02:00")

    main(["-d", str(tmp_path), "-t", "05:45"])
    main(["-d", str(tmp_path), "-t", "05:45"])

    assert _read_tags(image) == ("2024:05:01 13:45:00", "-", "+05:45")
    assert len(_undo_logs(tmp_path)) == 1  # the second run changed nothing, so it left no log


@requires_exiftool
def test_file_modification_time_is_preserved(tmp_path):
    image = _make_image(tmp_path, "a.jpg", DateTimeOriginal="2024:05:01 10:00:00", OffsetTimeOriginal="+02:00")
    os.utime(image, (1_000_000_000, 1_000_000_000))

    main(["-d", str(tmp_path), "-t", "05:00"])

    assert image.stat().st_mtime == 1_000_000_000


@requires_exiftool
def test_dry_run_changes_nothing(tmp_path, capsys):
    image = _make_image(tmp_path, "a.jpg", DateTimeOriginal="2024:05:01 10:00:00", OffsetTimeOriginal="+02:00")
    before = image.read_bytes()

    assert main(["-d", str(tmp_path), "-t", "05:00", "--dry-run"]) == 0

    assert image.read_bytes() == before
    assert not _undo_logs(tmp_path)
    assert "1 would be updated" in capsys.readouterr().out


@requires_exiftool
def test_undo_log_reverts_all_changes(tmp_path):
    image = _make_image(tmp_path, "a.jpg", DateTimeOriginal="2024:05:01 10:00:00")
    main(["-d", str(tmp_path), "-t", "05:00", "-s", "+02:00", "--all-dates"])
    assert _read_tags(image) == ("2024:05:01 13:00:00", "-", "+05:00")

    (log,) = _undo_logs(tmp_path)
    assert main(["--revert", str(log)]) == 0

    assert _read_tags(image) == ("2024:05:01 10:00:00", "-", "-")


@requires_exiftool
def test_backup_keeps_original_copy(tmp_path):
    _make_image(tmp_path, "a.jpg", DateTimeOriginal="2024:05:01 10:00:00", OffsetTimeOriginal="+02:00")

    main(["-d", str(tmp_path), "-t", "05:00", "--backup", "--no-undo-log"])

    assert (tmp_path / "a.jpg_original").exists()
    assert not _undo_logs(tmp_path)


@requires_exiftool
def test_all_dates_and_named_zone(tmp_path):
    image = _make_image(
        tmp_path, "a.jpg",
        DateTimeOriginal="2024:07:01 10:00:00", OffsetTimeOriginal="+00:00",
        CreateDate="2024:07:01 10:00:00", OffsetTimeDigitized="+00:00",
    )

    main(["-d", str(tmp_path), "-t", "Europe/Berlin", "--all-dates"])

    tags = ("EXIF:DateTimeOriginal", "OffsetTimeOriginal", "EXIF:CreateDate", "OffsetTimeDigitized", "EXIF:ModifyDate")
    assert _read_tags(image, tags) == ("2024:07:01 12:00:00", "+02:00", "2024:07:01 12:00:00", "+02:00", "-")


@requires_exiftool
def test_gps_time_supplies_missing_offset(tmp_path):
    image = _make_image(
        tmp_path, "a.jpg",
        DateTimeOriginal="2024:05:01 10:00:00", GPSDateStamp="2024:05:01", GPSTimeStamp="08:00:40",
    )

    main(["-d", str(tmp_path), "-t", "00:00", "--gps"])

    assert _read_tags(image) == ("2024:05:01 08:00:00", "-", "+00:00")


@requires_exiftool
def test_sidecar_is_updated(tmp_path):
    image = _make_image(tmp_path, "a.jpg", DateTimeOriginal="2024:05:01 10:00:00", OffsetTimeOriginal="+02:00")
    sidecar = tmp_path / "a.jpg.xmp"  # darktable naming; a valid RAW file is needed for Lightroom's a.xmp
    subprocess.run(
        ["exiftool", "-q", "-o", str(sidecar), "-XMP:DateTimeOriginal=2024:05:01 10:00:00+02:00", str(image)], check=True
    )

    assert main(["-d", str(tmp_path), "--timezone=-06:00"]) == 0

    assert _read_tags(sidecar, ("XMP:DateTimeOriginal",)) == ("2024:05:01 02:00:00-06:00",)


@requires_exiftool
def test_report_and_camera_filter(tmp_path):
    _make_image(tmp_path, "a.jpg", DateTimeOriginal="2024:05:01 10:00:00", OffsetTimeOriginal="+02:00", Model="EOS R5")
    _make_image(tmp_path, "b.jpg", DateTimeOriginal="2024:05:01 10:00:00", OffsetTimeOriginal="+02:00", Model="iPhone")
    report = tmp_path / "report.csv"

    main(["-d", str(tmp_path), "-t", "05:00", "--camera", "eos", "--report", str(report), "-q"])

    with report.open() as stream:
        outcomes = {Path(row["path"]).name: row["outcome"] for row in csv.DictReader(stream)}
    assert outcomes == {"a.jpg": "updated", "b.jpg": "skipped"}


@requires_exiftool
def test_unwritable_report_stops_before_any_change(tmp_path):
    image = _make_image(tmp_path, "a.jpg", DateTimeOriginal="2024:05:01 10:00:00", OffsetTimeOriginal="+02:00")

    with pytest.raises(SystemExit, match="Cannot write the report"):
        main(["-d", str(tmp_path), "-t", "05:00", "--report", str(tmp_path / "missing" / "report.csv")])

    assert _read_tags(image) == ("2024:05:01 10:00:00", "-", "+02:00")
    assert not _undo_logs(tmp_path)


@requires_exiftool
@pytest.mark.skipif(running_as_root, reason="root can write to read-only folders")
def test_write_failure_is_reported(tmp_path):
    photos = tmp_path / "photos"
    photos.mkdir()
    _make_image(photos, "a.jpg", DateTimeOriginal="2024:05:01 10:00:00", OffsetTimeOriginal="+02:00")
    photos.chmod(0o555)  # exiftool cannot create its temporary file
    try:
        exit_code = main(["-d", str(photos), "-t", "05:00", "--undo-log", str(tmp_path / "undo.jsonl")])
    finally:
        photos.chmod(0o755)

    assert exit_code == 1


@requires_exiftool
@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs named pipes")
def test_hung_exiftool_times_out(tmp_path):
    fifo = tmp_path / "stalled.jpg"
    os.mkfifo(fifo)  # exiftool blocks opening a pipe nobody writes to, like a stalled network drive

    with ExifTool(timeout=1) as store, pytest.raises(MetadataError, match="timed out"):
        store.read([fifo])
