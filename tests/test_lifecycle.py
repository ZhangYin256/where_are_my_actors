"""修改、重发、取消、删除等生命周期测试。"""

from __future__ import annotations

import re
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")

PUBLISH = """#发布排练
时间=周五7~10
地点=一排
人员=@张三 @李四
内容=1. 开场
备注=原备注"""


async def publish(app, text: str = PUBLISH) -> str:
    preview = await app.text(text)
    action = re.search(r"#确认发布 (P\d+)", preview)
    assert action, preview
    result = await app.text(f"#确认发布 {action.group(1)}")
    app.gateway.clear()
    return result


async def test_modify_location_preview_and_save(app):
    await publish(app)
    app.gateway.clear()
    preview = await app.text("#排练修改 1 地点=二排")
    assert "【修改预览】" in preview
    assert "编号：1" in preview
    assert "原时间：2026-11-13 周五 19:00-22:00" in preview
    assert "地点：二排" in preview
    assert "M001" in preview
    confirmed = await app.text("#确认修改 M001")
    assert "修改已保存" in confirmed
    assert "#排练重发 1" in confirmed
    # 修改后不自动重发
    assert app.gateway.messages_to("100001") == []
    rehearsal = app.repo.get_rehearsal(app.troupe_a.id, 1)
    assert rehearsal.location == "二排"


async def test_modify_time_reschedules_reminder(app):
    await publish(app)
    await app.text("#排练修改 1 时间=周五8~11")
    await app.text("#确认修改 M001")
    rehearsal = app.repo.get_rehearsal(app.troupe_a.id, 1)
    jobs = app.repo.pending_jobs(rehearsal.id)
    pending = [job for job in jobs if job.status == "pending"]
    assert len(pending) == 1
    local_trigger = pending[0].trigger_at.astimezone(TZ)
    assert (local_trigger.hour, local_trigger.minute) == (19, 30)


async def test_modify_reminder_off(app):
    await publish(app)
    await app.text("#排练修改 1 提醒=关闭")
    await app.text("#确认修改 M001")
    jobs = app.repo.pending_jobs(app.repo.get_rehearsal(app.troupe_a.id, 1).id)
    assert [job for job in jobs if job.status == "pending"] == []


async def test_modify_preview_cancel(app):
    await publish(app)
    await app.text("#排练修改 1 地点=三排")
    text = await app.text("#取消修改 M001")
    assert "已取消" in text
    assert app.repo.get_rehearsal(app.troupe_a.id, 1).location == "一排"


async def test_modify_without_fields_shows_notice(app):
    await publish(app)
    text = await app.text("#排练修改 1")
    assert "#排练通知" in text
    assert "修改请发送" in text


async def test_republish_preview_then_send(app):
    await publish(app)
    app.gateway.clear()
    preview = await app.text("#排练重发 1")
    assert "【重发预览】" in preview
    assert "R001" in preview
    assert await app.text("#确认重发 R001") == "✅ 已重新发布编号 1 的排练通知。"
    messages = app.gateway.messages_to("100001")
    assert len(messages) == 1
    assert "以下为更新后的排练通知" in messages[0].plain_text()


async def test_republish_cancelled_rehearsal_rejected(app):
    await publish(app)
    await app.text("#取消排练 1")
    await app.text("#确认取消 1 通知=否")
    text = await app.text("#排练重发 1")
    assert "已取消" in text


async def test_cancel_with_notice(app):
    await publish(app)
    app.gateway.clear()
    prompt = await app.text("#取消排练 1")
    assert "是否同时发布取消通知" in prompt
    text = await app.text("#确认取消 1 通知=是")
    assert "已取消" in text
    messages = app.gateway.messages_to("100001")
    assert len(messages) == 1
    cancel_text = messages[0].plain_text()
    assert cancel_text.startswith("#排练取消通知")
    assert "原定于11月13日 周五 19:00-22:00的排练已取消。" in cancel_text
    assert "地点：一排" in cancel_text
    rehearsal = app.repo.get_rehearsal(app.troupe_a.id, 1)
    assert rehearsal.status == "cancelled"


async def test_cancel_without_notice(app):
    await publish(app)
    app.gateway.clear()
    await app.text("#取消排练 1")
    text = await app.text("#确认取消 1 通知=否")
    assert "已取消" in text
    assert app.gateway.messages_to("100001") == []


async def test_cancel_stops_reminders(app):
    await publish(app)
    await app.text("#取消排练 1")
    await app.text("#确认取消 1 通知=否")
    job = app.repo.pending_jobs(app.repo.get_rehearsal(app.troupe_a.id, 1).id)[0]
    assert job.status == "cancelled"


async def test_delete_soft(app):
    await publish(app)
    prompt = await app.text("#删除排练 1")
    assert "确定删除编号为 1 的排练吗" in prompt
    text = await app.text("#确认删除 1")
    assert "已删除" in text
    rehearsal = app.repo.get_rehearsal(app.troupe_a.id, 1)
    assert rehearsal is None
    assert app.repo.get_rehearsal(app.troupe_a.id, 1, include_deleted=True).status == "deleted"
    listing = await app.text("#排练列表")
    assert "编号1" not in listing


async def test_delete_cancel(app):
    await publish(app)
    await app.text("#删除排练 1")
    text = await app.text("#取消删除 1")
    assert "已取消删除" in text
    assert app.repo.get_rehearsal(app.troupe_a.id, 1) is not None


async def test_hard_delete_requires_admin(app):
    await publish(app)
    text = await app.text("#彻底删除 1", qq="10002", role="admin", nickname="李四")
    assert "权限不足" in text or "只有管理员" in text


async def test_hard_delete(app):
    await publish(app)
    text = await app.text("#彻底删除 1")
    assert "彻底删除" in text
    assert app.repo.get_rehearsal(app.troupe_a.id, 1, include_deleted=True) is None


async def test_modify_requires_number(app):
    text = await app.text("#排练修改")
    assert "编号" in text
