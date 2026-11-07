"""中文帮助文本。"""

from __future__ import annotations

HELP_TOPICS = {
    "发布排练": "publish",
    "发布": "publish",
    "查询排练": "query",
    "查询": "query",
    "提醒配置": "remind",
    "提醒": "remind",
    "修改排练": "modify",
    "修改": "modify",
    "权限": "permission",
    "角色": "permission",
    "剧组": "troupe",
    "演员": "actor",
    "配置": "config",
    "预览": "confirm",
    "确认": "confirm",
}


def overview(
    prefix: str,
    *,
    troupe_name: str | None = None,
    role: str | None = None,
    is_private: bool = False,
) -> str:
    lines = ["【帮助】排练通知机器人", f"命令前缀：{prefix}"]
    if troupe_name:
        lines.append(f"当前剧组：{troupe_name}")
    from .permissions import role_label

    lines.append(f"我的角色：{role_label(role)}")
    lines.append("")
    lines.append("一、发布 / 修改（导演、副导演、管理员）")
    lines.append(f"{prefix}发布排练")
    lines.append("时间=周五7~10")
    lines.append("地点=一排")
    lines.append("人员=@张三 @李四 制作组")
    lines.append("内容=1. 开场 2. 走位")
    lines.append("备注=自愿参加")
    lines.append(f"→ 无风险直接发送；出现预览时执行 {prefix}确认发布 P001")
    lines.append("")
    lines.append("二、查询（所有成员）")
    lines.append(f"{prefix}排练列表        查看本剧组未来排练")
    lines.append(f"{prefix}排练列表 本周   查看本周排练")
    lines.append(f"{prefix}排练历史        查看历史排练")
    lines.append(f"{prefix}排练详情 3      查看编号 3 的详情")
    lines.append(f"{prefix}我的排练        私聊查询本人跨剧组安排")
    lines.append(f"{prefix}剧组列表        私聊查看自己参与的剧组")
    lines.append("")
    lines.append("三、管理")
    lines.append(f"{prefix}排练修改 3 时间=周五8~10")
    lines.append(f"{prefix}排练重发 3")
    lines.append(f"{prefix}取消排练 3")
    lines.append(f"{prefix}删除排练 3")
    lines.append(f"{prefix}演员列表 / {prefix}添加演员 @张三")
    lines.append(f"{prefix}配置查看 / {prefix}配置 默认提醒=30")
    lines.append(f"{prefix}审计日志 20")
    lines.append("")
    if is_private:
        lines.append("私聊管理：管理多个剧组时用 剧组=名称 指定，恰好一个可省略。")
        lines.append("管理员私聊初始化：#初始化剧组 剧组名 群号=123456789")
        lines.append("")
    lines.append(f"查看更多：{prefix}帮助发布排练、{prefix}帮助查询排练、{prefix}帮助提醒配置")
    return "\n".join(lines)


def topic(name: str, prefix: str) -> str | None:
    key = HELP_TOPICS.get(name.strip())
    if not key:
        return None
    topics = {
        "publish": _publish(prefix),
        "query": _query(prefix),
        "remind": _remind(prefix),
        "modify": _modify(prefix),
        "permission": _permission(prefix),
        "troupe": _troupe(prefix),
        "actor": _actor(prefix),
        "config": _config(prefix),
        "confirm": _confirm(prefix),
    }
    return topics[key]


def _publish(prefix: str) -> str:
    return f"""【帮助】发布排练
{prefix}发布排练
时间=周五7~10
地点=一排
人员=@张三 @李四 制作组
内容=
1. 福临门开场和声
2. 一念一宇宙
备注=其他演员可以来自由练习
提醒=30,10
通知范围=全体成员

字段分隔符可用 = 或 ： 或 :
地点、内容、备注可填「暂无」。
时间支持：周五7~10 / 周一晚8:30~10:00 / 明天下午2点~4点 / 10.21 19:30-21:00
只有导演、副导演、管理员可以发布；发布前必须预览确认。"""


def _query(prefix: str) -> str:
    return f"""【帮助】查询排练
{prefix}排练列表          未来排练
{prefix}排练列表 本周     本周（周一至周日）
{prefix}排练列表 下周     下周
{prefix}排练历史          已结束的排练
{prefix}排练详情 3        编号 3 的完整通知
{prefix}我的排练          私聊查询本人多个剧组的安排
{prefix}我的排练 今天 / 本周 / 历史

演员可以查看本剧组所有通知；私聊查询只返回本人相关安排。"""


def _modify(prefix: str) -> str:
    return f"""【帮助】修改与重发
{prefix}排练修改 3 时间=周五8~10
{prefix}排练修改 3 地点=二排
{prefix}排练修改 3 人员=@张三 @李四
{prefix}排练修改 3 备注=自愿参加
{prefix}排练修改 3 提醒=30,10
{prefix}排练修改 3 提醒=关闭

修改先预览，确认后保存；保存后不会自动重发。
需要重新发布时发送 {prefix}排练重发 3，确认后群内发送更新通知。
系统不保留历史版本，修改直接覆盖当前值。"""


def _remind(prefix: str) -> str:
    return f"""【帮助】提醒配置
{prefix}提醒默认 30          默认提前 30 分钟
{prefix}提醒默认 60,30,10    多个提醒时间
{prefix}提醒默认 关闭        关闭默认提醒

单条排练：{prefix}排练修改 3 提醒=30,10
关闭单条：{prefix}排练修改 3 提醒=关闭
私聊提醒：{prefix}配置 私聊提醒=开
晚上阈值：{prefix}配置 晚上阈值=9

提醒任务持久化，重启后恢复；同一提醒不会重复发送。"""


def _permission(prefix: str) -> str:
    return f"""【帮助】权限
管理员：系统最高权限，可管理所有剧组与全局配置
导演：本剧组通知、演员、提醒配置，可授权副导演
副导演：本剧组通知、演员、提醒配置，不能授权角色
演员：接收通知、查询排练、私聊查询本人安排

群主、群管理员自动获得导演角色；身份变化时自动撤销。
命令：{prefix}授权副导演 @张三 / {prefix}取消副导演 @张三
管理员：{prefix}授权导演 @张三 / {prefix}授权管理员 QQ号"""


def _troupe(prefix: str) -> str:
    return f"""【帮助】剧组
{prefix}初始化剧组 剧组名称              管理员在群内初始化
{prefix}初始化剧组 剧组名称 群号=123456789  管理员私聊初始化
{prefix}剧组列表                      查看自己参与 / 管理的剧组
{prefix}剧组信息                      查看当前剧组
{prefix}修改剧组名称 新名称            管理员
{prefix}解绑剧组                      管理员；停止提醒，历史数据保留

管理多个剧组时，私聊命令用 剧组=名称 指定。"""


def _actor(prefix: str) -> str:
    return f"""【帮助】演员
{prefix}添加演员 @张三
{prefix}添加演员 制作组
{prefix}移除演员 @张三
{prefix}演员列表
{prefix}演员别名 @张三 小张
{prefix}演员标签 @张三 湖区

文本人员（如「制作组」）只作为展示，不能 @，也不能私聊提醒。"""


def _config(prefix: str) -> str:
    return f"""【帮助】配置
{prefix}配置查看
{prefix}配置 默认提醒=30
{prefix}配置 私聊提醒=开
{prefix}配置 晚上阈值=9
{prefix}配置 显示标签=开
{prefix}配置 显示空字段=关
{prefix}配置 降级范围=参与人员

管理员可用 {prefix}全局配置 发送间隔=1.0 修改全局运行参数。"""


def _confirm(prefix: str) -> str:
    return f"""【帮助】预览与确认
{prefix}确认发布 P001
{prefix}取消发布 P001
{prefix}确认修改 M001
{prefix}取消修改 M001
{prefix}确认重发 R001
{prefix}取消重发 R001
{prefix}确认取消 3 通知=是
{prefix}确认取消 3 通知=否
{prefix}确认删除 3
{prefix}取消删除 3

预览 ID 30 分钟内有效；确认操作只能由发起人执行。"""
