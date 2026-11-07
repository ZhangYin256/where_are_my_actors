"""pytest 公共夹具：临时数据库 + 内存网关 + 固定时钟。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from rehearsal.config import Settings
from rehearsal.db import Database, Repository
from rehearsal.fake import FakeGateway
from rehearsal.fields import encode_mention
from rehearsal.models import Member
from rehearsal.service import CommandContext, RehearsalService

TZ = ZoneInfo("Asia/Shanghai")
# 2026-11-07 是周六；2026-11-13 是周五
NOW = datetime(2026, 11, 7, 13, 0, tzinfo=TZ)


class FakeClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def set(self, value: datetime) -> None:
        self.value = value

    def advance(self, *, minutes: float = 0, hours: float = 0, days: float = 0) -> None:
        self.value += timedelta(minutes=minutes, hours=hours, days=days)


@dataclass
class Harness:
    service: RehearsalService
    repo: Repository
    db: Database
    gateway: FakeGateway
    clock: FakeClock
    troupe_a: object
    troupe_b: object

    async def send(
        self,
        text: str,
        *,
        qq: str = "10001",
        scope: str = "group",
        group_id: str | None = "100001",
        role: str = "owner",
        nickname: str = "张三",
    ) -> list:
        if text.startswith(self.service.prefix):
            text = text[len(self.service.prefix) :]
        if scope == "group" and group_id:
            text = self.encode_mentions(text, group_id)
        ctx = CommandContext(
            scope=scope,
            qq=qq,
            nickname=nickname,
            group_id=group_id if scope == "group" else None,
            text=text,
            sender_role=role if scope == "group" else None,
        )
        return await self.service.handle(ctx)

    async def text(self, *args, **kwargs) -> str:
        replies = await self.send(*args, **kwargs)
        return "\n".join(reply.message.plain_text() for reply in replies)

    def encode_mentions(self, text: str, group_id: str) -> str:
        members = sorted(
            self.gateway.groups.get(str(group_id), []),
            key=lambda member: len(member.display_name),
            reverse=True,
        )
        for member in members:
            text = text.replace(
                f"@{member.display_name}", encode_mention(member.qq, member.display_name)
            )
        return text


@pytest.fixture()
def app(tmp_path):
    database = Database(str(tmp_path / "rehearsal.db"))
    repo = Repository(database)
    groups = {
        "100001": [
            Member("10001", "张三", role="owner"),
            Member("10002", "李四", role="admin"),
            Member("10003", "王五", role="member"),
            Member("10004", "赵六", role="member"),
            Member("10005", "钱七", role="member"),
        ],
        "100002": [
            Member("10001", "张三", role="member"),
            Member("10002", "李四", role="owner"),
            Member("10003", "王五", role="member"),
        ],
    }
    gateway = FakeGateway(groups, {"100001": True, "100002": True})
    settings = Settings(
        admins=("10001",),
        send_interval=0.0,
        member_cache_seconds=0,
        allow_owner_init=True,
    )
    clock = FakeClock()
    service = RehearsalService(repo, gateway, settings, clock=clock)
    asyncio.run(service.startup())
    troupe_a = repo.create_troupe("剧组A", "100001", now=clock.value)
    troupe_b = repo.create_troupe("剧组B", "100002", now=clock.value)
    repo.add_role(troupe_a.id, "10001", "director", source="auto_group_admin")
    repo.add_role(troupe_a.id, "10002", "director", source="auto_group_admin")
    repo.add_role(troupe_b.id, "10002", "director", source="auto_group_admin")
    return Harness(
        service=service,
        repo=repo,
        db=database,
        gateway=gateway,
        clock=clock,
        troupe_a=troupe_a,
        troupe_b=troupe_b,
    )
