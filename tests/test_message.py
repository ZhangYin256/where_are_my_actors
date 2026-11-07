"""消息拆分与 OneBot 转换测试。"""

from __future__ import annotations

from rehearsal.models import At, Message, split_message
from rehearsal.onebot import from_onebot, to_onebot


def test_split_short_message_unchanged():
    message = Message.text("hello")
    chunks = split_message(message, 100)
    assert len(chunks) == 1
    assert chunks[0].plain_text() == "hello"


def test_split_long_text():
    text = "\n".join(f"第{i}行内容" * 30 for i in range(50))
    message = Message.text(text)
    chunks = split_message(message, 200)
    assert len(chunks) > 1
    assert all(len(chunk) <= 200 for chunk in chunks)
    assert "".join(chunk.plain_text() for chunk in chunks) == text


def test_split_keeps_at_segments():
    message = Message()
    message.add_text("人员：")
    message.add_at("10001", "张三")
    message.add_text("\n" + "很长的内容" * 200)
    chunks = split_message(message, 100)
    assert len(chunks) > 1
    assert any(isinstance(segment, At) for segment in chunks[0].segments)


def test_to_onebot_and_back():
    message = Message()
    message.add_text("人员：")
    message.add_at("10001", "张三")
    message.add_text(" 制作组")
    message.add_at_all()
    onebot = to_onebot(message)
    text = from_onebot(onebot)
    assert "人员：" in text
    assert "@全体成员" in text
    assert "10001" in text
    assert "制作组" in text


def test_from_onebot_at_without_name_keeps_token():
    message = to_onebot(Message.at("10001"))
    text = from_onebot(message)
    decoded = Message.text(text).plain_text()
    assert "10001" in decoded or "@10001" in decoded
