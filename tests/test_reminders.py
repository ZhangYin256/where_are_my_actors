"""提醒任务测试：触发、私聊、降级、重试、重启恢复、幂等。"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")

PUBLISH = """#发布排练
时间=周五7~10
地点=一排
人员=@张三 @李四 制作组
内容=福临门合排
备注=请带谱子"""


async def publish(app, text: str = PUBLISH) -> str:
    preview = await app.text(text)
    action = re.search(r"#确认发布 (P\d+)", preview).group(1)
    result = await app.text(f"#确认发布 {action}")
    app.gateway.clear()
    return result


async def test_reminder_sent_once(app):
    await publish(app)
    app.clock.set(datetime(2026, 11, 13, 18, 30, tzinfo=TZ))
    assert await app.service.run_due_reminders() == 1
    messages = app.gateway.messages_to("100001")
    assert len(messages) == 1
    text = messages[0].plain_text()
    assert text.startswith("#排练提醒")
    assert "还有30分钟开始" in text
    assert "时间：今天 19:00-22:00" in text
    assert "地点：一排" in text
    assert "内容：\n1. 福临门合排" in text
    assert "请相关成员准时参加：" in text
    assert "@张三" in text and "@李四" in text
    assert "@制作组" not in text
    # 不重复发送
    assert await app.service.run_due_reminders() == 0
    assert len(app.gateway.messages_to("100001")) == 1


async def test_reminder_not_before_trigger(app):
    await publish(app)
    app.clock.set(datetime(2026, 11, 13, 18, 29, tzinfo=TZ))
    assert await app.service.run_due_reminders() == 0
    assert app.gateway.messages_to("100001") == []


async def test_reminder_status_and_audit(app):
    await publish(app)
    app.clock.set(datetime(2026, 11, 13, 18, 30, tzinfo=TZ))
    await app.service.run_due_reminders()
    rehearsal = app.repo.get_rehearsal(app.troupe_a.id, 1)
    job = app.repo.pending_jobs(rehearsal.id)[0]
    assert job.status == "sent"
    assert job.sent_at is not None
    entries = app.repo.list_audit(app.troupe_a.id, 10)
    assert any(entry.action == "reminder_sent" for entry in entries)


async def test_private_reminder(app):
    app.repo.update_troupe(app.troupe_a.id, private_remind=True)
    await publish(app)
    app.clock.set(datetime(2026, 11, 13, 18, 30, tzinfo=TZ))
    await app.service.run_due_reminders()
    private_messages = app.gateway.private_to("10001")
    assert len(private_messages) == 1
    text = private_messages[0].plain_text()
    assert text.startswith("【排练提醒】剧组A")
    assert "还有30分钟开始" in text
    assert "地点：一排" in text
    # 文本人员不私聊
    assert app.gateway.private_to("10003") == []


async def test_private_reminder_disabled_by_default(app):
    await publish(app)
    app.clock.set(datetime(2026, 11, 13, 18, 30, tzinfo=TZ))
    await app.service.run_due_reminders()
    assert app.gateway.private_to("10001") == []


async def test_private_reminder_failure_logged(app):
    app.repo.update_troupe(app.troupe_a.id, private_remind=True)
    await publish(app)
    app.gateway.private_send_error = "not friend"
    app.clock.set(datetime(2026, 11, 13, 18, 30, tzinfo=TZ))
    await app.service.run_due_reminders()
    entries = app.repo.list_audit(app.troupe_a.id, 20)
    assert any(
        entry.action == "reminder_failed" and "私聊" in entry.summary for entry in entries
    )


async def test_all_mode_degrades_to_member_mentions(app):
    app.gateway.bot_admin["100001"] = False
    await publish(app, "#发布排练\n时间=周五7~10\n地点=一排\n人员=@全体成员")
    app.clock.set(datetime(2026, 11, 13, 18, 30, tzinfo=TZ))
    await app.service.run_due_reminders()
    messages = app.gateway.messages_to("100001")
    assert messages
    text = messages[0].plain_text()
    assert "@张三" in text
    assert "@王五" in text
    assert "@全体成员" not in text
    # 分段发送：5 个成员、每段 20 人，一条消息即可
    assert len(messages) == 1


async def test_all_mode_at_all_when_admin(app):
    await publish(app, "#发布排练\n时间=周五7~10\n人员=@全体成员")
    app.clock.set(datetime(2026, 11, 13, 18, 30, tzinfo=TZ))
    await app.service.run_due_reminders()
    text = app.gateway.messages_to("100001")[0].plain_text()
    assert "@全体成员" in text


async def test_notify_all_without_admin_degrade(app):
    app.gateway.bot_admin["100001"] = False
    await publish(
        app, "#发布排练\n时间=周五7~10\n人员=@张三\n通知范围=全体成员"
    )
    app.clock.set(datetime(2026, 11, 13, 18, 30, tzinfo=TZ))
    await app.service.run_due_reminders()
    text = app.gateway.messages_to("100001")[0].plain_text()
    assert "@全体成员" not in text
    assert "@张三" in text


async def test_reminder_chunking(app):
    app.gateway.bot_admin["100001"] = False
    app.gateway.groups["100001"] = [
        app.gateway.groups["100001"][0],
        *[
            type(app.gateway.groups["100001"][0])(str(20000 + i), f"成员{i}", role="member")
            for i in range(25)
        ],
    ]
    app.repo.set_setting("at_chunk_size", "10")
    await publish(app, "#发布排练\n时间=周五7~10\n人员=@全体成员")
    app.clock.set(datetime(2026, 11, 13, 18, 30, tzinfo=TZ))
    await app.service.run_due_reminders()
    messages = app.gateway.messages_to("100001")
    assert len(messages) == 3  # 26 人 / 10 = 3 段
    assert "请相关成员准时参加" in messages[1].plain_text()


async def test_reminder_retry_then_fail(app):
    await publish(app)
    app.clock.set(datetime(2026, 11, 13, 18, 30, tzinfo=TZ))
    app.gateway.group_send_error = "网络错误"
    for _ in range(3):
        await app.service.run_due_reminders()
    rehearsal = app.repo.get_rehearsal(app.troupe_a.id, 1)
    job = app.repo.pending_jobs(rehearsal.id)[0]
    assert job.status == "failed"
    assert job.attempts == 3
    assert "网络错误" in job.last_error


async def test_reminder_retry_then_success(app):
    await publish(app)
    app.clock.set(datetime(2026, 11, 13, 18, 30, tzinfo=TZ))
    app.gateway.group_send_error = "网络错误"
    await app.service.run_due_reminders()
    app.gateway.group_send_error = None
    await app.service.run_due_reminders()
    rehearsal = app.repo.get_rehearsal(app.troupe_a.id, 1)
    job = app.repo.pending_jobs(rehearsal.id)[0]
    assert job.status == "sent"
    assert len(app.gateway.messages_to("100001")) == 1


async def test_missed_reminder_expired_not_sent(app):
    await publish(app)
    app.clock.set(datetime(2026, 11, 13, 19, 10, tzinfo=TZ))
    await app.service.run_due_reminders()
    rehearsal = app.repo.get_rehearsal(app.troupe_a.id, 1)
    job = app.repo.pending_jobs(rehearsal.id)[0]
    assert job.status == "expired"
    assert app.gateway.messages_to("100001") == []


async def test_restart_does_not_duplicate(app):
    await publish(app)
    now = datetime(2026, 11, 13, 18, 30, tzinfo=TZ)
    app.clock.set(now)
    job = app.repo.due_jobs(now)[0]
    assert app.repo.claim_job(job.id, now) is True
    # 模拟发送中崩溃：重启后标记失败，不再重发
    await app.service.startup()
    rehearsal = app.repo.get_rehearsal(app.troupe_a.id, 1)
    job = app.repo.pending_jobs(rehearsal.id)[0]
    assert job.status == "failed"
    await app.service.run_due_reminders()
    assert app.gateway.messages_to("100001") == []


async def test_cancelled_reminder_not_sent(app):
    await publish(app)
    rehearsal = app.repo.get_rehearsal(app.troupe_a.id, 1)
    app.repo.set_rehearsal_status(rehearsal.id, "cancelled")
    app.clock.set(datetime(2026, 11, 13, 18, 30, tzinfo=TZ))
    # 即使任务仍为 pending，也会被跳过
    job = app.repo.pending_jobs(rehearsal.id)[0]
    if job.status == "pending":
        assert app.repo.claim_job(job.id, app.clock.value) is True
        await app.service._deliver_reminder(job, app.clock.value)
    assert app.gateway.messages_to("100001") == []


async def test_pending_reminders_survive_restart(app):
    await publish(app)
    from rehearsal.service import RehearsalService

    restarted = RehearsalService(
        app.repo, app.gateway, app.service.settings, clock=app.clock
    )
    await restarted.startup()
    rehearsal = app.repo.get_rehearsal(app.troupe_a.id, 1)
    job = app.repo.pending_jobs(rehearsal.id)[0]
    assert job.status == "pending"
    app.clock.set(datetime(2026, 11, 13, 18, 30, tzinfo=TZ))
    assert await restarted.run_due_reminders() == 1
    assert app.gateway.messages_to("100001")


async def test_multiple_reminder_jobs(app):
    app.repo.update_troupe(app.troupe_a.id, default_remind_minutes="60,30")
    await publish(app)
    rehearsal = app.repo.get_rehearsal(app.troupe_a.id, 1)
    jobs = app.repo.pending_jobs(rehearsal.id)
    assert len(jobs) == 2
    local = [job.trigger_at.astimezone(TZ) for job in jobs]
    assert (local[0].hour, local[0].minute) == (18, 0)
    assert (local[1].hour, local[1].minute) == (18, 30)


async def test_reminder_omits_empty_fields(app):
    await publish(app, "#发布排练\n时间=周五7~10\n人员=@王五")
    app.clock.set(datetime(2026, 11, 13, 18, 30, tzinfo=TZ))
    await app.service.run_due_reminders()
    text = app.gateway.messages_to("100001")[0].plain_text()
    assert "地点：" not in text
    assert "内容：" not in text
    assert "备注：" not in text
