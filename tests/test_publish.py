"""发布流程集成测试：预览、确认、正式通知、提醒任务。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")

PUBLISH = """#发布排练
时间=周五7~10
地点=一排
人员=@张三 @李四 制作组
内容=
1. 开场和声
2. 一念一宇宙
备注=其他演员可以来自由练习"""


async def test_publish_preview_and_confirm(app):
    preview = await app.text(PUBLISH)
    assert "【发布预览】" in preview
    assert "标题：#排练通知" in preview
    assert "时间：2026-11-13 周五 19:00-22:00" in preview
    assert "地点：一排" in preview
    assert "人员：@张三 @李四 制作组" in preview
    assert "提醒：提前30分钟" in preview
    assert "无法精确匹配到群成员" in preview
    assert "#确认发布 P001" in preview
    assert "#取消发布 P001" in preview
    # 预览阶段不应发送群通知
    assert app.gateway.messages_to("100001") == []

    confirmed = await app.text("#确认发布 P001")
    assert "已发布排练通知" in confirmed
    assert "编号 1" in confirmed

    messages = app.gateway.messages_to("100001")
    assert len(messages) == 1
    notice = messages[0].plain_text()
    assert notice.startswith("#排练通知\n")
    assert "时间：11月13日 周五 19:00-22:00" in notice
    assert "地点：一排" in notice
    assert "人员：@张三 @李四 制作组" in notice
    assert "内容：\n1. 开场和声\n2. 一念一宇宙" in notice
    assert "备注：其他演员可以来自由练习" in notice

    rehearsal = app.repo.get_rehearsal(app.troupe_a.id, 1)
    assert rehearsal is not None
    assert rehearsal.participants[0].qq_id == "10001"
    assert len(rehearsal.participants) == 3
    assert rehearsal.status == "published"

    jobs = app.repo.pending_jobs(rehearsal.id)
    assert len(jobs) == 1
    assert jobs[0].trigger_at == datetime(2026, 11, 13, 18, 30, tzinfo=TZ)
    assert jobs[0].status == "pending"


async def test_publish_requires_preview_confirmation(app):
    preview = await app.text(PUBLISH)
    assert "P001" in preview
    # 未确认不会生成排练
    assert app.repo.get_rehearsal(app.troupe_a.id, 1) is None


async def test_actor_cannot_publish(app):
    text = await app.text(
        "#发布排练\n时间=明天7~9\n人员=@张三", qq="10003", role="member", nickname="王五"
    )
    assert "权限不足" in text


async def test_publish_missing_time(app):
    text = await app.text("#发布排练\n地点=一排\n人员=@张三")
    assert "请填写排练时间" in text or "时间无法识别" in text


async def test_publish_missing_participants(app):
    text = await app.text("#发布排练\n时间=明天7~9")
    assert "参与人员" in text or "人员" in text


async def test_second_publish_gets_next_seq(app):
    await app.text(PUBLISH)
    await app.text("#确认发布 P001")
    await app.text(PUBLISH)
    confirmed = await app.text("#确认发布 P002")
    assert "编号 2" in confirmed


async def test_confirm_requires_operator(app):
    await app.text(PUBLISH)
    text = await app.text("#确认发布 P001", qq="10002", role="admin", nickname="李四")
    assert "只有发起人" in text


async def test_confirm_unknown_preview(app):
    text = await app.text("#确认发布 P999")
    assert "没有找到" in text


async def test_preview_expires(app):
    await app.text(PUBLISH)
    app.clock.advance(minutes=31)
    text = await app.text("#确认发布 P001")
    assert "已过期" in text


async def test_start_time_passed_note(app):
    app.clock.advance(hours=8)  # 21:00
    text = await app.text("#发布排练\n时间=今天7~10\n地点=一排\n人员=@张三")
    assert "开始时间已过" in text
    assert "将不会发送提醒" in text


async def test_cross_year_date_note(app):
    text = await app.text("#发布排练\n时间=10.21 下午2点~4点\n地点=一排\n人员=@张三")
    assert "该日期今年已过，已按下一年处理" in text


async def test_at_all_downgrade_when_bot_not_admin(app):
    app.gateway.bot_admin["100001"] = False
    text = await app.text("#发布排练\n时间=周五7~10\n人员=@全体成员")
    assert "不是群管理员" in text
    assert "降级" in text
    action = text.split("#确认发布 ")[1].split("\n")[0].strip()
    await app.text(f"#确认发布 {action}")
    notice = app.gateway.messages_to("100001")[0].plain_text()
    assert "@全体成员" not in notice
    assert "@张三" in notice
    assert "@钱七" in notice


async def test_at_all_when_bot_admin(app):
    text = await app.text("#发布排练\n时间=周五7~10\n人员=@全体成员")
    assert "人员：@全体成员" in text
    assert "降级" not in text
    await app.text("#确认发布 P001")
    notice = app.gateway.messages_to("100001")[0].plain_text()
    assert "@全体成员" in notice


async def test_text_person_warning(app):
    text = await app.text(PUBLISH)
    assert "制作组" in text
    assert "不能 @ 或私聊提醒" in text


async def test_publish_in_private_with_troupe(app):
    text = await app.text(
        "#发布排练 剧组=剧组B\n时间=周一7~9\n地点=二楼\n人员=@王五",
        scope="private",
        group_id=None,
        qq="10002",
        nickname="李四",
    )
    assert "【发布预览】" in text
    assert "剧组" not in text or "时间：" in text
    confirmed = await app.text(
        "#确认发布 P001", scope="private", group_id=None, qq="10002", nickname="李四"
    )
    assert "已发布" in confirmed
    assert app.gateway.messages_to("100002")[0].plain_text().startswith("#排练通知")


async def test_publish_private_multi_troupe_requires_choice(app):
    text = await app.text(
        "#发布排练\n时间=周五7~10\n人员=@张三",
        scope="private",
        group_id=None,
        qq="10001",
    )
    assert "请用 剧组=名称 指定" in text


async def test_empty_fields_are_not_displayed(app):
    preview = await app.text("#发布排练\n时间=明天7~9\n人员=@王五")
    action = preview.split("#确认发布 ")[1].split("\n")[0].strip()
    await app.text(f"#确认发布 {action}")
    notice = app.gateway.messages_to("100001")[0].plain_text()
    assert "地点：" not in notice
    assert "内容：" not in notice
    assert "备注：" not in notice


async def test_tags_hidden_by_default(app):
    preview = await app.text("#发布排练\n时间=明天7~9\n人员=湖区@王五")
    action = preview.split("#确认发布 ")[1].split("\n")[0].strip()
    await app.text(f"#确认发布 {action}")
    notice = app.gateway.messages_to("100001")[0].plain_text()
    assert "@王五" in notice
    assert "湖区" not in notice


async def test_at_all_in_content_does_not_change_participants(app):
    preview = await app.text(
        "#发布排练\n时间=明天7~9\n人员=@王五\n内容=@全体成员 请注意"
    )
    assert "通知范围：全体成员" in preview
    action = preview.split("#确认发布 ")[1].split("\n")[0].strip()
    await app.text(f"#确认发布 {action}")
    rehearsal = app.repo.get_rehearsal(app.troupe_a.id, 1)
    assert rehearsal.participant_mode == "specified"
    assert rehearsal.notify_all is True


async def test_publish_with_missed_reminder_window(app):
    app.clock.set(datetime(2026, 11, 13, 18, 45, tzinfo=TZ))
    preview = await app.text("#发布排练\n时间=今天7~10\n地点=一排\n人员=@王五")
    assert "已错过" in preview
    action = preview.split("#确认发布 ")[1].split("\n")[0].strip()
    await app.text(f"#确认发布 {action}")
    rehearsal = app.repo.get_rehearsal(app.troupe_a.id, 1)
    jobs = app.repo.pending_jobs(rehearsal.id)
    assert [job.status for job in jobs] == ["expired"]
    app.gateway.clear()
    assert await app.service.run_due_reminders() == 0
    assert app.gateway.messages_to("100001") == []


async def test_publish_auto_sends_when_no_warnings(app):
    """默认（发布确认=关）：没有风险提示时直接发布，不生成预览。"""
    app.repo.update_troupe(app.troupe_a.id, publish_confirm_when_clean=False)
    reply = await app.text("#发布排练\n时间=明天7~9\n地点=一排\n人员=@王五")
    assert "【发布预览】" not in reply
    assert "已发布排练通知" in reply
    messages = app.gateway.messages_to("100001")
    assert len(messages) == 1
    assert messages[0].plain_text().startswith("#排练通知")
    rehearsal = app.repo.get_rehearsal(app.troupe_a.id, 1)
    assert rehearsal is not None
    assert rehearsal.seq == 1


async def test_publish_with_warnings_still_previews_when_auto(app):
    """发布确认=关，但存在文本人员等风险时仍需预览确认。"""
    app.repo.update_troupe(app.troupe_a.id, publish_confirm_when_clean=False)
    preview = await app.text("#发布排练\n时间=明天7~9\n人员=@王五 制作组")
    assert "【发布预览】" in preview
    assert "无法精确匹配" in preview
    assert "P001" in preview
    assert app.repo.get_rehearsal(app.troupe_a.id, 1) is None


async def test_config_publish_confirm_toggle(app):
    app.repo.update_troupe(app.troupe_a.id, publish_confirm_when_clean=False)
    enabled = await app.text(
        "#配置 发布确认=开", qq="10002", role="admin", nickname="李四"
    )
    assert "总是预览" in enabled
    preview = await app.text("#发布排练\n时间=明天7~9\n人员=@王五")
    assert "【发布预览】" in preview
    assert app.repo.get_rehearsal(app.troupe_a.id, 1) is None

    disabled = await app.text(
        "#配置 发布确认=关", qq="10002", role="admin", nickname="李四"
    )
    assert "无问题时直接发布" in disabled
    reply = await app.text("#发布排练\n时间=明天7~9\n人员=@王五")
    assert "已发布排练通知" in reply
    assert app.repo.get_rehearsal(app.troupe_a.id, 1) is not None
