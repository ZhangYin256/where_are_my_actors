#!/usr/bin/env python3
"""容器健康检查：数据库可读 + 提醒循环心跳新鲜。

退出码 0 表示健康，1 表示异常。Dockerfile 的 HEALTHCHECK 会调用本脚本。
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

DB_PATH = os.environ.get("REHEARSAL_DB_PATH", "/data/rehearsal.db")
HEARTBEAT_PATH = os.environ.get("REHEARSAL_HEARTBEAT_PATH", "/data/heartbeat")
MAX_HEARTBEAT_AGE = int(os.environ.get("REHEARSAL_HEALTH_MAX_AGE", "180"))


def fail(message: str) -> int:
    print(f"unhealthy: {message}", file=sys.stderr)
    return 1


def main() -> int:
    db_path = Path(DB_PATH)
    if not db_path.exists():
        return fail(f"数据库不存在：{db_path}")
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        result = connection.execute("PRAGMA quick_check").fetchone()
        connection.close()
    except sqlite3.Error as exc:
        return fail(f"数据库不可读：{exc}")
    if not result or str(result[0]).lower() != "ok":
        return fail(f"数据库完整性检查失败：{result}")

    heartbeat = Path(HEARTBEAT_PATH)
    if not heartbeat.exists():
        return fail(f"心跳文件不存在：{heartbeat}")
    try:
        stamp = datetime.fromisoformat(heartbeat.read_text(encoding="utf-8").strip())
    except (OSError, ValueError) as exc:
        return fail(f"心跳文件无效：{exc}")
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    age = (datetime.now(UTC) - stamp.astimezone(UTC)).total_seconds()
    if age > MAX_HEARTBEAT_AGE:
        return fail(f"提醒循环已 {int(age)} 秒未更新心跳（阈值 {MAX_HEARTBEAT_AGE} 秒）")
    print(f"healthy: 数据库正常，心跳 {int(age)} 秒前")
    return 0


if __name__ == "__main__":
    sys.exit(main())
