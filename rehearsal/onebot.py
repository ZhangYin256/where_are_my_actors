"""OneBot V11 消息与核心消息结构互转。

放在核心包中便于单元测试；适配器插件直接复用。
"""

from __future__ import annotations

from nonebot.adapters.onebot.v11 import Message as OneBotMessage
from nonebot.adapters.onebot.v11 import MessageSegment as OneBotSegment

from .fields import encode_mention
from .models import At, AtAll, Message, Text


def to_onebot(message: Message) -> OneBotMessage:
    result = OneBotMessage()
    for segment in message.segments:
        if isinstance(segment, Text):
            if segment.text:
                result.append(OneBotSegment.text(segment.text))
        elif isinstance(segment, At):
            result.append(OneBotSegment.at(segment.qq))
        elif isinstance(segment, AtAll):
            result.append(OneBotSegment.at("all"))
    return result


def from_onebot(message: OneBotMessage) -> str:
    """把 OneBot 消息转成带 @ 标记的文本。

    @ 成员被编码成内部占位符，避免昵称里的空格 / 特殊字符破坏字段解析。
    图片、表情、回复等段忽略，不参与通知字段解析。
    """
    parts: list[str] = []
    for segment in message:
        if segment.type == "text":
            parts.append(str(segment.data.get("text", "")))
        elif segment.type == "at":
            qq = str(segment.data.get("qq", "")).strip()
            if not qq:
                continue
            if qq == "all":
                parts.append("@全体成员")
            else:
                name = str(segment.data.get("name") or "")
                parts.append(encode_mention(qq, name))
    return "".join(parts)
