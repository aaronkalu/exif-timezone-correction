from datetime import datetime
from io import StringIO
from pathlib import Path

import pytest

from exif_timezone_correction import undo
from exif_timezone_correction.domain import CaptureTime, DateChange, DateTag, FileChange, StoredDate, UtcOffset


def test_log_round_trips(tmp_path):
    change = FileChange(
        tmp_path / "a.jpg",
        (
            DateChange(
                DateTag.ORIGINAL,
                StoredDate(CaptureTime(datetime(2024, 5, 1, 10)), None),
                StoredDate(
                    CaptureTime(datetime(2024, 5, 1, 2), UtcOffset(-360)),
                    CaptureTime(datetime(2024, 5, 1, 2, 0, 0, 45000), UtcOffset(-360)),
                ),
            ),
        ),
    )
    stream = StringIO()
    undo.UndoLog(stream).record(change)
    log = tmp_path / "undo.jsonl"
    log.write_text(stream.getvalue() + "\n")

    assert undo.load(log) == [change]


def test_invalid_log_is_rejected(tmp_path):
    log = tmp_path / "undo.jsonl"
    log.write_text('{"path": "a.jpg"}\n')

    with pytest.raises(ValueError, match="undo.jsonl:1"):
        undo.load(log)


def test_paths_are_stored_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    stream = StringIO()
    undo.UndoLog(stream).record(FileChange(Path("a.jpg"), ()))

    assert str(tmp_path) in stream.getvalue()
