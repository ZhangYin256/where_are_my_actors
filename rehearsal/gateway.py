"""消息网关接口。

业务层只依赖该协议，便于在没有 QQ 环境时使用模拟器 / 单元测试。
具体实现见 ``plugins/rehearsal_bot/gateway.py``（OneBot V11）与
``rehearsal/fake.py``（模拟器）。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import Member, Message


@runtime_checkable
class Gateway(Protocol):
    """平台能力抽象。"""

    self_id: str

    async def send_group(self, group_id: str, message: Message) -> str | None:
        """发送群消息，返回消息 ID；失败时抛出异常。"""

    async def send_private(self, qq: str, message: Message) -> str | None:
        """发送私聊消息，返回消息 ID；失败时抛出异常。"""

    async def get_group_member_list(self, group_id: str) -> list[Member]:
        """获取群成员列表；失败时抛出异常。"""

    async def get_group_member_info(self, group_id: str, qq: str) -> Member | None:
        """获取单个群成员信息；不存在或失败返回 None。"""

    async def bot_is_group_admin(self, group_id: str) -> bool:
        """机器人在群内是否为管理员 / 群主。"""
