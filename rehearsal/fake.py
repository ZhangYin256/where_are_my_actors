"""内存网关：用于单元测试与离线模拟器。

不需要 QQ / NapCat 即可完整跑通业务逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Member, Message


@dataclass(slots=True)
class SentMessage:
    target: str  # group / private
    id: str
    message: Message
    group_id: str | None = None
    user_id: str | None = None


class FakeGateway:
    """把发送内容记录到内存，便于断言。"""

    self_id = "900000"

    def __init__(
        self,
        groups: dict[str, list[Member]] | None = None,
        bot_admin: dict[str, bool] | None = None,
    ) -> None:
        self.groups: dict[str, list[Member]] = groups or {}
        self.bot_admin: dict[str, bool] = bot_admin or {}
        self.sent: list[SentMessage] = []
        self.group_send_error: str | None = None
        self.private_send_error: str | None = None

    # -- Gateway 协议 --------------------------------------------------
    async def send_group(self, group_id: str, message: Message) -> str | None:
        if self.group_send_error:
            raise RuntimeError(self.group_send_error)
        message_id = f"g{len(self.sent) + 1}"
        self.sent.append(
            SentMessage(
                target="group",
                id=message_id,
                message=message.copy(),
                group_id=str(group_id),
            )
        )
        return message_id

    async def send_private(self, qq: str, message: Message) -> str | None:
        if self.private_send_error:
            raise RuntimeError(self.private_send_error)
        message_id = f"p{len(self.sent) + 1}"
        self.sent.append(
            SentMessage(
                target="private",
                id=message_id,
                message=message.copy(),
                user_id=str(qq),
            )
        )
        return message_id

    async def get_group_member_list(self, group_id: str) -> list[Member]:
        group = self.groups.get(str(group_id))
        if group is None:
            raise RuntimeError(f"群 {group_id} 不存在或机器人未加入")
        return list(group)

    async def get_group_member_info(self, group_id: str, qq: str) -> Member | None:
        for member in self.groups.get(str(group_id), []):
            if member.qq == str(qq):
                return member
        return None

    async def bot_is_group_admin(self, group_id: str) -> bool:
        return bool(self.bot_admin.get(str(group_id), True))

    # -- 测试辅助 ------------------------------------------------------
    def messages_to(self, group_id: str) -> list[Message]:
        return [
            item.message
            for item in self.sent
            if item.target == "group" and item.group_id == str(group_id)
        ]

    def private_to(self, qq: str) -> list[Message]:
        return [
            item.message
            for item in self.sent
            if item.target == "private" and item.user_id == str(qq)
        ]

    def last_group_message(self, group_id: str) -> Message | None:
        messages = self.messages_to(group_id)
        return messages[-1] if messages else None

    def clear(self) -> None:
        self.sent.clear()
        self.group_send_error = None
        self.private_send_error = None
