#!/usr/bin/env python3
"""恢复 / 添加机器人管理员（忘记配置或管理员被全部移除时使用）。

用法::

    python scripts/grant_admin.py 123456789
    docker compose exec -T bot python scripts/grant_admin.py 123456789

默认读取 ``REHEARSAL_DB_PATH``，容器内为 ``/data/rehearsal.db``。
执行后立即生效，不需要重启机器人。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rehearsal.db import Database, Repository  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="恢复排练通知机器人管理员")
    parser.add_argument("qq", nargs="+", help="要恢复/添加的管理员 QQ 号")
    parser.add_argument(
        "--db",
        default=os.environ.get("REHEARSAL_DB_PATH", str(ROOT / "data" / "rehearsal.db")),
        help="SQLite 数据库路径",
    )
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"数据库不存在：{db_path}", file=sys.stderr)
        return 1
    database = Database(str(db_path))
    try:
        repo = Repository(database)
        for qq in args.qq:
            value = str(qq).strip()
            if not value.isdigit() or len(value) < 5:
                print(f"无效 QQ：{qq}", file=sys.stderr)
                return 2
            repo.add_admin(value, granted_by="cli", source="manual")
            print(f"已授予管理员：{value}")
        print("当前有效管理员：" + "、".join(repo.list_admins()))
    finally:
        database.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
