"""排练通知 QQ Bot · NoneBot2 插件入口。

业务逻辑全部位于 ``rehearsal`` 核心包，插件只负责：

* 把 OneBot 消息转换为与平台无关的 :class:`CommandContext`；
* 调用 :class:`RehearsalService`；
* 把回复发送回 QQ；
* 启动提醒循环、同步群主 / 群管理员身份。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from nonebot import get_driver, get_plugin_config, on_message, on_notice
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupAdminNoticeEvent,
    GroupMessageEvent,
    MessageEvent,
)
from nonebot.log import logger

from rehearsal.config import Settings
from rehearsal.db import Database, Repository
from rehearsal.onebot import from_onebot
from rehearsal.service import CommandContext, RehearsalService
from rehearsal.worker import ReminderWorker

from .config import Config
from .gateway import OneBotGateway

plugin_config = get_plugin_config(Config)
driver = get_driver()

_service: RehearsalService | None = None
_gateway: OneBotGateway | None = None
_database: Database | None = None
_worker: ReminderWorker | None = None
_worker_task: asyncio.Task | None = None


class _LoguruHandler(logging.Handler):
    """把标准库 logging 转发到 NoneBot 的 loguru。"""

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - 日志桥
        try:
            logger.opt(depth=6, exception=record.exc_info).log(
                record.levelname, record.getMessage()
            )
        except Exception:
            self.handleError(record)


def _install_logging() -> None:
    handler = _LoguruHandler()
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)
    logging.getLogger("rehearsal").setLevel(logging.INFO)


_install_logging()


def _build_settings() -> Settings:
    return Settings(
        admins=tuple(str(qq) for qq in plugin_config.rehearsal_admins),
        command_prefix=plugin_config.rehearsal_command_prefix,
        timezone=plugin_config.rehearsal_timezone,
        db_path=plugin_config.rehearsal_db_path,
        default_remind_minutes=plugin_config.rehearsal_default_remind_minutes,
        default_private_remind=plugin_config.rehearsal_default_private_remind,
        default_evening_threshold=plugin_config.rehearsal_evening_threshold,
        default_degrade_scope=plugin_config.rehearsal_degrade_at_scope,
        send_interval=plugin_config.rehearsal_send_interval,
        max_message_chars=plugin_config.rehearsal_max_message_chars,
        max_reminder_attempts=plugin_config.rehearsal_max_reminder_attempts,
        reminder_tick_seconds=plugin_config.rehearsal_reminder_tick_seconds,
        reminder_grace_seconds=plugin_config.rehearsal_reminder_grace_seconds,
        member_cache_seconds=plugin_config.rehearsal_member_cache_seconds,
        at_chunk_size=plugin_config.rehearsal_at_chunk_size,
        allow_owner_init=plugin_config.rehearsal_allow_owner_init,
        heartbeat_path=plugin_config.rehearsal_heartbeat_path,
        log_dir=plugin_config.rehearsal_log_dir,
    )


@driver.on_startup
async def _startup() -> None:
    global _service, _gateway, _database, _worker, _worker_task
    if _service is not None:
        return
    settings = _build_settings()
    _database = Database(settings.db_path)
    _gateway = OneBotGateway(
        max_message_chars=settings.max_message_chars,
        send_interval=settings.send_interval,
    )
    _service = RehearsalService(Repository(_database), _gateway, settings)
    await _service.startup()
    _worker = ReminderWorker(
        _service,
        interval=settings.reminder_tick_seconds,
        heartbeat_path=settings.heartbeat_path,
    )
    _worker_task = asyncio.create_task(_worker.run(), name="rehearsal-reminder")
    logger.info(
        "排练通知机器人已启动：数据库 {}，提醒间隔 {}s",
        settings.db_path,
        settings.reminder_tick_seconds,
    )


@driver.on_shutdown
async def _shutdown() -> None:
    global _service, _gateway, _database, _worker, _worker_task
    if _worker is not None:
        _worker.stop()
    if _worker_task is not None:
        _worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _worker_task
    if _database is not None:
        _database.close()
    _service = None
    _gateway = None
    _worker = None
    _worker_task = None
    _database = None


@driver.on_bot_connect
async def _on_bot_connect(bot: Bot) -> None:
    if _gateway is not None:
        _gateway.bind(bot)
    logger.info("QQ 账号已连接：{}", bot.self_id)


matcher = on_message(priority=10, block=False)


@matcher.handle()
async def _handle_message(bot: Bot, event: MessageEvent) -> None:
    if _service is None or _gateway is None:
        return
    if str(event.user_id) == str(bot.self_id):
        return
    prefix = plugin_config.rehearsal_command_prefix
    text = from_onebot(event.get_message()).strip()
    if not text.startswith(prefix):
        return
    command_text = text[len(prefix) :].strip()
    if not command_text:
        return

    _gateway.bind(bot)
    is_group = isinstance(event, GroupMessageEvent)
    nickname = ""
    sender_role = None
    if is_group:
        nickname = event.sender.card or event.sender.nickname or ""
        sender_role = event.sender.role
    else:
        nickname = getattr(event.sender, "nickname", "") or ""

    context = CommandContext(
        scope="group" if is_group else "private",
        qq=str(event.user_id),
        nickname=nickname,
        group_id=str(event.group_id) if is_group else None,
        text=command_text,
        sender_role=sender_role,
    )
    try:
        replies = await _service.handle(context)
    except Exception:
        logger.exception("处理消息失败")
        return
    for reply in replies:
        try:
            if reply.group_id:
                await _gateway.send_group(reply.group_id, reply.message)
            elif reply.user_id:
                await _gateway.send_private(reply.user_id, reply.message)
        except Exception:
            logger.exception("发送回复失败")


admin_notice = on_notice(priority=10, block=False)


@admin_notice.handle()
async def _handle_admin_notice(bot: Bot, event: GroupAdminNoticeEvent) -> None:
    if _service is None or _gateway is None:
        return
    _gateway.bind(bot)
    try:
        await _service.sync_group_roles(str(event.group_id))
    except Exception as exc:
        logger.warning("群管理变更后同步角色失败：{}", exc)
