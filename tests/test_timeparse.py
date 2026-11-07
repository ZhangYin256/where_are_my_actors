"""时间解析单元测试：覆盖需求 5.5 与验收 12.4。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from rehearsal.timeparse import (
    TimeParseError,
    format_date,
    format_notice_time,
    format_relative_day,
    format_time_range,
    parse_time,
)

TZ = ZoneInfo("Asia/Shanghai")
MON_9 = datetime(2026, 11, 9, 9, 0, tzinfo=TZ)  # 周一上午
MON_21 = datetime(2026, 11, 9, 21, 0, tzinfo=TZ)
SAT_13 = datetime(2026, 11, 7, 13, 0, tzinfo=TZ)  # 周五 11-13，故 11-07 为周六
FRI_20 = datetime(2026, 11, 13, 20, 0, tzinfo=TZ)


def parse(text: str, now: datetime = MON_9, threshold: int = 9):
    return parse_time(text, now=now, evening_threshold=threshold)


def test_weekday_evening_same_day():
    result = parse("周一晚8:30~10:00")
    assert result.start == datetime(2026, 11, 9, 20, 30, tzinfo=TZ)
    assert result.end == datetime(2026, 11, 9, 22, 0, tzinfo=TZ)
    assert result.notes == []


def test_weekday_evening_passed_rolls_to_next_week():
    result = parse("周一晚8:30~10:00", now=MON_21)
    assert result.start == datetime(2026, 11, 16, 20, 30, tzinfo=TZ)
    assert result.end == datetime(2026, 11, 16, 22, 0, tzinfo=TZ)
    assert "开始时间已过，按下一周处理" in result.notes


def test_friday_range_on_saturday_is_next_friday():
    result = parse("周五7~10", now=SAT_13)
    assert result.start == datetime(2026, 11, 13, 19, 0, tzinfo=TZ)
    assert result.end == datetime(2026, 11, 13, 22, 0, tzinfo=TZ)


def test_friday_range_on_friday_after_start():
    result = parse("周五7~10", now=FRI_20)
    assert result.start == datetime(2026, 11, 20, 19, 0, tzinfo=TZ)


def test_bare_hour_range_defaults_to_evening():
    result = parse("7~10")
    assert result.start.hour == 19
    assert result.end.hour == 22


def test_bare_hour_range_8_to_11():
    result = parse("8~11")
    assert result.start.hour == 20
    assert result.end.hour == 23


def test_cross_midnight():
    result = parse("8~1")
    assert result.start == datetime(2026, 11, 9, 20, 0, tzinfo=TZ)
    assert result.end == datetime(2026, 11, 10, 1, 0, tzinfo=TZ)
    assert result.cross_midnight is True
    assert "结束时间早于开始时间，已按跨天处理" in result.notes


def test_cross_midnight_explicit_24h():
    result = parse("19:30-01:00")
    assert result.start == datetime(2026, 11, 9, 19, 30, tzinfo=TZ)
    assert result.end == datetime(2026, 11, 10, 1, 0, tzinfo=TZ)


def test_evening_word():
    result = parse("晚上8:30~10:00")
    assert (result.start.hour, result.start.minute) == (20, 30)
    assert result.end.hour == 22


def test_afternoon_chinese_clock():
    result = parse("下午2点~4点")
    assert result.start.hour == 14
    assert result.end.hour == 16


def test_morning_chinese_clock():
    result = parse("上午9点~11点")
    assert result.start.hour == 9
    assert result.end.hour == 11


def test_tomorrow_afternoon():
    result = parse("明天下午2点~4点", now=SAT_13)
    assert result.start == datetime(2026, 11, 8, 14, 0, tzinfo=TZ)
    assert result.end == datetime(2026, 11, 8, 16, 0, tzinfo=TZ)


def test_day_after_tomorrow():
    result = parse("后天上午10点~12点", now=SAT_13)
    assert result.start.date().isoformat() == "2026-11-09"


def test_no_year_date_rolls_to_next_year():
    result = parse("10.21 下午2点~4点", now=SAT_13)
    assert result.start == datetime(2027, 10, 21, 14, 0, tzinfo=TZ)
    assert "该日期今年已过，已按下一年处理" in result.notes


def test_no_year_date_this_year():
    now = datetime(2026, 10, 1, 9, 0, tzinfo=TZ)
    result = parse("10.21 下午2点~4点", now=now)
    assert result.start == datetime(2026, 10, 21, 14, 0, tzinfo=TZ)


def test_chinese_date_with_year():
    result = parse("2025年12月31日 19:00-21:00", now=SAT_13)
    assert result.start == datetime(2025, 12, 31, 19, 0, tzinfo=TZ)


def test_evening_threshold_configurable():
    assert parse("9~11", threshold=9).start.hour == 9
    shifted = parse("9~11", threshold=10)
    assert shifted.start.hour == 21
    assert shifted.end.hour == 23


def test_bare_hour_9_is_morning_by_default():
    result = parse("9~11")
    assert (result.start.hour, result.end.hour) == (9, 11)


def test_this_week_friday_stays_in_this_week():
    result = parse("本周五7~10", now=SAT_13)
    assert result.start.date().isoformat() == "2026-11-06"


def test_next_week_friday():
    result = parse("下周五7~10", now=SAT_13)
    assert result.start.date().isoformat() == "2026-11-20"


def test_today_with_time_passed_keeps_today():
    late = datetime(2026, 11, 9, 23, 0, tzinfo=TZ)
    result = parse("今天7~10", now=late)
    assert result.start.date().isoformat() == "2026-11-09"
    assert result.start.hour == 19


def test_date_less_passed_time_moves_to_tomorrow():
    late = datetime(2026, 11, 9, 23, 0, tzinfo=TZ)
    result = parse("7~10", now=late)
    assert result.start.date().isoformat() == "2026-11-10"
    assert "今天该时间已过，按明天处理" in result.notes


def test_single_time_defaults_to_two_hours():
    result = parse("周五19:30")
    assert result.start.hour == 19 and result.start.minute == 30
    assert result.end.hour == 21 and result.end.minute == 30
    assert result.default_duration is True


def test_sunday():
    result = parse("周日19:00-21:00")
    assert result.start.date().isoformat() == "2026-11-15"


def test_xingqiyi_notation():
    result = parse("星期天 19:00-21:00", now=SAT_13)
    assert result.start.date().isoformat() == "2026-11-08"


def test_libai_notation():
    result = parse("礼拜二 晚上7点~9点")
    assert result.start.date().isoformat() == "2026-11-10"
    assert result.start.hour == 19


def test_to_separator():
    result = parse("周五晚上7点到10点")
    assert result.start.hour == 19
    assert result.end.hour == 22


@pytest.mark.parametrize("text", ["周五", "abc", "", "时间待定"])
def test_invalid_time(text):
    with pytest.raises(TimeParseError):
        parse(text)


def test_format_notice_time():
    result = parse("周五7~10", now=SAT_13)
    assert format_notice_time(result.start, result.end, SAT_13) == "11月13日 周五 19:00-22:00"


def test_format_cross_midnight():
    result = parse("8~1")
    assert format_time_range(result.start, result.end) == "20:00-次日01:00"


def test_format_next_year():
    result = parse("10.21 下午2点~4点", now=SAT_13)
    assert format_date(result.start, SAT_13) == "明年10月21日 周四"


def test_format_relative_day():
    result = parse("明天下午2点~4点", now=SAT_13)
    assert format_relative_day(result.start, SAT_13) == "明天"
