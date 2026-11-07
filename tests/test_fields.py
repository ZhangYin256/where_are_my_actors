"""字段 / 人员解析单元测试。"""

from __future__ import annotations

import pytest

from rehearsal.fields import (
    FieldParseError,
    PersonInfo,
    encode_mention,
    format_remind_minutes,
    parse_fields,
    parse_participants,
    parse_remind_minutes,
    parse_scope,
    split_content,
)


def test_parse_multiline_fields():
    fields = parse_fields(
        "时间=周五7~10\n地点：一排\n人员: @张三\n内容=\n1. 开场\n2. 走位\n备注=自愿参加"
    )
    assert fields.get("time") == "周五7~10"
    assert fields.get("location") == "一排"
    assert fields.get("participants") == "@张三"
    assert fields.get("content") == "1. 开场\n2. 走位"
    assert fields.get("remark") == "自愿参加"


def test_separators_equivalent():
    for separator in ("=", "：", ":"):
        fields = parse_fields(f"地点{separator}一排")
        assert fields.get("location") == "一排"


def test_inline_fields():
    fields = parse_fields("时间=周五7~10 地点=一排 人员=@张三")
    assert fields.get("time") == "周五7~10"
    assert fields.get("location") == "一排"
    assert fields.get("participants") == "@张三"


def test_content_continuation_without_label():
    fields = parse_fields("时间=明天7~9\n内容=第一行\n第二行\n第三行")
    assert fields.get("content") == "第一行\n第二行\n第三行"


def test_empty_values_are_preserved_for_interpretation():
    fields = parse_fields("时间=明天7~9\n地点=暂无\n内容=\n备注=无")
    assert fields.get("location") == "暂无"
    assert split_content(fields.get("content", "")) == []


def test_duplicate_field_error():
    with pytest.raises(FieldParseError):
        parse_fields("时间=周五7~10\n时间=周六7~10")


def test_missing_fields_error():
    with pytest.raises(FieldParseError):
        parse_fields("这不是字段")


def test_leading_text_error():
    with pytest.raises(FieldParseError):
        parse_fields("随便说说\n时间=周五7~10")


def test_split_content_numbering_preserved():
    assert split_content("1. 开场\n2. 走位") == ["1. 开场", "2. 走位"]
    assert split_content("") == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("30", (30,)),
        ("60,30,10", (60, 30, 10)),
        ("30、10", (30, 10)),
        ("关闭", ()),
        ("关", ()),
        ("", ()),
    ],
)
def test_parse_remind_minutes(text, expected):
    assert parse_remind_minutes(text) == expected


def test_parse_remind_invalid():
    with pytest.raises(FieldParseError):
        parse_remind_minutes("半小时后")


def test_format_remind_minutes():
    assert format_remind_minutes((30,)) == "提前30分钟"
    assert format_remind_minutes((60, 30)) == "提前60、30分钟"
    assert format_remind_minutes(()) == "不提醒"


def test_parse_scope():
    assert parse_scope("全体成员") is True
    assert parse_scope("人员") is False
    with pytest.raises(FieldParseError):
        parse_scope("一部分人")


def test_parse_mentions_and_text_person():
    text = f"{encode_mention('10003', '王五')} 制作组"
    result = parse_participants(text)
    assert result.all_flag is False
    assert len(result.participants) == 2
    assert result.participants[0].qq_id == "10003"
    assert result.participants[0].precise is True
    assert result.participants[1].display_name == "制作组"
    assert result.participants[1].precise is False


def test_parse_tag_before_mention():
    text = f"湖区{encode_mention('10003', '王五')}"
    result = parse_participants(text)
    assert result.participants[0].remark_tag == "湖区"


def test_parse_tag_suffix():
    text = f"{encode_mention('10003', '王五')}(湖区)"
    result = parse_participants(text)
    assert result.participants[0].remark_tag == "湖区"


def test_parse_at_all():
    result = parse_participants("@全体成员")
    assert result.all_flag is True
    assert result.participants == []


def test_parse_dedup_mentions():
    text = f"{encode_mention('10003', '王五')} {encode_mention('10003', '王五')}"
    result = parse_participants(text)
    assert len(result.participants) == 1


def test_parse_plain_at_resolves():
    def resolve(name):
        if name == "王五":
            return PersonInfo("10003", "王五", "湖区")
        return None

    result = parse_participants("@王五 @无名氏", resolve)
    assert result.participants[0].qq_id == "10003"
    assert result.participants[0].remark_tag == "湖区"
    assert result.participants[1].precise is False
    assert result.participants[1].display_name == "无名氏"


def test_parse_tagged_plain_at():
    def resolve(name):
        return PersonInfo("10003", "王五") if name == "王五" else None

    result = parse_participants("湖区@王五", resolve)
    assert result.participants[0].qq_id == "10003"
    assert result.participants[0].remark_tag == "湖区"


def test_parse_actor_alias_resolves():
    def resolve(name):
        return PersonInfo("10003", "王五") if name == "小五" else None

    result = parse_participants("小五", resolve)
    assert result.participants[0].qq_id == "10003"


def test_parse_empty_participants():
    result = parse_participants("")
    assert result.participants == []
    assert result.all_flag is False
