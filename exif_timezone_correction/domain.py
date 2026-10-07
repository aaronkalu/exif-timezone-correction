from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import ClassVar

_OFFSET_PATTERN = re.compile(r"^([+-]?)(\d{2}):(\d{2})$")


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

    def negated(self) -> UtcOffset:
        return UtcOffset(-self.total_minutes)

    def as_timedelta(self) -> timedelta:
        return timedelta(minutes=self.total_minutes)

    def __str__(self) -> str:
        sign = "-" if self.total_minutes < 0 else "+"
        hours, minutes = divmod(abs(self.total_minutes), 60)
        return f"{sign}{hours:02d}:{minutes:02d}"


UTC = UtcOffset(0)


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
