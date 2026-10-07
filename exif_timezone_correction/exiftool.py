from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO, Mapping, Sequence

from .domain import UTC, CaptureTime, DateTag, FileChange, ImageMetadata, MetadataError, StoredDate, UtcOffset

_EXECUTABLE = "exiftool"
TIMEOUT_SECONDS = 120.0
# The exit status echoed through -echo3 below needs ExifTool 12.10.
MINIMUM_VERSION = (12, 10)
_EXIF_DATETIME_FORMAT = "%Y:%m:%d %H:%M:%S"
_XMP_DATETIME_PATTERN = re.compile(r"^(\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2})(\.\d+)?(Z|[+-]\d{2}:\d{2})?$")

# exiftool omits a plain "{ready}" after some output; a numbered -execute always gets "{readyN}".
_SEQUENCE = "1"
_READY = f"{{ready{_SEQUENCE}}}"
_STATUS_PATTERN = re.compile(r"^\{status=(\d+)\}$")
_UPDATED_PATTERN = re.compile(r"^\s*1 image files updated$", re.MULTILINE)

_GPS_TAG = "Composite:GPSDateTime"
_MODEL_TAG = "EXIF:Model"


def _read_tags() -> list[str]:
    tags = [_GPS_TAG, _MODEL_TAG]
    for tag in DateTag:
        tags += [f"EXIF:{tag.tag_name}", f"EXIF:{tag.offset_tag_name}", f"XMP:{tag.tag_name}"]
    return tags


_READ_TAGS = _read_tags()


def version() -> tuple[int, ...] | None:
    try:
        output = subprocess.run(
            [_EXECUTABLE, "-ver"], capture_output=True, check=True, text=True, timeout=TIMEOUT_SECONDS
        ).stdout
        return tuple(int(part) for part in output.strip().split("."))
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


class ExifTool:
    """Keeps one exiftool process per thread: starting exiftool (Perl) costs more than a typical read or write."""

    def __init__(self, timeout: float = TIMEOUT_SECONDS) -> None:
        self._timeout = timeout
        self._local = threading.local()
        self._sessions: list[_Session] = []
        self._lock = threading.Lock()

    def __enter__(self) -> ExifTool:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            sessions, self._sessions = self._sessions, []
        for session in sessions:
            session.close()

    def read(self, paths: Sequence[Path]) -> Mapping[Path, ImageMetadata | Exception]:
        # One call for many files: exiftool's per-call overhead dominates for small reads.
        result = self._execute("-json", "-G0", *(f"-{tag}" for tag in _READ_TAGS), *map(_as_argument, paths))
        if not result.stdout.strip():
            raise MetadataError(result.stderr.strip() or f"{_EXECUTABLE} returned no metadata")
        try:
            records = json.loads(result.stdout)
        except ValueError as error:
            raise MetadataError(f"Unexpected exiftool output: {result.stdout[:200]!r}") from error

        by_key = {_path_key(record["SourceFile"]): record for record in records}
        found: dict[Path, ImageMetadata | Exception] = {}
        for path in paths:
            record = by_key.get(_path_key(_as_argument(path)))
            if record is None:
                found[path] = MetadataError(_error_for(path, result.stderr) or "could not be read")
                continue
            try:
                found[path] = _parse_record(record)
            except ValueError as error:
                found[path] = MetadataError(f"Unreadable timestamp: {error}")
        return found

    def write(self, change: FileChange, backup: bool = False) -> None:
        # SubSecTime tags are deliberately not written: shifts are whole seconds and leave them unchanged.
        args = ["-P"]  # keep the file modification time, so photo libraries and backups do not see a new file
        if not backup:
            args.append("-overwrite_original")
        for date in change.dates:
            args += _assignments(date.tag, date.before, date.after)
        result = self._execute(*args, _as_argument(change.path))
        if result.status != 0:
            raise MetadataError(result.stderr.strip() or f"{_EXECUTABLE} exited with code {result.status}")
        if not _UPDATED_PATTERN.search(result.stdout):
            raise MetadataError(f"exiftool did not update the file: {result.stdout.strip()!r}")

    def _execute(self, *args: str) -> _Result:
        session = getattr(self._local, "session", None)
        if session is None or not session.alive:
            session = _Session(self._timeout)
            self._local.session = session
            with self._lock:
                self._sessions.append(session)
        return session.execute(*args)


def _assignments(tag: DateTag, before: StoredDate, after: StoredDate) -> list[str]:
    old, new = before.exif, after.exif
    args = []
    if (old and old.local_time) != (new and new.local_time):
        args.append(f"-EXIF:{tag.tag_name}={_format_datetime(new.local_time) if new else ''}")
    if (old and old.offset) != (new and new.offset):
        args.append(f"-EXIF:{tag.offset_tag_name}={new.offset if new and new.offset else ''}")
    if before.xmp != after.xmp:
        xmp = after.xmp
        value = f"{_format_datetime(xmp.local_time)}{xmp.offset or ''}" if xmp else ""
        args.append(f"-XMP:{tag.tag_name}={value}")
    return args


@dataclass(frozen=True)
class _Result:
    status: int
    stdout: str
    stderr: str


class _Session:
    def __init__(self, timeout: float) -> None:
        self._timeout = timeout
        self._timed_out = False
        self._deadline: float | None = None
        self._closed = False
        self._condition = threading.Condition()
        try:
            self._process = subprocess.Popen(
                [_EXECUTABLE, "-stay_open", "True", "-@", "-", "-common_args", "-charset", "filename=utf8"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as error:
            raise MetadataError(f"Could not run {_EXECUTABLE}: {error}") from error
        threading.Thread(target=self._watchdog, daemon=True).start()

    @property
    def alive(self) -> bool:
        return self._process.poll() is None

    def execute(self, *args: str) -> _Result:
        if any("\n" in arg for arg in args):
            raise MetadataError("Paths and values containing line breaks are not supported.")

        command = [*args, "-echo3", "{status=${status}}", "-echo4", _READY, f"-execute{_SEQUENCE}"]
        self._set_deadline(time.monotonic() + self._timeout)
        try:
            self._process.stdin.write("\n".join(command) + "\n")
            self._process.stdin.flush()
            stdout = self._read_until_ready(self._process.stdout)
            stderr = self._read_until_ready(self._process.stderr)
        except OSError as error:
            raise MetadataError(f"Lost connection to {_EXECUTABLE}: {error}") from error
        finally:
            self._set_deadline(None)

        status = _STATUS_PATTERN.match(stdout[-1]) if stdout else None
        if status is None:
            raise MetadataError(f"Unexpected exiftool output: {''.join(stdout)!r}")
        return _Result(int(status.group(1)), "".join(stdout[:-1]), "".join(stderr))

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify()
        try:
            self._process.communicate("-stay_open\nFalse\n", timeout=self._timeout)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            self._process.kill()
            self._process.wait()

    def _set_deadline(self, deadline: float | None) -> None:
        with self._condition:
            self._deadline = deadline
            self._condition.notify()

    def _watchdog(self) -> None:
        # Killing a hung exiftool (e.g. on a stalled network drive) ends the blocking reads in execute().
        with self._condition:
            while not self._closed:
                if self._deadline is None:
                    self._condition.wait()
                    continue
                remaining = self._deadline - time.monotonic()
                if remaining > 0:
                    self._condition.wait(remaining)
                    continue
                self._timed_out = True
                self._deadline = None
                self._process.kill()

    def _read_until_ready(self, stream: IO[str]) -> list[str]:
        lines = []
        while True:
            line = stream.readline()
            if not line:
                reason = f"timed out after {self._timeout:g}s" if self._timed_out else "exited unexpectedly"
                raise MetadataError(f"{_EXECUTABLE} {reason}")
            if line.rstrip("\r\n") == _READY:
                return lines
            lines.append(line)


def _parse_record(record: Mapping[str, object]) -> ImageMetadata:
    dates = {}
    for tag in DateTag:
        exif_text = record.get(f"EXIF:{tag.tag_name}")
        offset_text = record.get(f"EXIF:{tag.offset_tag_name}")
        xmp_text = record.get(f"XMP:{tag.tag_name}")
        try:
            exif = _parse_exif(str(exif_text), offset_text) if exif_text is not None else None
            xmp = _parse_xmp(str(xmp_text)) if xmp_text is not None else None
        except ValueError:
            if tag is DateTag.ORIGINAL:
                raise
            continue  # a garbled secondary date is left alone rather than failing the image
        if exif or xmp:
            dates[tag] = StoredDate(exif, xmp)

    gps = record.get(_GPS_TAG)
    gps_time = None
    if gps is not None:
        try:
            gps_capture = _parse_xmp(str(gps))
            gps_time = gps_capture.local_time if gps_capture.offset == UTC else None
        except ValueError:
            pass
    model = record.get(_MODEL_TAG)
    return ImageMetadata(dates, gps_time, None if model is None else str(model).strip())


def _parse_exif(datetime_text: str, offset_text: object) -> CaptureTime:
    local_time = datetime.strptime(datetime_text.strip(), _EXIF_DATETIME_FORMAT)
    return CaptureTime(local_time, None if offset_text is None else UtcOffset.parse(str(offset_text)))


def _parse_xmp(text: str) -> CaptureTime:
    match = _XMP_DATETIME_PATTERN.match(text.strip())
    if match is None:
        raise ValueError(f"unrecognised date {text!r}")

    datetime_text, fraction, offset_text = match.groups()
    local_time = datetime.strptime(datetime_text, _EXIF_DATETIME_FORMAT)
    if fraction:
        local_time = local_time.replace(microsecond=int(fraction[1:7].ljust(6, "0")))
    if offset_text is None:
        offset = None
    elif offset_text == "Z":
        offset = UTC
    else:
        offset = UtcOffset.parse(offset_text)
    return CaptureTime(local_time, offset)


def _format_datetime(local_time: datetime) -> str:
    text = local_time.strftime(_EXIF_DATETIME_FORMAT)
    if local_time.microsecond:
        text += f".{local_time.microsecond:06d}".rstrip("0")
    return text


def _error_for(path: Path, stderr: str) -> str | None:
    argument = _as_argument(path)
    return next((line.strip() for line in stderr.splitlines() if argument in line), None)


def _path_key(text: str) -> str:
    # exiftool reports Windows paths with forward slashes.
    return os.path.normcase(os.path.normpath(text))


def _as_argument(path: Path) -> str:
    # An absolute path never starts with "-", so exiftool cannot mistake it for an option.
    return str(path.absolute())
