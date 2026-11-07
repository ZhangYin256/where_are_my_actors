"""后台提醒循环与心跳。

不依赖 NoneBot2，由插件在启动时创建 asyncio 任务。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .service import RehearsalService
from .timeutil import iso, now_utc

log = logging.getLogger(__name__)


class ReminderWorker:
    def __init__(
        self,
        service: RehearsalService,
        *,
        interval: int = 10,
        heartbeat_path: str | Path | None = "data/heartbeat",
        sync_interval: int = 1800,
    ) -> None:
        self.service = service
        self.interval = max(1, int(interval))
        self.heartbeat_path = Path(heartbeat_path) if heartbeat_path else None
        self.sync_interval = max(60, int(sync_interval))
        self._stop = asyncio.Event()
        self._last_sync = 0.0

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        log.info("提醒循环已启动，间隔 %ss", self.interval)
        loop = asyncio.get_running_loop()
        while not self._stop.is_set():
            started = loop.time()
            try:
                await self.service.run_due_reminders()
                self.service.maintenance()
                if loop.time() - self._last_sync >= self.sync_interval:
                    self._last_sync = loop.time()
                    await self.service.sync_all_groups()
            except asyncio.CancelledError:  # pragma: no cover - 正常退出
                raise
            except Exception:
                log.exception("提醒循环执行失败")
            self._write_heartbeat()
            elapsed = loop.time() - started
            wait = max(0.5, self.interval - elapsed)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=wait)
            except TimeoutError:
                continue

    def _write_heartbeat(self) -> None:
        if self.heartbeat_path is None:
            return
        try:
            self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
            self.heartbeat_path.write_text(iso(now_utc()) or "", encoding="utf-8")
        except OSError as exc:  # pragma: no cover - 磁盘异常
            log.warning("写入心跳文件失败：%s", exc)
