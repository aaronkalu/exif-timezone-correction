from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import ClassVar, Mapping, Union
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_OFFSET_PATTERN = re.compile(r"^([+-]?)(\d{2}):(\d{2})$")
_DURATION_PATTERN = re.compile(r"^([+-]?)(\d+):(\d{2})(?::(\d{2}))?$")


class MetadataError(Exception):
    pass


@dataclass(frozen=True)
class UtcOffset:
    total_minutes: int

    MIN_MINUTES: ClassVar[int] = -12 * 60
    MAX_MINUTES: ClassVar[int] = 14 * 60

    def __post_init__(self) -> None:
        if not self.MIN_MINUTES <= self.total_minutes <= self.MAX_MINUTES:
            raise ValueError(f"UTC offset {self} is outside the valid range -12:00 to +14:00.")

    @classmethod
    def parse(cls, text: str) -> UtcOffset:
        match = _OFFSET_PATTERN.match(text.strip())
        if match is None:
            raise ValueError(f"Invalid UTC offset {text!r}. Expected format: [+|-]HH:MM.")

        sign, hours, minutes = match.groups()
        if int(minutes) >= 60:
            raise ValueError(f"Invalid UTC offset {text!r}. Minutes must be below 60.")

        total_minutes = int(hours) * 60 + int(minutes)
        return cls(-total_minutes if sign == "-" else total_minutes)

    @classmethod
    def from_timedelta(cls, delta: timedelta) -> UtcOffset:
        # Historic zone offsets (local mean time) can include seconds, which no metadata tag can store.
        return cls(round(delta.total_seconds() / 60))

    def negated(self) -> UtcOffset:
        return UtcOffset(-self.total_minutes)

    def as_timedelta(self) -> timedelta:
        return timedelta(minutes=self.total_minutes)

    def offset_at_utc(self, utc_time: datetime) -> UtcOffset:
        return self

    def offset_at_local(self, local_time: datetime) -> UtcOffset:
        return self

    def __str__(self) -> str:
        sign = "-" if self.total_minutes < 0 else "+"
        hours, minutes = divmod(abs(self.total_minutes), 60)
        return f"{sign}{hours:02d}:{minutes:02d}"


UTC = UtcOffset(0)


@dataclass(frozen=True)
class NamedZone:
    """An IANA timezone, whose UTC offset depends on the date (daylight saving time)."""

    zone: ZoneInfo

    def offset_at_utc(self, utc_time: datetime) -> UtcOffset:
        aware = utc_time.replace(tzinfo=timezone.utc).astimezone(self.zone)
        return UtcOffset.from_timedelta(aware.utcoffset())

    def offset_at_local(self, local_time: datetime) -> UtcOffset:
        # A wall-clock time repeated when the clocks go back resolves to its first (daylight saving) occurrence.
        return UtcOffset.from_timedelta(local_time.replace(tzinfo=self.zone).utcoffset())

    def __str__(self) -> str:
        return self.zone.key


Zone = Union[UtcOffset, NamedZone]


def parse_zone(text: str) -> Zone:
    try:
        return UtcOffset.parse(text)
    except ValueError as offset_error:
        if _OFFSET_PATTERN.match(text.strip()) or not text.strip():
            raise
        try:
            return NamedZone(ZoneInfo(text.strip()))
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(
                f"Unknown timezone {text!r}. Use [+|-]HH:MM or an IANA name such as Europe/Berlin."
            ) from offset_error


def parse_duration(text: str) -> timedelta:
    match = _DURATION_PATTERN.match(text.strip())
    if match is None:
        raise ValueError(f"Invalid time shift {text!r}. Expected format: [+|-]HH:MM[:SS].")

    sign, hours, minutes, seconds = match.groups()
    if int(minutes) >= 60 or int(seconds or 0) >= 60:
        raise ValueError(f"Invalid time shift {text!r}. Minutes and seconds must be below 60.")

    duration = timedelta(hours=int(hours), minutes=int(minutes), seconds=int(seconds or 0))
    return -duration if sign == "-" else duration


@dataclass(frozen=True)
class CaptureTime:
    """An offset of None means the camera recorded none."""

    local_time: datetime
    offset: UtcOffset | None = None

    def in_timezone(self, target: UtcOffset) -> CaptureTime:
        if self.offset is None:
            raise ValueError("Cannot convert a capture time without a UTC offset.")
        utc_time = self.local_time - self.offset.as_timedelta()
        return CaptureTime(local_time=utc_time + target.as_timedelta(), offset=target)

    def __str__(self) -> str:
        offset = "(no offset)" if self.offset is None else str(self.offset)
        return f"{self.local_time:%Y-%m-%d %H:%M:%S} {offset}"


class DateTag(Enum):
    ORIGINAL = ("DateTimeOriginal", "OffsetTimeOriginal")
    DIGITIZED = ("CreateDate", "OffsetTimeDigitized")
    MODIFIED = ("ModifyDate", "OffsetTime")

    def __init__(self, tag_name: str, offset_tag_name: str) -> None:
        self.tag_name = tag_name
        self.offset_tag_name = offset_tag_name


@dataclass(frozen=True)
class StoredDate:
    """One date as stored in a file: the EXIF date with its EXIF offset tag, and an optional XMP copy.

    EXIF dates have whole seconds; XMP dates may carry fractions and their own offset.
    """

    exif: CaptureTime | None = None
    xmp: CaptureTime | None = None

    def effective(self) -> CaptureTime | None:
        if self.exif is None:
            return self.xmp
        xmp_matches = self.xmp is not None and self.xmp.local_time.replace(microsecond=0) == self.exif.local_time
        if not xmp_matches:
            return self.exif
        offset = self.exif.offset if self.exif.offset is not None else self.xmp.offset
        return CaptureTime(self.xmp.local_time, offset)

    def rewritten(self, capture: CaptureTime, with_exif: bool = True) -> StoredDate:
        exif = CaptureTime(capture.local_time.replace(microsecond=0), capture.offset) if with_exif else None
        return StoredDate(exif=exif, xmp=capture if self.xmp is not None else None)


@dataclass(frozen=True)
class ImageMetadata:
    dates: Mapping[DateTag, StoredDate] = field(default_factory=dict)
    gps_utc_time: datetime | None = None
    camera_model: str | None = None

    def date(self, tag: DateTag) -> StoredDate:
        return self.dates.get(tag, StoredDate())


@dataclass(frozen=True)
class DateChange:
    tag: DateTag
    before: StoredDate
    after: StoredDate


@dataclass(frozen=True)
class FileChange:
    path: Path
    dates: tuple[DateChange, ...]

    def reversed(self) -> FileChange:
        return FileChange(self.path, tuple(DateChange(c.tag, c.after, c.before) for c in self.dates))
