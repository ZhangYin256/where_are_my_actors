"""角色与权限校验（需求第 4 章）。"""

from __future__ import annotations

from collections.abc import Iterable

from .errors import PermissionDenied

ROLE_RANK = {"actor": 1, "assistant": 2, "director": 3, "admin": 4}

ROLE_LABELS = {
    "admin": "管理员",
    "director": "导演",
    "assistant": "副导演",
    "actor": "演员",
}

# 功能 -> 所需最低角色
CAPABILITY_RANK = {
    "view": 1,
    "publish": 2,
    "modify": 2,
    "republish": 2,
    "cancel": 2,
    "delete": 2,
    "manage_actor": 2,
    "config_troupe": 2,
    "audit": 2,
    "sync_members": 2,
    "grant_assistant": 3,
    "grant_director": 4,
    "grant_admin": 4,
    "unbind": 4,
    "global_config": 4,
    "hard_delete": 4,
}

CAPABILITY_LABELS = {
    "publish": "发布排练通知",
    "modify": "修改排练通知",
    "republish": "重新发布排练通知",
    "cancel": "取消排练通知",
    "delete": "删除排练通知",
    "manage_actor": "管理演员",
    "config_troupe": "修改剧组配置",
    "audit": "查看审计日志",
    "sync_members": "同步群成员",
    "grant_assistant": "授权 / 取消副导演",
    "grant_director": "授权 / 取消导演",
    "grant_admin": "授权 / 取消管理员",
    "unbind": "解绑剧组",
    "global_config": "修改全局配置",
    "hard_delete": "彻底删除排练",
}

_DENIED_HINTS = {
    "publish": "只有导演、副导演或管理员可以发布排练通知。",
    "modify": "只有导演、副导演或管理员可以修改排练通知。",
    "republish": "只有导演、副导演或管理员可以重新发布排练通知。",
    "cancel": "只有导演、副导演或管理员可以取消排练通知。",
    "delete": "只有导演、副导演或管理员可以删除排练通知。",
    "manage_actor": "只有导演、副导演或管理员可以管理演员。",
    "config_troupe": "只有导演、副导演或管理员可以修改剧组配置。",
    "audit": "只有副导演、导演或管理员可以查看审计日志。",
    "sync_members": "只有副导演、导演或管理员可以同步群成员。",
    "grant_assistant": "只有导演或管理员可以授权 / 取消副导演。",
    "grant_director": "只有管理员可以授权 / 取消导演。",
    "grant_admin": "只有管理员可以授权 / 取消管理员。",
    "unbind": "只有管理员可以解绑剧组。",
    "global_config": "只有管理员可以修改全局配置。",
    "hard_delete": "只有管理员可以彻底删除排练记录。",
}


def highest_role(roles: Iterable[str]) -> str | None:
    best: str | None = None
    for role in roles:
        if role not in ROLE_RANK:
            continue
        if best is None or ROLE_RANK[role] > ROLE_RANK[best]:
            best = role
    return best


def role_label(role: str | None) -> str:
    return ROLE_LABELS.get(role or "", "未授权")


def can(role: str | None, capability: str) -> bool:
    if role is None:
        return capability == "view"
    required = CAPABILITY_RANK.get(capability, 99)
    return ROLE_RANK.get(role, 0) >= required


def require(role: str | None, capability: str) -> None:
    if can(role, capability):
        return
    hint = _DENIED_HINTS.get(capability)
    if hint is None:
        hint = f"当前角色（{role_label(role)}）无权执行该操作。"
    raise PermissionDenied(f"权限不足：{hint}")


def can_grant(role: str | None, target_role: str) -> bool:
    """能否授权 / 取消某角色。"""
    if role == "admin":
        return target_role in {"admin", "director", "assistant"}
    if role == "director":
        return target_role == "assistant"
    return False
