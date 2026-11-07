"""离线模拟器端到端测试（也是 CLI 冒烟测试）。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from rehearsal.simulator import Simulator

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 11, 7, 13, 0, tzinfo=TZ)


def make_simulator(tmp_path) -> Simulator:
    return Simulator(str(tmp_path / "sim.db"), now=NOW, reset=True)


def test_simulator_publish_flow(tmp_path):
    sim = make_simulator(tmp_path)
    preview = sim.handle_line(
        "#发布排练\n时间=周五7~10\n地点=一排\n人员=@张三 @李四 制作组\n内容=1. 开场"
    )
    assert "【发布预览】" in preview
    assert "P001" in preview
    confirmed = sim.handle_line("#确认发布 P001")
    assert "机器人 → 群 100001" in confirmed
    assert "#排练通知" in confirmed
    assert "已发布排练通知" in confirmed


def test_simulator_switch_user_and_schedule(tmp_path):
    sim = make_simulator(tmp_path)
    sim.handle_line("#发布排练\n时间=明天7~9\n地点=一排\n人员=@王五")
    sim.handle_line("#确认发布 P001")
    sim.handle_line("/as 王五")
    sim.handle_line("/private")
    text = sim.handle_line("#我的排练")
    assert "【你的排练安排】" in text
    assert "示例剧组" in text


def test_simulator_advance_and_reminder(tmp_path):
    sim = make_simulator(tmp_path)
    sim.handle_line("#发布排练\n时间=今天7~9\n地点=一排\n人员=@王五")
    sim.handle_line("#确认发布 P001")
    sim.handle_line("/advance 330m")
    output = sim.handle_line("/tick")
    assert "机器人 → 群 100001" in output
    assert "#排练提醒" in output


def test_simulator_help_and_quit(tmp_path):
    sim = make_simulator(tmp_path)
    assert "业务命令" in sim.handle_line("/help")
    assert sim.handle_line("/quit") == "__QUIT__"
    assert "已切换到私聊" in sim.handle_line("/private")
