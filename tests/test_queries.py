"""查询功能测试：排练列表、历史、详情、我的排练。"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")


async def publish(app, text: str, *, qq="10001", group_id="100001", role="owner", nickname="张三"):
    preview = await app.text(
        text, qq=qq, group_id=group_id, role=role, nickname=nickname
    )
    action = re.search(r"#确认发布 (P\d+)", preview)
    assert action, preview
    await app.text(
        f"#确认发布 {action.group(1)}", qq=qq, group_id=group_id, role=role, nickname=nickname
    )
    app.gateway.clear()


async def test_list_future(app):
    await publish(app, "#发布排练\n时间=明天7~9\n地点=一排\n人员=@王五")
    await publish(app, "#发布排练\n时间=周五7~10\n地点=二排\n人员=@王五")
    text = await app.text("#排练列表")
    assert "【排练列表】剧组A" in text
    assert "共 2 条" in text
    assert text.index("编号1") < text.index("编号2")
    assert "11月08日 周日 19:00-21:00" in text
    assert "11月13日 周五 19:00-22:00" in text


async def test_list_this_week(app):
    await publish(app, "#发布排练\n时间=明天7~9\n地点=一排\n人员=@王五")
    await publish(app, "#发布排练\n时间=周五7~10\n地点=二排\n人员=@王五")
    text = await app.text("#排练列表 本周")
    assert "本周排练" in text
    assert "11月08日 周日" in text
    assert "11月13日" not in text


async def test_list_next_week(app):
    await publish(app, "#发布排练\n时间=明天7~9\n地点=一排\n人员=@王五")
    await publish(app, "#发布排练\n时间=周五7~10\n地点=二排\n人员=@王五")
    text = await app.text("#排练列表 下周")
    assert "11月13日 周五" in text
    assert "11月08日" not in text


async def test_list_history(app):
    await publish(app, "#发布排练\n时间=明天7~9\n地点=一排\n人员=@王五")
    await publish(app, "#发布排练\n时间=周五7~10\n地点=二排\n人员=@王五")
    app.clock.set(datetime(2026, 11, 14, 12, 0, tzinfo=TZ))
    text = await app.text("#排练历史")
    assert "历史排练" in text
    assert "共 2 条" in text
    assert text.index("11月13日") < text.index("11月08日")


async def test_list_unknown_scope(app):
    text = await app.text("#排练列表 明年")
    assert "无法识别的范围" in text


async def test_detail(app):
    await publish(app, "#发布排练\n时间=明天7~9\n地点=一排\n人员=@王五\n内容=1. 开场")
    text = await app.text("#排练详情 1")
    assert "【排练详情】编号 1" in text
    assert "状态：已发布" in text
    assert "时间：11月08日 周日 19:00-21:00" in text
    assert "地点：一排" in text
    assert "人员：@王五" in text
    assert "内容：\n1. 开场" in text


async def test_detail_not_found(app):
    text = await app.text("#排练详情 99")
    assert "未找到编号 99" in text


async def test_my_schedule_private_across_troupes(app):
    await publish(app, "#发布排练\n时间=明天7~9\n地点=一排\n人员=@王五")
    await publish(
        app,
        "#发布排练\n时间=周一7~9\n地点=二楼\n人员=@王五",
        qq="10002",
        group_id="100002",
        nickname="李四",
    )
    text = await app.text(
        "#我的排练", qq="10003", scope="private", group_id=None, nickname="王五"
    )
    assert "【你的排练安排】" in text
    assert "剧组A" in text
    assert "剧组B" in text
    assert "2026-11-08 周日" in text
    assert "2026-11-09 周一" in text
    assert "地点：一排" in text
    assert "地点：二楼" in text
    # 不泄露他人 QQ / @
    assert "10001" not in text
    assert "@李四" not in text


async def test_my_schedule_shows_tag(app):
    await publish(app, "#发布排练\n时间=明天7~9\n地点=一排\n人员=湖区@王五")
    text = await app.text(
        "#我的排练", qq="10003", scope="private", group_id=None, nickname="王五"
    )
    assert "剧组A（湖区）" in text


async def test_my_schedule_today_empty(app):
    await publish(app, "#发布排练\n时间=明天7~9\n地点=一排\n人员=@王五")
    text = await app.text(
        "#我的排练 今天", qq="10003", scope="private", group_id=None, nickname="王五"
    )
    assert "暂无排练安排" in text


async def test_my_schedule_in_group_notes_privacy(app):
    await publish(app, "#发布排练\n时间=明天7~9\n地点=一排\n人员=@王五")
    text = await app.text("#我的排练", qq="10003", group_id="100001", role="member", nickname="王五")
    assert "仅显示你自己的安排" in text
    assert "剧组A" in text


async def test_my_schedule_future_excludes_past(app):
    await publish(app, "#发布排练\n时间=明天7~9\n地点=一排\n人员=@王五")
    app.clock.set(datetime(2026, 11, 14, 12, 0, tzinfo=TZ))
    text = await app.text(
        "#我的排练 未来", qq="10003", scope="private", group_id=None, nickname="王五"
    )
    assert "暂无排练安排" in text
    history = await app.text(
        "#我的排练 历史", qq="10003", scope="private", group_id=None, nickname="王五"
    )
    assert "剧组A（已结束）" in history or "剧组A" in history


async def test_cancelled_marked_in_list(app):
    await publish(app, "#发布排练\n时间=明天7~9\n地点=一排\n人员=@王五")
    await app.text("#取消排练 1")
    await app.text("#确认取消 1 通知=否")
    text = await app.text("#排练列表")
    assert "（已取消）" in text
