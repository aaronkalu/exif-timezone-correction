from __future__ import annotations

import re
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import IO

from .domain import UTC, CaptureTime, MetadataError, UtcOffset

_EXECUTABLE = "exiftool"
TIMEOUT_SECONDS = 120.0
_EXIF_DATETIME_FORMAT = "%Y:%m:%d %H:%M:%S"
_MISSING_VALUE = "-"  # Placeholder exiftool prints for an absent tag in table (-T) output.
_XMP_DATETIME_PATTERN = re.compile(r"^(\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2})(\.\d+)?(Z|[+-]\d{2}:\d{2})?$")

# exiftool omits a plain "{ready}" after -T output; a numbered -execute always gets "{readyN}".
_SEQUENCE = "1"
_READY = f"{{ready{_SEQUENCE}}}"
_STATUS_PATTERN = re.compile(r"^\{status=(\d+)\}$")
_UPDATED_PATTERN = re.compile(r"^\s*1 image files updated$", re.MULTILINE)

# Read and write the same EXIF tags, so a second run sees what the first one wrote.
_DATETIME_TAG = "EXIF:DateTimeOriginal"
_OFFSET_TAG = "EXIF:OffsetTimeOriginal"
# Editing software sometimes records the capture time only in XMP.
_XMP_DATETIME_TAG = "XMP:DateTimeOriginal"


def is_installed() -> bool:
    try:
        subprocess.run([_EXECUTABLE, "-ver"], capture_output=True, check=True, timeout=TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


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

    def read_capture_time(self, path: Path) -> CaptureTime | None:
        output = self._execute("-T", f"-{_DATETIME_TAG}", f"-{_OFFSET_TAG}", f"-{_XMP_DATETIME_TAG}", _as_argument(path))
        fields = output.rstrip("\n").split("\t")
        if len(fields) != 3:
            raise MetadataError(f"Unexpected exiftool output: {output!r}")

        exif_text, offset_text, xmp_text = fields
        try:
            if exif_text != _MISSING_VALUE:
                return _parse_exif(exif_text, offset_text, xmp_text)
            if xmp_text != _MISSING_VALUE:
                return _parse_xmp(xmp_text)
        except ValueError as error:
            raise MetadataError(f"Unreadable timestamp: {error}") from error
        return None

    def write_capture_time(self, path: Path, capture: CaptureTime) -> None:
        # SubSecTimeOriginal is deliberately not written: a whole-minute shift leaves it unchanged.
        if capture.offset is None:
            raise ValueError("Refusing to write a capture time without a UTC offset.")

        # Without a group, exiftool creates the EXIF tag and also updates an existing XMP copy. EXIF keeps
        # only the whole seconds; the offset goes into its own EXIF tag.
        output = self._execute(
            "-overwrite_original",
            f"-DateTimeOriginal={_format_xmp(capture.local_time)}{capture.offset}",
            f"-{_OFFSET_TAG}={capture.offset}",
            _as_argument(path),
        )
        if not _UPDATED_PATTERN.search(output):
            raise MetadataError(f"exiftool did not update the file: {output.strip()!r}")

    def _execute(self, *args: str) -> str:
        session = getattr(self._local, "session", None)
        if session is None or not session.alive:
            session = _Session(self._timeout)
            self._local.session = session
            with self._lock:
                self._sessions.append(session)
        return session.execute(*args)


class _Session:
    def __init__(self, timeout: float) -> None:
        self._timeout = timeout
        self._timed_out = False
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

    @property
    def alive(self) -> bool:
        return self._process.poll() is None

    def execute(self, *args: str) -> str:
        if any("\n" in arg for arg in args):
            raise MetadataError("Paths and values containing line breaks are not supported.")

        command = [*args, "-echo3", "{status=${status}}", "-echo4", _READY, f"-execute{_SEQUENCE}"]
        # Killing a hung exiftool (e.g. on a stalled network drive) ends the blocking reads below.
        timer = threading.Timer(self._timeout, self._kill)
        timer.start()
        try:
            self._process.stdin.write("\n".join(command) + "\n")
            self._process.stdin.flush()
            stdout = self._read_until_ready(self._process.stdout)
            stderr = self._read_until_ready(self._process.stderr)
        except OSError as error:
            raise MetadataError(f"Lost connection to {_EXECUTABLE}: {error}") from error
        finally:
            timer.cancel()

        status = _STATUS_PATTERN.match(stdout[-1]) if stdout else None
        if status is None:
            raise MetadataError(f"Unexpected exiftool output: {''.join(stdout)!r}")
        if status.group(1) != "0":
            raise MetadataError("".join(stderr).strip() or f"{_EXECUTABLE} exited with code {status.group(1)}")
        return "".join(stdout[:-1])

    def close(self) -> None:
        try:
            self._process.communicate("-stay_open\nFalse\n", timeout=self._timeout)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            self._process.kill()
            self._process.wait()

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

    def _kill(self) -> None:
        self._timed_out = True
        self._process.kill()


def _parse_exif(datetime_text: str, offset_text: str, xmp_text: str) -> CaptureTime:
    local_time = datetime.strptime(datetime_text, _EXIF_DATETIME_FORMAT)
    if offset_text != _MISSING_VALUE:
        return CaptureTime(local_time, UtcOffset.parse(offset_text))

    xmp = _parse_xmp(xmp_text) if _XMP_DATETIME_PATTERN.match(xmp_text) else None
    if xmp is not None and xmp.local_time.replace(microsecond=0) == local_time:
        return CaptureTime(local_time, xmp.offset)
    return CaptureTime(local_time)


def _parse_xmp(text: str) -> CaptureTime:
    match = _XMP_DATETIME_PATTERN.match(text)
    if match is None:
        raise ValueError(f"unrecognised XMP date {text!r}")

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


def _format_xmp(local_time: datetime) -> str:
    text = local_time.strftime(_EXIF_DATETIME_FORMAT)
    if local_time.microsecond:
        text += f".{local_time.microsecond:06d}".rstrip("0")
    return text


def _as_argument(path: Path) -> str:
    # An absolute path never starts with "-", so exiftool cannot mistake it for an option.
    return str(path.absolute())
