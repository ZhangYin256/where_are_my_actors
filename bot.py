#!/usr/bin/env python3
"""排练通知 QQ Bot 启动入口。

用法::

    python bot.py

连接参数、管理员 QQ 等通过项目根目录的 ``.env`` 配置，详见 docs/DEPLOY-LOCAL.md。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import nonebot
from nonebot.adapters.onebot.v11 import Adapter
from nonebot.log import logger

ROOT = Path(__file__).resolve().parent


def main() -> None:
    log_dir = Path(os.environ.get("REHEARSAL_LOG_DIR", str(ROOT / "logs")))
    log_dir.mkdir(parents=True, exist_ok=True)
    nonebot.init()
    driver = nonebot.get_driver()
    driver.register_adapter(Adapter)
    logger.add(
        log_dir / "nonebot.log",
        rotation="10 MB",
        retention="14 days",
        encoding="utf-8",
        enqueue=True,
    )
    # 显式加载插件，不依赖运行时存在 pyproject.toml（容器镜像可能只包含源码目录）
    plugin_dir = ROOT / "plugins"
    if str(plugin_dir) not in sys.path:
        sys.path.insert(0, str(plugin_dir))
    nonebot.load_plugin("rehearsal_bot")
    nonebot.run()


if __name__ == "__main__":
    main()
