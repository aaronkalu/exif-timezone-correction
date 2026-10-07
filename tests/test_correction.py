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


def test_skips_images_without_offset_when_no_source_timezone_is_given():
    path = Path("a.jpg")
    store = FakeStore({path: CaptureTime(datetime(2024, 5, 1, 10, 0), offset=None)})

    result = correct_timezone(path, PLUS_TWO, store)

    assert result.outcome is Outcome.SKIPPED
    assert not store.writes


def test_uses_source_timezone_when_no_offset_was_recorded():
    path = Path("a.jpg")
    store = FakeStore({path: CaptureTime(datetime(2024, 5, 1, 10, 0), offset=None)})

    result = correct_timezone(path, MINUS_SIX, store, assumed_source=PLUS_TWO)

    assert result.outcome is Outcome.UPDATED
    assert store.writes[path] == CaptureTime(datetime(2024, 5, 1, 2, 0), MINUS_SIX)


def test_source_timezone_equal_to_target_only_records_the_offset():
    path = Path("a.jpg")
    store = FakeStore({path: CaptureTime(datetime(2024, 5, 1, 10, 0), offset=None)})

    correct_timezone(path, PLUS_TWO, store, assumed_source=PLUS_TWO)

    assert store.writes[path] == CaptureTime(datetime(2024, 5, 1, 10, 0), PLUS_TWO)


def test_recorded_offset_takes_precedence_over_source_timezone():
    path = Path("a.jpg")
    store = FakeStore({path: CaptureTime(datetime(2024, 5, 1, 10, 0), PLUS_TWO)})

    correct_timezone(path, UtcOffset(0), store, assumed_source=MINUS_SIX)

    assert store.writes[path] == CaptureTime(datetime(2024, 5, 1, 8, 0), UtcOffset(0))


def test_metadata_errors_become_failed_results():
    path = Path("a.jpg")
    store = FakeStore({path: MetadataError("corrupt file")})

    result = correct_timezone(path, PLUS_TWO, store)

    assert result.outcome is Outcome.FAILED
    assert "corrupt file" in result.detail


def test_unexpected_errors_become_failed_results():
    path = Path("a.jpg")
    store = FakeStore({path: CaptureTime(datetime(9999, 12, 31, 23, 30), UtcOffset(0))})

    result = correct_timezone(path, PLUS_TWO, store)

    assert result.outcome is Outcome.FAILED
    assert not store.writes


def test_results_follow_input_order():
    paths = [Path(f"{i}.jpg") for i in range(20)]
    store = FakeStore({path: None for path in paths})

    results = list(correct_all(paths, PLUS_TWO, store, workers=4))

    assert [r.path for r in results] == paths


def test_one_failure_does_not_stop_the_batch():
    good, bad = Path("good.jpg"), Path("bad.jpg")
    store = FakeStore({good: CaptureTime(datetime(2024, 5, 1), PLUS_TWO), bad: MetadataError("boom")})

    results = list(correct_all([good, bad], MINUS_SIX, store, workers=2))

    assert sorted(r.outcome.value for r in results) == ["failed", "updated"]
