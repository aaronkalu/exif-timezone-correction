import threading
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from exif_timezone_correction.correction import (
    Correction,
    Mode,
    Outcome,
    Selection,
    correct_all,
    plan_correction,
    revert_all,
)
from exif_timezone_correction.domain import (
    CaptureTime,
    DateTag,
    ImageMetadata,
    MetadataError,
    NamedZone,
    StoredDate,
    UtcOffset,
)

PLUS_TWO = UtcOffset.parse("+02:00")
MINUS_SIX = UtcOffset.parse("-06:00")
BERLIN = NamedZone(ZoneInfo("Europe/Berlin"))


def image(local, offset=None, xmp=None, gps=None, model=None, **other_dates):
    dates = {DateTag.ORIGINAL: StoredDate(CaptureTime(local, offset), xmp)} if local else {}
    dates.update({DateTag[name.upper()]: value for name, value in other_dates.items()})
    return ImageMetadata(dates, gps, model)


class FakeStore:
    def __init__(self, files):
        self.files = dict(files)
        self.writes = []
        self.lock = threading.Lock()

    def read(self, paths):
        return {path: self.files[path] for path in paths if path in self.files}

    def write(self, change, backup=False):
        current = self.files[change.path]
        if isinstance(current, Exception):
            raise current
        dates = dict(current.dates)
        dates.update({date.tag: date.after for date in change.dates})
        with self.lock:
            self.files[change.path] = ImageMetadata(dates, current.gps_utc_time, current.camera_model)
            self.writes.append(change)

    def original(self, path):
        return self.files[path].date(DateTag.ORIGINAL)


def run(files, correction, **options):
    store = FakeStore(files)
    results = list(correct_all(list(files), correction, store, **options))
    return store, {result.path: result for result in results}


def test_converts_to_target_timezone():
    path = Path("a.jpg")
    store, results = run({path: image(datetime(2024, 5, 1, 10, 0), PLUS_TWO)}, Correction(MINUS_SIX))

    assert results[path].outcome is Outcome.UPDATED
    assert store.original(path).exif == CaptureTime(datetime(2024, 5, 1, 2, 0), MINUS_SIX)


def test_skips_when_already_correct():
    path = Path("a.jpg")
    store, results = run({path: image(datetime(2024, 5, 1, 10, 0), PLUS_TWO)}, Correction(PLUS_TWO))

    assert results[path].outcome is Outcome.SKIPPED
    assert not store.writes


def test_skips_images_without_capture_time():
    path = Path("a.jpg")
    _, results = run({path: image(None)}, Correction(PLUS_TWO))

    assert results[path].outcome is Outcome.SKIPPED


def test_skips_images_without_offset_when_no_source_timezone_is_given():
    path = Path("a.jpg")
    store, results = run({path: image(datetime(2024, 5, 1, 10, 0))}, Correction(PLUS_TWO))

    assert results[path].outcome is Outcome.SKIPPED
    assert not store.writes


def test_uses_source_timezone_when_no_offset_was_recorded():
    path = Path("a.jpg")
    store, _ = run({path: image(datetime(2024, 5, 1, 10, 0))}, Correction(MINUS_SIX, assumed_source=PLUS_TWO))

    assert store.original(path).exif == CaptureTime(datetime(2024, 5, 1, 2, 0), MINUS_SIX)


def test_recorded_offset_takes_precedence_over_source_timezone():
    path = Path("a.jpg")
    store, _ = run({path: image(datetime(2024, 5, 1, 10, 0), PLUS_TWO)}, Correction(UtcOffset(0), assumed_source=MINUS_SIX))

    assert store.original(path).exif == CaptureTime(datetime(2024, 5, 1, 8, 0), UtcOffset(0))


def test_offset_recorded_only_in_xmp_is_copied_to_exif_even_when_already_in_target_timezone():
    path = Path("a.jpg")
    xmp = CaptureTime(datetime(2024, 5, 1, 10, 0), MINUS_SIX)
    store, results = run({path: image(datetime(2024, 5, 1, 10, 0), xmp=xmp)}, Correction(MINUS_SIX))

    assert results[path].outcome is Outcome.UPDATED
    assert store.original(path).exif == CaptureTime(datetime(2024, 5, 1, 10, 0), MINUS_SIX)


def test_xmp_sub_seconds_are_kept():
    path = Path("a.jpg")
    xmp = CaptureTime(datetime(2024, 5, 1, 10, 0, 0, 45000), PLUS_TWO)
    store, _ = run({path: image(datetime(2024, 5, 1, 10, 0), PLUS_TWO, xmp=xmp)}, Correction(MINUS_SIX))

    assert store.original(path).xmp == CaptureTime(datetime(2024, 5, 1, 2, 0, 0, 45000), MINUS_SIX)


def test_named_zone_follows_daylight_saving_time():
    winter, summer = Path("winter.jpg"), Path("summer.jpg")
    files = {
        winter: image(datetime(2024, 1, 15, 12, 0), UtcOffset(0)),
        summer: image(datetime(2024, 7, 15, 12, 0), UtcOffset(0)),
    }
    store, _ = run(files, Correction(BERLIN))

    assert store.original(winter).exif == CaptureTime(datetime(2024, 1, 15, 13, 0), UtcOffset(60))
    assert store.original(summer).exif == CaptureTime(datetime(2024, 7, 15, 14, 0), UtcOffset(120))


def test_named_source_zone_uses_the_offset_at_capture_time():
    path = Path("a.jpg")
    store, _ = run({path: image(datetime(2024, 7, 15, 12, 0))}, Correction(UtcOffset(0), assumed_source=BERLIN))

    assert store.original(path).exif == CaptureTime(datetime(2024, 7, 15, 10, 0), UtcOffset(0))


def test_relabel_keeps_clock_time():
    path = Path("a.jpg")
    store, _ = run({path: image(datetime(2024, 5, 1, 10, 0), PLUS_TWO)}, Correction(MINUS_SIX, mode=Mode.RELABEL))

    assert store.original(path).exif == CaptureTime(datetime(2024, 5, 1, 10, 0), MINUS_SIX)


def test_relabel_does_not_need_a_recorded_offset():
    path = Path("a.jpg")
    store, _ = run({path: image(datetime(2024, 5, 1, 10, 0))}, Correction(MINUS_SIX, mode=Mode.RELABEL))

    assert store.original(path).exif == CaptureTime(datetime(2024, 5, 1, 10, 0), MINUS_SIX)


def test_shift_moves_clock_and_keeps_offset():
    path = Path("a.jpg")
    store, _ = run({path: image(datetime(2024, 5, 1, 10, 0), PLUS_TWO)}, Correction(shift=timedelta(hours=-1, seconds=-30)))

    assert store.original(path).exif == CaptureTime(datetime(2024, 5, 1, 8, 59, 30), PLUS_TWO)


def test_shift_works_without_any_offset():
    path = Path("a.jpg")
    store, _ = run({path: image(datetime(2024, 5, 1, 10, 0))}, Correction(shift=timedelta(minutes=5)))

    assert store.original(path).exif == CaptureTime(datetime(2024, 5, 1, 10, 5))


def test_shift_is_applied_before_converting():
    path = Path("a.jpg")
    correction = Correction(MINUS_SIX, shift=timedelta(hours=1))
    store, _ = run({path: image(datetime(2024, 5, 1, 10, 0), PLUS_TWO)}, correction)

    assert store.original(path).exif == CaptureTime(datetime(2024, 5, 1, 3, 0), MINUS_SIX)


def test_source_timezone_alone_fills_in_missing_offsets():
    recorded, missing = Path("recorded.jpg"), Path("missing.jpg")
    files = {recorded: image(datetime(2024, 5, 1, 10, 0), MINUS_SIX), missing: image(datetime(2024, 5, 1, 10, 0))}
    store, results = run(files, Correction(assumed_source=PLUS_TWO))

    assert results[recorded].outcome is Outcome.SKIPPED
    assert store.original(missing).exif == CaptureTime(datetime(2024, 5, 1, 10, 0), PLUS_TWO)


def test_offset_is_inferred_from_gps_time():
    path = Path("a.jpg")
    gps = datetime(2024, 5, 1, 8, 3, 10)  # camera clock is 3 minutes fast
    store, results = run({path: image(datetime(2024, 5, 1, 10, 6), gps=gps)}, Correction(UtcOffset(0), infer_from_gps=True))

    assert store.original(path).exif == CaptureTime(datetime(2024, 5, 1, 8, 6), UtcOffset(0))
    assert "inferred from GPS" in results[path].detail


def test_gps_time_from_another_day_is_ignored():
    path = Path("a.jpg")
    files = {path: image(datetime(2024, 5, 1, 10, 0), gps=datetime(2024, 4, 20, 8, 0))}
    _, results = run(files, Correction(UtcOffset(0), infer_from_gps=True))

    assert results[path].outcome is Outcome.SKIPPED


def test_all_dates_corrects_existing_secondary_dates_only():
    path = Path("a.jpg")
    created = StoredDate(CaptureTime(datetime(2024, 5, 1, 10, 0, 5), PLUS_TWO))
    store, results = run({path: image(datetime(2024, 5, 1, 10, 0), PLUS_TWO, digitized=created)}, Correction(MINUS_SIX, tags=tuple(DateTag)))

    dates = store.files[path].dates
    assert dates[DateTag.DIGITIZED].exif == CaptureTime(datetime(2024, 5, 1, 2, 0, 5), MINUS_SIX)
    assert DateTag.MODIFIED not in dates
    assert "CreateDate" in results[path].detail


def test_secondary_dates_are_untouched_by_default():
    path = Path("a.jpg")
    created = StoredDate(CaptureTime(datetime(2024, 5, 1, 10, 0), PLUS_TWO))
    store, _ = run({path: image(datetime(2024, 5, 1, 10, 0), PLUS_TWO, digitized=created)}, Correction(MINUS_SIX))

    assert store.files[path].dates[DateTag.DIGITIZED] == created


def test_selection_by_date_range_and_camera():
    early, inside, other_camera = Path("early.jpg"), Path("inside.jpg"), Path("other.jpg")
    files = {
        early: image(datetime(2024, 4, 30, 23, 0), PLUS_TWO, model="Canon EOS R5"),
        inside: image(datetime(2024, 5, 1, 10, 0), PLUS_TWO, model="Canon EOS R5"),
        other_camera: image(datetime(2024, 5, 1, 10, 0), PLUS_TWO, model="iPhone 15"),
    }
    selection = Selection(start=datetime(2024, 5, 1), end=datetime(2024, 5, 2), camera_model="eos r5")
    _, results = run(files, Correction(MINUS_SIX), selection=selection)

    assert {path: r.outcome for path, r in results.items()} == {
        early: Outcome.SKIPPED, inside: Outcome.UPDATED, other_camera: Outcome.SKIPPED,
    }


def test_sidecar_xmp_is_updated_with_the_image():
    raw, sidecar = Path("a.cr2"), Path("a.xmp")
    sidecar_data = ImageMetadata({DateTag.ORIGINAL: StoredDate(xmp=CaptureTime(datetime(2024, 5, 1, 10, 0)))})
    files = {raw: image(datetime(2024, 5, 1, 10, 0), PLUS_TWO), sidecar: sidecar_data}
    store = FakeStore(files)

    results = list(correct_all([raw], Correction(MINUS_SIX), store, sidecars={raw: [sidecar]}))

    assert results[0].outcome is Outcome.UPDATED
    assert store.original(sidecar) == StoredDate(xmp=CaptureTime(datetime(2024, 5, 1, 2, 0), MINUS_SIX))
    assert [change.path for change in results[0].changes] == [raw, sidecar]


def test_dry_run_writes_nothing():
    path = Path("a.jpg")
    store, results = run({path: image(datetime(2024, 5, 1, 10, 0), PLUS_TWO)}, Correction(MINUS_SIX), dry_run=True)

    assert results[path].outcome is Outcome.WOULD_UPDATE
    assert results[path].changes
    assert not store.writes


def test_read_errors_become_failed_results():
    path = Path("a.jpg")
    _, results = run({path: MetadataError("corrupt file")}, Correction(PLUS_TWO))

    assert results[path].outcome is Outcome.FAILED
    assert "corrupt file" in results[path].detail


def test_dates_shifted_out_of_range_fail_only_that_image():
    bad, good = Path("bad.jpg"), Path("good.jpg")
    files = {bad: image(datetime(9999, 12, 31, 23, 30), UtcOffset(0)), good: image(datetime(2024, 5, 1), PLUS_TWO)}
    store, results = run(files, Correction(PLUS_TWO), workers=2)

    assert results[bad].outcome is Outcome.FAILED
    assert results[good].outcome is Outcome.SKIPPED


def test_every_image_gets_one_result_across_batches_and_workers():
    paths = [Path(f"{i}.jpg") for i in range(60)]
    _, results = run({path: image(datetime(2024, 5, 1), PLUS_TWO) for path in paths}, Correction(MINUS_SIX), workers=4)

    assert sorted(results) == sorted(paths)
    assert all(result.outcome is Outcome.UPDATED for result in results.values())


def test_write_failure_is_reported():
    path = Path("a.jpg")

    class FailingStore(FakeStore):
        def write(self, change, backup=False):
            raise MetadataError("disk full")

    store = FailingStore({path: image(datetime(2024, 5, 1, 10, 0), PLUS_TWO)})
    results = list(correct_all([path], Correction(MINUS_SIX), store))

    assert results[0].outcome is Outcome.FAILED
    assert results[0].changes == ()


def test_revert_restores_the_original_values():
    path = Path("a.jpg")
    original = image(datetime(2024, 5, 1, 10, 0))
    store, results = run({path: original}, Correction(MINUS_SIX, assumed_source=PLUS_TWO))

    reverted = list(revert_all(results[path].changes, store))

    assert reverted[0].outcome is Outcome.UPDATED
    assert store.files[path].dates == original.dates


def test_revert_skips_files_changed_since():
    path = Path("a.jpg")
    store, results = run({path: image(datetime(2024, 5, 1, 10, 0), PLUS_TWO)}, Correction(MINUS_SIX))
    list(correct_all([path], Correction(UtcOffset(0)), store))

    reverted = list(revert_all(results[path].changes, store))

    assert reverted[0].outcome is Outcome.SKIPPED


def test_plan_reports_assumed_source():
    plan = plan_correction(Path("a.jpg"), image(datetime(2024, 5, 1, 10, 0)), Correction(MINUS_SIX, assumed_source=PLUS_TWO))

    assert "assumed +02:00" in plan.detail
