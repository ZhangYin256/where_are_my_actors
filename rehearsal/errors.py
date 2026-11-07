"""用户可见错误类型。

业务层只抛出 :class:`UserError` 的子类，适配器捕获后直接把 ``str(exc)``
作为提示回复用户；其他异常统一记录日志并回复通用错误。
"""

from __future__ import annotations


class UserError(Exception):
    """可以直接展示给用户的错误。"""


class PermissionDenied(UserError):
    """权限不足。"""


class NotFound(UserError):
    """目标不存在。"""


class StateError(UserError):
    """当前状态不允许该操作。"""


class ConfigError(UserError):
    """配置项不合法。"""
