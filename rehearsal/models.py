"""核心数据模型与消息结构。"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from .timeutil import localzone


# ---------------------------------------------------------------------------
# 消息段
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Text:
    text: str


@dataclass(frozen=True, slots=True)
class At:
    qq: str
    name: str = ""


@dataclass(frozen=True, slots=True)
class AtAll:
    pass


Segment = Text | At | AtAll


class Message:
    """与平台无关的消息，由若干消息段组成。"""

    __slots__ = ("segments",)

    def __init__(self, segments: Iterable[Segment] | None = None) -> None:
        self.segments: list[Segment] = list(segments or [])

    # -- 构造 ---------------------------------------------------------
    @classmethod
    def text(cls, text: str) -> Message:
        return cls([Text(text)])

    @classmethod
    def at(cls, qq: str, name: str = "") -> Message:
        return cls([At(str(qq), name)])

    @classmethod
    def at_all(cls) -> Message:
        return cls([AtAll()])

    # -- 修改 ---------------------------------------------------------
    def add_text(self, text: str) -> Message:
        if text:
            if self.segments and isinstance(self.segments[-1], Text):
                self.segments[-1] = Text(self.segments[-1].text + text)
            else:
                self.segments.append(Text(text))
        return self

    def add_at(self, qq: str, name: str = "") -> Message:
        self.segments.append(At(str(qq), name))
        self._merge_text()
        return self

    def add_at_all(self) -> Message:
        self.segments.append(AtAll())
        self._merge_text()
        return self

    def extend(self, other: Message) -> Message:
        for segment in other.segments:
            if isinstance(segment, Text):
                self.add_text(segment.text)
            elif isinstance(segment, At):
                self.add_at(segment.qq, segment.name)
            else:
                self.add_at_all()
        return self

    def _merge_text(self) -> None:
        merged: list[Segment] = []
        for segment in self.segments:
            if isinstance(segment, Text) and not segment.text:
                continue
            if merged and isinstance(segment, Text) and isinstance(merged[-1], Text):
                merged[-1] = Text(merged[-1].text + segment.text)
            else:
                merged.append(segment)
        self.segments = merged

    # -- 输出 ---------------------------------------------------------
    def plain_text(self) -> str:
        out: list[str] = []
        for segment in self.segments:
            if isinstance(segment, Text):
                out.append(segment.text)
            elif isinstance(segment, At):
                out.append(f"@{segment.name or segment.qq}")
            else:
                out.append("@全体成员")
        return "".join(out)

    def __str__(self) -> str:  # pragma: no cover - 便于调试
        return self.plain_text()

    def __bool__(self) -> bool:
        return bool(self.plain_text().strip())

    def __len__(self) -> int:
        return len(self.plain_text())

    def copy(self) -> Message:
        return Message(list(self.segments))


def join_messages(messages: Sequence[Message], sep: str = "\n") -> Message:
    result = Message()
    for index, message in enumerate(messages):
        if index and sep:
            result.add_text(sep)
        result.extend(message)
    return result


def _split_text(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > max_chars:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(line[:max_chars])
            line = line[max_chars:]
        if current and len(current) + len(line) > max_chars:
            pieces.append(current)
            current = ""
        current += line
    if current:
        pieces.append(current)
    return [piece for piece in pieces if piece]


def split_message(message: Message, max_chars: int = 1500) -> list[Message]:
    """按字符上限拆分长消息，尽量在换行处断开，保留 @ 段。"""
    if max_chars <= 0 or len(message) <= max_chars:
        return [message.copy()]
    chunks: list[list[Segment]] = []
    current: list[Segment] = []
    size = 0

    def flush() -> None:
        nonlocal current, size
        if current:
            chunks.append(current)
            current = []
            size = 0

    for segment in message.segments:
        if isinstance(segment, Text):
            for piece in _split_text(segment.text, max_chars):
                if size + len(piece) > max_chars and current:
                    flush()
                current.append(Text(piece))
                size += len(piece)
        else:
            label = 1 if isinstance(segment, AtAll) else max(1, len(segment.name or segment.qq) + 1)
            if size + label > max_chars and current:
                flush()
            current.append(segment)
            size += label
    flush()
    return [Message(parts) for parts in chunks] or [Message()]


# ---------------------------------------------------------------------------
# 业务实体
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Member:
    qq: str
    nickname: str = ""
    card: str = ""
    role: str = "member"  # owner / admin / member

    @property
    def display_name(self) -> str:
        return self.card or self.nickname or self.qq


@dataclass(frozen=True, slots=True)
class Mention:
    qq: str
    name: str = ""
    all: bool = False


@dataclass(slots=True)
class Troupe:
    id: int
    name: str
    group_id: str
    timezone: str = "Asia/Shanghai"
    default_remind_minutes: str = "30"
    private_remind: bool = False
    ambiguous_evening_threshold: int = 9
    show_tags: bool = False
    show_empty: bool = False
    publish_confirm_when_clean: bool = False
    degrade_at_scope: str = "participants"
    status: str = "active"
    created_at: datetime | None = None

    @property
    def tz(self):
        return localzone(self.timezone)

    @property
    def reminders(self) -> tuple[int, ...]:
        from .fields import parse_remind_minutes

        return parse_remind_minutes(self.default_remind_minutes)


@dataclass(frozen=True, slots=True)
class RoleBinding:
    qq_id: str
    role: str
    source: str = "manual"
    granted_by: str = ""
    granted_at: datetime | None = None


@dataclass(slots=True)
class ActorProfile:
    id: int
    troupe_id: int
    qq_id: str | None
    display_name: str
    aliases: list[str] = field(default_factory=list)
    remark_tags: list[str] = field(default_factory=list)
    active: bool = True
    created_at: datetime | None = None

    @property
    def precise(self) -> bool:
        return bool(self.qq_id)


@dataclass(slots=True)
class Participant:
    qq_id: str | None
    display_name: str
    remark_tag: str = ""
    precise: bool = False
    note: str = ""

    @classmethod
    def from_qq(cls, qq: str, display_name: str, remark_tag: str = "") -> Participant:
        return cls(qq_id=str(qq), display_name=display_name, remark_tag=remark_tag, precise=True)


@dataclass(slots=True)
class Rehearsal:
    id: int
    troupe_id: int
    seq: int
    location: str
    start_at: datetime
    end_at: datetime
    content: list[str] = field(default_factory=list)
    remark: str = ""
    title: str = "#排练通知"
    raw_text: str = ""
    participant_mode: str = "specified"
    notify_all: bool = False
    remind_minutes: tuple[int, ...] = (30,)
    status: str = "published"
    created_by: str = ""
    updated_by: str = ""
    created_at: datetime | None = None
    published_at: datetime | None = None
    deleted_at: datetime | None = None
    participants: list[Participant] = field(default_factory=list)

    @property
    def precise_participants(self) -> list[Participant]:
        return [p for p in self.participants if p.precise and p.qq_id]

    @property
    def text_participants(self) -> list[Participant]:
        return [p for p in self.participants if not p.precise]

    @property
    def visible(self) -> bool:
        return self.status in ("published", "cancelled", "completed")


@dataclass(slots=True)
class RehearsalDraft:
    """尚未落库的排练通知草稿（发布 / 修改预览共用）。"""

    start_at: datetime
    end_at: datetime
    location: str = ""
    content: list[str] = field(default_factory=list)
    remark: str = ""
    participants: list[Participant] = field(default_factory=list)
    participant_mode: str = "specified"
    notify_all: bool = False
    remind_minutes: tuple[int, ...] = (30,)
    raw_text: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def precise_participants(self) -> list[Participant]:
        return [p for p in self.participants if p.precise and p.qq_id]

    @property
    def text_participants(self) -> list[Participant]:
        return [p for p in self.participants if not p.precise]


@dataclass(slots=True)
class ReminderJob:
    id: int
    rehearsal_id: int
    troupe_id: int
    trigger_at: datetime
    status: str = "pending"
    attempts: int = 0
    last_error: str = ""
    sent_message_id: str | None = None
    sent_at: datetime | None = None


@dataclass(slots=True)
class AuditEntry:
    id: int
    troupe_id: int | None
    qq_id: str
    action: str
    target: str = ""
    summary: str = ""
    result: str = "success"
    message: str = ""
    created_at: datetime | None = None


@dataclass(slots=True)
class PendingAction:
    id: str
    troupe_id: int
    kind: str
    operator_qq: str
    group_id: str
    payload: dict
    rehearsal_id: int | None = None
    status: str = "pending"
    created_at: datetime | None = None
    expires_at: datetime | None = None
