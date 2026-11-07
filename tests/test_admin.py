"""演员、配置、审计、帮助等管理命令测试。"""

from __future__ import annotations

import re


async def publish(app, text: str, *, qq="10001", nickname="张三", role="owner"):
    preview = await app.text(text, qq=qq, role=role, nickname=nickname)
    action = re.search(r"#确认发布 (P\d+)", preview)
    assert action, preview
    result = await app.text(
        f"#确认发布 {action.group(1)}", qq=qq, role=role, nickname=nickname
    )
    app.gateway.clear()
    return result


async def test_add_and_list_actors(app):
    text = await app.text("#添加演员 @王五")
    assert "已添加演员「王五」" in text
    text = await app.text("#添加演员 制作组")
    assert "已添加演员「制作组」" in text
    assert "文本人员" in text
    listing = await app.text("#演员列表")
    assert "王五（10003）" in listing
    assert "制作组" in listing
    assert "群成员（可精确提醒）：1" in listing
    assert "文本人员（仅展示）：1" in listing


async def test_add_duplicate_actor(app):
    await app.text("#添加演员 @王五")
    text = await app.text("#添加演员 @王五")
    assert "已在演员列表" in text


async def test_actor_alias_and_tag(app):
    await app.text("#添加演员 @王五")
    text = await app.text("#演员别名 @王五 小五")
    assert "已设置" in text and "小五" in text
    text = await app.text("#演员标签 @王五 湖区")
    assert "湖区" in text
    listing = await app.text("#演员列表")
    assert "别名：小五" in listing
    assert "标签：湖区" in listing


async def test_actor_alias_resolution_in_publish(app):
    await app.text("#添加演员 @王五")
    await app.text("#演员别名 @王五 小五")
    preview = await app.text("#发布排练\n时间=明天7~9\n人员=小五")
    assert "人员：@王五" in preview
    await app.text("#确认发布 P001")
    notice = app.gateway.messages_to("100001")[0].plain_text()
    assert "@王五" in notice


async def test_remove_actor(app):
    await app.text("#添加演员 @王五")
    text = await app.text("#移除演员 @王五")
    assert "已移除演员" in text
    listing = await app.text("#演员列表")
    assert "王五（10003）" not in listing


async def test_config_view_and_update(app):
    view = await app.text("#配置查看", qq="10002", role="admin", nickname="李四")
    assert "【剧组配置】剧组A" in view
    assert "默认提醒：提前30分钟" in view

    updated = await app.text(
        "#配置 默认提醒=60,30", qq="10002", role="admin", nickname="李四"
    )
    assert "配置已更新" in updated
    assert "提前60、30分钟" in updated
    assert app.repo.get_troupe(app.troupe_a.id).reminders == (60, 30)


async def test_config_private_remind(app):
    await app.text("#配置 私聊提醒=开", qq="10002", role="admin", nickname="李四")
    assert app.repo.get_troupe(app.troupe_a.id).private_remind is True
    await app.text("#配置 私聊提醒=关", qq="10002", role="admin", nickname="李四")
    assert app.repo.get_troupe(app.troupe_a.id).private_remind is False


async def test_config_evening_threshold(app):
    await app.text("#配置 晚上阈值=10", qq="10002", role="admin", nickname="李四")
    preview = await app.text("#发布排练\n时间=9~11\n人员=@王五")
    assert "21:00-23:00" in preview


async def test_config_show_tags(app):
    await app.text("#配置 显示标签=开", qq="10002", role="admin", nickname="李四")
    preview = await app.text("#发布排练\n时间=明天7~9\n人员=湖区@王五")
    action = re.search(r"#确认发布 (P\d+)", preview).group(1)
    await app.text(f"#确认发布 {action}")
    notice = app.gateway.messages_to("100001")[0].plain_text()
    assert "@王五（湖区）" in notice


async def test_config_invalid_value(app):
    text = await app.text("#配置 私聊提醒=也许", qq="10002", role="admin", nickname="李四")
    assert "只能是 开 / 关" in text


async def test_config_no_pairs(app):
    text = await app.text("#配置", qq="10002", role="admin", nickname="李四")
    assert "请指定配置项" in text


async def test_default_remind_command(app):
    text = await app.text("#提醒默认 60,30,10", qq="10002", role="admin", nickname="李四")
    assert "提前60、30、10分钟" in text
    assert app.repo.get_troupe(app.troupe_a.id).reminders == (60, 30, 10)
    preview = await app.text("#发布排练\n时间=明天7~9\n人员=@王五")
    assert "提醒：提前60、30、10分钟" in preview


async def test_default_remind_off(app):
    await app.text("#提醒默认 关闭", qq="10002", role="admin", nickname="李四")
    assert app.repo.get_troupe(app.troupe_a.id).reminders == ()
    preview = await app.text("#发布排练\n时间=明天7~9\n人员=@王五")
    assert "提醒：不提醒" in preview


async def test_global_config_admin_only(app):
    denied = await app.text("#全局配置 发送间隔=0.5", qq="10002", role="admin", nickname="李四")
    assert "只有管理员" in denied
    text = await app.text("#全局配置 发送间隔=0.5 重试次数=2")
    assert "已更新" in text
    assert app.repo.get_setting("send_interval") == "0.5"
    assert app.repo.get_setting("max_attempts") == "2"
    view = await app.text("#全局配置查看")
    assert "发送间隔：0.5" in view
    assert "管理员：10001" in view


async def test_audit_log_records_actions(app):
    await publish(app, "#发布排练\n时间=明天7~9\n地点=一排\n人员=@王五")
    await app.text("#排练修改 1 地点=二排")
    await app.text("#确认修改 M001")
    text = await app.text("#审计日志 20")
    assert "【审计日志】" in text
    assert "发布排练" in text
    assert "修改排练" in text
    assert "查看审计日志" not in text  # 本次记录在查询之后写入


async def test_audit_requires_role(app):
    text = await app.text("#审计日志", qq="10004", role="member", nickname="赵六")
    assert "权限不足" in text or "只有" in text


async def test_help_overview_and_topic(app):
    text = await app.text("#帮助", qq="10004", role="member", nickname="赵六")
    assert "【帮助】排练通知机器人" in text
    assert "我的角色：演员" in text
    assert "#发布排练" in text
    topic = await app.text("#帮助发布排练")
    assert "时间=周五7~10" in topic
    assert "支持" in topic
    unknown = await app.text("#帮助不存在")
    assert "没有找到" in unknown


async def test_troupe_info(app):
    text = await app.text("#剧组信息", qq="10004", role="member", nickname="赵六")
    assert "【剧组信息】剧组A" in text
    assert "群号：100001" in text
    assert "默认提醒：提前30分钟" in text
    assert "我的角色：演员" in text
