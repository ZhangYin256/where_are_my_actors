"""离线模拟器：不连接 QQ 也能完整体验机器人。

用法::

    python -m rehearsal.simulator --reset

进入后像在 QQ 里一样输入 ``#发布排练 ...``，用 ``/`` 开头的命令切换身份 / 群。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Settings
from .db import Database, Repository
from .fake import FakeGateway
from .fields import encode_mention
from .models import Member
from .service import CommandContext, RehearsalService

TZ = ZoneInfo("Asia/Shanghai")

GROUPS: dict[str, tuple[str, list[Member]]] = {
    "100001": (
        "示例剧组",
        [
            Member("10001", "张三", role="owner"),
            Member("10002", "李四", role="admin"),
            Member("10003", "王五", role="member"),
            Member("10004", "赵六", role="member"),
            Member("10005", "钱七", role="member"),
        ],
    ),
    "100002": (
        "话剧B组",
        [
            Member("10002", "李四", role="owner"),
            Member("10001", "张三", role="member"),
            Member("10003", "王五", role="member"),
        ],
    ),
}


class Simulator:
    def __init__(
        self,
        db_path: str = "data/simulator.db",
        *,
        now: datetime | None = None,
        reset: bool = False,
    ) -> None:
        path = Path(db_path)
        if reset and path.exists():
            for suffix in ("", "-wal", "-shm"):
                candidate = Path(str(path) + suffix)
                if candidate.exists():
                    candidate.unlink()
        self.db = Database(str(path))
        self.repo = Repository(self.db)
        self.now = now or datetime.now(TZ)
        if self.now.tzinfo is None:
            self.now = self.now.replace(tzinfo=TZ)
        self.gateway = FakeGateway(
            {group_id: list(members) for group_id, (_, members) in GROUPS.items()},
            dict.fromkeys(GROUPS, True),
        )
        self.settings = Settings(
            admins=("10001",),
            send_interval=0.0,
            member_cache_seconds=0,
            allow_owner_init=True,
        )
        self.service = RehearsalService(
            self.repo, self.gateway, self.settings, clock=lambda: self.now
        )
        asyncio.run(self.service.startup())
        self._seed()
        self.qq = "10001"
        self.scope = "group"
        self.group_id = "100001"

    # ------------------------------------------------------------------
    def _seed(self) -> None:
        troupe_a = self.repo.get_troupe_by_group("100001")
        if troupe_a is None:
            troupe_a = self.repo.create_troupe("示例剧组", "100001", now=self.now)
        troupe_b = self.repo.get_troupe_by_group("100002")
        if troupe_b is None:
            troupe_b = self.repo.create_troupe("话剧B组", "100002", now=self.now)
        self.repo.add_role(troupe_a.id, "10001", "director", source="auto_group_admin")
        self.repo.add_role(troupe_a.id, "10002", "director", source="auto_group_admin")
        self.repo.add_role(troupe_b.id, "10002", "director", source="auto_group_admin")
        self.repo.upsert_actor(troupe_a.id, "10003", "王五", aliases=["小五"], remark_tags=["湖区"])
        self.repo.upsert_actor(troupe_a.id, None, "制作组")
        self.repo.upsert_actor(troupe_b.id, "10001", "张三")
        self.repo.upsert_actor(troupe_b.id, "10003", "王五")

    # ------------------------------------------------------------------
    def members(self) -> list[Member]:
        return self.gateway.groups.get(self.group_id, [])

    def all_members(self) -> list[Member]:
        result: dict[str, Member] = {}
        for members in self.gateway.groups.values():
            for member in members:
                result.setdefault(member.qq, member)
        return list(result.values())

    def current_user(self) -> Member:
        for member in self.all_members():
            if member.qq == self.qq:
                return member
        return Member(self.qq, self.qq)

    def prompt(self) -> str:
        scope = f"群 {self.group_id}" if self.scope == "group" else "私聊"
        return f"[{scope} · {self.current_user().display_name}] # "

    # ------------------------------------------------------------------
    def encode_mentions(self, text: str) -> str:
        pool = self.members() if self.scope == "group" else self.all_members()
        for member in sorted(pool, key=lambda item: len(item.display_name), reverse=True):
            text = text.replace(
                f"@{member.display_name}", encode_mention(member.qq, member.display_name)
            )
        return text

    def handle_line(self, line: str) -> str:
        stripped = line.strip()
        if not stripped:
            return ""
        if stripped.startswith("/"):
            return self._handle_slash(stripped)
        if stripped.startswith("#"):
            return asyncio.run(self._send(stripped))
        return "（普通聊天已忽略；机器人只响应 # 开头的命令，输入 /help 查看模拟器指令）"

    async def _send(self, line: str) -> str:
        prefix = self.service.prefix
        text = line[len(prefix) :] if line.startswith(prefix) else line
        text = self.encode_mentions(text)
        context = CommandContext(
            scope=self.scope,
            qq=self.qq,
            nickname=self.current_user().display_name,
            group_id=self.group_id if self.scope == "group" else None,
            text=text,
            sender_role=self.current_user().role if self.scope == "group" else None,
        )
        before = len(self.gateway.sent)
        replies = await self.service.handle(context)
        outputs: list[str] = []
        for sent in self.gateway.sent[before:]:
            if sent.target == "group":
                outputs.append(f"[机器人 → 群 {sent.group_id}]\n{sent.message.plain_text()}")
            else:
                outputs.append(f"[机器人 → 私聊 {sent.user_id}]\n{sent.message.plain_text()}")
        for reply in replies:
            if reply.group_id:
                outputs.append(f"[机器人 → 群 {reply.group_id}]\n{reply.message.plain_text()}")
            else:
                outputs.append(f"[机器人 → 私聊 {reply.user_id}]\n{reply.message.plain_text()}")
        return "\n\n".join(outputs).rstrip()

    def _handle_slash(self, line: str) -> str:
        parts = line[1:].split(maxsplit=1)
        command = parts[0].lower() if parts else ""
        argument = parts[1].strip() if len(parts) > 1 else ""
        if command in ("quit", "exit", "q"):
            return "__QUIT__"
        if command == "help":
            return (
                "模拟器指令：\n"
                "/as 名字或QQ     切换当前用户\n"
                "/group 群号      切换到群聊\n"
                "/private         切换到私聊\n"
                "/members         查看当前群成员\n"
                "/whoami          查看当前身份\n"
                "/now             查看模拟时间\n"
                "/advance 30m     推进时间（m 分钟 / h 小时 / d 天）\n"
                "/tick            立即执行一次提醒检查\n"
                "/quit            退出\n\n"
                "业务命令直接以 # 开头，例如：\n"
                "#发布排练\n时间=周五7~10\n地点=一排\n人员=@张三 @李四 制作组"
            )
        if command == "as":
            target = argument.lstrip("@")
            for member in self.all_members():
                if target in (member.qq, member.display_name, member.nickname):
                    self.qq = member.qq
                    return f"当前用户：{member.display_name}（{member.qq}）"
            return f"未找到用户：{argument}"
        if command in ("group", "g"):
            if argument and argument not in self.gateway.groups:
                return "可用群：" + "、".join(f"{gid}（{GROUPS[gid][0]}）" for gid in GROUPS)
            if argument:
                self.group_id = argument
            self.scope = "group"
            return f"已切换到群 {self.group_id}（{GROUPS[self.group_id][0]}）"
        if command in ("private", "p"):
            self.scope = "private"
            return "已切换到私聊"
        if command == "members":
            lines = [f"群 {self.group_id} 成员："]
            for member in self.members():
                lines.append(f"- {member.display_name}（{member.qq}，{member.role}）")
            return "\n".join(lines)
        if command == "whoami":
            member = self.current_user()
            return (
                f"用户：{member.display_name}（{member.qq}）\n"
                f"场景：{'群 ' + self.group_id if self.scope == 'group' else '私聊'}"
            )
        if command == "now":
            return f"模拟时间：{self.now:%Y-%m-%d %H:%M:%S}（{TZ}）"
        if command == "advance":
            amount = argument or "30m"
            unit = amount[-1].lower() if amount else "m"
            try:
                number = float(amount[:-1] if unit in "mhd" else amount)
            except ValueError:
                return "格式示例：/advance 30m、/advance 2h、/advance 1d"
            if unit == "h":
                self.now += timedelta(hours=number)
            elif unit == "d":
                self.now += timedelta(days=number)
            else:
                self.now += timedelta(minutes=number)
            return f"模拟时间已推进到：{self.now:%Y-%m-%d %H:%M}"
        if command == "tick":
            before = len(self.gateway.sent)
            count = asyncio.run(self.service.run_due_reminders())
            lines = [f"已执行提醒检查，处理 {count} 条任务。"]
            for sent in self.gateway.sent[before:]:
                target = (
                    f"群 {sent.group_id}" if sent.target == "group" else f"私聊 {sent.user_id}"
                )
                lines.append(f"[机器人 → {target}]\n{sent.message.plain_text()}")
            return "\n\n".join(lines)
        return f"未知模拟器指令：/{command}，输入 /help 查看。"

    # ------------------------------------------------------------------
    def run_script(self, lines: list[str]) -> list[str]:
        """执行脚本。

        空行分隔命令块；块内多行会作为同一条消息发送（模拟 QQ 多行消息）。
        以 ``//`` 开头的行为注释。
        """
        blocks: list[list[str]] = []
        current: list[str] = []
        for raw in lines:
            line = raw.rstrip()
            stripped = line.strip()
            if not stripped or stripped.startswith("//"):
                if current:
                    blocks.append(current)
                    current = []
                continue
            if stripped.startswith("/") and current:
                blocks.append(current)
                current = []
            current.append(line)
        if current:
            blocks.append(current)

        transcript: list[str] = []
        for block in blocks:
            command = "\n".join(block)
            output = self.handle_line(command)
            if output == "__QUIT__":
                break
            echoed = command.replace("\n", "\n> ")
            transcript.append(f"> {echoed}\n{output}".rstrip())
        return transcript

    def run(self) -> None:
        print("排练通知机器人 · 离线模拟器")
        print("输入 #帮助 查看命令；输入 /help 查看模拟器指令；/quit 退出。\n")
        while True:
            try:
                line = input(self.prompt())
            except (EOFError, KeyboardInterrupt):
                print()
                break
            output = self.handle_line(line)
            if output == "__QUIT__":
                break
            if output:
                print(output + "\n")


def _parse_now(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=TZ)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="排练通知机器人离线模拟器")
    parser.add_argument("--db", default="data/simulator.db", help="模拟器数据库路径")
    parser.add_argument("--now", help="固定当前时间，如 2026-11-07T13:00:00")
    parser.add_argument("--reset", action="store_true", help="重置模拟器数据库")
    parser.add_argument("--script", help="从文件读取命令并输出完整对话")
    args = parser.parse_args(argv)
    simulator = Simulator(args.db, now=_parse_now(args.now), reset=args.reset)
    if args.script:
        lines = Path(args.script).read_text(encoding="utf-8").splitlines()
        for block in simulator.run_script(lines):
            print(block + "\n")
        return 0
    simulator.run()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
