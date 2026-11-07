# 架构设计

本文档说明系统的分层、模块职责、关键流程、数据模型与可靠性设计，供二次开发与运维排障使用。

---

## 1. 总览

```text
QQ 用户 / 群
    │  消息
    ▼
NapCat（个人 QQ 协议端）
    │  OneBot V11 / 正向 WebSocket
    ▼
NoneBot2 驱动 + rehearsal_bot 插件
    │  转换为平台无关的 CommandContext
    ▼
RehearsalService（业务核心）
    ├── 权限 / 剧组解析
    ├── 时间解析、字段解析、人员匹配
    ├── 排练生命周期（发布 / 修改 / 重发 / 取消 / 删除）
    ├── 查询与渲染
    └── 提醒任务调度
    │
    ├── SQLite（WAL）
    └── ReminderWorker（10 秒 tick，持久化任务、心跳）

离线模拟器 / 单元测试
    └── 同一个 RehearsalService + FakeGateway（不经过 QQ）
```

设计目标：

1. **业务与协议解耦**：核心 `rehearsal/` 不依赖 NoneBot，可在模拟器与测试中完整运行；
2. **预览后确认**：所有对外发送的正式通知都先落预览、再确认；
3. **可靠提醒**：任务持久化、原子抢占、防重复、失败重试、重启恢复；
4. **可追溯**：审计日志覆盖关键操作与提醒结果。

---

## 2. 目录与模块职责

| 模块 | 职责 |
|---|---|
| `bot.py` | 初始化 NoneBot、注册 OneBot 适配器、加载插件、配置文件日志 |
| `plugins/rehearsal_bot/__init__.py` | 消息事件接入、前缀过滤、回复发送、启动提醒任务、群管理变更同步 |
| `plugins/rehearsal_bot/config.py` | NoneBot 插件配置（`REHEARSAL_*` 环境变量） |
| `plugins/rehearsal_bot/gateway.py` | OneBot V11 网关：发群 / 发私聊 / 群成员查询 / 机器人是否群管理 |
| `rehearsal/service.py` | 命令分发、权限校验、业务流程、提醒发送 |
| `rehearsal/db.py` | SQLite schema、迁移版本、Repository |
| `rehearsal/models.py` | 实体、消息段（Text / At / AtAll）、长消息拆分 |
| `rehearsal/timeparse.py` | 中文模糊时间解析与格式化 |
| `rehearsal/fields.py` | 字段、人员、提醒、范围、标签解析 |
| `rehearsal/render.py` | 通知、预览、提醒、列表、私聊安排、审计等渲染 |
| `rehearsal/permissions.py` | 角色等级、能力矩阵、授权规则 |
| `rehearsal/help.py` | 中文帮助文本 |
| `rehearsal/worker.py` | 提醒循环、心跳、定时群成员同步 |
| `rehearsal/fake.py` | 内存网关（测试 / 模拟器） |
| `rehearsal/simulator.py` | 离线 REPL 与脚本执行 |
| `rehearsal/onebot.py` | 核心消息段 ↔ OneBot 消息 |
| `scripts/` | 备份、健康检查、演示生成 |
| `tests/` | 185 个单元 / 集成测试 |

---

## 3. 关键流程

### 3.1 消息处理

```text
OneBot 消息事件
  → 忽略机器人自己发送的消息
  → 提取文本与 @ 占位符（from_onebot）
  → 校验业务前缀（默认 #）
  → 构造 CommandContext(scope, qq, group_id, sender_role, text)
  → RehearsalService.handle
       → 记录 / 更新用户昵称
       → 命令识别
       → 解析剧组（群号 / 私聊 剧组=名称）
       → 同步群主/群管理员自动导演
       → 权限校验
       → 业务处理（可能写库、发通知、建提醒）
       → 返回 Reply 列表
  → 网关发送回复（长消息自动拆分）
```

`CommandContext` 与 `Reply` 都是纯数据，使核心逻辑可以脱离 NoneBot 测试。

### 3.2 发布流程

```text
#发布排练 + 字段
  → parse_fields
  → parse_time（返回完整日期时间 + 顺延提示）
  → 人员解析（@段 / 演员表 / 群成员 / 文本人员）
  → 构造 RehearsalDraft
  → 写入 pending_action（P001，30 分钟有效，JSON payload）
  → 返回预览

#确认发布 P001
  → 校验：存在、类型、发起人、未过期、群匹配
  → 同一事务写 rehearsal + rehearsal_participant
  → 计算触发时间并写 reminder_job（已过期则 expired）
  → pending_action 标记 consumed
  → 渲染并发送 #排练通知
  → 写审计日志

#取消发布 P001
  → pending_action 标记 cancelled
```

### 3.3 修改 / 重发流程

```text
#排练修改 3 字段...
  → 读取当前排练，合并字段，重新解析时间/人员
  → 写 pending_action（M001，payload 含完整 draft）
  → 返回修改预览

#确认修改 M001
  → 覆盖 rehearsal 当前值 + 重建参与人员
  → reset_jobs（删除未发送任务，保留已发送记录）
  → 按新时间重新创建提醒
  → 不发送群消息
  → 审计

#排练重发 3
  → 返回重发预览（R001）

#确认重发 R001
  → 发送「以下为更新后的排练通知」
  → 更新 published_at
  → 重建未发送提醒
  → 审计
```

### 3.4 提醒流程

```text
ReminderWorker 每 10 秒：
  maintenance:
    - pending_action 过期标记
    - 已结束排练 status=completed
  run_due_reminders:
    - 查询 status=pending 且 trigger_at <= now
    - claim: UPDATE ... SET status=sending, attempts+=1
              WHERE id=? AND status=pending
    - 校验排练状态（published）与宽限窗口
    - 计算 @ 目标（全体 / 参与人员 / 降级全体群成员）
    - 发送群提醒（必要时分段）
    - 若开启私聊提醒，逐个私聊精确参与人员
    - 成功：status=sent；失败：重试或 failed
    - 审计 reminder_sent / reminder_failed
  每 30 分钟：
    - 全量同步所有剧组的群主 / 群管理员身份
  每轮：
    - 写心跳文件 data/heartbeat
```

---

## 4. 数据模型

SQLite 文件默认 `data/rehearsal.db`，启用 WAL。schema 版本通过 `PRAGMA user_version` 管理（当前为 `1`）。

```text
troupe 1 ──< role_binding
       │
       ├──< actor_profile
       │
       └──< rehearsal 1 ──< rehearsal_participant
                     │
                     └──< reminder_job

admin_binding（全局）
pending_action（预览，全局 ID）
audit_log（审计）
setting（全局运行参数）
meta / counter（内部）
user（昵称缓存）
```

### 4.1 主要表

| 表 | 说明 | 关键约束 |
|---|---|---|
| `troupe` | 剧组 / 群绑定 | `group_id UNIQUE` |
| `role_binding` | 剧组内角色 | `UNIQUE(troupe_id, qq_id, role, source)` |
| `admin_binding` | 全局管理员 | `qq_id PRIMARY KEY` |
| `actor_profile` | 演员资料（QQ / 文本人员） | 活跃 QQ 唯一、活跃文本名唯一 |
| `rehearsal` | 排练通知 | `UNIQUE(troupe_id, seq)` |
| `rehearsal_participant` | 本次排练参与人 | 级联删除 |
| `reminder_job` | 提醒任务 | `UNIQUE(rehearsal_id, trigger_at)` |
| `pending_action` | 预览 / 待确认操作 | 主键 `P001/M001/R001` |
| `audit_log` | 审计 | 按 `troupe_id, id` 索引 |
| `setting` | 全局运行参数 | 键值 |

### 4.2 时间存储

- 所有绝对时间以 **UTC ISO 8601** 存储（如 `2026-11-13T11:00:00+00:00`）；
- 展示时转换为剧组时区；
- 排练编号 `seq` 在剧组内自增，跨剧组可重复；数据库主键 `id` 全局唯一；
- 预览 ID 全局自增（P/M/R 前缀分别计数），避免跨剧组冲突。

### 4.3 状态

排练状态：

```text
published ──到期──> completed
    │
    ├──取消──> cancelled
    └──删除──> deleted（软删除，deleted_at）
                  │
                  └──管理员──> 物理删除
```

提醒任务状态：

```text
pending ──claim──> sending ──成功──> sent
   │                  │
   │                  ├──失败且未达上限──> pending（下轮重试）
   │                  └──失败达上限──> failed
   ├──超时/开始已过──> expired
   ├──排练取消/删除──> cancelled
   └──进程崩溃中断──> failed（重启恢复，不重发）
```

---

## 5. 时间解析设计

`rehearsal/timeparse.py` 的处理顺序：

```text
1. 归一化：全角转半角、今晚→今天 晚上、点/时→:、半→:30、到/至→-、空白压缩
2. 提取日期：2026-11-13 / 10月21日 / 10.21 / 周一 / 星期一 / 礼拜一 / 今天 / 明天 / 后天
3. 提取时间范围与时段词：早上 / 上午 / 中午 / 下午 / 傍晚 / 晚上 / 晚 / 凌晨
4. 解析日期基准：
   - 无年份月日：今年已过 → 明年，并记提示
   - 周几：取下一次出现；今天已过 → 下一周，并记提示
   - 无日期：今天已过 → 明天，并记提示
5. 解析小时：
   - 有时段词：按时段偏移
   - 无时段词：开始小时 < 晚上阈值 → 视为晚上（默认 +12）
   - 结束时间跟随开始时段；结束 ≤ 开始 → 跨天
6. 生成 ParsedTime(start, end, notes)
```

晚上阈值按剧组配置（`ambiguous_evening_threshold`），默认 9。所有自动调整都写入 `notes`，在预览中展示。

---

## 6. 权限模型

### 6.1 角色来源

| 来源 | 存储 | 说明 |
|---|---|---|
| 配置管理员 | `admin_binding.source=config` | 启动时若从未有记录则写入 |
| 手动管理员 | `admin_binding.source=manual` | `#授权管理员` |
| 手动导演 / 副导演 | `role_binding.source=manual` | `#授权导演` / `#授权副导演` |
| 群主 / 群管理员 | `role_binding.source=auto_group_admin` | 系统自动同步 |
| 演员 | 无绑定 | 群成员默认展示为演员 |

管理员是**全局**角色；导演 / 副导演按剧组。一个 QQ 可在不同剧组拥有不同角色。

### 6.2 角色解析

```text
if 全局管理员: role = admin
else: role = max(该剧组所有 active 角色绑定)
```

普通群成员在群内展示为「演员」；私聊未注册用户没有角色。

### 6.3 能力矩阵

`permissions.py` 用「能力 → 最低角色等级」实现：

```text
view                                    演员
publish / modify / republish / cancel   副导演
delete / manage_actor / config_troupe   副导演
audit / sync_members                    副导演
grant_assistant                         导演
grant_director / grant_admin            管理员
unbind / global_config / hard_delete    管理员
```

授权目标限制由 `can_grant` 再校验一次：导演只能授权副导演，管理员可授权导演 / 副导演 / 管理员。

### 6.4 群身份同步

- 收到群消息时，根据 `sender.role` 增量同步该用户；
- 收到群管理员变更通知时，立即全量同步该群；
- 提醒循环每 30 分钟全量同步所有剧组；
- `#同步成员` 手动触发。

`auto_group_admin` 与 `manual` 分开记录，撤销群身份只删除自动来源，不影响手动授权。

---

## 7. 提醒可靠性设计

### 7.1 持久化

提醒不是内存定时器，而是 `reminder_job` 行：

- 发布 / 重发 / 修改时按「开始时间 − 提醒分钟」生成；
- 触发时间已过 → 直接写 `expired`，不补发；
- 进程重启后，`pending` 任务继续被扫描。

### 7.2 防重复

```sql
UPDATE reminder_job
SET status='sending', attempts=attempts+1
WHERE id=? AND status='pending'
```

只有 `rowcount=1` 的 worker 才能发送，避免多协程 / 重复扫描导致重复发送。

### 7.3 重启策略

- 启动时 `recover_inflight_jobs()`：所有 `sending` 标记 `failed`，原因「进程重启，发送状态未知」；
- 选择**至多一次**语义：宁可漏发一次，也不重复打扰全群；
- 需要补发时，管理员可 `#排练重发 N` 重建未发送提醒。

### 7.4 重试

- 发送异常 → `attempts < max_attempts` 回到 `pending`，下一轮重试；
- 达到上限 → `failed`，写审计 `reminder_failed`；
- 默认最多 3 次，可在 `#全局配置 重试次数=` 修改。

### 7.5 宽限窗口

`trigger_at` 超过 `grace_seconds`（默认 90 秒）仍未发送 → `expired`，不再补发，避免机器人离线数小时后突然群发过期提醒。

### 7.6 分段与限速

- 逐个 @ 时按 `at_chunk_size`（默认 20）分段；
- 每条消息之间按 `send_interval`（默认 1 秒）限速；
- 长文本按 `max_message_chars`（默认 1500）在换行处拆分；
- 降低触发 QQ 风控的概率。

---

## 8. @全体成员策略

| 场景 | 机器人是群管理员 | 机器人不是群管理员 |
|---|---|---|
| `人员=@全体成员` | 发送真正 `@全体成员` | 降级为逐个 @：默认参与人员，或配置为全体群成员 |
| `通知范围=全体成员` | 提醒时 `@全体成员` | 降级为逐个 @ 精确参与人员（或全体群成员） |
| 普通通知 | 只 @ 参与人员 | 只 @ 参与人员 |

降级目标由剧组配置 `degrade_at_scope` 控制：`participants` / `all_members`。预览中会提示降级原因。

---

## 9. 消息渲染

核心 `Message` 由三种段组成：

```python
Text("时间：...")
At(qq="10001", name="张三")
AtAll()
```

- 业务层只构造 `Message`，不关心 OneBot；
- 插件层转换为 OneBot 消息段（`MessageSegment.at` / `text`）；
- `plain_text()` 用于预览、模拟器、测试断言；
- `split_message()` 负责长消息拆分，保留 @ 段。

---

## 10. 配置分层

```text
.env（部署）
  ├── 连接 / token / 数据库路径 / 初始管理员 / 默认值
  └── 启动时注入 Settings

setting 表（全局运行参数）
  └── 发送间隔、重试次数、宽限、分段人数（立即生效）

troupe 表（剧组配置）
  └── 默认提醒、私聊提醒、晚上阈值、显示标签、显示空字段、降级范围、时区（立即生效）
```

优先级：单条排练字段 > 剧组配置 / 全局参数 > `.env` 默认值。

---

## 11. 并发与性能

- 单进程、单 asyncio 事件循环；
- SQLite 连接使用 `check_same_thread=False` + `RLock`，WAL + `busy_timeout=15s`；
- 写操作多为单行 / 小事务，命令响应远低于 2 秒目标；
- 群成员列表按 `member_cache_seconds` 缓存，减少 OneBot API 调用；
- 提醒每轮最多处理 20 条，避免瞬时大量群发；
- 单实例假设：不要同时启动多个 bot 进程写同一个数据库。

---

## 12. 安全边界

- 所有管理命令先做角色校验，再执行业务；
- SQL 全部参数化；动态列名仅来自白名单（配置键）；
- 私聊查询只按 `qq_id` 过滤本人数据，不返回他人 QQ；
- 审计日志仅副导演及以上可见；
- 预览只能由发起人确认，且 30 分钟过期、校验群归属；
- `.env` / NapCat token 不进入日志与回复；
- 消息发送限速，长消息拆分。

---

## 13. 扩展点

| 需求 | 扩展方式 |
|---|---|
| 新增命令 | `service._dispatch` 增加命令 → 实现 `_cmd_xxx` → 补充帮助与测试 |
| 新增时间写法 | `timeparse.normalize` / 日期 / 时间正则，补 `tests/test_timeparse.py` |
| 新增字段 | `fields.FIELD_ALIASES` + 解析 + 渲染 + 测试 |
| 新增通知渠道 | 实现新的 `Gateway` 协议，复用 `RehearsalService` |
| 迁移 PostgreSQL | 替换 `Repository` 实现，保持服务层接口 |
| Web 管理后台 | 直接复用 `Repository` + `RehearsalService` 方法 |

---

## 14. 与规格的差异说明

| 项目 | 规格 | 实现 | 原因 |
|---|---|---|---|
| 管理员存储 | `role_binding.troupe_id` + admin | 独立 `admin_binding`（全局） | 规格要求管理员“管理所有剧组” |
| 预览确认 | 发布 / 修改 / 取消 / 删除 | 增加重发预览 `R001` | 规格 5.8 要求重发前预览 |
| 彻底删除 | 管理员可物理删除 | 新增 `#彻底删除` | 提供明确的物理删除入口 |
| 初始化权限 | 管理员 | 管理员或群主（可配置） | 首次部署更友好；可关闭 |
| `#排练通知` 别名 | 可作为发布别名 | 群内仅有字段时触发；无字段不响应 | 避免机器人把自己的通知当命令 |
| 私聊提醒 + 全体成员 | 未明确 | 只发群提醒，不逐个私聊 | 避免大规模私聊风控 |
| 提醒崩溃恢复 | 未发送应恢复 | 发送中崩溃标记失败、不重发 | 优先保证不重复发送 |
| 审计角色字段 | 示例含角色 | 存 QQ + 动作；展示时解析昵称 | 规格字段列表未要求快照角色 |
| 全局配置 | 聊天命令修改 | 仅运行参数可改；连接 / 路径改 `.env` | 避免聊天误改导致服务不可用 |

这些差异均不影响规格验收项，详见 [ACCEPTANCE.md](ACCEPTANCE.md)。
