"""SQLite 持久化层。

设计要点：

* WAL 模式，写少读多；
* 所有时间以 UTC ISO 字符串存储，展示时转换到剧组时区；
* 排练编号（seq）在剧组内自增，命令与展示均使用该编号；
* 提醒任务持久化，进程重启后可恢复；
* 修改采用当前值覆盖，不保留历史版本（审计日志记录动作摘要）。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .models import (
    ActorProfile,
    AuditEntry,
    Participant,
    PendingAction,
    Rehearsal,
    RehearsalDraft,
    ReminderJob,
    RoleBinding,
    Troupe,
)
from .timeutil import iso, now_utc, parse_iso

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS counter (
    name  TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS user (
    qq_id     TEXT PRIMARY KEY,
    nickname  TEXT NOT NULL DEFAULT '',
    cached_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS troupe (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    name                      TEXT NOT NULL,
    group_id                  TEXT NOT NULL UNIQUE,
    timezone                  TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    default_remind_minutes    TEXT NOT NULL DEFAULT '30',
    private_remind            INTEGER NOT NULL DEFAULT 0,
    ambiguous_evening_threshold INTEGER NOT NULL DEFAULT 9,
    show_tags                 INTEGER NOT NULL DEFAULT 0,
    show_empty                INTEGER NOT NULL DEFAULT 0,
    degrade_at_scope          TEXT NOT NULL DEFAULT 'participants',
    status                    TEXT NOT NULL DEFAULT 'active',
    created_at                TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_binding (
    qq_id      TEXT PRIMARY KEY,
    source     TEXT NOT NULL DEFAULT 'manual',
    granted_by TEXT NOT NULL DEFAULT '',
    granted_at TEXT NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS role_binding (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    troupe_id  INTEGER NOT NULL REFERENCES troupe(id) ON DELETE CASCADE,
    qq_id      TEXT NOT NULL,
    role       TEXT NOT NULL CHECK (role IN ('director', 'assistant', 'actor')),
    source     TEXT NOT NULL CHECK (source IN ('manual', 'auto_group_admin')),
    granted_by TEXT NOT NULL DEFAULT '',
    granted_at TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'active',
    UNIQUE (troupe_id, qq_id, role, source)
);
CREATE INDEX IF NOT EXISTS ix_role_troupe_qq ON role_binding(troupe_id, qq_id, status);

CREATE TABLE IF NOT EXISTS actor_profile (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    troupe_id    INTEGER NOT NULL REFERENCES troupe(id) ON DELETE CASCADE,
    qq_id        TEXT,
    display_name TEXT NOT NULL,
    aliases      TEXT NOT NULL DEFAULT '[]',
    remark_tags  TEXT NOT NULL DEFAULT '[]',
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_actor_troupe ON actor_profile(troupe_id, active);
CREATE UNIQUE INDEX IF NOT EXISTS ux_actor_qq
    ON actor_profile(troupe_id, qq_id) WHERE qq_id IS NOT NULL AND active = 1;
CREATE UNIQUE INDEX IF NOT EXISTS ux_actor_text
    ON actor_profile(troupe_id, display_name) WHERE qq_id IS NULL AND active = 1;

CREATE TABLE IF NOT EXISTS rehearsal (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    troupe_id        INTEGER NOT NULL REFERENCES troupe(id) ON DELETE CASCADE,
    seq              INTEGER NOT NULL,
    title            TEXT NOT NULL DEFAULT '#排练通知',
    location         TEXT NOT NULL DEFAULT '',
    start_at         TEXT NOT NULL,
    end_at           TEXT NOT NULL,
    content          TEXT NOT NULL DEFAULT '[]',
    remark           TEXT NOT NULL DEFAULT '',
    raw_text         TEXT NOT NULL DEFAULT '',
    participant_mode TEXT NOT NULL DEFAULT 'specified',
    notify_all       INTEGER NOT NULL DEFAULT 0,
    remind_minutes   TEXT NOT NULL DEFAULT '30',
    status           TEXT NOT NULL DEFAULT 'published',
    created_by       TEXT NOT NULL DEFAULT '',
    updated_by       TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    published_at     TEXT,
    deleted_at       TEXT,
    UNIQUE (troupe_id, seq)
);
CREATE INDEX IF NOT EXISTS ix_rehearsal_troupe_start ON rehearsal(troupe_id, start_at);
CREATE INDEX IF NOT EXISTS ix_rehearsal_status ON rehearsal(status);

CREATE TABLE IF NOT EXISTS rehearsal_participant (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    rehearsal_id INTEGER NOT NULL REFERENCES rehearsal(id) ON DELETE CASCADE,
    qq_id        TEXT,
    display_name TEXT NOT NULL,
    remark_tag   TEXT NOT NULL DEFAULT '',
    precise      INTEGER NOT NULL DEFAULT 0,
    note         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_participant_rehearsal ON rehearsal_participant(rehearsal_id);
CREATE INDEX IF NOT EXISTS ix_participant_qq ON rehearsal_participant(qq_id, precise);

CREATE TABLE IF NOT EXISTS reminder_job (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    rehearsal_id    INTEGER NOT NULL REFERENCES rehearsal(id) ON DELETE CASCADE,
    troupe_id       INTEGER NOT NULL,
    trigger_at      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT NOT NULL DEFAULT '',
    sent_message_id TEXT,
    sent_at         TEXT,
    updated_at      TEXT NOT NULL,
    UNIQUE (rehearsal_id, trigger_at)
);
CREATE INDEX IF NOT EXISTS ix_reminder_due ON reminder_job(status, trigger_at);

CREATE TABLE IF NOT EXISTS pending_action (
    id           TEXT PRIMARY KEY,
    troupe_id    INTEGER NOT NULL,
    kind         TEXT NOT NULL,
    operator_qq  TEXT NOT NULL,
    group_id     TEXT NOT NULL,
    payload      TEXT NOT NULL,
    rehearsal_id INTEGER,
    status       TEXT NOT NULL DEFAULT 'pending',
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_pending_operator ON pending_action(operator_qq, status);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    troupe_id  INTEGER,
    qq_id      TEXT NOT NULL,
    action     TEXT NOT NULL,
    target     TEXT NOT NULL DEFAULT '',
    summary    TEXT NOT NULL DEFAULT '',
    result     TEXT NOT NULL DEFAULT 'success',
    message    TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_audit_troupe ON audit_log(troupe_id, id);

CREATE TABLE IF NOT EXISTS setting (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

PENDING_PREFIX = {
    "publish": "P",
    "modify": "M",
    "republish": "R",
}


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any) -> bool:
    return bool(int(value)) if isinstance(value, (int, str)) else bool(value)


class Database:
    """SQLite 连接与建表。"""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(
            self.path,
            check_same_thread=False,
            isolation_level=None,
            timeout=15.0,
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=15000")
        self._create_schema()

    # -- 基础 ---------------------------------------------------------
    def _create_schema(self) -> None:
        with self._lock:
            self.conn.executescript(SCHEMA_SQL)
            version = self.conn.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            elif version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"数据库版本 {version} 高于程序支持的版本 {SCHEMA_VERSION}，请升级程序。"
                )

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            else:
                self.conn.execute("COMMIT")

    def query_all(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self.conn.execute(sql, params).fetchall())

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(sql, params).fetchone()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self._lock:
            cursor = self.conn.execute(sql, params)
            return cursor.rowcount

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def backup_to(self, target: str | Path) -> None:
        """使用 SQLite 在线备份 API 复制数据库（WAL 安全）。"""
        target_path = Path(target)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            destination = sqlite3.connect(str(target_path))
            try:
                self.conn.backup(destination)
            finally:
                destination.close()


class Repository:
    """业务数据访问。"""

    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # 用户
    # ------------------------------------------------------------------
    def upsert_user(self, qq_id: str, nickname: str = "", now: datetime | None = None) -> None:
        stamp = iso(now or now_utc())
        self.db.execute(
            """
            INSERT INTO user (qq_id, nickname, cached_at) VALUES (?, ?, ?)
            ON CONFLICT(qq_id) DO UPDATE SET
                nickname = CASE WHEN excluded.nickname <> '' THEN excluded.nickname
                                ELSE user.nickname END,
                cached_at = excluded.cached_at
            """,
            (str(qq_id), nickname or "", stamp),
        )

    def get_user(self, qq_id: str) -> sqlite3.Row | None:
        return self.db.query_one("SELECT * FROM user WHERE qq_id = ?", (str(qq_id),))

    # ------------------------------------------------------------------
    # 剧组
    # ------------------------------------------------------------------
    def create_troupe(
        self,
        name: str,
        group_id: str,
        *,
        timezone: str = "Asia/Shanghai",
        default_remind_minutes: str = "30",
        private_remind: bool = False,
        ambiguous_evening_threshold: int = 9,
        show_tags: bool = False,
        show_empty: bool = False,
        degrade_at_scope: str = "participants",
        now: datetime | None = None,
    ) -> Troupe:
        stamp = iso(now or now_utc())
        cursor = self.db.conn.execute(
            """
            INSERT INTO troupe (
                name, group_id, timezone, default_remind_minutes, private_remind,
                ambiguous_evening_threshold, show_tags, show_empty, degrade_at_scope,
                status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
            """,
            (
                name,
                str(group_id),
                timezone,
                default_remind_minutes,
                int(private_remind),
                int(ambiguous_evening_threshold),
                int(show_tags),
                int(show_empty),
                degrade_at_scope,
                stamp,
            ),
        )
        troupe_id = int(cursor.lastrowid or 0)
        troupe = self.get_troupe(troupe_id)
        assert troupe is not None
        return troupe

    def get_troupe(self, troupe_id: int) -> Troupe | None:
        row = self.db.query_one("SELECT * FROM troupe WHERE id = ?", (int(troupe_id),))
        return _row_to_troupe(row) if row else None

    def get_troupe_by_group(self, group_id: str) -> Troupe | None:
        row = self.db.query_one("SELECT * FROM troupe WHERE group_id = ?", (str(group_id),))
        return _row_to_troupe(row) if row else None

    def get_troupe_by_name(self, name: str) -> Troupe | None:
        row = self.db.query_one(
            "SELECT * FROM troupe WHERE name = ? AND status = 'active' ORDER BY id LIMIT 1",
            (name.strip(),),
        )
        return _row_to_troupe(row) if row else None

    def find_troupes_by_name(self, keyword: str) -> list[Troupe]:
        rows = self.db.query_all(
            "SELECT * FROM troupe WHERE name LIKE ? AND status = 'active' ORDER BY id",
            (f"%{keyword.strip()}%",),
        )
        return [_row_to_troupe(row) for row in rows]

    def list_active_troupes(self) -> list[Troupe]:
        rows = self.db.query_all("SELECT * FROM troupe WHERE status = 'active' ORDER BY id")
        return [_row_to_troupe(row) for row in rows]

    def update_troupe(self, troupe_id: int, **fields: Any) -> None:
        allowed = {
            "name",
            "timezone",
            "default_remind_minutes",
            "private_remind",
            "ambiguous_evening_threshold",
            "show_tags",
            "show_empty",
            "degrade_at_scope",
            "status",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        for key in ("private_remind", "show_tags", "show_empty"):
            if key in updates:
                updates[key] = int(bool(updates[key]))
        if "ambiguous_evening_threshold" in updates:
            updates["ambiguous_evening_threshold"] = int(updates["ambiguous_evening_threshold"])
        assignments = ", ".join(f"{key} = ?" for key in updates)
        self.db.execute(
            f"UPDATE troupe SET {assignments} WHERE id = ?",
            (*updates.values(), int(troupe_id)),
        )

    def archive_troupe(self, troupe_id: int) -> None:
        self.db.execute("UPDATE troupe SET status = 'archived' WHERE id = ?", (int(troupe_id),))

    # ------------------------------------------------------------------
    # 角色
    # ------------------------------------------------------------------
    def add_role(
        self,
        troupe_id: int,
        qq_id: str,
        role: str,
        *,
        source: str = "manual",
        granted_by: str = "",
        now: datetime | None = None,
    ) -> None:
        stamp = iso(now or now_utc())
        self.db.execute(
            """
            INSERT INTO role_binding (troupe_id, qq_id, role, source, granted_by, granted_at, status)
            VALUES (?, ?, ?, ?, ?, ?, 'active')
            ON CONFLICT(troupe_id, qq_id, role, source) DO UPDATE SET
                status = 'active',
                granted_by = excluded.granted_by,
                granted_at = excluded.granted_at
            """,
            (int(troupe_id), str(qq_id), role, source, granted_by, stamp),
        )

    def remove_role(
        self, troupe_id: int, qq_id: str, role: str, *, source: str | None = None
    ) -> int:
        if source:
            return self.db.execute(
                """
                UPDATE role_binding SET status = 'removed'
                WHERE troupe_id = ? AND qq_id = ? AND role = ? AND source = ? AND status = 'active'
                """,
                (int(troupe_id), str(qq_id), role, source),
            )
        return self.db.execute(
            """
            UPDATE role_binding SET status = 'removed'
            WHERE troupe_id = ? AND qq_id = ? AND role = ? AND status = 'active'
            """,
            (int(troupe_id), str(qq_id), role),
        )

    def list_roles(self, troupe_id: int, *, active_only: bool = True) -> list[RoleBinding]:
        sql = "SELECT * FROM role_binding WHERE troupe_id = ?"
        params: list[Any] = [int(troupe_id)]
        if active_only:
            sql += " AND status = 'active'"
        sql += " ORDER BY id"
        return [
            RoleBinding(
                qq_id=row["qq_id"],
                role=row["role"],
                source=row["source"],
                granted_by=row["granted_by"],
                granted_at=parse_iso(row["granted_at"]),
            )
            for row in self.db.query_all(sql, params)
        ]

    def get_roles(self, troupe_id: int, qq_id: str) -> list[RoleBinding]:
        rows = self.db.query_all(
            """
            SELECT * FROM role_binding
            WHERE troupe_id = ? AND qq_id = ? AND status = 'active'
            """,
            (int(troupe_id), str(qq_id)),
        )
        return [
            RoleBinding(
                qq_id=row["qq_id"],
                role=row["role"],
                source=row["source"],
                granted_by=row["granted_by"],
                granted_at=parse_iso(row["granted_at"]),
            )
            for row in rows
        ]

    def set_auto_directors(self, troupe_id: int, qq_ids: Iterable[str]) -> tuple[int, int]:
        """同步群主 / 群管理员自动导演角色，返回 (新增, 撤销)。"""
        desired = {str(qq) for qq in qq_ids if str(qq)}
        rows = self.db.query_all(
            """
            SELECT qq_id FROM role_binding
            WHERE troupe_id = ? AND role = 'director' AND source = 'auto_group_admin'
              AND status = 'active'
            """,
            (int(troupe_id),),
        )
        current = {row["qq_id"] for row in rows}
        added = desired - current
        removed = current - desired
        for qq in added:
            self.add_role(
                troupe_id, qq, "director", source="auto_group_admin", granted_by="system"
            )
        for qq in removed:
            self.db.execute(
                """
                UPDATE role_binding SET status = 'removed'
                WHERE troupe_id = ? AND qq_id = ? AND role = 'director'
                  AND source = 'auto_group_admin' AND status = 'active'
                """,
                (int(troupe_id), qq),
            )
        return len(added), len(removed)

    def list_troupes_for_role(self, qq_id: str) -> list[Troupe]:
        rows = self.db.query_all(
            """
            SELECT t.* FROM troupe t
            JOIN role_binding rb ON rb.troupe_id = t.id
            WHERE rb.qq_id = ? AND rb.status = 'active' AND t.status = 'active'
            GROUP BY t.id
            ORDER BY t.id
            """,
            (str(qq_id),),
        )
        return [_row_to_troupe(row) for row in rows]

    def list_troupes_for_user(self, qq_id: str) -> list[Troupe]:
        """用户有角色或演员记录的剧组。"""
        rows = self.db.query_all(
            """
            SELECT t.* FROM troupe t
            WHERE t.status = 'active' AND (
                EXISTS (
                    SELECT 1 FROM role_binding rb
                    WHERE rb.troupe_id = t.id AND rb.qq_id = ? AND rb.status = 'active'
                )
                OR EXISTS (
                    SELECT 1 FROM actor_profile ap
                    WHERE ap.troupe_id = t.id AND ap.qq_id = ? AND ap.active = 1
                )
            )
            ORDER BY t.id
            """,
            (str(qq_id), str(qq_id)),
        )
        return [_row_to_troupe(row) for row in rows]

    def is_registered_in_troupe(self, troupe_id: int, qq_id: str) -> bool:
        row = self.db.query_one(
            """
            SELECT 1 FROM role_binding
            WHERE troupe_id = ? AND qq_id = ? AND status = 'active'
            UNION ALL
            SELECT 1 FROM actor_profile
            WHERE troupe_id = ? AND qq_id = ? AND active = 1
            LIMIT 1
            """,
            (int(troupe_id), str(qq_id), int(troupe_id), str(qq_id)),
        )
        return row is not None

    # ------------------------------------------------------------------
    # 管理员
    # ------------------------------------------------------------------
    def add_admin(self, qq_id: str, granted_by: str = "", source: str = "manual") -> None:
        stamp = iso(now_utc())
        self.db.execute(
            """
            INSERT INTO admin_binding (qq_id, source, granted_by, granted_at, active)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(qq_id) DO UPDATE SET active = 1, granted_by = excluded.granted_by,
                granted_at = excluded.granted_at, source = excluded.source
            """,
            (str(qq_id), source, granted_by, stamp),
        )

    def remove_admin(self, qq_id: str) -> int:
        return self.db.execute(
            "UPDATE admin_binding SET active = 0 WHERE qq_id = ? AND active = 1", (str(qq_id),)
        )

    def list_admins(self) -> list[str]:
        rows = self.db.query_all("SELECT qq_id FROM admin_binding WHERE active = 1 ORDER BY qq_id")
        return [row["qq_id"] for row in rows]

    def is_admin(self, qq_id: str) -> bool:
        row = self.db.query_one(
            "SELECT 1 FROM admin_binding WHERE qq_id = ? AND active = 1", (str(qq_id),)
        )
        return row is not None

    def has_admin_record(self, qq_id: str) -> bool:
        row = self.db.query_one("SELECT 1 FROM admin_binding WHERE qq_id = ?", (str(qq_id),))
        return row is not None

    # ------------------------------------------------------------------
    # 演员
    # ------------------------------------------------------------------
    def upsert_actor(
        self,
        troupe_id: int,
        qq_id: str | None,
        display_name: str,
        *,
        aliases: Iterable[str] = (),
        remark_tags: Iterable[str] = (),
        now: datetime | None = None,
    ) -> ActorProfile:
        stamp = iso(now or now_utc())
        alias_list = [a for a in dict.fromkeys(a.strip() for a in aliases) if a]
        tag_list = [t for t in dict.fromkeys(t.strip() for t in remark_tags) if t]
        existing = self._find_actor_row(troupe_id, qq_id, display_name)
        if existing:
            merged_aliases = list(
                dict.fromkeys([*_loads(existing["aliases"], []), *alias_list])
            )
            merged_tags = list(dict.fromkeys([*_loads(existing["remark_tags"], []), *tag_list]))
            self.db.execute(
                """
                UPDATE actor_profile
                SET active = 1, display_name = ?, aliases = ?, remark_tags = ?
                WHERE id = ?
                """,
                (
                    display_name or existing["display_name"],
                    json.dumps(merged_aliases, ensure_ascii=False),
                    json.dumps(merged_tags, ensure_ascii=False),
                    existing["id"],
                ),
            )
            actor_id = int(existing["id"])
        else:
            self.db.execute(
                """
                INSERT INTO actor_profile
                    (troupe_id, qq_id, display_name, aliases, remark_tags, active, created_at)
                VALUES (?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    int(troupe_id),
                    str(qq_id) if qq_id else None,
                    display_name,
                    json.dumps(alias_list, ensure_ascii=False),
                    json.dumps(tag_list, ensure_ascii=False),
                    stamp,
                ),
            )
            row = self.db.query_one(
                "SELECT * FROM actor_profile WHERE troupe_id = ? AND display_name = ? "
                "ORDER BY id DESC LIMIT 1",
                (int(troupe_id), display_name),
            )
            actor_id = int(row["id"]) if row else 0
        profile = self.get_actor_by_id(actor_id)
        assert profile is not None
        return profile

    def _find_actor_row(self, troupe_id: int, qq_id: str | None, display_name: str):
        if qq_id:
            return self.db.query_one(
                "SELECT * FROM actor_profile WHERE troupe_id = ? AND qq_id = ? "
                "ORDER BY active DESC, id LIMIT 1",
                (int(troupe_id), str(qq_id)),
            )
        return self.db.query_one(
            "SELECT * FROM actor_profile WHERE troupe_id = ? AND qq_id IS NULL "
            "AND display_name = ? ORDER BY active DESC, id LIMIT 1",
            (int(troupe_id), display_name),
        )

    def get_actor_by_id(self, actor_id: int) -> ActorProfile | None:
        row = self.db.query_one("SELECT * FROM actor_profile WHERE id = ?", (int(actor_id),))
        return _row_to_actor(row) if row else None

    def get_actor(self, troupe_id: int, qq_id: str) -> ActorProfile | None:
        row = self.db.query_one(
            "SELECT * FROM actor_profile WHERE troupe_id = ? AND qq_id = ? AND active = 1",
            (int(troupe_id), str(qq_id)),
        )
        return _row_to_actor(row) if row else None

    def list_actors(self, troupe_id: int, *, active_only: bool = True) -> list[ActorProfile]:
        sql = "SELECT * FROM actor_profile WHERE troupe_id = ?"
        if active_only:
            sql += " AND active = 1"
        sql += " ORDER BY id"
        return [_row_to_actor(row) for row in self.db.query_all(sql, (int(troupe_id),))]

    def find_actor_by_name(self, troupe_id: int, name: str) -> ActorProfile | None:
        keyword = (name or "").strip().lstrip("@")
        if not keyword:
            return None
        for actor in self.list_actors(troupe_id):
            if keyword in (actor.display_name, *actor.aliases):
                return actor
        return None

    def update_actor_aliases(self, actor_id: int, aliases: Iterable[str]) -> None:
        cleaned = [a.strip() for a in aliases if a and a.strip()]
        self.db.execute(
            "UPDATE actor_profile SET aliases = ? WHERE id = ?",
            (json.dumps(list(dict.fromkeys(cleaned)), ensure_ascii=False), int(actor_id)),
        )

    def update_actor_tags(self, actor_id: int, tags: Iterable[str]) -> None:
        cleaned = [t.strip() for t in tags if t and t.strip()]
        self.db.execute(
            "UPDATE actor_profile SET remark_tags = ? WHERE id = ?",
            (json.dumps(list(dict.fromkeys(cleaned)), ensure_ascii=False), int(actor_id)),
        )

    def deactivate_actor(self, troupe_id: int, actor_id: int) -> None:
        self.db.execute(
            "UPDATE actor_profile SET active = 0 WHERE id = ? AND troupe_id = ?",
            (int(actor_id), int(troupe_id)),
        )

    # ------------------------------------------------------------------
    # 排练
    # ------------------------------------------------------------------
    def next_seq(self, troupe_id: int) -> int:
        row = self.db.query_one(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM rehearsal WHERE troupe_id = ?",
            (int(troupe_id),),
        )
        return int(row["next"]) if row else 1

    def create_rehearsal(
        self,
        troupe_id: int,
        draft: RehearsalDraft,
        *,
        created_by: str,
        now: datetime,
        status: str = "published",
    ) -> Rehearsal:
        stamp = iso(now)
        with self.db.tx() as conn:
            seq = self.next_seq(troupe_id)
            cursor = conn.execute(
                """
                INSERT INTO rehearsal (
                    troupe_id, seq, title, location, start_at, end_at, content, remark,
                    raw_text, participant_mode, notify_all, remind_minutes, status,
                    created_by, updated_by, created_at, published_at
                ) VALUES (?, ?, '#排练通知', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(troupe_id),
                    seq,
                    draft.location,
                    iso(draft.start_at),
                    iso(draft.end_at),
                    json.dumps(draft.content, ensure_ascii=False),
                    draft.remark,
                    draft.raw_text,
                    draft.participant_mode,
                    int(draft.notify_all),
                    ",".join(str(m) for m in draft.remind_minutes),
                    status,
                    created_by,
                    created_by,
                    stamp,
                    stamp if status == "published" else None,
                ),
            )
            rehearsal_id = int(cursor.lastrowid or 0)
            self._insert_participants(conn, rehearsal_id, draft.participants)
        rehearsal = self.get_rehearsal_by_id(rehearsal_id)
        assert rehearsal is not None
        return rehearsal

    def _insert_participants(
        self, conn: sqlite3.Connection, rehearsal_id: int, participants: Sequence[Participant]
    ) -> None:
        conn.executemany(
            """
            INSERT INTO rehearsal_participant
                (rehearsal_id, qq_id, display_name, remark_tag, precise, note)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    int(rehearsal_id),
                    str(p.qq_id) if p.qq_id else None,
                    p.display_name,
                    p.remark_tag,
                    int(bool(p.precise and p.qq_id)),
                    p.note,
                )
                for p in participants
            ],
        )

    def get_rehearsal_by_id(self, rehearsal_id: int) -> Rehearsal | None:
        row = self.db.query_one("SELECT * FROM rehearsal WHERE id = ?", (int(rehearsal_id),))
        if not row:
            return None
        rehearsal = _row_to_rehearsal(row)
        rehearsal.participants = self.get_participants([rehearsal.id]).get(rehearsal.id, [])
        return rehearsal

    def get_rehearsal(
        self, troupe_id: int, seq: int, *, include_deleted: bool = False
    ) -> Rehearsal | None:
        sql = "SELECT * FROM rehearsal WHERE troupe_id = ? AND seq = ?"
        if not include_deleted:
            sql += " AND status <> 'deleted'"
        row = self.db.query_one(sql, (int(troupe_id), int(seq)))
        if not row:
            return None
        rehearsal = _row_to_rehearsal(row)
        rehearsal.participants = self.get_participants([rehearsal.id]).get(rehearsal.id, [])
        return rehearsal

    def update_rehearsal(
        self,
        rehearsal_id: int,
        draft: RehearsalDraft,
        *,
        updated_by: str,
        now: datetime,
    ) -> None:
        with self.db.tx() as conn:
            conn.execute(
                """
                UPDATE rehearsal SET
                    location = ?, start_at = ?, end_at = ?, content = ?, remark = ?,
                    participant_mode = ?, notify_all = ?, remind_minutes = ?,
                    updated_by = ?
                WHERE id = ?
                """,
                (
                    draft.location,
                    iso(draft.start_at),
                    iso(draft.end_at),
                    json.dumps(draft.content, ensure_ascii=False),
                    draft.remark,
                    draft.participant_mode,
                    int(draft.notify_all),
                    ",".join(str(m) for m in draft.remind_minutes),
                    updated_by,
                    int(rehearsal_id),
                ),
            )
            conn.execute(
                "DELETE FROM rehearsal_participant WHERE rehearsal_id = ?", (int(rehearsal_id),)
            )
            self._insert_participants(conn, rehearsal_id, draft.participants)

    def set_rehearsal_status(
        self,
        rehearsal_id: int,
        status: str,
        *,
        deleted_at: datetime | None = None,
        published_at: datetime | None = None,
    ) -> None:
        self.db.execute(
            """
            UPDATE rehearsal SET status = ?, deleted_at = COALESCE(?, deleted_at),
                published_at = COALESCE(?, published_at)
            WHERE id = ?
            """,
            (
                status,
                iso(deleted_at) if deleted_at else None,
                iso(published_at) if published_at else None,
                int(rehearsal_id),
            ),
        )

    def mark_republished(self, rehearsal_id: int, now: datetime) -> None:
        self.db.execute(
            "UPDATE rehearsal SET published_at = ? WHERE id = ?",
            (iso(now), int(rehearsal_id)),
        )

    def soft_delete(self, rehearsal_id: int, now: datetime, operator: str) -> None:
        self.db.execute(
            """
            UPDATE rehearsal SET status = 'deleted', deleted_at = ?, updated_by = ?
            WHERE id = ?
            """,
            (iso(now), operator, int(rehearsal_id)),
        )

    def hard_delete(self, rehearsal_id: int) -> None:
        self.db.execute("DELETE FROM rehearsal WHERE id = ?", (int(rehearsal_id),))

    def list_rehearsals(
        self,
        troupe_id: int,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        statuses: Sequence[str] = ("published",),
        order: str = "asc",
        limit: int | None = None,
    ) -> list[Rehearsal]:
        sql = "SELECT * FROM rehearsal WHERE troupe_id = ?"
        params: list[Any] = [int(troupe_id)]
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            sql += f" AND status IN ({placeholders})"
            params.extend(statuses)
        if start:
            sql += " AND start_at >= ?"
            params.append(iso(start))
        if end:
            sql += " AND start_at < ?"
            params.append(iso(end))
        direction = "DESC" if order.lower() == "desc" else "ASC"
        sql += f" ORDER BY start_at {direction}, id {direction}"
        if limit:
            sql += " LIMIT ?"
            params.append(int(limit))
        rows = self.db.query_all(sql, params)
        rehearsals = [_row_to_rehearsal(row) for row in rows]
        self._attach_participants(rehearsals)
        return rehearsals

    def list_participations(
        self,
        qq_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        statuses: Sequence[str] = ("published", "completed"),
        order: str = "asc",
        limit: int | None = 100,
    ) -> list[Rehearsal]:
        placeholders = ",".join("?" for _ in statuses) if statuses else "''"
        params: list[Any] = [str(qq_id)]
        sql = f"""
            SELECT r.* FROM rehearsal r
            JOIN rehearsal_participant p ON p.rehearsal_id = r.id
            WHERE p.qq_id = ? AND p.precise = 1
              AND r.status IN ({placeholders})
        """
        params.extend(statuses)
        if start:
            sql += " AND r.start_at >= ?"
            params.append(iso(start))
        if end:
            sql += " AND r.start_at < ?"
            params.append(iso(end))
        rows = self.db.query_all(sql, params)
        results = {row["id"]: _row_to_rehearsal(row) for row in rows}

        # 全体成员通知：仅对在该剧组有角色 / 演员记录的用户可见
        all_params: list[Any] = [str(qq_id), str(qq_id)]
        all_sql = f"""
            SELECT r.* FROM rehearsal r
            WHERE r.participant_mode = 'all' AND r.status IN ({placeholders})
              AND r.troupe_id IN (
                  SELECT troupe_id FROM role_binding WHERE qq_id = ? AND status = 'active'
                  UNION
                  SELECT troupe_id FROM actor_profile WHERE qq_id = ? AND active = 1
              )
        """
        all_params_params = list(statuses) + all_params
        if start:
            all_sql += " AND r.start_at >= ?"
            all_params_params.append(iso(start))
        if end:
            all_sql += " AND r.start_at < ?"
            all_params_params.append(iso(end))
        for row in self.db.query_all(all_sql, all_params_params):
            results.setdefault(row["id"], _row_to_rehearsal(row))

        rehearsals = list(results.values())
        rehearsals.sort(key=lambda r: (r.start_at, r.id), reverse=order.lower() == "desc")
        if limit:
            rehearsals = rehearsals[: int(limit)]
        self._attach_participants(rehearsals)
        return rehearsals

    def _attach_participants(self, rehearsals: Sequence[Rehearsal]) -> None:
        if not rehearsals:
            return
        mapping = self.get_participants([r.id for r in rehearsals])
        for rehearsal in rehearsals:
            rehearsal.participants = mapping.get(rehearsal.id, [])

    def get_participants(self, rehearsal_ids: Sequence[int]) -> dict[int, list[Participant]]:
        if not rehearsal_ids:
            return {}
        placeholders = ",".join("?" for _ in rehearsal_ids)
        rows = self.db.query_all(
            f"""
            SELECT * FROM rehearsal_participant
            WHERE rehearsal_id IN ({placeholders}) ORDER BY id
            """,
            [int(rid) for rid in rehearsal_ids],
        )
        mapping: dict[int, list[Participant]] = {}
        for row in rows:
            mapping.setdefault(int(row["rehearsal_id"]), []).append(
                Participant(
                    qq_id=row["qq_id"],
                    display_name=row["display_name"],
                    remark_tag=row["remark_tag"],
                    precise=_as_bool(row["precise"]),
                    note=row["note"],
                )
            )
        return mapping

    # ------------------------------------------------------------------
    # 提醒任务
    # ------------------------------------------------------------------
    def create_jobs(
        self,
        rehearsal_id: int,
        troupe_id: int,
        triggers: Iterable[datetime],
        now: datetime,
    ) -> None:
        stamp = iso(now)
        rows = []
        for trigger in triggers:
            late = trigger <= now
            rows.append(
                (
                    int(rehearsal_id),
                    int(troupe_id),
                    iso(trigger),
                    "expired" if late else "pending",
                    "已错过提醒时间，不再补发" if late else "",
                    stamp,
                )
            )
        if not rows:
            return
        self.db.conn.executemany(
            """
            INSERT INTO reminder_job
                (rehearsal_id, troupe_id, trigger_at, status, last_error, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(rehearsal_id, trigger_at) DO NOTHING
            """,
            rows,
        )

    def cancel_pending_jobs(self, rehearsal_id: int) -> int:
        return self.db.execute(
            """
            UPDATE reminder_job SET status = 'cancelled'
            WHERE rehearsal_id = ? AND status IN ('pending', 'sending')
            """,
            (int(rehearsal_id),),
        )

    def reset_jobs(self, rehearsal_id: int) -> int:
        """修改 / 重发时重建未发送提醒，保留已发送记录避免重复。"""
        return self.db.execute(
            """
            DELETE FROM reminder_job
            WHERE rehearsal_id = ? AND status IN ('pending', 'cancelled', 'failed', 'expired')
            """,
            (int(rehearsal_id),),
        )

    def cancel_all_pending_jobs(self, troupe_id: int) -> int:
        return self.db.execute(
            """
            UPDATE reminder_job SET status = 'cancelled'
            WHERE troupe_id = ? AND status IN ('pending', 'sending')
            """,
            (int(troupe_id),),
        )

    def mark_completed(self, now: datetime) -> int:
        return self.db.execute(
            "UPDATE rehearsal SET status = 'completed' WHERE status = 'published' AND end_at < ?",
            (iso(now),),
        )

    def due_jobs(self, now: datetime, limit: int = 20) -> list[ReminderJob]:
        rows = self.db.query_all(
            """
            SELECT * FROM reminder_job
            WHERE status = 'pending' AND trigger_at <= ?
            ORDER BY trigger_at LIMIT ?
            """,
            (iso(now), int(limit)),
        )
        return [_row_to_job(row) for row in rows]

    def claim_job(self, job_id: int, now: datetime) -> bool:
        count = self.db.execute(
            """
            UPDATE reminder_job
            SET status = 'sending', attempts = attempts + 1, updated_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (iso(now), int(job_id)),
        )
        return count == 1

    def mark_job_sent(self, job_id: int, message_id: str | None, now: datetime) -> None:
        self.db.execute(
            """
            UPDATE reminder_job
            SET status = 'sent', sent_message_id = ?, sent_at = ?, last_error = '', updated_at = ?
            WHERE id = ?
            """,
            (message_id, iso(now), iso(now), int(job_id)),
        )

    def mark_job_failed(self, job_id: int, error: str, now: datetime, final: bool) -> None:
        self.db.execute(
            """
            UPDATE reminder_job SET status = ?, last_error = ?, updated_at = ?
            WHERE id = ?
            """,
            ("failed" if final else "pending", error[:500], iso(now), int(job_id)),
        )

    def mark_job_expired(self, job_id: int, reason: str, now: datetime) -> None:
        self.db.execute(
            """
            UPDATE reminder_job SET status = 'expired', last_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (reason[:500], iso(now), int(job_id)),
        )

    def mark_job_cancelled(self, job_id: int, reason: str = "") -> None:
        self.db.execute(
            """
            UPDATE reminder_job SET status = 'cancelled', last_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (reason[:500], iso(now_utc()), int(job_id)),
        )

    def recover_inflight_jobs(self) -> int:
        """重启后处理上次中断的发送任务：标记失败，避免重复发送。"""
        return self.db.execute(
            """
            UPDATE reminder_job
            SET status = 'failed',
                last_error = '进程重启，上次发送状态未知；为避免重复发送不再重试',
                updated_at = ?
            WHERE status = 'sending'
            """,
            (iso(now_utc()),),
        )

    def pending_jobs(self, rehearsal_id: int) -> list[ReminderJob]:
        rows = self.db.query_all(
            "SELECT * FROM reminder_job WHERE rehearsal_id = ? ORDER BY trigger_at",
            (int(rehearsal_id),),
        )
        return [_row_to_job(row) for row in rows]

    # ------------------------------------------------------------------
    # 待确认操作
    # ------------------------------------------------------------------
    def _next_counter(self, name: str) -> int:
        with self.db.tx() as conn:
            row = conn.execute("SELECT value FROM counter WHERE name = ?", (name,)).fetchone()
            value = int(row["value"]) + 1 if row else 1
            conn.execute(
                "INSERT INTO counter (name, value) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET value = excluded.value",
                (name, value),
            )
        return value

    def create_pending(
        self,
        troupe_id: int,
        kind: str,
        operator_qq: str,
        group_id: str,
        payload: dict,
        *,
        now: datetime,
        ttl_minutes: int = 30,
        rehearsal_id: int | None = None,
    ) -> PendingAction:
        prefix = PENDING_PREFIX.get(kind, kind[:1].upper())
        number = self._next_counter(f"pending:{prefix}")
        action_id = f"{prefix}{number:03d}"
        stamp = iso(now)
        expires = iso(now + timedelta(minutes=ttl_minutes))
        self.db.execute(
            """
            INSERT INTO pending_action
                (id, troupe_id, kind, operator_qq, group_id, payload, rehearsal_id,
                 status, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                action_id,
                int(troupe_id),
                kind,
                str(operator_qq),
                str(group_id),
                json.dumps(payload, ensure_ascii=False),
                rehearsal_id,
                stamp,
                expires,
            ),
        )
        return PendingAction(
            id=action_id,
            troupe_id=int(troupe_id),
            kind=kind,
            operator_qq=str(operator_qq),
            group_id=str(group_id),
            payload=payload,
            rehearsal_id=rehearsal_id,
            created_at=parse_iso(stamp),
            expires_at=parse_iso(expires),
        )

    def get_pending(self, action_id: str) -> PendingAction | None:
        row = self.db.query_one(
            "SELECT * FROM pending_action WHERE id = ?", (action_id.strip().upper(),)
        )
        return _row_to_pending(row) if row else None

    def set_pending_status(self, action_id: str, status: str) -> None:
        self.db.execute(
            "UPDATE pending_action SET status = ? WHERE id = ?",
            (status, action_id.strip().upper()),
        )

    def expire_pending(self, now: datetime) -> int:
        return self.db.execute(
            """
            UPDATE pending_action SET status = 'expired'
            WHERE status = 'pending' AND expires_at <= ?
            """,
            (iso(now),),
        )

    def latest_pending(self, operator_qq: str, kind: str) -> PendingAction | None:
        row = self.db.query_one(
            """
            SELECT * FROM pending_action
            WHERE operator_qq = ? AND kind = ? AND status = 'pending'
            ORDER BY created_at DESC LIMIT 1
            """,
            (str(operator_qq), kind),
        )
        return _row_to_pending(row) if row else None

    # ------------------------------------------------------------------
    # 审计日志
    # ------------------------------------------------------------------
    def add_audit(
        self,
        troupe_id: int | None,
        qq_id: str,
        action: str,
        *,
        target: str = "",
        summary: str = "",
        result: str = "success",
        message: str = "",
        now: datetime | None = None,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO audit_log
                (troupe_id, qq_id, action, target, summary, result, message, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(troupe_id) if troupe_id else None,
                str(qq_id),
                action,
                target,
                summary,
                result,
                message,
                iso(now or now_utc()),
            ),
        )

    def list_audit(self, troupe_id: int | None, limit: int = 10) -> list[AuditEntry]:
        limit = max(1, min(int(limit), 100))
        if troupe_id is None:
            rows = self.db.query_all(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
            )
        else:
            rows = self.db.query_all(
                "SELECT * FROM audit_log WHERE troupe_id = ? ORDER BY id DESC LIMIT ?",
                (int(troupe_id), limit),
            )
        return [_row_to_audit(row) for row in rows]

    # ------------------------------------------------------------------
    # 设置与统计
    # ------------------------------------------------------------------
    def get_setting(self, key: str, default: str = "") -> str:
        row = self.db.query_one("SELECT value FROM setting WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_setting(self, key: str, value: str, now: datetime | None = None) -> None:
        self.db.execute(
            """
            INSERT INTO setting (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, value, iso(now or now_utc())),
        )

    def all_settings(self) -> dict[str, str]:
        return {row["key"]: row["value"] for row in self.db.query_all("SELECT * FROM setting")}

    def stats(self, troupe_id: int | None = None) -> dict[str, int]:
        if troupe_id is None:
            rehearsal_count = self.db.query_one("SELECT COUNT(*) AS n FROM rehearsal")
            troupe_count = self.db.query_one("SELECT COUNT(*) AS n FROM troupe")
            pending = self.db.query_one(
                "SELECT COUNT(*) AS n FROM reminder_job WHERE status = 'pending'"
            )
            return {
                "troupes": int(troupe_count["n"]) if troupe_count else 0,
                "rehearsals": int(rehearsal_count["n"]) if rehearsal_count else 0,
                "pending_reminders": int(pending["n"]) if pending else 0,
            }
        rehearsal_count = self.db.query_one(
            "SELECT COUNT(*) AS n FROM rehearsal WHERE troupe_id = ?", (int(troupe_id),)
        )
        pending = self.db.query_one(
            "SELECT COUNT(*) AS n FROM reminder_job WHERE troupe_id = ? AND status = 'pending'",
            (int(troupe_id),),
        )
        return {
            "rehearsals": int(rehearsal_count["n"]) if rehearsal_count else 0,
            "pending_reminders": int(pending["n"]) if pending else 0,
        }


# ---------------------------------------------------------------------------
# 行 -> 实体
# ---------------------------------------------------------------------------
def _row_to_troupe(row: sqlite3.Row) -> Troupe:
    return Troupe(
        id=int(row["id"]),
        name=row["name"],
        group_id=row["group_id"],
        timezone=row["timezone"],
        default_remind_minutes=row["default_remind_minutes"],
        private_remind=_as_bool(row["private_remind"]),
        ambiguous_evening_threshold=int(row["ambiguous_evening_threshold"]),
        show_tags=_as_bool(row["show_tags"]),
        show_empty=_as_bool(row["show_empty"]),
        degrade_at_scope=row["degrade_at_scope"],
        status=row["status"],
        created_at=parse_iso(row["created_at"]),
    )


def _row_to_actor(row: sqlite3.Row) -> ActorProfile:
    return ActorProfile(
        id=int(row["id"]),
        troupe_id=int(row["troupe_id"]),
        qq_id=row["qq_id"],
        display_name=row["display_name"],
        aliases=_loads(row["aliases"], []),
        remark_tags=_loads(row["remark_tags"], []),
        active=_as_bool(row["active"]),
        created_at=parse_iso(row["created_at"]),
    )


def _row_to_rehearsal(row: sqlite3.Row) -> Rehearsal:
    remind_raw = row["remind_minutes"] or ""
    minutes = tuple(int(part) for part in remind_raw.split(",") if part.strip().isdigit())
    return Rehearsal(
        id=int(row["id"]),
        troupe_id=int(row["troupe_id"]),
        seq=int(row["seq"]),
        location=row["location"],
        start_at=parse_iso(row["start_at"]),
        end_at=parse_iso(row["end_at"]),
        content=_loads(row["content"], []),
        remark=row["remark"],
        title=row["title"],
        raw_text=row["raw_text"],
        participant_mode=row["participant_mode"],
        notify_all=_as_bool(row["notify_all"]),
        remind_minutes=minutes,
        status=row["status"],
        created_by=row["created_by"],
        updated_by=row["updated_by"],
        created_at=parse_iso(row["created_at"]),
        published_at=parse_iso(row["published_at"]),
        deleted_at=parse_iso(row["deleted_at"]),
    )


def _row_to_job(row: sqlite3.Row) -> ReminderJob:
    return ReminderJob(
        id=int(row["id"]),
        rehearsal_id=int(row["rehearsal_id"]),
        troupe_id=int(row["troupe_id"]),
        trigger_at=parse_iso(row["trigger_at"]),
        status=row["status"],
        attempts=int(row["attempts"]),
        last_error=row["last_error"],
        sent_message_id=row["sent_message_id"],
        sent_at=parse_iso(row["sent_at"]),
    )


def _row_to_pending(row: sqlite3.Row) -> PendingAction:
    return PendingAction(
        id=row["id"],
        troupe_id=int(row["troupe_id"]),
        kind=row["kind"],
        operator_qq=row["operator_qq"],
        group_id=row["group_id"],
        payload=_loads(row["payload"], {}),
        rehearsal_id=int(row["rehearsal_id"]) if row["rehearsal_id"] else None,
        status=row["status"],
        created_at=parse_iso(row["created_at"]),
        expires_at=parse_iso(row["expires_at"]),
    )


def _row_to_audit(row: sqlite3.Row) -> AuditEntry:
    return AuditEntry(
        id=int(row["id"]),
        troupe_id=int(row["troupe_id"]) if row["troupe_id"] else None,
        qq_id=row["qq_id"],
        action=row["action"],
        target=row["target"],
        summary=row["summary"],
        result=row["result"],
        message=row["message"],
        created_at=parse_iso(row["created_at"]),
    )
