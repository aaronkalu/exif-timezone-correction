from datetime import datetime

import pytest

from exif_timezone_correction.domain import UTC, CaptureTime, UtcOffset


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
