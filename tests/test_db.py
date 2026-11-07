"""数据库层测试：WAL、备份、编号、约束。"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from rehearsal.db import Database, Repository
from rehearsal.models import Participant, RehearsalDraft

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 11, 7, 13, 0, tzinfo=TZ)


def make_db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    return database, Repository(database)


def test_wal_mode_and_schema(tmp_path):
    database, repo = make_db(tmp_path)
    mode = database.conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    assert database.conn.execute("PRAGMA user_version").fetchone()[0] == 1


def test_troupe_group_unique(tmp_path):
    database, repo = make_db(tmp_path)
    repo.create_troupe("A", "100001", now=NOW)
    try:
        repo.create_troupe("B", "100001", now=NOW)
    except sqlite3.IntegrityError:
        pass
    else:  # pragma: no cover
        raise AssertionError("group_id 应唯一")


def test_rehearsal_seq_increments(tmp_path):
    database, repo = make_db(tmp_path)
    troupe = repo.create_troupe("A", "100001", now=NOW)
    draft = RehearsalDraft(
        start_at=NOW,
        end_at=NOW,
        participants=[Participant.from_qq("10001", "张三")],
    )
    first = repo.create_rehearsal(troupe.id, draft, created_by="10001", now=NOW)
    second = repo.create_rehearsal(troupe.id, draft, created_by="10001", now=NOW)
    assert (first.seq, second.seq) == (1, 2)


def test_backup(tmp_path):
    database, repo = make_db(tmp_path)
    repo.create_troupe("A", "100001", now=NOW)
    target = tmp_path / "backup" / "copy.db"
    database.backup_to(target)
    copy = sqlite3.connect(str(target))
    assert copy.execute("SELECT COUNT(*) FROM troupe").fetchone()[0] == 1
    copy.close()


def test_pending_ids_are_globally_unique(tmp_path):
    database, repo = make_db(tmp_path)
    first = repo.create_troupe("A", "100001", now=NOW)
    second = repo.create_troupe("B", "100002", now=NOW)
    a = repo.create_pending(first.id, "publish", "1", "100001", {}, now=NOW)
    b = repo.create_pending(second.id, "publish", "1", "100002", {}, now=NOW)
    assert a.id == "P001"
    assert b.id == "P002"


def test_auto_director_sync(tmp_path):
    database, repo = make_db(tmp_path)
    troupe = repo.create_troupe("A", "100001", now=NOW)
    added, removed = repo.set_auto_directors(troupe.id, {"1", "2"})
    assert (added, removed) == (2, 0)
    added, removed = repo.set_auto_directors(troupe.id, {"2", "3"})
    assert (added, removed) == (1, 1)
    roles = {r.qq_id for r in repo.list_roles(troupe.id) if r.role == "director"}
    assert roles == {"2", "3"}


def test_actor_partial_unique(tmp_path):
    database, repo = make_db(tmp_path)
    troupe = repo.create_troupe("A", "100001", now=NOW)
    repo.upsert_actor(troupe.id, "10003", "王五")
    actor = repo.upsert_actor(troupe.id, "10003", "王五")
    assert actor.qq_id == "10003"
    text_actor = repo.upsert_actor(troupe.id, None, "制作组")
    assert text_actor.qq_id is None
    assert repo.find_actor_by_name(troupe.id, "制作组").display_name == "制作组"


def test_reminder_job_unique(tmp_path):
    database, repo = make_db(tmp_path)
    troupe = repo.create_troupe("A", "100001", now=NOW)
    draft = RehearsalDraft(start_at=NOW, end_at=NOW)
    rehearsal = repo.create_rehearsal(troupe.id, draft, created_by="1", now=NOW)
    trigger = NOW
    repo.create_jobs(rehearsal.id, troupe.id, [trigger], NOW)
    repo.create_jobs(rehearsal.id, troupe.id, [trigger], NOW)
    assert len(repo.pending_jobs(rehearsal.id)) == 1
