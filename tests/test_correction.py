from datetime import datetime
from pathlib import Path

from exif_timezone_correction.correction import Outcome, correct_all, correct_timezone
from exif_timezone_correction.domain import CaptureTime, MetadataError, UtcOffset

PLUS_TWO = UtcOffset.parse("+02:00")
MINUS_SIX = UtcOffset.parse("-06:00")


class FakeStore:
    def __init__(self, captures):
        self.captures = dict(captures)
        self.writes = {}

    def read_capture_time(self, path):
        capture = self.captures[path]
        if isinstance(capture, Exception):
            raise capture
        return capture

    def write_capture_time(self, path, capture):
        self.writes[path] = capture


def test_updates_capture_time():
    path = Path("a.jpg")
    store = FakeStore({path: CaptureTime(datetime(2024, 5, 1, 10, 0), PLUS_TWO)})

    result = correct_timezone(path, MINUS_SIX, store)

    assert result.outcome is Outcome.UPDATED
    assert store.writes[path] == CaptureTime(datetime(2024, 5, 1, 2, 0), MINUS_SIX)


def test_skips_when_timezone_already_set():
    path = Path("a.jpg")
    store = FakeStore({path: CaptureTime(datetime(2024, 5, 1, 10, 0), PLUS_TWO)})

    result = correct_timezone(path, PLUS_TWO, store)

    assert result.outcome is Outcome.SKIPPED
    assert not store.writes


def test_skips_images_without_capture_time():
    path = Path("a.jpg")
    store = FakeStore({path: None})

    assert correct_timezone(path, PLUS_TWO, store).outcome is Outcome.SKIPPED


def test_writes_utc_offset_when_none_was_recorded():
    path = Path("a.jpg")
    store = FakeStore({path: CaptureTime(datetime(2024, 5, 1, 10, 0), offset=None)})

    result = correct_timezone(path, UtcOffset(0), store)

    assert result.outcome is Outcome.UPDATED
    assert store.writes[path] == CaptureTime(datetime(2024, 5, 1, 10, 0), UtcOffset(0))


def test_metadata_errors_become_failed_results():
    path = Path("a.jpg")
    store = FakeStore({path: MetadataError("corrupt file")})

    result = correct_timezone(path, PLUS_TWO, store)

    assert result.outcome is Outcome.FAILED
    assert "corrupt file" in result.detail


def test_one_failure_does_not_stop_the_batch():
    good, bad = Path("good.jpg"), Path("bad.jpg")
    store = FakeStore({good: CaptureTime(datetime(2024, 5, 1), PLUS_TWO), bad: MetadataError("boom")})

    results = list(correct_all([good, bad], MINUS_SIX, store, workers=2))

    assert sorted(r.outcome.value for r in results) == ["failed", "updated"]
