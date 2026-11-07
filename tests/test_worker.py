"""提醒循环 / 心跳测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from rehearsal.timeutil import iso, now_utc
from rehearsal.worker import ReminderWorker


class StubService:
    def __init__(self) -> None:
        self.reminders = 0
        self.maintenance_calls = 0
        self.sync_calls = 0

    async def run_due_reminders(self) -> int:
        self.reminders += 1
        return 0

    def maintenance(self) -> None:
        self.maintenance_calls += 1

    async def sync_all_groups(self) -> None:
        self.sync_calls += 1


async def test_worker_writes_heartbeat_and_stops(tmp_path):
    service = StubService()
    heartbeat = Path(tmp_path) / "heartbeat"
    worker = ReminderWorker(service, interval=1, heartbeat_path=heartbeat, sync_interval=1)
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.2)
    worker.stop()
    await asyncio.wait_for(task, timeout=2)
    assert heartbeat.exists()
    assert service.reminders >= 1
    assert service.maintenance_calls >= 1
    assert service.sync_calls >= 1


async def test_worker_survives_errors(tmp_path):
    class Broken(StubService):
        async def run_due_reminders(self) -> int:
            self.reminders += 1
            raise RuntimeError("boom")

    service = Broken()
    worker = ReminderWorker(service, interval=1, heartbeat_path=None, sync_interval=999)
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.15)
    worker.stop()
    await asyncio.wait_for(task, timeout=2)
    assert service.reminders >= 1


def test_heartbeat_content(tmp_path):
    service = StubService()
    heartbeat = Path(tmp_path) / "hb"
    worker = ReminderWorker(service, interval=1, heartbeat_path=heartbeat)
    worker._write_heartbeat()
    value = heartbeat.read_text(encoding="utf-8")
    assert value
    assert "T" in value


def test_iso_now():
    value = iso(now_utc())
    assert value is not None and value.endswith("+00:00")
