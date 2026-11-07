#!/usr/bin/env python3
"""运行离线模拟器并生成完整演示文档 docs/DEMO-TRANSCRIPT.md。"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rehearsal.simulator import TZ, Simulator  # noqa: E402

SCRIPT = ROOT / "examples" / "demo-script.txt"
OUTPUT = ROOT / "docs" / "DEMO-TRANSCRIPT.md"


def main() -> int:
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    database = ROOT / "data" / "demo.db"
    simulator = Simulator(
        str(database), now=datetime(2026, 11, 7, 13, 0, tzinfo=TZ), reset=True
    )
    blocks = simulator.run_script(lines)
    parts = [
        "# 模拟器完整演示对话",
        "",
        "> 由 `python scripts/demo.py` 自动生成。固定模拟时间为 2026-11-07 13:00（周六）。",
        "> 命令与真实 QQ 群内完全一致，可对照《使用手册》阅读。",
        "",
    ]
    for block in blocks:
        parts.append("```text")
        parts.append(block)
        parts.append("```")
        parts.append("")
    OUTPUT.write_text("\n".join(parts), encoding="utf-8")
    print(f"已写入 {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
