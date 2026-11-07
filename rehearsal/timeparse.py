"""中文排练时间解析。

支持：今天 / 明天 / 后天 / 周几 / 星期几 / 礼拜几 / 10.21 / 10月21日 /
晚上8:30~10:00 / 下午2点~4点 / 7~10 / 19:30-21:00 等写法。

解析结果始终是带时区的完整时间，并附带自动顺延、跨天等提示，
由上层在预览中明确展示。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from .errors import UserError

WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

_WEEKDAY_MAP = {
    "一": 0,
    "二": 1,
    "三": 2,
    "四": 3,
    "五": 4,
    "六": 5,
    "日": 6,
    "天": 6,
    "1": 0,
    "2": 1,
    "3": 2,
    "4": 3,
    "5": 4,
    "6": 5,
    "7": 6,
}

PERIODS = "凌晨|清晨|早上|早晨|上午|中午|下午|傍晚|晚上|晚间|夜里|夜晚|晚|早"

_MORNING = {"早上", "早晨", "清晨", "上午", "早"}
_NOON = {"中午"}
_AFTERNOON = {"下午", "傍晚"}
_EVENING = {"晚上", "晚间", "夜里", "夜晚", "晚"}
_EARLY = {"凌晨"}

_YMD_RE = re.compile(
    r"(?<!\d)(?P<y>\d{4})\s*[年/.\-]\s*(?P<m>\d{1,2})\s*[月/.\-]\s*(?P<d>\d{1,2})\s*日?"
)
_MD_RE = re.compile(r"(?<!\d)(?P<m>\d{1,2})\s*[月/.]\s*(?P<d>\d{1,2})\s*日?(?!\d)")
_WEEK_RE = re.compile(
    r"(?P<mod>下下|下|本|这)?\s*(?:周|星期|礼拜)\s*(?P<wd>[一二三四五六日天1-7])"
)
_REL_RE = re.compile(r"(?P<rel>大后天|后天|明天|明日|今天|今日)")

_RANGE_RE = re.compile(
    rf"(?:(?P<p1>{PERIODS}))?\s*(?P<h1>\d{{1,2}})(?::(?P<mi1>\d{{2}}))?"
    rf"\s*-\s*"
    rf"(?:(?P<p2>{PERIODS}))?\s*(?P<h2>\d{{1,2}})(?::(?P<mi2>\d{{2}}))?"
)
_SINGLE_RE = re.compile(
    rf"(?:(?P<p1>{PERIODS}))?\s*(?P<h1>\d{{1,2}})(?::(?P<mi1>\d{{2}}))?"
)

TIME_HELP = "时间无法识别，请使用类似格式：\n周五7~10\n周一晚8:30~10:00\n明天下午2点~4点"


class TimeParseError(UserError, ValueError):
    """时间无法解析。"""

    def __init__(self, message: str = TIME_HELP) -> None:
        super().__init__(message)


@dataclass(slots=True)
class ParsedTime:
    start: datetime
    end: datetime
    notes: list[str] = field(default_factory=list)
    cross_midnight: bool = False
    default_duration: bool = False


def normalize(text: str) -> str:
    """归一化全角符号、中文时间词。"""
    raw = unicodedata.normalize("NFKC", text or "").strip()
    replacements = {
        "今晚": "今天 晚上",
        "明晚": "明天 晚上",
        "后晚": "后天 晚上",
        "今早": "今天 早上",
        "明早": "明天 早上",
        "今儿": "今天",
        "明儿": "明天",
    }
    for src, dst in replacements.items():
        raw = raw.replace(src, dst)
    for src, dst in (("～", "-"), ("~", "-"), ("—", "-"), ("–", "-"), ("－", "-")):
        raw = raw.replace(src, dst)
    raw = re.sub(r"(\d{1,2})\s*[点时時]\s*半", r"\1:30", raw)
    raw = re.sub(r"(\d{1,2})\s*[点时時]\s*(\d{1,2})\s*分?", r"\1:\2", raw)
    raw = re.sub(r"(\d{1,2})\s*[点时時]", r"\1:00", raw)
    raw = re.sub(r"(?<=\d)\s*(?:至|到)\s*(?=\d)", "-", raw)
    raw = re.sub(r"\s*:\s*", ":", raw)
    raw = re.sub(r"\s*-\s*", "-", raw)
    raw = re.sub(r"\s+", " ", raw)
    return raw.strip(" ，,。;；")


def _group(period: str | None) -> str | None:
    if not period:
        return None
    if period in _MORNING:
        return "morning"
    if period in _NOON:
        return "noon"
    if period in _AFTERNOON:
        return "afternoon"
    if period in _EVENING:
        return "evening"
    if period in _EARLY:
        return "early"
    return None


def _find_date(text: str) -> tuple[str, re.Match[str]] | None:
    candidates: list[tuple[int, int, str, re.Match[str]]] = []
    for priority, (kind, regex) in enumerate(
        (("ymd", _YMD_RE), ("md", _MD_RE), ("week", _WEEK_RE), ("relative", _REL_RE))
    ):
        for match in regex.finditer(text):
            if kind in ("ymd", "md"):
                month = int(match.group("m"))
                day = int(match.group("d"))
                if not (1 <= month <= 12 and 1 <= day <= 31):
                    continue
            candidates.append((match.start(), priority, kind, match))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    _, _, kind, match = candidates[0]
    return kind, match


def _start_hour(hour: int, period: str | None, threshold: int) -> tuple[int, int, str | None]:
    """返回 (24 小时制小时, 日期偏移, 时段分组)。"""
    group = _group(period)
    if hour == 24:
        return 0, 1, group or "day"
    if group == "evening":
        if hour == 12:
            return 0, 1, group
        if 1 <= hour <= 5:
            return hour, 1, group
        if 6 <= hour <= 11:
            return hour + 12, 0, group
        return hour, 0, group
    if group == "early":
        return (0 if hour == 12 else hour), 0, group
    if group == "noon":
        return (hour + 12 if hour < 11 else hour), 0, group
    if group == "afternoon":
        return (hour + 12 if hour < 12 else hour), 0, group
    if group == "morning":
        return hour, 0, group
    # 未指定上午 / 下午 / 晚上：小于阈值按晚上解释
    if hour == 0:
        return 0, 0, "day"
    if hour < threshold:
        return hour + 12, 0, "evening"
    return hour, 0, "day"


def _end_hour(hour: int, start_hour: int, start_group: str | None, period: str | None) -> int:
    group = _group(period) or start_group
    if hour == 24:
        return 0
    if group == "evening":
        if hour == 12:
            return 0
        if 1 <= hour <= 11:
            candidate = hour + 12
            return candidate if candidate > start_hour else hour
        return hour
    if group == "morning":
        if hour < 12 and hour < start_hour:
            return hour + 12
        return hour
    if group == "noon":
        return hour + 12 if hour < 11 else hour
    if group == "afternoon":
        return hour + 12 if hour < 12 else hour
    if group == "early":
        return 0 if hour == 12 else hour
    return hour


def _combine(base: date, hour: int, minute: int, tz) -> datetime:
    return datetime.combine(base, time(hour, minute), tzinfo=tz)


def parse_time(
    text: str,
    now: datetime,
    evening_threshold: int = 9,
    default_duration_minutes: int = 120,
) -> ParsedTime:
    """解析时间表达式。

    :param text: 用户输入的时间字段，如 ``周五7~10``。
    :param now: 当前时间，必须带时区；返回时间与其使用同一时区。
    :param evening_threshold: 模糊时间晚上判定阈值。
    """
    if now.tzinfo is None:
        raise ValueError("now 必须带时区")
    if not text or not text.strip():
        raise TimeParseError()

    normalized = normalize(text)
    date_info = _find_date(normalized)
    work = normalized
    if date_info:
        _, match = date_info
        work = (normalized[: match.start()] + " " + normalized[match.end() :]).strip()

    notes: list[str] = []
    cross_midnight = False
    default_duration = False

    range_match = _RANGE_RE.search(work)
    if range_match:
        g = range_match
        p1 = g.group("p1")
        p2 = g.group("p2")
        h1, mi1 = int(g.group("h1")), int(g.group("mi1") or 0)
        h2, mi2 = int(g.group("h2")), int(g.group("mi2") or 0)
        if p1 is None:
            prefix = work[: g.start()]
            found = re.search(PERIODS, prefix)
            p1 = found.group(0) if found else None
    else:
        single = _SINGLE_RE.search(work)
        if not single:
            raise TimeParseError()
        p1 = single.group("p1")
        p2 = None
        h1, mi1 = int(single.group("h1")), int(single.group("mi1") or 0)
        if p1 is None:
            prefix = work[: single.start()]
            found = re.search(PERIODS, prefix)
            p1 = found.group(0) if found else None
        h2, mi2 = h1, mi1
        default_duration = True

    if not (0 <= mi1 <= 59 and 0 <= mi2 <= 59 and 0 <= h1 <= 24 and 0 <= h2 <= 24):
        raise TimeParseError()

    start_hour, start_day_offset, start_group = _start_hour(h1, p1, evening_threshold)

    # ---------- 日期基准 ----------
    explicit_date = False
    relative_kind = "none"
    base: date
    if date_info:
        kind, match = date_info
        if kind == "ymd":
            year, month, day = int(match.group("y")), int(match.group("m")), int(match.group("d"))
            try:
                base = date(year, month, day)
            except ValueError as exc:
                raise TimeParseError(f"日期无效：{year}-{month}-{day}") from exc
            explicit_date = True
            relative_kind = "ymd"
        elif kind == "md":
            month, day = int(match.group("m")), int(match.group("d"))
            try:
                base = date(now.year, month, day)
            except ValueError as exc:
                raise TimeParseError(f"日期无效：{month}月{day}日") from exc
            if base < now.date():
                try:
                    base = date(now.year + 1, month, day)
                except ValueError as exc:
                    raise TimeParseError(f"日期无效：{month}月{day}日") from exc
                notes.append("该日期今年已过，已按下一年处理")
            explicit_date = True
            relative_kind = "md"
        elif kind == "week":
            mod = match.group("mod")
            target = _WEEKDAY_MAP[match.group("wd")]
            days_ahead = (target - now.weekday()) % 7
            if mod in ("下", "下下"):
                days_ahead += 7 if mod == "下" else 14
            elif mod in ("本", "这"):
                days_ahead = target - now.weekday()
            base = now.date() + timedelta(days=days_ahead)
            explicit_date = mod in ("本", "这", "下", "下下")
            relative_kind = "week"
        else:
            offsets = {"今天": 0, "今日": 0, "明天": 1, "明日": 1, "后天": 2, "大后天": 3}
            base = now.date() + timedelta(days=offsets[match.group("rel")])
            explicit_date = True
            relative_kind = "relative"
    else:
        base = now.date()

    start_dt = _combine(base + timedelta(days=start_day_offset), start_hour, mi1, now.tzinfo)

    if range_match:
        end_hour = _end_hour(h2, start_hour + start_day_offset * 24, start_group, p2)
        end_dt = _combine(start_dt.date(), end_hour, mi2, now.tzinfo)
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)
        if end_dt.date() != start_dt.date():
            cross_midnight = True
            notes.append("结束时间早于开始时间，已按跨天处理")
    else:
        end_dt = start_dt + timedelta(minutes=default_duration_minutes)
        notes.append(f"未填写结束时间，默认按 {default_duration_minutes // 60} 小时处理")

    # ---------- 已过时间自动顺延 ----------
    if relative_kind == "week" and not explicit_date and start_dt <= now:
        start_dt += timedelta(days=7)
        end_dt += timedelta(days=7)
        notes.append("开始时间已过，按下一周处理")
    elif relative_kind == "none" and start_dt <= now:
        start_dt += timedelta(days=1)
        end_dt += timedelta(days=1)
        notes.append("今天该时间已过，按明天处理")

    if start_dt.tzinfo is None:  # pragma: no cover - 防御
        start_dt = start_dt.replace(tzinfo=now.tzinfo)
        end_dt = end_dt.replace(tzinfo=now.tzinfo)

    return ParsedTime(
        start=start_dt,
        end=end_dt,
        notes=notes,
        cross_midnight=cross_midnight,
        default_duration=default_duration,
    )


# ---------------------------------------------------------------------------
# 格式化
# ---------------------------------------------------------------------------
def format_date(dt: datetime, now: datetime) -> str:
    prefix = ""
    if dt.year > now.year:
        prefix = "明年" if dt.year == now.year + 1 else f"{dt.year}年"
    return f"{prefix}{dt.month}月{dt.day:02d}日 {WEEKDAY_NAMES[dt.weekday()]}"


def format_time_range(start: datetime, end: datetime) -> str:
    if end.date() == start.date():
        return f"{start:%H:%M}-{end:%H:%M}"
    if (end.date() - start.date()).days == 1:
        return f"{start:%H:%M}-次日{end:%H:%M}"
    return f"{start:%H:%M}-{end.month}月{end.day}日 {end:%H:%M}"


def format_notice_time(start: datetime, end: datetime, now: datetime) -> str:
    return f"{format_date(start, now)} {format_time_range(start, end)}"


def format_preview_time(start: datetime, end: datetime) -> str:
    return (
        f"{start.year}-{start.month:02d}-{start.day:02d} "
        f"{WEEKDAY_NAMES[start.weekday()]} {format_time_range(start, end)}"
    )


def format_relative_day(dt: datetime, now: datetime) -> str:
    delta = (dt.date() - now.date()).days
    if delta == 0:
        return "今天"
    if delta == 1:
        return "明天"
    if delta == 2:
        return "后天"
    return format_date(dt, now)
