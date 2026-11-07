"""业务服务层：命令分发、权限校验、排练生命周期、提醒发送。"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from datetime import time as dtime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import help as help_texts
from .config import Settings
from .db import Repository
from .errors import ConfigError, NotFound, PermissionDenied, StateError, UserError
from .fields import (
    MENTION_TOKEN_RE,
    PersonInfo,
    clean_value,
    format_remind_minutes,
    is_empty_value,
    parse_fields,
    parse_participants,
    parse_remind_minutes,
    parse_scope,
    split_content,
)
from .gateway import Gateway
from .models import (
    Member,
    Message,
    Participant,
    PendingAction,
    Rehearsal,
    RehearsalDraft,
    Troupe,
)
from .permissions import can_grant, highest_role, require, role_label
from .render import (
    audit_message,
    cancel_notice_message,
    config_message,
    detail_message,
    list_message,
    mention_continuation,
    my_schedule_message,
    notice_message,
    preview_message,
    private_reminder_message,
    reminder_message,
    troupe_info_message,
)
from .timeparse import format_notice_time, format_preview_time, parse_time
from .timeutil import iso, now_utc, parse_iso

log = logging.getLogger(__name__)

CONFIRM_KINDS = {
    "确认发布": "publish",
    "确认修改": "modify",
    "确认重发": "republish",
}
CANCEL_KINDS = {
    "取消发布": "publish",
    "取消修改": "modify",
    "取消重发": "republish",
}
KIND_LABELS = {"publish": "发布", "modify": "修改", "republish": "重发"}

# 需要“管理权限”的命令：私聊时只在本人的管理剧组中自动选择
_MANAGE_COMMANDS = {
    "修改剧组名称",
    "解绑剧组",
    "授权导演",
    "取消导演",
    "授权副导演",
    "取消副导演",
    "添加演员",
    "移除演员",
    "演员列表",
    "演员别名",
    "演员标签",
    "发布排练",
    "排练通知",
    "排练修改",
    "排练重发",
    "取消排练",
    "删除排练",
    "彻底删除",
    "确认取消",
    "确认删除",
    "取消删除",
    "提醒默认",
    "配置查看",
    "配置",
    "审计日志",
    "同步成员",
}

_TROUPE_ARG_RE = re.compile(r"(?:^|\s)剧组\s*[=:：]\s*(?P<name>[^\s]+)")

_CONFIG_ALIASES = {
    "默认提醒": "default_remind",
    "提醒默认": "default_remind",
    "私聊提醒": "private_remind",
    "晚上阈值": "threshold",
    "显示标签": "show_tags",
    "显示空字段": "show_empty",
    "发布确认": "publish_confirm",
    "降级范围": "degrade_scope",
    "时区": "timezone",
}
_GLOBAL_CONFIG_ALIASES = {
    "发送间隔": "send_interval",
    "重试次数": "max_attempts",
    "提醒宽限": "grace_seconds",
    "分段人数": "at_chunk_size",
}

_TRUE_WORDS = {"开", "开启", "是", "true", "1", "on", "yes", "显示"}
_FALSE_WORDS = {"关", "关闭", "否", "false", "0", "off", "no", "不显示"}


@dataclass(slots=True)
class CommandContext:
    scope: str  # group / private
    qq: str
    nickname: str = ""
    group_id: str | None = None
    text: str = ""
    mentions: list = field(default_factory=list)
    sender_role: str | None = None  # owner / admin / member（群消息）


@dataclass(slots=True)
class Reply:
    message: Message
    group_id: str | None = None
    user_id: str | None = None

    @property
    def target(self) -> str:
        return "group" if self.group_id else "private"


def _bool_value(text: str, label: str) -> bool:
    value = text.strip().casefold()
    if value in _TRUE_WORDS:
        return True
    if value in _FALSE_WORDS:
        return False
    raise ConfigError(f"{label} 只能是 开 / 关，收到：{text}")


def _draft_to_payload(draft: RehearsalDraft) -> dict:
    return {
        "start_at": iso(draft.start_at),
        "end_at": iso(draft.end_at),
        "location": draft.location,
        "content": list(draft.content),
        "remark": draft.remark,
        "participants": [
            {
                "qq_id": p.qq_id,
                "display_name": p.display_name,
                "remark_tag": p.remark_tag,
                "precise": p.precise,
                "note": p.note,
            }
            for p in draft.participants
        ],
        "participant_mode": draft.participant_mode,
        "notify_all": draft.notify_all,
        "remind_minutes": list(draft.remind_minutes),
        "raw_text": draft.raw_text,
        "notes": list(draft.notes),
    }


def _draft_from_payload(payload: dict) -> RehearsalDraft:
    return RehearsalDraft(
        start_at=parse_iso(payload.get("start_at")) or now_utc(),
        end_at=parse_iso(payload.get("end_at")) or now_utc(),
        location=payload.get("location", ""),
        content=list(payload.get("content", [])),
        remark=payload.get("remark", ""),
        participants=[
            Participant(
                qq_id=item.get("qq_id"),
                display_name=item.get("display_name", ""),
                remark_tag=item.get("remark_tag", ""),
                precise=bool(item.get("precise")),
                note=item.get("note", ""),
            )
            for item in payload.get("participants", [])
        ],
        participant_mode=payload.get("participant_mode", "specified"),
        notify_all=bool(payload.get("notify_all")),
        remind_minutes=tuple(int(m) for m in payload.get("remind_minutes", [])),
        raw_text=payload.get("raw_text", ""),
        notes=list(payload.get("notes", [])),
    )


def _draft_from_rehearsal(rehearsal: Rehearsal) -> RehearsalDraft:
    return RehearsalDraft(
        start_at=rehearsal.start_at,
        end_at=rehearsal.end_at,
        location=rehearsal.location,
        content=list(rehearsal.content),
        remark=rehearsal.remark,
        participants=[
            Participant(
                qq_id=p.qq_id,
                display_name=p.display_name,
                remark_tag=p.remark_tag,
                precise=p.precise,
                note=p.note,
            )
            for p in rehearsal.participants
        ],
        participant_mode=rehearsal.participant_mode,
        notify_all=rehearsal.notify_all,
        remind_minutes=rehearsal.remind_minutes,
        raw_text=rehearsal.raw_text,
    )


class RehearsalService:
    def __init__(
        self,
        repo: Repository,
        gateway: Gateway,
        settings: Settings,
        *,
        clock=None,
    ) -> None:
        self.repo = repo
        self.gateway = gateway
        self.settings = settings
        self._clock = clock or now_utc
        self._member_cache: dict[str, tuple[float, list[Member]]] = {}
        self._admin_cache: dict[str, tuple[float, bool]] = {}
        self._last_send = 0.0
        self._send_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 基础
    # ------------------------------------------------------------------
    def now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @property
    def prefix(self) -> str:
        return self.settings.command_prefix

    def _reply(self, ctx: CommandContext, message: Message | str) -> Reply:
        if isinstance(message, str):
            message = Message.text(message)
        if ctx.scope == "group":
            return Reply(message=message, group_id=ctx.group_id)
        return Reply(message=message, user_id=ctx.qq)

    def _name_of(self, qq: str) -> str:
        row = self.repo.get_user(str(qq))
        if row and row["nickname"]:
            return row["nickname"]
        return str(qq)

    async def startup(self) -> None:
        self.repo.recover_inflight_jobs()
        for admin in self.settings.admins:
            if not self.repo.has_admin_record(admin):
                self.repo.add_admin(admin, granted_by="config", source="config")
                log.info("已从配置写入初始管理员：%s", admin)

    def maintenance(self) -> None:
        now = self.now()
        self.repo.expire_pending(now)
        self.repo.mark_completed(now)

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------
    async def handle(self, ctx: CommandContext) -> list[Reply]:
        ctx.qq = str(ctx.qq)
        if ctx.nickname:
            self.repo.upsert_user(ctx.qq, ctx.nickname)
        try:
            return await self._dispatch(ctx)
        except UserError as exc:
            return [self._reply(ctx, str(exc))]
        except Exception:
            log.exception("处理命令失败：%r", ctx)
            return [self._reply(ctx, "处理命令时出现内部错误，请稍后重试或联系管理员。")]

    async def _dispatch(self, ctx: CommandContext) -> list[Reply]:
        text = (ctx.text or "").strip()
        if not text:
            return []
        match = re.match(r"^(\S+)\s*(.*)$", text, re.S)
        if not match:
            return []
        cmd = match.group(1).strip()
        rest = match.group(2).strip()

        if cmd in ("帮助", "命令列表"):
            return await self._cmd_help(ctx, rest if cmd == "帮助" else "")
        if cmd.startswith("帮助") and len(cmd) > 2:
            return await self._cmd_help(ctx, cmd[2:])
        if cmd == "初始化剧组":
            return await self._cmd_init_troupe(ctx, rest)
        if cmd == "我的排练":
            return await self._cmd_my_schedule(ctx, rest)
        if cmd in ("授权管理员", "取消管理员"):
            return await self._cmd_global_admin(ctx, cmd, rest)
        if cmd in ("全局配置", "全局配置查看"):
            return await self._cmd_global_config(ctx, cmd, rest)
        if cmd in CONFIRM_KINDS or cmd in CANCEL_KINDS:
            return await self._cmd_pending(ctx, cmd, rest)

        troupe, rest = self._resolve_troupe(
            ctx, rest, manage_only=cmd in _MANAGE_COMMANDS
        )
        self._sync_sender_role(troupe, ctx)
        self._check_active(troupe, cmd)

        if cmd in ("剧组信息",):
            return await self._cmd_troupe_info(ctx, troupe)
        if cmd in ("修改剧组名称",):
            return await self._cmd_rename_troupe(ctx, troupe, rest)
        if cmd == "解绑剧组":
            return await self._cmd_unbind_troupe(ctx, troupe)
        if cmd in ("授权导演", "取消导演", "授权副导演", "取消副导演"):
            role = "director" if "导演" in cmd and "副" not in cmd else "assistant"
            return await self._cmd_grant(ctx, troupe, rest, role=role, grant=not cmd.startswith("取消"))
        if cmd in ("添加演员", "移除演员", "演员列表", "演员别名", "演员标签"):
            return await self._cmd_actor(ctx, troupe, cmd, rest)
        if cmd in ("发布排练", "排练通知"):
            if not rest:
                if cmd == "排练通知" and ctx.scope == "group":
                    return []
                return [self._reply(ctx, help_texts.topic("发布排练", self.prefix) or "")]
            return await self._cmd_publish(ctx, troupe, rest)
        if cmd in ("排练列表", "排练历史"):
            return await self._cmd_list(ctx, troupe, rest, history=cmd == "排练历史")
        if cmd == "排练详情":
            return await self._cmd_detail(ctx, troupe, rest)
        if cmd == "排练修改":
            return await self._cmd_modify(ctx, troupe, rest)
        if cmd == "排练重发":
            return await self._cmd_republish(ctx, troupe, rest)
        if cmd == "取消排练":
            return await self._cmd_cancel(ctx, troupe, rest)
        if cmd == "删除排练":
            return await self._cmd_delete(ctx, troupe, rest)
        if cmd == "彻底删除":
            return await self._cmd_hard_delete(ctx, troupe, rest)
        if cmd == "确认取消":
            return await self._cmd_confirm_cancel(ctx, troupe, rest)
        if cmd in ("确认删除", "取消删除"):
            return await self._cmd_confirm_delete(ctx, troupe, cmd, rest)
        if cmd == "提醒默认":
            return await self._cmd_default_remind(ctx, troupe, rest)
        if cmd == "配置查看":
            return await self._cmd_config_view(ctx, troupe)
        if cmd == "配置":
            return await self._cmd_config_set(ctx, troupe, rest)
        if cmd == "审计日志":
            return await self._cmd_audit(ctx, troupe, rest)
        if cmd == "同步成员":
            return await self._cmd_sync_members(ctx, troupe)

        if ctx.scope == "private":
            return [self._reply(ctx, f"未知命令。发送 {self.prefix}帮助 查看可用命令。")]
        return []

    def _check_active(self, troupe: Troupe, cmd: str) -> None:
        if troupe.status == "active":
            return
        if cmd in ("剧组信息", "排练列表", "排练历史", "排练详情", "审计日志"):
            return
        raise StateError("该剧组已解绑，不能再发布或管理排练，仅可查询历史数据。")

    # ------------------------------------------------------------------
    # 剧组解析 / 权限
    # ------------------------------------------------------------------
    def _candidate_troupes(self, qq: str, *, manage_only: bool = False) -> list[Troupe]:
        if self.repo.is_admin(qq):
            return self.repo.list_active_troupes()
        if manage_only:
            return self.repo.list_troupes_for_management(qq)
        return self.repo.list_troupes_for_user(qq)

    def _extract_troupe_arg(self, text: str) -> tuple[str | None, str]:
        match = _TROUPE_ARG_RE.search(text or "")
        if not match:
            return None, text
        name = match.group("name")
        stripped = (text[: match.start()] + " " + text[match.end() :]).strip()
        return name, stripped

    def _resolve_troupe(
        self, ctx: CommandContext, rest: str, *, manage_only: bool = False
    ) -> tuple[Troupe, str]:
        name, rest = self._extract_troupe_arg(rest)
        if ctx.scope == "group":
            troupe = self.repo.get_troupe_by_group(ctx.group_id or "")
            if troupe is None:
                raise NotFound(
                    f"本群尚未绑定剧组。管理员可发送：{self.prefix}初始化剧组 剧组名称"
                )
            return troupe, rest
        if name:
            exact = self.repo.get_troupe_by_name(name)
            if exact:
                return exact, rest
            matches = self.repo.find_troupes_by_name(name)
            if not matches:
                raise NotFound(f"未找到剧组「{name}」。")
            if len(matches) > 1:
                raise UserError(
                    "存在多个名称相近的剧组，请输入完整名称：\n"
                    + "\n".join(f"- {t.name}" for t in matches[:10])
                )
            return matches[0], rest
        candidates = self._candidate_troupes(ctx.qq, manage_only=manage_only)
        if len(candidates) == 1:
            return candidates[0], rest
        if not candidates:
            if manage_only:
                raise NotFound(
                    "你还没有管理任何剧组（需要导演或副导演角色）。\n"
                    "请先在剧组群内使用，或联系管理员授权。"
                )
            raise NotFound(
                "你还没有加入任何剧组。请先在剧组群内使用机器人，或联系管理员添加。"
            )
        if manage_only:
            raise UserError(
                "你管理多个剧组，请用 剧组=名称 指定：\n"
                + "\n".join(f"- {t.name}" for t in candidates[:10])
                + f"\n例如：{self.prefix}发布排练 剧组={candidates[0].name} 时间=周五7~10 ..."
            )
        raise UserError(
            "你参与了多个剧组，请用 剧组=名称 指定：\n"
            + "\n".join(f"- {t.name}" for t in candidates[:10])
            + f"\n例如：{self.prefix}排练列表 剧组={candidates[0].name} 本周"
        )

    def _role(self, troupe: Troupe, qq: str) -> str | None:
        if self.repo.is_admin(qq):
            return "admin"
        return highest_role(r.role for r in self.repo.get_roles(troupe.id, qq))

    def _display_role(self, troupe: Troupe, ctx: CommandContext) -> str | None:
        role = self._role(troupe, ctx.qq)
        if role:
            return role
        if ctx.scope == "group" or self.repo.is_registered_in_troupe(troupe.id, ctx.qq):
            return "actor"
        return None

    def _require(self, troupe: Troupe, qq: str, capability: str) -> str:
        role = self._role(troupe, qq)
        require(role, capability)
        return role or "actor"

    def _sync_sender_role(self, troupe: Troupe, ctx: CommandContext) -> None:
        if ctx.scope != "group" or not ctx.sender_role:
            return
        roles = self.repo.get_roles(troupe.id, ctx.qq)
        auto = any(r.role == "director" and r.source == "auto_group_admin" for r in roles)
        if ctx.sender_role in ("owner", "admin") and not auto:
            self.repo.add_role(
                troupe.id, ctx.qq, "director", source="auto_group_admin", granted_by="system"
            )
        elif ctx.sender_role == "member" and auto:
            self.repo.remove_role(troupe.id, ctx.qq, "director", source="auto_group_admin")

    async def sync_group_roles(self, group_id: str) -> tuple[int, int, int]:
        troupe = self.repo.get_troupe_by_group(str(group_id))
        if troupe is None:
            raise NotFound("本群尚未绑定剧组。")
        try:
            members = await self.gateway.get_group_member_list(str(group_id))
        except Exception as exc:
            log.warning("获取群成员失败：%s", exc)
            raise StateError(f"获取群成员失败：{exc}") from exc
        for member in members:
            if member.nickname:
                self.repo.upsert_user(member.qq, member.nickname)
        auto = [m.qq for m in members if m.role in ("owner", "admin")]
        added, removed = self.repo.set_auto_directors(troupe.id, auto)
        if added or removed:
            log.info("剧组 %s 同步自动导演：+%d -%d", troupe.name, added, removed)
        return added, removed, len(members)

    async def sync_all_groups(self) -> None:
        """定时全量同步群主 / 群管理员身份。"""
        for troupe in self.repo.list_active_troupes():
            try:
                await self.sync_group_roles(troupe.group_id)
            except Exception as exc:  # 单个群失败不影响其他群
                log.warning("同步剧组 %s 失败：%s", troupe.name, exc)

    async def _group_members(self, group_id: str) -> list[Member]:
        cached = self._member_cache.get(str(group_id))
        now = time.monotonic()
        if cached and now - cached[0] < self.settings.member_cache_seconds:
            return cached[1]
        try:
            members = await self.gateway.get_group_member_list(str(group_id))
        except Exception as exc:  # 风控或未进群时不应阻塞发布
            log.warning("获取群成员失败（将仅使用演员表匹配）：%s", exc)
            return cached[1] if cached else []
        self._member_cache[str(group_id)] = (now, members)
        return members

    async def _bot_is_admin(self, group_id: str) -> bool:
        cached = self._admin_cache.get(str(group_id))
        now = time.monotonic()
        if cached and now - cached[0] < 300:
            return cached[1]
        try:
            value = await self.gateway.bot_is_group_admin(str(group_id))
        except Exception as exc:
            log.warning("查询机器人群权限失败：%s", exc)
            value = False
        self._admin_cache[str(group_id)] = (now, value)
        return value

    def _resolver(self, troupe: Troupe, members: list[Member]):
        member_map = {m.qq: m for m in members}

        def resolve(name: str) -> PersonInfo | None:
            keyword = (name or "").strip().lstrip("@")
            if not keyword:
                return None
            if keyword.isdigit() and len(keyword) >= 5:
                member = member_map.get(keyword)
                return PersonInfo(
                    qq=keyword,
                    display_name=member.display_name if member else keyword,
                )
            actor = self.repo.find_actor_by_name(troupe.id, keyword)
            if actor and actor.qq_id:
                tag = actor.remark_tags[0] if len(actor.remark_tags) == 1 else ""
                return PersonInfo(actor.qq_id, actor.display_name, tag)
            for member in members:
                if keyword in (member.display_name, member.nickname, member.qq):
                    return PersonInfo(member.qq, member.display_name)
            return None

        return resolve

    async def _parse_people(
        self, troupe: Troupe, text: str
    ) -> tuple[str, list[Participant], list[str]]:
        members = await self._group_members(troupe.group_id)
        result = parse_participants(text, self._resolver(troupe, members))
        if result.all_flag:
            return "all", [], []
        member_map = {m.qq: m for m in members}
        for participant in result.participants:
            if not participant.precise or not participant.qq_id:
                continue
            if not participant.display_name or participant.display_name == participant.qq_id:
                member = member_map.get(participant.qq_id)
                if member:
                    participant.display_name = member.display_name
                else:
                    row = self.repo.get_user(participant.qq_id)
                    if row and row["nickname"]:
                        participant.display_name = row["nickname"]
            if not participant.remark_tag:
                actor = self.repo.get_actor(troupe.id, participant.qq_id)
                if actor and len(actor.remark_tags) == 1:
                    participant.remark_tag = actor.remark_tags[0]
        return "specified", result.participants, []

    # ------------------------------------------------------------------
    # 帮助 / 剧组信息
    # ------------------------------------------------------------------
    def _display_name(self, qq: str) -> str:
        """仅返回昵称；未知时返回空串，避免向普通成员泄露 QQ。"""
        row = self.repo.get_user(str(qq))
        if row and row["nickname"]:
            return row["nickname"]
        return ""

    def _audit_troupe_id(self, ctx: CommandContext) -> int | None:
        if ctx.scope == "group" and ctx.group_id:
            troupe = self.repo.get_troupe_by_group(ctx.group_id)
            if troupe is not None:
                return troupe.id
        return None

    async def _cmd_help(self, ctx: CommandContext, topic: str) -> list[Reply]:
        troupe = (
            self.repo.get_troupe_by_group(ctx.group_id or "")
            if ctx.scope == "group"
            else None
        )
        role = self._display_role(troupe, ctx) if troupe else ("admin" if self.repo.is_admin(ctx.qq) else None)
        content = None
        if topic:
            content = help_texts.topic(topic, self.prefix)
            if content is None:
                content = (
                    f"没有找到「{topic}」相关的帮助。\n\n"
                    + help_texts.overview(
                        self.prefix,
                        troupe_name=troupe.name if troupe else None,
                        role=role,
                        is_private=ctx.scope == "private",
                    )
                )
        else:
            content = help_texts.overview(
                self.prefix,
                troupe_name=troupe.name if troupe else None,
                role=role,
                is_private=ctx.scope == "private",
            )
        return [self._reply(ctx, content)]

    async def _cmd_troupe_info(self, ctx: CommandContext, troupe: Troupe) -> list[Reply]:
        role = self._display_role(troupe, ctx)
        if ctx.scope == "private" and not (
            role or self.repo.is_registered_in_troupe(troupe.id, ctx.qq)
        ):
            raise PermissionDenied("你不在该剧组中，无法查看剧组信息。")
        message = troupe_info_message(
            troupe,
            self.now(),
            role_label=role_label(role),
            is_admin=self.repo.is_admin(ctx.qq),
        )
        return [self._reply(ctx, message)]

    async def _cmd_init_troupe(self, ctx: CommandContext, rest: str) -> list[Reply]:
        if ctx.scope != "group":
            raise StateError("请在 QQ 群内初始化剧组。")
        existing = self.repo.get_troupe_by_group(ctx.group_id or "")
        name = rest.strip()
        if len(name) > 30:
            raise UserError("剧组名称不能超过 30 个字符。")
        if existing:
            if existing.status == "archived" and self.repo.is_admin(ctx.qq):
                new_name = name or existing.name
                self.repo.update_troupe(existing.id, name=new_name, status="active")
                if ctx.sender_role in ("owner", "admin"):
                    self.repo.add_role(
                        existing.id,
                        ctx.qq,
                        "director",
                        source="auto_group_admin",
                        granted_by="system",
                    )
                try:
                    await self.sync_group_roles(existing.group_id)
                except Exception as exc:
                    log.warning("重新绑定后同步群成员失败：%s", exc)
                self.repo.add_audit(
                    existing.id,
                    ctx.qq,
                    "init_troupe",
                    target=new_name,
                    summary="重新绑定已解绑的剧组",
                )
                return [
                    self._reply(
                        ctx,
                        f"✅ 已重新绑定剧组「{new_name}」。\n"
                        "历史数据保留；如需恢复提醒，请对相关排练执行 "
                        f"{self.prefix}排练重发 编号。",
                    )
                ]
            raise StateError(
                f"本群已绑定剧组「{existing.name}」。\n如需改名，管理员可发送："
                f"{self.prefix}修改剧组名称 新名称"
            )
        if not name:
            raise UserError(f"请提供剧组名称，例如：{self.prefix}初始化剧组 某某剧组")
        allowed = self.repo.is_admin(ctx.qq) or (
            self.settings.allow_owner_init and ctx.sender_role == "owner"
        )
        if not allowed:
            raise PermissionDenied("只有管理员或群主可以初始化剧组。")
        now = self.now()
        troupe = self.repo.create_troupe(
            name,
            ctx.group_id or "",
            timezone=self.settings.timezone,
            default_remind_minutes=self.settings.default_remind_minutes,
            private_remind=self.settings.default_private_remind,
            ambiguous_evening_threshold=self.settings.default_evening_threshold,
            publish_confirm_when_clean=self.settings.default_publish_confirm,
            degrade_at_scope=self.settings.default_degrade_scope,
            now=now,
        )
        if ctx.sender_role in ("owner", "admin"):
            self.repo.add_role(
                troupe.id, ctx.qq, "director", source="auto_group_admin", granted_by="system"
            )
        try:
            await self.sync_group_roles(troupe.group_id)
        except Exception as exc:  # 初始化成功即可，同步失败稍后重试
            log.warning("初始化后同步群成员失败：%s", exc)
        self.repo.add_audit(
            troupe.id,
            ctx.qq,
            "init_troupe",
            target=troupe.name,
            summary=f"初始化剧组，绑定群 {troupe.group_id}",
            now=now,
        )
        role = role_label(self._role(troupe, ctx.qq))
        return [
            self._reply(
                ctx,
                f"✅ 剧组「{troupe.name}」已初始化。\n"
                f"绑定群：{troupe.group_id}\n"
                f"你的角色：{role}\n"
                f"默认提醒：{format_remind_minutes(troupe.reminders)}\n\n"
                f"发布第一条通知：\n{self.prefix}发布排练\n"
                f"时间=周五7~10\n地点=一排\n人员=@张三\n",
            )
        ]

    async def _cmd_rename_troupe(
        self, ctx: CommandContext, troupe: Troupe, rest: str
    ) -> list[Reply]:
        if not self.repo.is_admin(ctx.qq):
            raise PermissionDenied("只有管理员可以修改剧组名称。")
        name = rest.strip()
        if not name:
            raise UserError(f"请提供新名称，例如：{self.prefix}修改剧组名称 某某剧组")
        if len(name) > 30:
            raise UserError("剧组名称不能超过 30 个字符。")
        old = troupe.name
        self.repo.update_troupe(troupe.id, name=name)
        self.repo.add_audit(
            troupe.id, ctx.qq, "config", target="剧组名称", summary=f"{old} -> {name}"
        )
        return [self._reply(ctx, f"✅ 剧组名称已修改为「{name}」。")]

    async def _cmd_unbind_troupe(self, ctx: CommandContext, troupe: Troupe) -> list[Reply]:
        if not self.repo.is_admin(ctx.qq):
            raise PermissionDenied("只有管理员可以解绑剧组。")
        if troupe.status != "active":
            raise StateError("该剧组已经解绑。")
        self.repo.archive_troupe(troupe.id)
        self.repo.cancel_all_pending_jobs(troupe.id)
        self.repo.add_audit(
            troupe.id, ctx.qq, "unbind", target=troupe.name, summary="解绑剧组，停止提醒"
        )
        return [
            self._reply(
                ctx,
                f"✅ 剧组「{troupe.name}」已解绑。\n"
                "提醒任务已停止，历史数据保留；重新绑定需要管理员重新初始化。",
            )
        ]

    # ------------------------------------------------------------------
    # 角色
    # ------------------------------------------------------------------
    async def _extract_person(
        self, troupe: Troupe | None, text: str
    ) -> tuple[str | None, str, str]:
        match = MENTION_TOKEN_RE.search(text or "")
        if match:
            qq = match.group(1)
            name = match.group(2).strip()
            rest = (text[: match.start()] + " " + text[match.end() :]).strip()
            if not name and troupe is not None:
                members = await self._group_members(troupe.group_id)
                name = next((m.display_name for m in members if m.qq == qq), "")
            return qq, name or self._name_of(qq), rest
        number = re.search(r"(?<!\d)(\d{5,12})(?!\d)", text or "")
        if number:
            qq = number.group(1)
            rest = (text[: number.start()] + " " + text[number.end() :]).strip()
            return qq, self._name_of(qq), rest
        token = (text or "").strip().split(maxsplit=1)
        if not token:
            return None, "", ""
        name = token[0].lstrip("@")
        rest = token[1] if len(token) > 1 else ""
        if troupe is not None:
            actor = self.repo.find_actor_by_name(troupe.id, name)
            if actor and actor.qq_id:
                return actor.qq_id, actor.display_name, rest
            members = await self._group_members(troupe.group_id)
            for member in members:
                if name in (member.display_name, member.nickname, member.qq):
                    return member.qq, member.display_name, rest
        return None, name, rest

    async def _cmd_global_admin(
        self, ctx: CommandContext, cmd: str, rest: str
    ) -> list[Reply]:
        if not self.repo.is_admin(ctx.qq):
            raise PermissionDenied("只有管理员可以授权 / 取消管理员。")
        qq, name, _ = await self._extract_person(None, rest)
        if not qq:
            raise UserError(
                f"请 @ 目标成员或提供 QQ 号，例如：{self.prefix}授权管理员 123456789"
            )
        if cmd == "授权管理员":
            self.repo.add_admin(qq, granted_by=ctx.qq, source="manual")
            self.repo.add_audit(self._audit_troupe_id(ctx), ctx.qq, "grant_role", target=f"{name}({qq})", summary="管理员")
            return [self._reply(ctx, f"✅ 已授予 {name}（{qq}）管理员角色。")]
        admins = self.repo.list_admins()
        if qq == ctx.qq and len(admins) <= 1:
            raise StateError("不能取消唯一管理员的权限。")
        if not self.repo.is_admin(qq):
            raise StateError(f"{name} 不是管理员。")
        self.repo.remove_admin(qq)
        self.repo.add_audit(
            self._audit_troupe_id(ctx), ctx.qq, "revoke_role", target=f"{name}({qq})", summary="管理员"
        )
        return [self._reply(ctx, f"✅ 已取消 {name}（{qq}）的管理员角色。")]

    async def _cmd_grant(
        self,
        ctx: CommandContext,
        troupe: Troupe,
        rest: str,
        *,
        role: str,
        grant: bool,
    ) -> list[Reply]:
        capability = f"grant_{role}"
        self._require(troupe, ctx.qq, capability)
        actor_role = self._role(troupe, ctx.qq)
        if grant and not can_grant(actor_role, role):
            raise PermissionDenied(f"当前角色（{role_label(actor_role)}）不能授权该角色。")
        qq, name, _ = await self._extract_person(troupe, rest)
        if not qq:
            target = "导演" if role == "director" else "副导演"
            raise UserError(
                f"请 @ 目标成员或提供 QQ 号，例如：{self.prefix}{'授权' if grant else '取消'}{target} @张三"
            )
        display = name or self._name_of(qq)
        if grant:
            self.repo.add_role(troupe.id, qq, role, source="manual", granted_by=ctx.qq)
            self.repo.upsert_user(qq, name)
            self.repo.add_audit(
                troupe.id,
                ctx.qq,
                "grant_role",
                target=f"{display}({qq})",
                summary=role_label(role),
            )
            return [self._reply(ctx, f"✅ 已授予 {display}（{qq}）{role_label(role)}角色。")]
        if role == "director":
            removed = self.repo.remove_role(
                troupe.id, qq, "director", source="manual"
            )
            remaining_auto = [
                r
                for r in self.repo.get_roles(troupe.id, qq)
                if r.role == "director" and r.source == "auto_group_admin"
            ]
            if removed == 0 and not remaining_auto:
                raise StateError(f"{display} 没有导演角色。")
            if removed == 0 and remaining_auto:
                raise StateError(
                    f"{display} 仍是群主 / 群管理员，自动拥有导演角色，无法手动取消。\n"
                    "如需取消，请先在 QQ 群中调整其群管理身份。"
                )
        else:
            removed = self.repo.remove_role(troupe.id, qq, role)
            if removed == 0:
                raise StateError(f"{display} 没有{role_label(role)}角色。")
        self.repo.add_audit(
            troupe.id,
            ctx.qq,
            "revoke_role",
            target=f"{display}({qq})",
            summary=role_label(role),
        )
        return [self._reply(ctx, f"✅ 已取消 {display}（{qq}）的{role_label(role)}角色。")]

    # ------------------------------------------------------------------
    # 演员
    # ------------------------------------------------------------------
    async def _extract_actor_target(
        self, troupe: Troupe, text: str
    ) -> tuple[str | None, str, str]:
        match = MENTION_TOKEN_RE.search(text or "")
        if match:
            qq = match.group(1)
            name = match.group(2).strip()
            rest = (text[: match.start()] + " " + text[match.end() :]).strip()
            if not name:
                members = await self._group_members(troupe.group_id)
                name = next((m.display_name for m in members if m.qq == qq), "") or self._name_of(qq)
            return qq, name, rest
        number = re.search(r"(?<!\d)(\d{5,12})(?!\d)", text or "")
        if number:
            qq = number.group(1)
            members = await self._group_members(troupe.group_id)
            name = next((m.display_name for m in members if m.qq == qq), "") or self._name_of(qq)
            rest = (text[: number.start()] + " " + text[number.end() :]).strip()
            return qq, name, rest
        parts = (text or "").strip().split(maxsplit=1)
        if not parts:
            return None, "", ""
        name = parts[0]
        rest = parts[1] if len(parts) > 1 else ""
        members = await self._group_members(troupe.group_id)
        for member in members:
            if name in (member.display_name, member.nickname, member.qq):
                return member.qq, member.display_name, rest
        return None, name, rest

    async def _cmd_actor(
        self, ctx: CommandContext, troupe: Troupe, cmd: str, rest: str
    ) -> list[Reply]:
        if cmd == "演员列表":
            self._require(troupe, ctx.qq, "manage_actor")
            return await self._cmd_actor_list(ctx, troupe)
        self._require(troupe, ctx.qq, "manage_actor")
        if cmd == "添加演员":
            qq, name, remaining = await self._extract_actor_target(troupe, rest)
            if not name:
                raise UserError(f"请指定演员，例如：{self.prefix}添加演员 @张三 或 {self.prefix}添加演员 制作组")
            tags: list[str] = []
            tag_match = re.search(r"(?:标签|备注)\s*[=:：]\s*(\S+)", remaining or "")
            if tag_match:
                tags = [tag_match.group(1)]
            existing = (
                self.repo.get_actor(troupe.id, qq)
                if qq
                else self.repo.find_actor_by_name(troupe.id, name)
            )
            if existing and existing.active:
                raise StateError(f"「{existing.display_name}」已在演员列表中。")
            profile = self.repo.upsert_actor(troupe.id, qq, name, remark_tags=tags)
            kind = "群成员" if qq else "文本人员"
            self.repo.add_audit(
                troupe.id, ctx.qq, "add_actor", target=profile.display_name, summary=kind
            )
            extra = "（文本人员不能 @，也不能私聊提醒）" if not qq else ""
            return [self._reply(ctx, f"✅ 已添加演员「{profile.display_name}」{extra}。")]
        if cmd == "移除演员":
            qq, name, _ = await self._extract_actor_target(troupe, rest)
            actor = (
                self.repo.get_actor(troupe.id, qq)
                if qq
                else self.repo.find_actor_by_name(troupe.id, name)
            )
            if not actor or not actor.active:
                raise NotFound(f"演员「{name or qq}」不在演员列表中。")
            self.repo.deactivate_actor(troupe.id, actor.id)
            self.repo.add_audit(
                troupe.id, ctx.qq, "remove_actor", target=actor.display_name
            )
            return [
                self._reply(
                    ctx,
                    f"✅ 已移除演员「{actor.display_name}」。历史排练参与记录保留。",
                )
            ]
        qq, name, remaining = await self._extract_actor_target(troupe, rest)
        actor = (
            self.repo.get_actor(troupe.id, qq)
            if qq
            else self.repo.find_actor_by_name(troupe.id, name)
        )
        if not actor or not actor.active:
            raise NotFound(f"演员「{name or qq}」不在演员列表中，请先添加。")
        values = [item for item in re.split(r"[\s,，、;；]+", remaining or "") if item]
        if cmd == "演员别名":
            if not values:
                current = "、".join(actor.aliases) or "（无）"
                return [
                    self._reply(
                        ctx,
                        f"「{actor.display_name}」当前别名：{current}\n"
                        f"设置别名：{self.prefix}演员别名 @{actor.display_name} 小张",
                    )
                ]
            self.repo.update_actor_aliases(actor.id, values)
            self.repo.add_audit(
                troupe.id, ctx.qq, "update_actor", target=actor.display_name, summary="别名"
            )
            return [
                self._reply(ctx, f"✅ 已设置「{actor.display_name}」的别名为：{'、'.join(values)}")
            ]
        if cmd == "演员标签":
            if not values:
                current = "、".join(actor.remark_tags) or "（无）"
                return [
                    self._reply(
                        ctx,
                        f"「{actor.display_name}」当前标签：{current}\n"
                        f"设置标签：{self.prefix}演员标签 @{actor.display_name} 湖区",
                    )
                ]
            self.repo.update_actor_tags(actor.id, values)
            self.repo.add_audit(
                troupe.id, ctx.qq, "update_actor", target=actor.display_name, summary="标签"
            )
            return [
                self._reply(ctx, f"✅ 已设置「{actor.display_name}」的标签为：{'、'.join(values)}")
            ]
        raise NotFound("未知的演员命令。")

    async def _cmd_actor_list(self, ctx: CommandContext, troupe: Troupe) -> list[Reply]:
        actors = self.repo.list_actors(troupe.id)
        if not actors:
            return [
                self._reply(
                    ctx,
                    f"【演员列表】{troupe.name}\n暂无演员记录。\n"
                    f"添加演员：{self.prefix}添加演员 @张三",
                )
            ]
        precise = [a for a in actors if a.qq_id]
        textual = [a for a in actors if not a.qq_id]
        lines = [f"【演员列表】{troupe.name}", f"群成员（可精确提醒）：{len(precise)}"]
        for index, actor in enumerate(precise, 1):
            extra = []
            if actor.aliases:
                extra.append("别名：" + "、".join(actor.aliases))
            if actor.remark_tags:
                extra.append("标签：" + "、".join(actor.remark_tags))
            suffix = f"（{'；'.join(extra)}）" if extra else ""
            lines.append(f"{index}. {actor.display_name}（{actor.qq_id}）{suffix}")
        lines.append(f"文本人员（仅展示）：{len(textual)}")
        for actor in textual:
            lines.append(f"- {actor.display_name}")
        return [self._reply(ctx, "\n".join(lines))]

    # ------------------------------------------------------------------
    # 发布 / 修改 / 重发
    # ------------------------------------------------------------------
    async def _cmd_publish(
        self, ctx: CommandContext, troupe: Troupe, rest: str
    ) -> list[Reply]:
        self._require(troupe, ctx.qq, "publish")
        now = self.now()
        try:
            fields = parse_fields(rest)
            if not fields.get("time"):
                raise UserError(f"请填写排练时间，例如：\n{self.prefix}发布排练\n时间=周五7~10")
            if is_empty_value(fields.get("participants")):
                raise UserError("请填写参与人员，例如：人员=@张三 @李四 制作组")
            local_now = now.astimezone(troupe.tz)
            parsed = parse_time(
                fields.get("time"),
                local_now,
                troupe.ambiguous_evening_threshold,
            )
            mode, participants, people_notes = await self._parse_people(
                troupe, fields.get("participants", "")
            )
            if mode == "specified" and not participants:
                raise UserError("参与人员为空，请重新填写人员字段。")
            minutes = (
                parse_remind_minutes(fields.get("remind"))
                if fields.has("remind")
                else troupe.reminders
            )
            notify_all = parse_scope(fields.get("scope")) if fields.has("scope") else False
            if any(
                key != "participants" and "@全体成员" in (value or "")
                for key, value in fields.values.items()
            ):
                notify_all = True
            draft = RehearsalDraft(
                start_at=parsed.start,
                end_at=parsed.end,
                location=clean_value(fields.get("location")),
                content=split_content(fields.get("content", "")),
                remark=clean_value(fields.get("remark")),
                participants=participants,
                participant_mode=mode,
                notify_all=notify_all,
                remind_minutes=minutes,
                raw_text=rest,
                notes=list(parsed.notes) + people_notes,
            )
            if mode == "all" and not await self._bot_is_admin(troupe.group_id):
                draft.notes.append(
                    "当前机器人不是群管理员，无法发送 @全体成员，将降级为逐个 @ 相关人员。"
                )
            if draft.start_at <= now:
                draft.notes.append("该排练开始时间已过，将不会发送提醒。如仍需发布，请确认。")
            else:
                missed = [m for m in draft.remind_minutes if draft.start_at - timedelta(minutes=m) <= now]
                if missed:
                    draft.notes.append(
                        f"已错过 {format_remind_minutes(tuple(missed))} 的提醒，将不再补发。"
                    )
            draft.notes = list(dict.fromkeys(draft.notes))
        except UserError as exc:
            self.repo.add_audit(
                troupe.id,
                ctx.qq,
                "parse_failed",
                target="发布排练",
                summary=str(exc)[:200],
                result="fail",
                message=str(exc),
                now=now,
            )
            raise
        # 无任何风险提示时，按剧组配置决定是否直接发布（默认直接发布）
        has_warnings = bool(draft.notes) or bool(draft.text_participants)
        if not has_warnings and not troupe.publish_confirm_when_clean:
            return await self._publish_now(ctx, troupe, draft)
        action = self.repo.create_pending(
            troupe.id,
            "publish",
            ctx.qq,
            troupe.group_id,
            _draft_to_payload(draft),
            now=now,
        )
        message = preview_message(
            troupe,
            draft,
            kind="publish",
            action_id=action.id,
            now=now,
            prefix=self.prefix,
        )
        return [self._reply(ctx, message)]

    async def _cmd_modify(
        self, ctx: CommandContext, troupe: Troupe, rest: str
    ) -> list[Reply]:
        self._require(troupe, ctx.qq, "modify")
        seq, fields_text = self._split_seq(rest)
        rehearsal = self.repo.get_rehearsal(troupe.id, seq)
        if not rehearsal:
            raise NotFound(f"未找到编号 {seq} 的排练。发送 {self.prefix}排练列表 查看编号。")
        if rehearsal.status != "published":
            raise StateError(
                f"编号 {seq} 的排练当前状态为「{rehearsal.status}」，只有已发布的排练可以修改。"
            )
        now = self.now()
        if not fields_text:
            notice = notice_message(troupe, rehearsal, now)
            notice.add_text(
                f"\n修改请发送（字段可只填要改的）：\n{self.prefix}排练修改 {seq} 时间=周五8~10\n"
                f"{self.prefix}排练修改 {seq} 地点=二排\n"
                f"{self.prefix}排练修改 {seq} 提醒=关闭\n"
                f"时间字段支持与发布相同的写法。"
            )
            return [self._reply(ctx, notice)]
        try:
            fields = parse_fields(fields_text)
            draft = _draft_from_rehearsal(rehearsal)
            notes: list[str] = []
            if fields.has("time"):
                parsed = parse_time(
                    fields.get("time"),
                    now.astimezone(troupe.tz),
                    troupe.ambiguous_evening_threshold,
                )
                draft.start_at = parsed.start
                draft.end_at = parsed.end
                notes.extend(parsed.notes)
            if fields.has("location"):
                draft.location = clean_value(fields.get("location"))
            if fields.has("content"):
                draft.content = split_content(fields.get("content", ""))
            if fields.has("remark"):
                draft.remark = clean_value(fields.get("remark"))
            if fields.has("scope"):
                draft.notify_all = parse_scope(fields.get("scope"))
            if fields.has("participants"):
                mode, participants, people_notes = await self._parse_people(
                    troupe, fields.get("participants", "")
                )
                if mode == "specified" and not participants:
                    raise UserError("参与人员不能为空。")
                draft.participant_mode = mode
                draft.participants = participants
                notes.extend(people_notes)
            if fields.has("remind"):
                draft.remind_minutes = parse_remind_minutes(fields.get("remind"))
            if draft.start_at <= now:
                notes.append("该排练开始时间已过，将不会发送提醒。")
            draft.notes = list(dict.fromkeys(notes))
            draft.raw_text = fields_text
        except UserError as exc:
            self.repo.add_audit(
                troupe.id,
                ctx.qq,
                "parse_failed",
                target=f"修改排练 编号{seq}",
                summary=str(exc)[:200],
                result="fail",
                message=str(exc),
                now=now,
            )
            raise
        original = format_preview_time(
            rehearsal.start_at.astimezone(troupe.tz), rehearsal.end_at.astimezone(troupe.tz)
        )
        payload = {"rehearsal_id": rehearsal.id, "seq": seq, "draft": _draft_to_payload(draft)}
        action = self.repo.create_pending(
            troupe.id,
            "modify",
            ctx.qq,
            troupe.group_id,
            payload,
            now=now,
            rehearsal_id=rehearsal.id,
        )
        message = preview_message(
            troupe,
            draft,
            kind="modify",
            action_id=action.id,
            now=now,
            prefix=self.prefix,
            seq=seq,
            original_time=original,
        )
        return [self._reply(ctx, message)]

    async def _cmd_republish(
        self, ctx: CommandContext, troupe: Troupe, rest: str
    ) -> list[Reply]:
        self._require(troupe, ctx.qq, "republish")
        seq, _ = self._split_seq(rest)
        rehearsal = self.repo.get_rehearsal(troupe.id, seq)
        if not rehearsal:
            raise NotFound(f"未找到编号 {seq} 的排练。")
        if rehearsal.status == "cancelled":
            raise StateError(f"编号 {seq} 的排练已取消，不能重新发布。")
        if rehearsal.status != "published":
            raise StateError(f"编号 {seq} 的排练当前状态为「{rehearsal.status}」，不能重新发布。")
        now = self.now()
        draft = _draft_from_rehearsal(rehearsal)
        if draft.start_at <= now:
            draft.notes = ["该排练开始时间已过，重发后不会发送提醒。"]
        action = self.repo.create_pending(
            troupe.id,
            "republish",
            ctx.qq,
            troupe.group_id,
            {"rehearsal_id": rehearsal.id, "seq": seq},
            now=now,
            rehearsal_id=rehearsal.id,
        )
        message = preview_message(
            troupe,
            draft,
            kind="republish",
            action_id=action.id,
            now=now,
            prefix=self.prefix,
            seq=seq,
        )
        return [self._reply(ctx, message)]

    # ------------------------------------------------------------------
    # 待确认操作
    # ------------------------------------------------------------------
    def _load_pending(
        self, ctx: CommandContext, kind: str, action_id: str
    ) -> PendingAction:
        action = None
        if action_id:
            action = self.repo.get_pending(action_id)
        else:
            action = self.repo.latest_pending(ctx.qq, kind)
        label = KIND_LABELS.get(kind, kind)
        if action is None:
            raise NotFound(
                f"没有找到待确认的{label}操作。请先发送对应命令生成预览。"
            )
        if action.kind != kind:
            raise StateError("该预览类型不匹配，请重新生成。")
        if action.operator_qq != ctx.qq:
            raise PermissionDenied("只有发起人可以确认或取消该操作。")
        if action.status != "pending":
            raise StateError("该预览已失效或已处理，请重新发送命令。")
        now = self.now()
        if action.expires_at and action.expires_at <= now:
            self.repo.set_pending_status(action.id, "expired")
            raise StateError("该预览已过期（30 分钟有效），请重新发送命令。")
        if ctx.scope == "group" and action.group_id != str(ctx.group_id):
            raise StateError("该预览不属于当前群。")
        return action

    async def _cmd_pending(
        self, ctx: CommandContext, cmd: str, rest: str
    ) -> list[Reply]:
        if cmd in CANCEL_KINDS:
            kind = CANCEL_KINDS[cmd]
            action = self._load_pending(ctx, kind, rest.strip())
            self.repo.set_pending_status(action.id, "cancelled")
            self.repo.add_audit(
                action.troupe_id,
                ctx.qq,
                "cancel_preview",
                target=action.id,
                summary=f"取消{KIND_LABELS.get(kind, kind)}预览",
            )
            return [self._reply(ctx, f"已取消本次{KIND_LABELS.get(kind, kind)}操作（{action.id}）。")]
        kind = CONFIRM_KINDS[cmd]
        action = self._load_pending(ctx, kind, rest.strip())
        troupe = self.repo.get_troupe(action.troupe_id)
        if troupe is None:
            raise NotFound("预览对应的剧组不存在。")
        self._check_active(troupe, cmd)
        self._require(
            troupe,
            ctx.qq,
            {"publish": "publish", "modify": "modify", "republish": "republish"}[kind],
        )
        if kind == "publish":
            return await self._execute_publish(ctx, action, troupe)
        if kind == "modify":
            return await self._execute_modify(ctx, action, troupe)
        return await self._execute_republish(ctx, action, troupe)

    def _triggers(self, start_at: datetime, minutes: tuple[int, ...]) -> list[datetime]:
        return [start_at - timedelta(minutes=m) for m in minutes]

    async def _execute_publish(
        self, ctx: CommandContext, action: PendingAction, troupe: Troupe
    ) -> list[Reply]:
        draft = _draft_from_payload(action.payload)
        return await self._publish_now(ctx, troupe, draft, action=action)

    async def _publish_now(
        self,
        ctx: CommandContext,
        troupe: Troupe,
        draft: RehearsalDraft,
        *,
        action: PendingAction | None = None,
    ) -> list[Reply]:
        now = self.now()
        rehearsal = self.repo.create_rehearsal(
            troupe.id, draft, created_by=ctx.qq, now=now
        )
        self.repo.create_jobs(
            rehearsal.id,
            troupe.id,
            self._triggers(rehearsal.start_at, draft.remind_minutes),
            now,
        )
        if action is not None:
            self.repo.set_pending_status(action.id, "consumed")
        summary = (
            f"编号{rehearsal.seq} "
            f"{format_notice_time(rehearsal.start_at.astimezone(troupe.tz), rehearsal.end_at.astimezone(troupe.tz), now.astimezone(troupe.tz))}"
        )
        self.repo.add_audit(
            troupe.id, ctx.qq, "publish", target=f"编号{rehearsal.seq}", summary=summary, now=now
        )
        try:
            await self._send_notice(troupe, rehearsal, now)
        except Exception as exc:
            log.exception("发布通知发送失败")
            self.repo.add_audit(
                troupe.id,
                ctx.qq,
                "publish",
                target=f"编号{rehearsal.seq}",
                summary="群消息发送失败",
                result="fail",
                message=str(exc),
                now=now,
            )
            return [
                self._reply(
                    ctx,
                    f"⚠️ 通知已保存为编号 {rehearsal.seq}，但群消息发送失败：{exc}\n"
                    f"请稍后发送 {self.prefix}排练重发 {rehearsal.seq} 重试。",
                )
            ]
        pending_reminders = [
            m for m in draft.remind_minutes if rehearsal.start_at - timedelta(minutes=m) > now
        ]
        if draft.remind_minutes:
            if pending_reminders:
                remind_note = format_remind_minutes(tuple(pending_reminders))
            else:
                remind_note = "所有提醒均已错过，不再补发"
        else:
            remind_note = "不提醒"
        return [
            self._reply(
                ctx,
                f"✅ 已发布排练通知（编号 {rehearsal.seq}）。\n"
                f"时间：{format_notice_time(rehearsal.start_at.astimezone(troupe.tz), rehearsal.end_at.astimezone(troupe.tz), now.astimezone(troupe.tz))}\n"
                f"提醒：{remind_note}\n"
                f"发送 {self.prefix}排练详情 {rehearsal.seq} 查看详情。",
            )
        ]

    async def _execute_modify(
        self, ctx: CommandContext, action: PendingAction, troupe: Troupe
    ) -> list[Reply]:
        now = self.now()
        rehearsal = self.repo.get_rehearsal_by_id(int(action.payload.get("rehearsal_id", 0)))
        if rehearsal is None:
            raise NotFound("要修改的排练不存在。")
        if rehearsal.status == "deleted":
            raise StateError("该排练已删除。")
        draft = _draft_from_payload(action.payload.get("draft", {}))
        self.repo.update_rehearsal(rehearsal.id, draft, updated_by=ctx.qq, now=now)
        self.repo.reset_jobs(rehearsal.id)
        self.repo.create_jobs(
            rehearsal.id,
            troupe.id,
            self._triggers(draft.start_at, draft.remind_minutes),
            now,
        )
        self.repo.set_pending_status(action.id, "consumed")
        self.repo.add_audit(
            troupe.id,
            ctx.qq,
            "modify",
            target=f"编号{rehearsal.seq}",
            summary="修改并保存",
            now=now,
        )
        return [
            self._reply(
                ctx,
                "✅ 修改已保存。\n"
                f"如需在群内重新发布，请发送：\n{self.prefix}排练重发 {rehearsal.seq}",
            )
        ]

    async def _execute_republish(
        self, ctx: CommandContext, action: PendingAction, troupe: Troupe
    ) -> list[Reply]:
        now = self.now()
        rehearsal = self.repo.get_rehearsal_by_id(int(action.payload.get("rehearsal_id", 0)))
        if rehearsal is None:
            raise NotFound("要重发的排练不存在。")
        if rehearsal.status != "published":
            raise StateError("该排练当前状态不能重新发布。")
        self.repo.set_pending_status(action.id, "consumed")
        try:
            await self._send_notice(troupe, rehearsal, now, updated=True)
        except Exception as exc:
            self.repo.add_audit(
                troupe.id,
                ctx.qq,
                "republish",
                target=f"编号{rehearsal.seq}",
                result="fail",
                message=str(exc),
                now=now,
            )
            return [
                self._reply(
                    ctx,
                    f"⚠️ 重新发布失败：{exc}\n请稍后发送 {self.prefix}排练重发 {rehearsal.seq} 重试。",
                )
            ]
        self.repo.mark_republished(rehearsal.id, now)
        self.repo.reset_jobs(rehearsal.id)
        self.repo.create_jobs(
            rehearsal.id,
            troupe.id,
            self._triggers(rehearsal.start_at, rehearsal.remind_minutes),
            now,
        )
        self.repo.add_audit(
            troupe.id,
            ctx.qq,
            "republish",
            target=f"编号{rehearsal.seq}",
            summary="重新发布",
            now=now,
        )
        return [self._reply(ctx, f"✅ 已重新发布编号 {rehearsal.seq} 的排练通知。")]

    # ------------------------------------------------------------------
    # 取消 / 删除
    # ------------------------------------------------------------------
    async def _cmd_cancel(
        self, ctx: CommandContext, troupe: Troupe, rest: str
    ) -> list[Reply]:
        self._require(troupe, ctx.qq, "cancel")
        seq, _ = self._split_seq(rest)
        rehearsal = self.repo.get_rehearsal(troupe.id, seq)
        if not rehearsal:
            raise NotFound(f"未找到编号 {seq} 的排练。")
        if rehearsal.status == "cancelled":
            raise StateError(f"编号 {seq} 的排练已经取消。")
        if rehearsal.status != "published":
            raise StateError(f"编号 {seq} 的排练当前状态为「{rehearsal.status}」，不能取消。")
        return [
            self._reply(
                ctx,
                f"确定取消编号为 {seq} 的排练吗？\n"
                "是否同时发布取消通知？\n\n请发送：\n"
                f"{self.prefix}确认取消 {seq} 通知=是\n"
                f"或\n{self.prefix}确认取消 {seq} 通知=否",
            )
        ]

    async def _cmd_confirm_cancel(
        self, ctx: CommandContext, troupe: Troupe, rest: str
    ) -> list[Reply]:
        self._require(troupe, ctx.qq, "cancel")
        seq_match = re.search(r"(\d+)", rest or "")
        if not seq_match:
            raise UserError(f"请提供排练编号，例如：{self.prefix}确认取消 3 通知=是")
        seq = int(seq_match.group(1))
        notify_match = re.search(r"通知\s*[=:：]\s*(\S+)", rest or "")
        if not notify_match:
            raise UserError(
                f"请指定是否发布取消通知：{self.prefix}确认取消 {seq} 通知=是 或 通知=否"
            )
        notify = _bool_value(notify_match.group(1), "是否发布取消通知")
        rehearsal = self.repo.get_rehearsal(troupe.id, seq)
        if not rehearsal:
            raise NotFound(f"未找到编号 {seq} 的排练。")
        if rehearsal.status == "cancelled":
            raise StateError(f"编号 {seq} 的排练已经取消。")
        if rehearsal.status != "published":
            raise StateError(f"编号 {seq} 的排练当前状态为「{rehearsal.status}」，不能取消。")
        now = self.now()
        self.repo.set_rehearsal_status(rehearsal.id, "cancelled")
        self.repo.cancel_pending_jobs(rehearsal.id)
        self.repo.add_audit(
            troupe.id,
            ctx.qq,
            "cancel",
            target=f"编号{seq}",
            summary=f"取消排练，通知={'是' if notify else '否'}",
            now=now,
        )
        text = f"✅ 编号 {seq} 的排练已取消，提醒已停止。"
        if notify:
            try:
                await self._send_group(
                    troupe.group_id, cancel_notice_message(troupe, rehearsal, now)
                )
                text += "\n已在群内发布取消通知。"
            except Exception as exc:
                text += f"\n⚠️ 取消通知发送失败：{exc}"
        return [self._reply(ctx, text)]

    async def _cmd_delete(
        self, ctx: CommandContext, troupe: Troupe, rest: str
    ) -> list[Reply]:
        self._require(troupe, ctx.qq, "delete")
        seq, _ = self._split_seq(rest)
        rehearsal = self.repo.get_rehearsal(troupe.id, seq)
        if not rehearsal:
            raise NotFound(f"未找到编号 {seq} 的排练。")
        return [
            self._reply(
                ctx,
                f"确定删除编号为 {seq} 的排练吗？\n"
                "删除后普通成员将无法看到该通知，提醒任务同时停止。\n\n请发送：\n"
                f"{self.prefix}确认删除 {seq}\n取消请发送：{self.prefix}取消删除 {seq}",
            )
        ]

    async def _cmd_confirm_delete(
        self, ctx: CommandContext, troupe: Troupe, cmd: str, rest: str
    ) -> list[Reply]:
        self._require(troupe, ctx.qq, "delete")
        seq, _ = self._split_seq(rest)
        if cmd == "取消删除":
            return [self._reply(ctx, f"已取消删除编号 {seq} 的操作。")]
        rehearsal = self.repo.get_rehearsal(troupe.id, seq)
        if not rehearsal:
            raise NotFound(f"未找到编号 {seq} 的排练。")
        now = self.now()
        self.repo.soft_delete(rehearsal.id, now, ctx.qq)
        self.repo.cancel_pending_jobs(rehearsal.id)
        self.repo.add_audit(
            troupe.id, ctx.qq, "delete", target=f"编号{seq}", summary="软删除", now=now
        )
        return [self._reply(ctx, f"✅ 编号 {seq} 的排练已删除，提醒已停止。")]

    async def _cmd_hard_delete(
        self, ctx: CommandContext, troupe: Troupe, rest: str
    ) -> list[Reply]:
        self._require(troupe, ctx.qq, "hard_delete")
        seq, _ = self._split_seq(rest)
        rehearsal = self.repo.get_rehearsal(troupe.id, seq, include_deleted=True)
        if not rehearsal:
            raise NotFound(f"未找到编号 {seq} 的排练。")
        self.repo.cancel_pending_jobs(rehearsal.id)
        self.repo.hard_delete(rehearsal.id)
        self.repo.add_audit(
            troupe.id, ctx.qq, "hard_delete", target=f"编号{seq}", summary="物理删除"
        )
        return [self._reply(ctx, f"✅ 编号 {seq} 的排练已从数据库彻底删除。")]

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def _split_seq(self, rest: str) -> tuple[int, str]:
        parts = (rest or "").strip().split(maxsplit=1)
        if not parts or not parts[0].isdigit():
            raise UserError(
                f"请提供排练编号，例如：{self.prefix}排练详情 3\n"
                f"发送 {self.prefix}排练列表 查看编号。"
            )
        return int(parts[0]), (parts[1] if len(parts) > 1 else "")

    def _week_range(self, troupe: Troupe, offset_weeks: int = 0):
        local_now = self.now().astimezone(troupe.tz)
        monday = local_now.date() - timedelta(days=local_now.weekday()) + timedelta(
            weeks=offset_weeks
        )
        start_local = datetime.combine(monday, dtime.min, tzinfo=troupe.tz)
        end_local = start_local + timedelta(days=7)
        return start_local.astimezone(UTC), end_local.astimezone(UTC)

    async def _cmd_list(
        self, ctx: CommandContext, troupe: Troupe, rest: str, *, history: bool
    ) -> list[Reply]:
        if ctx.scope == "private" and not (
            self.repo.is_admin(ctx.qq) or self.repo.is_registered_in_troupe(troupe.id, ctx.qq)
        ):
            raise PermissionDenied("你不在该剧组中，无法查看排练。")
        now = self.now()
        text = rest.strip()
        if history:
            count = int(text) if text.isdigit() else 10
            count = max(1, min(count, 50))
            rows = self.repo.list_rehearsals(
                troupe.id,
                end=now,
                statuses=("published", "cancelled", "completed"),
                order="desc",
                limit=count,
            )
            return [
                self._reply(ctx, list_message(troupe, rows, now, label=f"历史排练（最近 {count} 条）"))
            ]
        if text in ("", "未来"):
            rows = self.repo.list_rehearsals(
                troupe.id,
                start=now,
                statuses=("published", "cancelled"),
                order="asc",
                limit=20,
            )
            label = "未来排练"
        elif text in ("本周", "下周"):
            offset = 0 if text == "本周" else 1
            start, end = self._week_range(troupe, offset)
            rows = self.repo.list_rehearsals(
                troupe.id,
                start=start,
                end=end,
                statuses=("published", "cancelled", "completed"),
                order="asc",
            )
            label = f"{text}排练"
        elif text == "全部":
            rows = self.repo.list_rehearsals(
                troupe.id,
                statuses=("published", "cancelled", "completed"),
                order="asc",
                limit=50,
            )
            label = "全部排练"
        elif text == "历史":
            rows = self.repo.list_rehearsals(
                troupe.id,
                end=now,
                statuses=("published", "cancelled", "completed"),
                order="desc",
                limit=10,
            )
            label = "历史排练"
        else:
            raise UserError(
                f"无法识别的范围：{text}\n可用：本周、下周、未来、全部、历史"
            )
        return [self._reply(ctx, list_message(troupe, rows, now, label=label))]

    async def _cmd_detail(
        self, ctx: CommandContext, troupe: Troupe, rest: str
    ) -> list[Reply]:
        if ctx.scope == "private" and not (
            self.repo.is_admin(ctx.qq) or self.repo.is_registered_in_troupe(troupe.id, ctx.qq)
        ):
            raise PermissionDenied("你不在该剧组中，无法查看排练。")
        seq, _ = self._split_seq(rest)
        rehearsal = self.repo.get_rehearsal(troupe.id, seq)
        if not rehearsal:
            raise NotFound(f"未找到编号 {seq} 的排练。")
        return [
            self._reply(
                ctx,
                detail_message(
                    troupe,
                    rehearsal,
                    self.now(),
                    created_by_name=self._display_name(rehearsal.created_by),
                ),
            )
        ]

    async def _cmd_my_schedule(self, ctx: CommandContext, rest: str) -> list[Reply]:
        scope = rest.strip() or "未来"
        if scope not in ("今天", "本周", "下周", "未来", "历史", "全部"):
            raise UserError(
                f"无法识别的范围：{scope}\n可用：今天、本周、下周、未来、历史、全部"
            )
        now = self.now()
        rows = self.repo.list_participations(
            ctx.qq,
            statuses=("published", "cancelled", "completed"),
            limit=300,
        )
        entries: list[tuple[Troupe, Rehearsal, str]] = []
        for rehearsal in rows:
            troupe = self.repo.get_troupe(rehearsal.troupe_id)
            if troupe is None:
                continue
            local_start = rehearsal.start_at.astimezone(troupe.tz)
            local_now = now.astimezone(troupe.tz)
            if scope == "今天" and local_start.date() != local_now.date():
                continue
            if scope in ("本周", "下周"):
                start, end = self._week_range(troupe, 0 if scope == "本周" else 1)
                if not (start <= rehearsal.start_at < end):
                    continue
            if scope == "未来" and rehearsal.start_at < now:
                continue
            if scope == "历史" and rehearsal.start_at >= now:
                continue
            tag = ""
            for participant in rehearsal.participants:
                if participant.qq_id == ctx.qq:
                    tag = participant.remark_tag
                    break
            if not tag:
                actor = self.repo.get_actor(troupe.id, ctx.qq)
                if actor and actor.remark_tags:
                    tag = actor.remark_tags[0]
            entries.append((troupe, rehearsal, tag))
        entries.sort(key=lambda item: item[1].start_at, reverse=scope == "历史")
        message = my_schedule_message(entries)
        if ctx.scope == "group":
            prefix = Message.text("（以下仅显示你自己的安排，建议私聊机器人查询）\n")
            prefix.extend(message)
            message = prefix
        return [self._reply(ctx, message)]

    # ------------------------------------------------------------------
    # 配置 / 审计 / 同步
    # ------------------------------------------------------------------
    async def _cmd_default_remind(
        self, ctx: CommandContext, troupe: Troupe, rest: str
    ) -> list[Reply]:
        self._require(troupe, ctx.qq, "config_troupe")
        if not rest.strip():
            raise UserError(
                f"当前默认提醒：{format_remind_minutes(troupe.reminders)}\n"
                f"设置：{self.prefix}提醒默认 30 或 {self.prefix}提醒默认 60,30,10 或 关闭"
            )
        minutes = parse_remind_minutes(rest)
        value = ",".join(str(m) for m in minutes)
        self.repo.update_troupe(troupe.id, default_remind_minutes=value)
        self.repo.add_audit(
            troupe.id,
            ctx.qq,
            "config",
            target="默认提醒",
            summary=format_remind_minutes(minutes),
        )
        return [self._reply(ctx, f"✅ 默认提醒已设置为：{format_remind_minutes(minutes)}")]

    async def _cmd_config_view(self, ctx: CommandContext, troupe: Troupe) -> list[Reply]:
        self._require(troupe, ctx.qq, "config_troupe")
        settings = {
            "send_interval": self.repo.get_setting("send_interval", str(self.settings.send_interval)),
            "max_attempts": self.repo.get_setting(
                "max_attempts", str(self.settings.max_reminder_attempts)
            ),
            "grace_seconds": self.repo.get_setting(
                "grace_seconds", str(self.settings.reminder_grace_seconds)
            ),
        }
        return [self._reply(ctx, config_message(troupe, settings))]

    def _parse_pairs(self, text: str, aliases: dict[str, str]) -> list[tuple[str, str]]:
        keys = sorted(aliases, key=len, reverse=True)
        pattern = re.compile(
            r"(?:^|(?<=\s))[ \t]*(?P<key>" + "|".join(map(re.escape, keys)) + r")[ \t]*[=:：][ \t]*",
            re.MULTILINE,
        )
        matches = list(pattern.finditer(text or ""))
        if not matches:
            return []
        pairs = []
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            value = text[match.end() : end].strip()
            pairs.append((aliases[match.group("key")], value))
        return pairs

    async def _cmd_config_set(
        self, ctx: CommandContext, troupe: Troupe, rest: str
    ) -> list[Reply]:
        self._require(troupe, ctx.qq, "config_troupe")
        pairs = self._parse_pairs(rest, _CONFIG_ALIASES)
        if not pairs:
            raise UserError(
                f"请指定配置项，例如：{self.prefix}配置 默认提醒=30 私聊提醒=关\n"
                f"可用：默认提醒、私聊提醒、晚上阈值、显示标签、显示空字段、降级范围、时区"
            )
        updates: dict = {}
        summaries: list[str] = []
        for key, value in pairs:
            if key == "default_remind":
                minutes = parse_remind_minutes(value, default=troupe.default_remind_minutes)
                updates["default_remind_minutes"] = ",".join(str(m) for m in minutes)
                summaries.append(f"默认提醒={format_remind_minutes(minutes)}")
            elif key == "private_remind":
                flag = _bool_value(value, "私聊提醒")
                updates["private_remind"] = flag
                summaries.append(f"私聊提醒={'开' if flag else '关'}")
            elif key == "threshold":
                try:
                    threshold = int(value)
                except ValueError as exc:
                    raise ConfigError("晚上阈值必须是 0-12 的整数。") from exc
                if not 0 <= threshold <= 12:
                    raise ConfigError("晚上阈值必须是 0-12 的整数。")
                updates["ambiguous_evening_threshold"] = threshold
                summaries.append(f"晚上阈值={threshold}")
            elif key == "show_tags":
                flag = _bool_value(value, "显示标签")
                updates["show_tags"] = flag
                summaries.append(f"显示标签={'开' if flag else '关'}")
            elif key == "show_empty":
                flag = _bool_value(value, "显示空字段")
                updates["show_empty"] = flag
                summaries.append(f"显示空字段={'开' if flag else '关'}")
            elif key == "publish_confirm":
                text_value = value.strip()
                if text_value in ("自动", "智能"):
                    flag = False
                elif text_value in ("总是", "始终"):
                    flag = True
                else:
                    flag = _bool_value(text_value, "发布确认")
                updates["publish_confirm_when_clean"] = flag
                summaries.append(
                    f"发布确认={'总是预览' if flag else '无问题时直接发布'}"
                )
            elif key == "degrade_scope":
                flag_value = value.strip().casefold()
                if flag_value in ("全体", "全体成员", "all_members", "群成员"):
                    updates["degrade_at_scope"] = "all_members"
                    summaries.append("降级范围=全体群成员")
                elif flag_value in ("参与人员", "人员", "participants"):
                    updates["degrade_at_scope"] = "participants"
                    summaries.append("降级范围=仅参与人员")
                else:
                    raise ConfigError("降级范围只能是 参与人员 或 全体成员。")
            elif key == "timezone":
                try:
                    ZoneInfo(value.strip())
                except (ZoneInfoNotFoundError, ValueError) as exc:
                    raise ConfigError(f"无效的时区：{value}") from exc
                updates["timezone"] = value.strip()
                summaries.append(f"时区={value.strip()}")
        self.repo.update_troupe(troupe.id, **updates)
        self.repo.add_audit(
            troupe.id, ctx.qq, "config", target="剧组配置", summary="；".join(summaries)
        )
        updated = self.repo.get_troupe(troupe.id) or troupe
        settings = {
            "send_interval": self.repo.get_setting("send_interval", str(self.settings.send_interval)),
            "max_attempts": self.repo.get_setting(
                "max_attempts", str(self.settings.max_reminder_attempts)
            ),
            "grace_seconds": self.repo.get_setting(
                "grace_seconds", str(self.settings.reminder_grace_seconds)
            ),
        }
        message = Message.text("✅ 配置已更新。\n\n")
        message.extend(config_message(updated, settings))
        return [self._reply(ctx, message)]

    async def _cmd_global_config(
        self, ctx: CommandContext, cmd: str, rest: str
    ) -> list[Reply]:
        if not self.repo.is_admin(ctx.qq):
            raise PermissionDenied("只有管理员可以查看 / 修改全局配置。")
        if cmd == "全局配置查看":
            settings = self.repo.all_settings()
            stats = self.repo.stats()
            lines = ["【全局配置】"]
            lines.append(f"发送间隔：{settings.get('send_interval', self.settings.send_interval)} 秒")
            lines.append(
                f"提醒重试：{settings.get('max_attempts', self.settings.max_reminder_attempts)} 次"
            )
            lines.append(
                f"提醒宽限：{settings.get('grace_seconds', self.settings.reminder_grace_seconds)} 秒"
            )
            lines.append(f"分段人数：{settings.get('at_chunk_size', self.settings.at_chunk_size)}")
            lines.append(f"管理员：{'、'.join(self.repo.list_admins())}")
            lines.append(f"剧组数：{stats['troupes']}")
            lines.append(f"排练记录：{stats['rehearsals']}")
            lines.append(f"待发送提醒：{stats['pending_reminders']}")
            return [self._reply(ctx, "\n".join(lines))]
        pairs = self._parse_pairs(rest, _GLOBAL_CONFIG_ALIASES)
        if not pairs:
            raise UserError(
                f"请指定配置项，例如：{self.prefix}全局配置 发送间隔=1.0 重试次数=3\n"
                "可用：发送间隔、重试次数、提醒宽限、分段人数"
            )
        summaries = []
        for key, value in pairs:
            if key == "send_interval":
                try:
                    number = float(value)
                except ValueError as exc:
                    raise ConfigError("发送间隔必须是数字（秒）。") from exc
                if not 0 <= number <= 30:
                    raise ConfigError("发送间隔需在 0-30 秒之间。")
                self.repo.set_setting("send_interval", str(number))
                summaries.append(f"发送间隔={number}")
            elif key == "max_attempts":
                number = int(value) if value.strip().isdigit() else -1
                if not 1 <= number <= 10:
                    raise ConfigError("重试次数需在 1-10 之间。")
                self.repo.set_setting("max_attempts", str(number))
                summaries.append(f"重试次数={number}")
            elif key == "grace_seconds":
                number = int(value) if value.strip().isdigit() else -1
                if not 0 <= number <= 3600:
                    raise ConfigError("提醒宽限需在 0-3600 秒之间。")
                self.repo.set_setting("grace_seconds", str(number))
                summaries.append(f"提醒宽限={number}")
            elif key == "at_chunk_size":
                number = int(value) if value.strip().isdigit() else -1
                if not 1 <= number <= 50:
                    raise ConfigError("分段人数需在 1-50 之间。")
                self.repo.set_setting("at_chunk_size", str(number))
                summaries.append(f"分段人数={number}")
        self.repo.add_audit(
            self._audit_troupe_id(ctx), ctx.qq, "global_config", target="全局配置", summary="；".join(summaries)
        )
        return [self._reply(ctx, "✅ 全局配置已更新：" + "；".join(summaries))]

    async def _cmd_audit(
        self, ctx: CommandContext, troupe: Troupe, rest: str
    ) -> list[Reply]:
        self._require(troupe, ctx.qq, "audit")
        count = int(rest.strip()) if rest.strip().isdigit() else 10
        count = max(1, min(count, 50))
        entries = self.repo.list_audit(troupe.id, count)
        self.repo.add_audit(
            troupe.id, ctx.qq, "view_audit", target=f"最近{count}条", summary="查看审计日志"
        )
        return [
            self._reply(ctx, audit_message(entries, self._name_of, troupe.tz))
        ]

    async def _cmd_sync_members(
        self, ctx: CommandContext, troupe: Troupe
    ) -> list[Reply]:
        self._require(troupe, ctx.qq, "sync_members")
        if ctx.scope != "group":
            raise StateError("请在群内执行同步成员。")
        added, removed, total = await self.sync_group_roles(troupe.group_id)
        self.repo.add_audit(
            troupe.id,
            ctx.qq,
            "sync_members",
            target=troupe.name,
            summary=f"成员 {total}，新增导演 {added}，撤销导演 {removed}",
        )
        return [
            self._reply(
                ctx,
                f"✅ 已同步群成员：共 {total} 人。\n自动导演：新增 {added}，撤销 {removed}。",
            )
        ]

    # ------------------------------------------------------------------
    # 提醒发送
    # ------------------------------------------------------------------
    def _setting_int(self, key: str, fallback: int) -> int:
        try:
            return int(self.repo.get_setting(key, str(fallback)))
        except (TypeError, ValueError):
            return fallback

    def _setting_float(self, key: str, fallback: float) -> float:
        try:
            return float(self.repo.get_setting(key, str(fallback)))
        except (TypeError, ValueError):
            return fallback

    async def _throttle(self) -> None:
        interval = self._setting_float("send_interval", self.settings.send_interval)
        if interval <= 0:
            return
        async with self._send_lock:
            loop = asyncio.get_running_loop()
            wait = self._last_send + interval - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_send = loop.time()

    async def _send_group(self, group_id: str, message: Message) -> str | None:
        await self._throttle()
        return await self.gateway.send_group(str(group_id), message)

    async def _send_notice(
        self,
        troupe: Troupe,
        rehearsal: Rehearsal,
        now: datetime,
        *,
        updated: bool = False,
    ) -> str | None:
        """发送正式通知；非群管理员时把 @全体成员 降级为逐个 @。"""
        if rehearsal.participant_mode == "all" and not await self._bot_is_admin(
            troupe.group_id
        ):
            members = await self._group_members(troupe.group_id)
            participants = [
                Participant.from_qq(member.qq, member.display_name)
                for member in members
                if str(member.qq) != str(self.gateway.self_id)
            ]
            if not participants:
                participants = [Participant(qq_id=None, display_name="全体成员")]
            view = replace(
                rehearsal,
                participant_mode="specified",
                participants=participants,
            )
            return await self._send_group(
                troupe.group_id, notice_message(troupe, view, now, updated=updated)
            )
        return await self._send_group(
            troupe.group_id, notice_message(troupe, rehearsal, now, updated=updated)
        )

    async def _send_private(self, qq: str, message: Message) -> str | None:
        await self._throttle()
        return await self.gateway.send_private(str(qq), message)

    async def run_due_reminders(self) -> int:
        now = self.now()
        jobs = self.repo.due_jobs(now, limit=20)
        processed = 0
        for job in jobs:
            if not self.repo.claim_job(job.id, now):
                continue
            processed += 1
            try:
                await self._deliver_reminder(job, now)
            except Exception as exc:
                log.exception("提醒发送失败：job=%s", job.id)
                attempts = job.attempts + 1
                final = attempts >= self._setting_int(
                    "max_attempts", self.settings.max_reminder_attempts
                )
                self.repo.mark_job_failed(job.id, str(exc), now, final)
                self.repo.add_audit(
                    job.troupe_id,
                    "system",
                    "reminder_failed",
                    target=f"排练{job.rehearsal_id}",
                    summary=f"第{attempts}次发送失败",
                    result="fail",
                    message=str(exc),
                    now=now,
                )
        return processed

    async def _deliver_reminder(self, job, now: datetime) -> None:
        rehearsal = self.repo.get_rehearsal_by_id(job.rehearsal_id)
        troupe = self.repo.get_troupe(job.troupe_id)
        if rehearsal is None or troupe is None:
            self.repo.mark_job_cancelled(job.id, "排练或剧组不存在")
            return
        if rehearsal.status != "published":
            self.repo.mark_job_cancelled(job.id, f"排练状态为 {rehearsal.status}")
            return
        grace = self._setting_int("grace_seconds", self.settings.reminder_grace_seconds)
        if job.trigger_at < now - timedelta(seconds=grace):
            self.repo.mark_job_expired(job.id, "触发时间已错过，不再补发", now)
            return
        minutes = max(
            0,
            int(round((rehearsal.start_at - job.trigger_at).total_seconds() / 60)),
        )
        bot_admin = await self._bot_is_admin(troupe.group_id)
        at_all, mention_targets = await self._reminder_targets(
            troupe, rehearsal, bot_admin
        )
        chunk_size = max(1, self._setting_int("at_chunk_size", self.settings.at_chunk_size))
        chunks = [
            mention_targets[index : index + chunk_size]
            for index in range(0, len(mention_targets), chunk_size)
        ]
        first_chunk = chunks[0] if chunks else []
        message = reminder_message(
            troupe,
            rehearsal,
            minutes,
            now,
            at_all=at_all,
            mention_participants=first_chunk,
        )
        sent_id = await self._send_group(troupe.group_id, message)
        for chunk in chunks[1:]:
            await self._send_group(troupe.group_id, mention_continuation(chunk))
        if troupe.private_remind:
            for participant in rehearsal.precise_participants:
                try:
                    await self._send_private(
                        participant.qq_id or "",
                        private_reminder_message(troupe, rehearsal, minutes, now),
                    )
                except Exception as exc:
                    log.warning("私聊提醒失败 qq=%s: %s", participant.qq_id, exc)
                    self.repo.add_audit(
                        troupe.id,
                        "system",
                        "reminder_failed",
                        target=f"{participant.display_name}({participant.qq_id})",
                        summary="私聊提醒失败",
                        result="fail",
                        message=str(exc),
                        now=now,
                    )
        self.repo.mark_job_sent(job.id, sent_id, now)
        self.repo.add_audit(
            troupe.id,
            "system",
            "reminder_sent",
            target=f"编号{rehearsal.seq}",
            summary=f"提前{minutes}分钟提醒",
            now=now,
        )

    async def _reminder_targets(
        self, troupe: Troupe, rehearsal: Rehearsal, bot_admin: bool
    ) -> tuple[bool, list[Participant]]:
        if rehearsal.participant_mode == "all":
            if bot_admin:
                return True, []
            members = await self._group_members(troupe.group_id)
            return False, self._members_as_participants(members)
        precise = rehearsal.precise_participants
        if rehearsal.notify_all:
            if bot_admin:
                return True, []
            if troupe.degrade_at_scope == "all_members":
                members = await self._group_members(troupe.group_id)
                return False, self._members_as_participants(members)
            return False, precise
        return False, precise

    def _members_as_participants(self, members: list[Member]) -> list[Participant]:
        return [
            Participant.from_qq(member.qq, member.display_name)
            for member in members
            if str(member.qq) != str(self.gateway.self_id)
        ]
