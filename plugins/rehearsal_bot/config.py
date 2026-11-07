"""NoneBot2 插件配置（环境变量前缀 REHEARSAL_）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Config(BaseModel):
    rehearsal_admins: set[int] = Field(default_factory=set)
    rehearsal_db_path: str = "data/rehearsal.db"
    rehearsal_command_prefix: str = "#"
    rehearsal_timezone: str = "Asia/Shanghai"
    rehearsal_default_remind_minutes: str = "30"
    rehearsal_default_private_remind: bool = False
    rehearsal_publish_confirm: bool = False
    rehearsal_evening_threshold: int = 9
    rehearsal_degrade_at_scope: str = "participants"
    rehearsal_send_interval: float = 1.0
    rehearsal_max_message_chars: int = 1500
    rehearsal_max_reminder_attempts: int = 3
    rehearsal_reminder_tick_seconds: int = 10
    rehearsal_reminder_grace_seconds: int = 90
    rehearsal_member_cache_seconds: int = 300
    rehearsal_at_chunk_size: int = 20
    rehearsal_allow_owner_init: bool = True
    rehearsal_heartbeat_path: str = "data/heartbeat"
    rehearsal_log_dir: str = "logs"

    model_config = {"extra": "ignore"}
