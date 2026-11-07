"""时区与时间格式化工具。"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "Asia/Shanghai"


def localzone(name: str | None) -> ZoneInfo:
    """返回时区对象，非法时区回退到默认时区。"""
    try:
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE)


def ensure_aware(dt: datetime, tz: ZoneInfo | None = None) -> datetime:
    """把 naive 时间按给定时区解释，aware 时间原样返回。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz or ZoneInfo(DEFAULT_TIMEZONE))
    return dt


def to_utc(dt: datetime) -> datetime:
    return ensure_aware(dt).astimezone(UTC)


def to_local(dt: datetime, tz: ZoneInfo) -> datetime:
    return ensure_aware(dt).astimezone(tz)


def now_utc() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime | None) -> str | None:
    """序列化为 UTC ISO 字符串。"""
    if dt is None:
        return None
    return to_utc(dt).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt
