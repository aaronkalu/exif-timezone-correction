from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from exif_timezone_correction.domain import (
    UTC,
    CaptureTime,
    DateChange,
    DateTag,
    FileChange,
    NamedZone,
    StoredDate,
    UtcOffset,
    parse_duration,
    parse_zone,
)


@pytest.mark.parametrize(
    ("text", "minutes"),
    [("02:00", 120), ("+05:45", 345), ("-06:30", -390), ("-00:30", -30), ("00:00", 0), ("+14:00", 840)],
)
def test_parse_offset(text, minutes):
    assert UtcOffset.parse(text).total_minutes == minutes


@pytest.mark.parametrize("text", ["2:00", "02:0", "0200", "++02:00", "02:60", "15:00", "-13:00", "", "Z"])
def test_parse_rejects_invalid_offsets(text):
    with pytest.raises(ValueError):
        UtcOffset.parse(text)


@pytest.mark.parametrize("text", ["+02:00", "-06:30", "-00:30", "+00:00", "+05:45", "-12:00"])
def test_offset_round_trips_through_text(text):
    assert str(UtcOffset.parse(text)) == text


def test_negated():
    assert UtcOffset.parse("06:30").negated() == UtcOffset.parse("-06:30")


def test_in_timezone_keeps_the_same_instant():
    original = CaptureTime(datetime(2024, 5, 1, 10, 0), UtcOffset.parse("+02:00"))

    corrected = original.in_timezone(UtcOffset.parse("-06:30"))

    assert corrected == CaptureTime(datetime(2024, 5, 1, 1, 30), UtcOffset.parse("-06:30"))


def test_in_timezone_crosses_date_boundary():
    original = CaptureTime(datetime(2024, 12, 31, 23, 30), UTC)

    corrected = original.in_timezone(UtcOffset.parse("+05:45"))

    assert corrected.local_time == datetime(2025, 1, 1, 5, 15)


def test_converting_without_an_offset_is_refused():
    with pytest.raises(ValueError):
        CaptureTime(datetime(2024, 5, 1, 10, 0), offset=None).in_timezone(UtcOffset.parse("+02:00"))


@pytest.mark.parametrize(("text", "expected"), [("+02:00", UtcOffset(120)), ("Asia/Kolkata", "Asia/Kolkata")])
def test_parse_zone(text, expected):
    assert str(parse_zone(text)) == str(expected)


@pytest.mark.parametrize("text", ["", "15:00", "Mars/Olympus", "../etc"])
def test_parse_zone_rejects_unknown_zones(text):
    with pytest.raises(ValueError):
        parse_zone(text)


def test_named_zone_offsets_follow_daylight_saving_time():
    berlin = NamedZone(ZoneInfo("Europe/Berlin"))

    assert berlin.offset_at_local(datetime(2024, 1, 1, 12)) == UtcOffset(60)
    assert berlin.offset_at_local(datetime(2024, 7, 1, 12)) == UtcOffset(120)
    assert berlin.offset_at_utc(datetime(2024, 3, 31, 0, 59)) == UtcOffset(60)
    assert berlin.offset_at_utc(datetime(2024, 3, 31, 1, 0)) == UtcOffset(120)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("1:00", timedelta(hours=1)), ("-0:05:30", -timedelta(minutes=5, seconds=30)), ("+26:00", timedelta(hours=26))],
)
def test_parse_duration(text, expected):
    assert parse_duration(text) == expected


@pytest.mark.parametrize("text", ["", "1", "1:60", "1:00:60", "1h"])
def test_parse_duration_rejects_invalid_values(text):
    with pytest.raises(ValueError):
        parse_duration(text)


def test_effective_date_takes_offset_and_sub_seconds_from_matching_xmp():
    exif = CaptureTime(datetime(2024, 5, 1, 10, 0))
    xmp = CaptureTime(datetime(2024, 5, 1, 10, 0, 0, 500000), UTC)

    assert StoredDate(exif, xmp).effective() == xmp


def test_effective_date_ignores_xmp_for_a_different_moment():
    exif = CaptureTime(datetime(2024, 5, 1, 10, 0))
    xmp = CaptureTime(datetime(2024, 5, 1, 11, 0), UTC)

    assert StoredDate(exif, xmp).effective() == exif


def test_file_change_reverses():
    before, after = StoredDate(CaptureTime(datetime(2024, 5, 1))), StoredDate(CaptureTime(datetime(2024, 5, 2)))
    change = FileChange(Path("a.jpg"), (DateChange(DateTag.ORIGINAL, before, after),))

    assert change.reversed().reversed() == change
    assert change.reversed().dates[0].after == before
