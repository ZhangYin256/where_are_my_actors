"""OneBot V11 网关实现。"""

from __future__ import annotations

import asyncio

import nonebot
from nonebot.adapters.onebot.v11 import Bot

from rehearsal.models import Member, Message, split_message
from rehearsal.onebot import to_onebot


class OneBotGateway:
    """按需获取当前已登录的机器人账号。"""

    def __init__(
        self, *, max_message_chars: int = 1500, send_interval: float = 1.0
    ) -> None:
        self.max_message_chars = max(500, int(max_message_chars))
        self.send_interval = max(0.0, float(send_interval))
        self._bound: Bot | None = None

    def bind(self, bot: Bot) -> None:
        self._bound = bot

    def _bot(self) -> Bot:
        if self._bound is not None:
            return self._bound
        bots = nonebot.get_bots()
        if not bots:
            raise RuntimeError("没有已连接的 QQ 账号，请检查 NapCat 是否已登录")
        return next(iter(bots.values()))

    @property
    def self_id(self) -> str:
        try:
            return str(self._bot().self_id)
        except RuntimeError:
            return ""

    async def send_group(self, group_id: str, message: Message) -> str | None:
        bot = self._bot()
        message_ids: list[str] = []
        for index, chunk in enumerate(split_message(message, self.max_message_chars)):
            if index and self.send_interval:
                await asyncio.sleep(self.send_interval)
            result = await bot.send_group_msg(group_id=int(group_id), message=to_onebot(chunk))
            if isinstance(result, dict) and result.get("message_id") is not None:
                message_ids.append(str(result["message_id"]))
        return message_ids[-1] if message_ids else None

    async def send_private(self, qq: str, message: Message) -> str | None:
        bot = self._bot()
        message_ids: list[str] = []
        for index, chunk in enumerate(split_message(message, self.max_message_chars)):
            if index and self.send_interval:
                await asyncio.sleep(self.send_interval)
            result = await bot.send_private_msg(user_id=int(qq), message=to_onebot(chunk))
            if isinstance(result, dict) and result.get("message_id") is not None:
                message_ids.append(str(result["message_id"]))
        return message_ids[-1] if message_ids else None

    async def get_group_member_list(self, group_id: str) -> list[Member]:
        bot = self._bot()
        data = await bot.get_group_member_list(group_id=int(group_id))
        members: list[Member] = []
        for item in data or []:
            members.append(
                Member(
                    qq=str(item.get("user_id", "")),
                    nickname=str(item.get("nickname", "")),
                    card=str(item.get("card", "") or ""),
                    role=str(item.get("role", "member")),
                )
            )
        return members

    async def get_group_member_info(self, group_id: str, qq: str) -> Member | None:
        bot = self._bot()
        try:
            data = await bot.get_group_member_info(
                group_id=int(group_id), user_id=int(qq), no_cache=False
            )
        except Exception:
            return None
        if not data:
            return None
        return Member(
            qq=str(data.get("user_id", qq)),
            nickname=str(data.get("nickname", "")),
            card=str(data.get("card", "") or ""),
            role=str(data.get("role", "member")),
        )

    async def bot_is_group_admin(self, group_id: str) -> bool:
        bot = self._bot()
        info = await self.get_group_member_info(str(group_id), str(bot.self_id))
        return bool(info and info.role in ("owner", "admin"))
