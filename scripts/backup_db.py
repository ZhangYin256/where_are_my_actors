#!/usr/bin/env python3
"""在线备份 SQLite 数据库（WAL 安全），并清理过期备份。

示例::

    python scripts/backup_db.py
    python scripts/backup_db.py --db /data/rehearsal.db --out-dir /data/backups --keep 14

容器内执行::

    docker compose exec -T bot python scripts/backup_db.py --keep 14
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def backup(db_path: Path, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = out_dir / f"rehearsal-{stamp}.db"
    if target.exists():  # 同一秒内重复执行
        target = out_dir / f"rehearsal-{stamp}-{os.getpid()}.db"
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=15)
    destination = sqlite3.connect(str(target))
    try:
        source.backup(destination)
        result = destination.execute("PRAGMA quick_check").fetchone()
        if not result or str(result[0]).lower() != "ok":
            raise RuntimeError(f"备份完整性检查失败：{result}")
    finally:
        destination.close()
        source.close()
    return target


def cleanup(out_dir: Path, keep: int) -> list[Path]:
    backups = sorted(out_dir.glob("rehearsal-*.db"), key=lambda path: path.stat().st_mtime)
    removed: list[Path] = []
    while len(backups) > keep:
        oldest = backups.pop(0)
        oldest.unlink(missing_ok=True)
        removed.append(oldest)
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="备份排练通知机器人数据库")
    parser.add_argument(
        "--db",
        default=os.environ.get("REHEARSAL_DB_PATH", "data/rehearsal.db"),
        help="源数据库路径",
    )
    parser.add_argument("--out-dir", help="备份目录，默认与数据库同级的 backups/")
    parser.add_argument("--keep", type=int, default=14, help="保留最近多少份备份（默认 14）")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"数据库不存在：{db_path}", file=sys.stderr)
        return 1
    out_dir = Path(args.out_dir) if args.out_dir else db_path.parent / "backups"
    try:
        target = backup(db_path, out_dir)
        removed = cleanup(out_dir, max(1, args.keep))
    except (sqlite3.Error, RuntimeError, OSError) as exc:
        print(f"备份失败：{exc}", file=sys.stderr)
        return 1
    if not args.quiet:
        print(f"备份完成：{target}")
        for path in removed:
            print(f"已清理过期备份：{path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
