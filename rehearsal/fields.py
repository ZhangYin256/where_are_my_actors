"""排练通知字段解析。

输入形如::

    时间=周五7~10
    地点：一排
    内容=
    1. 福临门开场和声
    2. 一念一宇宙
    备注: 其他演员可以来自由练习

字段分隔符支持 ``=``、``：``、``:``；内容支持多行。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .errors import UserError
from .models import Participant

# 字段别名 -> 规范字段名
FIELD_ALIASES: dict[str, str] = {
    "时间": "time",
    "排练时间": "time",
    "日期": "time",
    "地点": "location",
    "地址": "location",
    "场地": "location",
    "人员": "participants",
    "参与人员": "participants",
    "参加人员": "participants",
    "参加人": "participants",
    "内容": "content",
    "排练内容": "content",
    "备注": "remark",
    "说明": "remark",
    "提醒": "remind",
    "提前提醒": "remind",
    "通知范围": "scope",
    "范围": "scope",
    "剧组": "troupe",
}

FIELD_LABELS = {
    "time": "时间",
    "location": "地点",
    "participants": "人员",
    "content": "内容",
    "remark": "备注",
    "remind": "提醒",
    "scope": "通知范围",
    "troupe": "剧组",
}

_KEYS_SORTED = sorted(FIELD_ALIASES, key=len, reverse=True)
_KEY_RE = re.compile(
    r"(?:^|(?<=\s))[ \t]*(?P<key>" + "|".join(map(re.escape, _KEYS_SORTED)) + r")[ \t]*[=:：][ \t]*",
    re.MULTILINE,
)

EMPTY_VALUES = {"", "暂无", "无", "-", "—", "－", "空", "none", "null", "n/a"}

MENTION_OPEN = "\x01"
MENTION_SEP = "\x02"
MENTION_CLOSE = "\x03"
MENTION_TOKEN_RE = re.compile(r"\x01(\d+)\x02([^\x03]*)\x03")

_TAG_SUFFIX_RE = re.compile(r"[（(\[【]\s*([^()（）\[\]【】]+?)\s*[)）\]】]\s*$")
_ALL_WORDS = {"全体成员", "全体", "全群", "全员", "所有人", "everyone", "all"}

REMIND_OFF_WORDS = {"关闭", "关", "否", "off", "none", "无", "不提醒"}

FIELD_HELP = (
    "未识别到有效字段。发布请使用：\n"
    "#发布排练\n"
    "时间=周五7~10\n"
    "地点=一排\n"
    "人员=@张三 @李四\n"
    "内容=1. 开场 2. 走位\n"
    "备注=自愿参加"
)


class FieldParseError(UserError):
    """字段格式错误。"""


def encode_mention(qq: str, name: str = "") -> str:
    safe = (name or "").replace(MENTION_CLOSE, "").replace(MENTION_SEP, "")
    return f"{MENTION_OPEN}{qq}{MENTION_SEP}{safe}{MENTION_CLOSE}"


def decode_mentions(text: str) -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2)) for m in MENTION_TOKEN_RE.finditer(text or "")]


def is_empty_value(value: str | None) -> bool:
    if value is None:
        return True
    return value.strip().casefold() in EMPTY_VALUES


def clean_value(value: str | None) -> str:
    if is_empty_value(value):
        return ""
    return (value or "").strip()


@dataclass(slots=True)
class ParsedFields:
    values: dict[str, str] = field(default_factory=dict)
    raw: str = ""

    def get(self, key: str, default: str = "") -> str:
        return self.values.get(key, default)

    def has(self, key: str) -> bool:
        return key in self.values

    def __contains__(self, key: str) -> bool:
        return key in self.values


def parse_fields(text: str) -> ParsedFields:
    """把多行字段文本解析为规范字段字典。"""
    source = (text or "").strip("\n")
    if not source.strip():
        raise FieldParseError(FIELD_HELP)

    matches = list(_KEY_RE.finditer(source))
    if not matches:
        raise FieldParseError(FIELD_HELP)

    leading = source[: matches[0].start()].strip()
    if leading:
        raise FieldParseError(f"无法识别的内容：{leading}\n{FIELD_HELP}")

    values: dict[str, str] = {}
    for index, match in enumerate(matches):
        key = FIELD_ALIASES[match.group("key")]
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        value = source[match.end() : end]
        value = value.strip("\n").rstrip()
        if key in values:
            raise FieldParseError(f"字段「{FIELD_LABELS[key]}」重复出现，请只填写一次。")
        values[key] = value
    return ParsedFields(values=values, raw=text or "")


def split_content(value: str) -> list[str]:
    """内容字段按行拆分，去掉空行。"""
    if is_empty_value(value):
        return []
    lines = [line.rstrip() for line in (value or "").splitlines()]
    return [line for line in lines if line.strip()]


def parse_remind_minutes(value: str | None, default: str = "30") -> tuple[int, ...]:
    """解析提醒分钟列表，如 ``30``、``60,30,10``、``关闭``。

    ``None`` 使用 ``default``；空字符串表示关闭提醒。
    """
    text = default.strip() if value is None else str(value).strip()
    if not text:
        return ()
    if text.casefold() in REMIND_OFF_WORDS:
        return ()
    parts = [part for part in re.split(r"[,，、;；\s]+", text) if part]
    minutes: set[int] = set()
    for part in parts:
        if part.casefold() in REMIND_OFF_WORDS:
            return ()
        match = re.fullmatch(r"(\d{1,4})\s*(?:分钟|分|min)?", part, re.IGNORECASE)
        if not match:
            raise FieldParseError(
                f"提醒时间无法识别：{part}\n请使用分钟数，如 30 或 60,30,10；关闭提醒请填写 提醒=关闭"
            )
        number = int(match.group(1))
        if number > 1440:
            raise FieldParseError("提醒时间不能超过 1440 分钟（24 小时）。")
        minutes.add(number)
    return tuple(sorted(minutes, reverse=True))


def format_remind_minutes(minutes: tuple[int, ...] | list[int]) -> str:
    if not minutes:
        return "不提醒"
    ordered = sorted({int(m) for m in minutes}, reverse=True)
    if len(ordered) == 1:
        return f"提前{ordered[0]}分钟"
    return "提前" + "、".join(str(m) for m in ordered) + "分钟"


def parse_scope(value: str | None) -> bool:
    """通知范围字段：返回是否 @ 全体成员 / 提醒全群。"""
    text = (value or "").strip()
    if not text:
        return False
    lowered = text.casefold()
    if lowered in _ALL_WORDS or lowered in {"全体成员", "全群", "all"}:
        return True
    if lowered in {"人员", "仅人员", "参与者", "参与人员", "不提醒全体", "false", "否"}:
        return False
    raise FieldParseError(
        f"通知范围无法识别：{text}\n请填写 全体成员 或 人员（仅参与人员）。"
    )


@dataclass(frozen=True, slots=True)
class PersonInfo:
    """人员解析结果（来自演员表或群成员）。"""

    qq: str | None
    display_name: str
    tag: str = ""


@dataclass(slots=True)
class ParticipantResult:
    participants: list[Participant] = field(default_factory=list)
    all_flag: bool = False

    @property
    def unresolved(self) -> list[str]:
        return [p.display_name for p in self.participants if not p.precise]


def _strip_tag(text: str) -> tuple[str, str]:
    match = _TAG_SUFFIX_RE.search(text or "")
    if not match:
        return (text or "").strip(), ""
    return text[: match.start()].strip(), match.group(1).strip()


def _parse_token(token: str, resolve) -> Participant | None:
    mentions = list(MENTION_TOKEN_RE.finditer(token))
    if mentions:
        first = mentions[0]
        prefix = token[: first.start()].strip()
        suffix = token[first.end() :].strip()
        qq = first.group(1)
        name = first.group(2).strip() or qq
        tag = prefix
        if suffix:
            suffix_name, suffix_tag = _strip_tag(suffix)
            if suffix_tag and not tag:
                tag = suffix_tag
            elif suffix_name and not tag:
                tag = suffix_name
        return Participant(qq_id=qq, display_name=name, remark_tag=tag, precise=True)

    stripped = token.strip()
    if not stripped:
        return None

    if stripped.startswith("@"):
        body, tag = _strip_tag(stripped[1:])
        if not body:
            return None
        info = resolve(body) if resolve else None
        if info and info.qq:
            return Participant(
                qq_id=info.qq,
                display_name=info.display_name,
                remark_tag=tag or info.tag,
                precise=True,
            )
        return Participant(qq_id=None, display_name=body, remark_tag=tag, precise=False)

    if "@" in stripped:
        tag_part, _, name = stripped.partition("@")
        tag_part = tag_part.strip()
        name, suffix_tag = _strip_tag(name)
        if name:
            info = resolve(name) if resolve else None
            if info and info.qq:
                return Participant(
                    qq_id=info.qq,
                    display_name=info.display_name,
                    remark_tag=tag_part or suffix_tag or info.tag,
                    precise=True,
                )
            return Participant(
                qq_id=None,
                display_name=name,
                remark_tag=tag_part or suffix_tag,
                precise=False,
            )

    info = resolve(stripped) if resolve else None
    if info and info.qq:
        return Participant(
            qq_id=info.qq, display_name=info.display_name, remark_tag=info.tag, precise=True
        )
    return Participant(qq_id=None, display_name=stripped, precise=False)


def parse_participants(text: str, resolve=None) -> ParticipantResult:
    """解析人员字段。

    ``resolve(name)`` 返回 :class:`PersonInfo` 或 ``None``，用于把文本人员
    映射到 QQ 成员；无法匹配的作为文本人员保留。
    """
    result = ParticipantResult()
    if not (text or "").strip():
        return result

    seen: dict[str, Participant] = {}
    for token in re.split(r"[\s,，、;；]+", text.strip()):
        if not token:
            continue
        stripped = token.strip()
        all_candidate = stripped[1:] if stripped.startswith("@") else stripped
        if all_candidate.casefold() in _ALL_WORDS:
            result.all_flag = True
            continue
        participant = _parse_token(stripped, resolve)
        if participant is None:
            continue
        key = (
            f"q:{participant.qq_id}"
            if participant.precise and participant.qq_id
            else f"t:{participant.display_name}"
        )
        if key in seen:
            existing = seen[key]
            if not existing.remark_tag and participant.remark_tag:
                existing.remark_tag = participant.remark_tag
            continue
        seen[key] = participant
        result.participants.append(participant)
    return result
