"""消息渲染：正式通知、预览、提醒、查询结果等。"""

from __future__ import annotations

from datetime import datetime

from .fields import format_remind_minutes
from .models import Message, Participant, Rehearsal, RehearsalDraft, Troupe
from .timeparse import (
    WEEKDAY_NAMES,
    format_date,
    format_notice_time,
    format_preview_time,
    format_relative_day,
    format_time_range,
)


def _content_lines(lines: list[str]) -> list[str]:
    if not lines:
        return []
    if any(line.lstrip()[:1].isdigit() for line in lines):
        return list(lines)
    return [f"{index}. {line}" for index, line in enumerate(lines, 1)]


def _add_field(message: Message, label: str, value: str) -> None:
    if value:
        message.add_text(f"{label}：{value}\n")


def add_participants(
    message: Message,
    troupe: Troupe,
    participants: list[Participant],
    participant_mode: str = "specified",
) -> None:
    if participant_mode == "all":
        message.add_at_all()
        return
    first = True
    for participant in participants:
        if not first:
            message.add_text(" ")
        first = False
        if participant.precise and participant.qq_id:
            message.add_at(participant.qq_id, participant.display_name)
            if troupe.show_tags and participant.remark_tag:
                message.add_text(f"（{participant.remark_tag}）")
        else:
            message.add_text(participant.display_name)


def _add_content(message: Message, lines: list[str]) -> None:
    rendered = _content_lines(lines)
    if not rendered:
        return
    message.add_text("内容：\n")
    message.add_text("\n".join(rendered) + "\n")


def notice_message(
    troupe: Troupe,
    rehearsal: Rehearsal,
    now: datetime,
    *,
    updated: bool = False,
) -> Message:
    """正式群内排练通知，标题固定为 ``#排练通知``。"""
    start = rehearsal.start_at.astimezone(troupe.tz)
    end = rehearsal.end_at.astimezone(troupe.tz)
    message = Message.text("#排练通知\n")
    if updated:
        message.add_text("以下为更新后的排练通知\n")
    message.add_text(f"时间：{format_notice_time(start, end, now.astimezone(troupe.tz))}\n")
    if rehearsal.location:
        message.add_text(f"地点：{rehearsal.location}\n")
    elif troupe.show_empty:
        message.add_text("地点：暂无\n")
    message.add_text("人员：")
    add_participants(message, troupe, rehearsal.participants, rehearsal.participant_mode)
    message.add_text("\n")
    if rehearsal.content:
        _add_content(message, rehearsal.content)
    elif troupe.show_empty:
        message.add_text("内容：暂无\n")
    if rehearsal.remark:
        message.add_text(f"备注：{rehearsal.remark}\n")
    elif troupe.show_empty:
        message.add_text("备注：暂无\n")
    return message


def preview_message(
    troupe: Troupe,
    draft: RehearsalDraft,
    *,
    kind: str,
    action_id: str,
    now: datetime,
    prefix: str = "#",
    seq: int | None = None,
    original_time: str | None = None,
) -> Message:
    """发布 / 修改 / 重发预览。"""
    titles = {"publish": "【发布预览】", "modify": "【修改预览】", "republish": "【重发预览】"}
    confirms = {"publish": "确认发布", "modify": "确认修改", "republish": "确认重发"}
    cancels = {"publish": "取消发布", "modify": "取消修改", "republish": "取消重发"}
    start = draft.start_at.astimezone(troupe.tz)
    end = draft.end_at.astimezone(troupe.tz)

    message = Message.text(titles.get(kind, "【预览】") + "\n")
    if seq is not None:
        message.add_text(f"编号：{seq}\n")
    message.add_text("标题：#排练通知\n")
    if original_time:
        message.add_text(f"原时间：{original_time}\n")
    message.add_text(f"时间：{format_preview_time(start, end)}\n")
    if draft.location:
        message.add_text(f"地点：{draft.location}\n")
    if draft.participant_mode == "all":
        message.add_text("人员：")
        message.add_at_all()
        message.add_text("\n")
    elif draft.participants:
        message.add_text("人员：")
        add_participants(message, troupe, draft.participants, draft.participant_mode)
        message.add_text("\n")
    if draft.content:
        _add_content(message, draft.content)
    if draft.remark:
        message.add_text(f"备注：{draft.remark}\n")
    message.add_text(f"提醒：{format_remind_minutes(draft.remind_minutes)}\n")
    if draft.notify_all or draft.participant_mode == "all":
        message.add_text("通知范围：全体成员\n")

    if draft.notes:
        message.add_text("\n")
        for note in draft.notes:
            message.add_text(f"⚠️ {note}\n")

    if draft.text_participants:
        names = "、".join(p.display_name for p in draft.text_participants)
        message.add_text(
            "\n⚠️ 以下人员无法精确匹配到群成员，将仅作为文本展示，不能 @ 或私聊提醒：\n"
            f"- {names}\n"
        )

    message.add_text("\n")
    confirm_label = confirms.get(kind, "操作")
    cancel_label = cancels.get(kind, "取消")
    message.add_text(f"如{confirm_label}，请发送：\n{prefix}{confirm_label} {action_id}\n")
    message.add_text(f"\n取消请发送：\n{prefix}{cancel_label} {action_id}\n")
    return message


def reminder_message(
    troupe: Troupe,
    rehearsal: Rehearsal,
    minutes: int,
    now: datetime,
    *,
    at_all: bool = False,
    mention_participants: list[Participant] | None = None,
) -> Message:
    local_now = now.astimezone(troupe.tz)
    start = rehearsal.start_at.astimezone(troupe.tz)
    end = rehearsal.end_at.astimezone(troupe.tz)
    message = Message.text("#排练提醒\n")
    if minutes > 0:
        message.add_text(f"还有{minutes}分钟开始\n")
    else:
        message.add_text("排练即将开始\n")
    message.add_text(f"时间：{format_relative_day(start, local_now)} {format_time_range(start, end)}\n")
    if rehearsal.location:
        message.add_text(f"地点：{rehearsal.location}\n")
    if rehearsal.content:
        _add_content(message, rehearsal.content)
    if rehearsal.remark:
        message.add_text(f"备注：{rehearsal.remark}\n")
    participants = mention_participants
    if participants is None:
        participants = rehearsal.precise_participants
    if at_all or participants:
        message.add_text("\n请相关成员准时参加：\n")
        if at_all:
            message.add_at_all()
        else:
            first = True
            for participant in participants:
                if not first:
                    message.add_text(" ")
                first = False
                message.add_at(participant.qq_id or "", participant.display_name)
    return message


def mention_continuation(participants: list[Participant]) -> Message:
    message = Message.text("请相关成员准时参加：\n")
    first = True
    for participant in participants:
        if not first:
            message.add_text(" ")
        first = False
        message.add_at(participant.qq_id or "", participant.display_name)
    return message


def private_reminder_message(
    troupe: Troupe,
    rehearsal: Rehearsal,
    minutes: int,
    now: datetime,
) -> Message:
    local_now = now.astimezone(troupe.tz)
    start = rehearsal.start_at.astimezone(troupe.tz)
    end = rehearsal.end_at.astimezone(troupe.tz)
    message = Message.text(f"【排练提醒】{troupe.name}\n")
    if minutes > 0:
        message.add_text(f"还有{minutes}分钟开始\n")
    else:
        message.add_text("排练即将开始\n")
    message.add_text(f"时间：{format_relative_day(start, local_now)} {format_time_range(start, end)}\n")
    if rehearsal.location:
        message.add_text(f"地点：{rehearsal.location}\n")
    if rehearsal.content:
        message.add_text(f"内容：{'；'.join(rehearsal.content)}\n")
    if rehearsal.remark:
        message.add_text(f"备注：{rehearsal.remark}\n")
    return message


def cancel_notice_message(troupe: Troupe, rehearsal: Rehearsal, now: datetime) -> Message:
    local_now = now.astimezone(troupe.tz)
    start = rehearsal.start_at.astimezone(troupe.tz)
    end = rehearsal.end_at.astimezone(troupe.tz)
    message = Message.text("#排练取消通知\n")
    message.add_text(
        f"原定于{format_date(start, local_now)} {format_time_range(start, end)}的排练已取消。\n"
    )
    if rehearsal.location:
        message.add_text(f"地点：{rehearsal.location}\n")
    if rehearsal.remark:
        message.add_text(f"备注：{rehearsal.remark}\n")
    return message


def list_message(
    troupe: Troupe,
    rehearsals: list[Rehearsal],
    now: datetime,
    *,
    label: str = "未来排练",
) -> Message:
    local_now = now.astimezone(troupe.tz)
    message = Message.text(f"【排练列表】{troupe.name}\n{label}\n")
    if not rehearsals:
        message.add_text("暂无排练安排。\n")
        return message
    message.add_text(f"共 {len(rehearsals)} 条\n\n")
    for index, rehearsal in enumerate(rehearsals, 1):
        start = rehearsal.start_at.astimezone(troupe.tz)
        end = rehearsal.end_at.astimezone(troupe.tz)
        status = ""
        if rehearsal.status == "cancelled":
            status = "（已取消）"
        elif rehearsal.status == "completed":
            status = "（已结束）"
        message.add_text(
            f"{index}. 编号{rehearsal.seq} {format_date(start, local_now)} "
            f"{format_time_range(start, end)}{status}\n"
        )
        if rehearsal.location:
            message.add_text(f"   地点：{rehearsal.location}\n")
        if rehearsal.content:
            message.add_text(f"   内容：{'；'.join(rehearsal.content)}\n")
    message.add_text("\n发送 #排练详情 编号 查看完整通知。\n")
    return message


def detail_message(
    troupe: Troupe, rehearsal: Rehearsal, now: datetime, *, created_by_name: str = ""
) -> Message:
    local_now = now.astimezone(troupe.tz)
    start = rehearsal.start_at.astimezone(troupe.tz)
    end = rehearsal.end_at.astimezone(troupe.tz)
    status_labels = {
        "published": "已发布",
        "cancelled": "已取消",
        "completed": "已结束",
        "deleted": "已删除",
    }
    message = Message.text(f"【排练详情】编号 {rehearsal.seq}\n")
    message.add_text(f"状态：{status_labels.get(rehearsal.status, rehearsal.status)}\n")
    message.add_text(f"时间：{format_notice_time(start, end, local_now)}\n")
    if rehearsal.location:
        message.add_text(f"地点：{rehearsal.location}\n")
    message.add_text("人员：")
    add_participants(message, troupe, rehearsal.participants, rehearsal.participant_mode)
    message.add_text("\n")
    if rehearsal.content:
        _add_content(message, rehearsal.content)
    if rehearsal.remark:
        message.add_text(f"备注：{rehearsal.remark}\n")
    message.add_text(f"提醒：{format_remind_minutes(rehearsal.remind_minutes)}\n")
    if created_by_name:
        message.add_text(f"发布人：{created_by_name}\n")
    return message


def my_schedule_message(
    entries: list[tuple[Troupe, Rehearsal, str]],
    title: str = "【你的排练安排】",
) -> Message:
    message = Message.text(f"{title}\n")
    if not entries:
        message.add_text("暂无排练安排。\n")
        return message
    previous_date = None
    for troupe, rehearsal, tag in entries:
        start = rehearsal.start_at.astimezone(troupe.tz)
        end = rehearsal.end_at.astimezone(troupe.tz)
        if start.date() != previous_date:
            if previous_date is not None:
                message.add_text("\n")
            message.add_text(f"{start:%Y-%m-%d} {WEEKDAY_NAMES[start.weekday()]}\n")
            previous_date = start.date()
        tag_text = f"（{tag}）" if tag else ""
        status_text = ""
        if rehearsal.status == "cancelled":
            status_text = "（已取消）"
        elif rehearsal.status == "completed":
            status_text = "（已结束）"
        message.add_text(f"{format_time_range(start, end)} {troupe.name}{tag_text}{status_text}\n")
        if rehearsal.location:
            message.add_text(f"地点：{rehearsal.location}\n")
        if rehearsal.content:
            message.add_text(f"内容：{'；'.join(rehearsal.content)}\n")
    return message


def audit_message(entries, name_of, tz) -> Message:
    labels = {
        "grant_role": "授权角色",
        "revoke_role": "取消角色",
        "add_actor": "添加演员",
        "remove_actor": "移除演员",
        "update_actor": "修改演员",
        "publish": "发布排练",
        "modify": "修改排练",
        "republish": "重发排练",
        "cancel": "取消排练",
        "delete": "删除排练",
        "hard_delete": "彻底删除排练",
        "config": "修改配置",
        "global_config": "修改全局配置",
        "reminder_sent": "提醒发送",
        "reminder_failed": "提醒失败",
        "parse_failed": "解析失败",
        "view_audit": "查看审计日志",
        "cancel_preview": "取消预览",
        "init_troupe": "初始化剧组",
        "unbind": "解绑剧组",
        "sync_members": "同步群成员",
        "login": "系统",
    }
    message = Message.text("【审计日志】\n")
    if not entries:
        message.add_text("暂无记录。\n")
        return message
    for entry in entries:
        created = entry.created_at.astimezone(tz) if entry.created_at else None
        stamp = created.strftime("%m-%d %H:%M") if created else "--"
        action = labels.get(entry.action, entry.action)
        name = name_of(entry.qq_id)
        target = f" {entry.target}" if entry.target else ""
        summary = f" {entry.summary}" if entry.summary else ""
        mark = "✓" if entry.result == "success" else "✗"
        message.add_text(f"{stamp} {name} {action}{target}{summary} {mark}\n")
        if entry.message and entry.result != "success":
            message.add_text(f"    原因：{entry.message}\n")
    return message


def troupe_info_message(
    troupe: Troupe, now: datetime, *, role_label: str = "演员", is_admin: bool = False
) -> Message:
    local_now = now.astimezone(troupe.tz)
    message = Message.text(f"【剧组信息】{troupe.name}\n")
    message.add_text(f"群号：{troupe.group_id}\n")
    message.add_text(f"状态：{'已解绑' if troupe.status != 'active' else '正常'}\n")
    message.add_text(f"时区：{troupe.timezone}\n")
    message.add_text(f"默认提醒：{format_remind_minutes(troupe.reminders)}\n")
    message.add_text(f"私聊提醒：{'开启' if troupe.private_remind else '关闭'}\n")
    message.add_text(f"晚上阈值：{troupe.ambiguous_evening_threshold}\n")
    message.add_text(f"显示备注标签：{'开启' if troupe.show_tags else '关闭'}\n")
    message.add_text(
        "发布确认："
        + ("总是预览" if troupe.publish_confirm_when_clean else "无问题时直接发布")
        + "\n"
    )
    message.add_text(f"我的角色：{'管理员' if is_admin else role_label}\n")
    message.add_text(f"当前时间：{local_now:%Y-%m-%d %H:%M}（{troupe.timezone}）\n")
    if troupe.created_at:
        message.add_text(f"创建时间：{troupe.created_at.astimezone(troupe.tz):%Y-%m-%d}\n")
    return message


def config_message(troupe: Troupe, settings: dict[str, str]) -> Message:
    message = Message.text(f"【剧组配置】{troupe.name}\n")
    message.add_text(f"默认提醒：{format_remind_minutes(troupe.reminders)}\n")
    message.add_text(f"私聊提醒：{'开启' if troupe.private_remind else '关闭'}\n")
    message.add_text(
        f"晚上阈值：{troupe.ambiguous_evening_threshold}"
        f"（开始小时 < {troupe.ambiguous_evening_threshold} 时按晚上解释）\n"
    )
    message.add_text(f"显示标签：{'开启' if troupe.show_tags else '关闭'}\n")
    message.add_text(f"显示空字段：{'开启' if troupe.show_empty else '关闭'}\n")
    message.add_text(
        "发布确认："
        + ("总是预览" if troupe.publish_confirm_when_clean else "无问题时直接发布")
        + "\n"
    )
    scope = "全体群成员" if troupe.degrade_at_scope == "all_members" else "仅参与人员"
    message.add_text(f"降级 @ 范围：{scope}\n")
    message.add_text(f"时区：{troupe.timezone}\n")
    message.add_text("\n【全局设置】\n")
    message.add_text(f"发送间隔：{settings.get('send_interval', '1.0')} 秒\n")
    message.add_text(f"提醒重试：{settings.get('max_attempts', '3')} 次\n")
    message.add_text(f"提醒宽限：{settings.get('grace_seconds', '90')} 秒\n")
    return message
