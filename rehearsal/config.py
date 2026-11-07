"""运行时配置（与 NoneBot 解耦）。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Settings:
    admins: tuple[str, ...] = ()
    command_prefix: str = "#"
    timezone: str = "Asia/Shanghai"
    db_path: str = "data/rehearsal.db"
    default_remind_minutes: str = "30"
    default_private_remind: bool = False
    default_evening_threshold: int = 9
    default_degrade_scope: str = "participants"
    default_publish_confirm: bool = False
    send_interval: float = 1.0
    max_message_chars: int = 1500
    max_reminder_attempts: int = 3
    reminder_tick_seconds: int = 10
    reminder_grace_seconds: int = 90
    member_cache_seconds: int = 300
    at_chunk_size: int = 20
    allow_owner_init: bool = True
    heartbeat_path: str = "data/heartbeat"
    log_dir: str = "logs"
    extra: dict = field(default_factory=dict)
