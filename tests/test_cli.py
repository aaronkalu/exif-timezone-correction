import shutil
import subprocess
from pathlib import Path

import pytest

from exif_timezone_correction.cli import main, resolve_target_offset
from exif_timezone_correction.domain import UtcOffset

BLANK_JPEG = Path(__file__).parent / "data" / "blank.jpg"
requires_exiftool = pytest.mark.skipif(shutil.which("exiftool") is None, reason="exiftool not installed")


@pytest.mark.parametrize(
    ("timezone", "negative", "expected"),
    [("06:00", False, "+06:00"), ("06:00", True, "-06:00"), ("-06:30", False, "-06:30"), ("+02:00", False, "+02:00")],
)
def test_resolve_target_offset(timezone, negative, expected):
    assert resolve_target_offset(timezone, negative) == UtcOffset.parse(expected)


def test_signed_timezone_conflicts_with_negative_flag():
    with pytest.raises(ValueError):
        resolve_target_offset("+06:00", negative=True)


@pytest.mark.parametrize("argv", [["-t", "99:00"], ["-t", "02:00", "-w", "0"], ["-t", "02:00", "-d", "/does/not/exist"]])
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
        args = [f"-{tag}={value}" for tag, value in tags.items()]
        subprocess.run(["exiftool", "-q", "-overwrite_original", *args, str(path)], check=True)
    return path


def _read_tags(path):
    output = subprocess.run(
        ["exiftool", "-T", "-DateTimeOriginal", "-SubSecTimeOriginal", "-OffsetTimeOriginal", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    return tuple(output.rstrip("\n").split("\t"))


@requires_exiftool
def test_end_to_end(tmp_path):
    with_subsec = _make_image(
        tmp_path, "a.jpg",
        DateTimeOriginal="2024:05:01 10:00:00", SubSecTimeOriginal="045", OffsetTimeOriginal="+02:00",
    )
    no_offset = _make_image(tmp_path, "b.jpg", DateTimeOriginal="2024:05:01 10:00:00")
    no_date = _make_image(tmp_path, "c.jpg")

    exit_code = main(["-d", str(tmp_path), "-t", "06:30", "-n", "-w", "2"])

    assert exit_code == 0
    assert _read_tags(with_subsec) == ("2024:05:01 01:30:00", "045", "-06:30")
    assert _read_tags(no_offset) == ("2024:05:01 03:30:00", "-", "-06:30")
    assert _read_tags(no_date) == ("-", "-", "-")


@requires_exiftool
def test_running_twice_is_idempotent(tmp_path):
    image = _make_image(tmp_path, "a.jpg", DateTimeOriginal="2024:05:01 10:00:00", OffsetTimeOriginal="+02:00")

    main(["-d", str(tmp_path), "-t", "05:45"])
    main(["-d", str(tmp_path), "-t", "05:45"])

    assert _read_tags(image) == ("2024:05:01 13:45:00", "-", "+05:45")


@requires_exiftool
def test_write_failure_is_reported(tmp_path):
    _make_image(tmp_path, "a.jpg", DateTimeOriginal="2024:05:01 10:00:00", OffsetTimeOriginal="+02:00")
    tmp_path.chmod(0o555)  # exiftool cannot create its temporary file
    try:
        exit_code = main(["-d", str(tmp_path), "-t", "05:00"])
    finally:
        tmp_path.chmod(0o755)

    assert exit_code == 1
