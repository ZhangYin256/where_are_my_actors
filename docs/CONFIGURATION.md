# 配置说明

机器人配置分为三层：

| 层级 | 存放位置 | 生效方式 | 谁可以改 |
|---|---|---|---|
| 部署配置 | `.env` / 环境变量 | 重启后生效 | 服务器管理员 |
| 全局运行参数 | SQLite `setting` 表 | 立即生效 | 机器人管理员（`#全局配置`） |
| 剧组配置 | SQLite `troupe` 表 | 立即生效 | 导演 / 副导演 / 管理员（`#配置`） |

---

## 1. `.env` 部署配置

复制模板：

```bash
cp .env.example .env
```

`.env` 含敏感信息（OneBot token、管理员 QQ），**不要提交到版本库**（`.gitignore` 已忽略）。

### 1.1 NoneBot2 / OneBot

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DRIVER` | `~websockets` | NoneBot 驱动；NapCat 正向 WS 使用 `~websockets` |
| `ONEBOT_WS_URLS` | `["ws://napcat:3001"]` | NapCat 的 OneBot WebSocket 地址（JSON 数组） |
| `ONEBOT_ACCESS_TOKEN` | 无 | 访问 token，必须与 NapCat 网络配置一致 |
| `LOG_LEVEL` | `INFO` | 日志级别：DEBUG / INFO / WARNING / ERROR |
| `SUPERUSERS` | `[]` | NoneBot 内置超级用户，业务权限请用 `REHEARSAL_ADMINS` |
| `COMMAND_START` | `["/"]` | NoneBot 自身命令前缀；不影响业务命令 `#` |

### 1.2 业务配置

| 变量 | 默认值 | 说明 |
|---|---|---|
| `REHEARSAL_ADMINS` | `[]` | 初始管理员 QQ 列表，如 `["10000001"]` |
| `REHEARSAL_DB_PATH` | `data/rehearsal.db` | SQLite 路径；容器内建议 `/data/rehearsal.db` |
| `REHEARSAL_COMMAND_PREFIX` | `#` | 业务命令前缀 |
| `REHEARSAL_TIMEZONE` | `Asia/Shanghai` | 新建剧组的默认时区 |
| `REHEARSAL_DEFAULT_REMIND_MINUTES` | `30` | 新建剧组的默认提醒（可多个：`60,30`） |
| `REHEARSAL_DEFAULT_PRIVATE_REMIND` | `false` | 新建剧组是否默认私聊提醒 |
| `REHEARSAL_PUBLISH_CONFIRM` | `false` | 新建剧组是否总是预览确认；`false` 时无问题直接发布 |
| `REHEARSAL_EVENING_THRESHOLD` | `9` | 新建剧组的晚上判定阈值 |
| `REHEARSAL_DEGRADE_AT_SCOPE` | `participants` | 非管理员降级 @ 范围：`participants` / `all_members` |
| `REHEARSAL_SEND_INTERVAL` | `1.0` | 消息发送最小间隔（秒），防风控 |
| `REHEARSAL_MAX_MESSAGE_CHARS` | `1500` | 单条消息字符上限，超出自动分段 |
| `REHEARSAL_MAX_REMINDER_ATTEMPTS` | `3` | 提醒失败重试次数（1-10） |
| `REHEARSAL_REMINDER_TICK_SECONDS` | `10` | 提醒扫描间隔（秒） |
| `REHEARSAL_REMINDER_GRACE_SECONDS` | `90` | 触发时间超过该秒数视为错过，不再补发 |
| `REHEARSAL_MEMBER_CACHE_SECONDS` | `300` | 群成员列表缓存时间 |
| `REHEARSAL_AT_CHUNK_SIZE` | `20` | 逐个 @ 时每段人数（1-50） |
| `REHEARSAL_ALLOW_OWNER_INIT` | `true` | 允许群主在未初始化时自助初始化剧组 |
| `REHEARSAL_HEARTBEAT_PATH` | `data/heartbeat` | 提醒循环心跳文件，容器健康检查使用 |
| `REHEARSAL_LOG_DIR` | `logs` | 日志目录 |

> 环境变量名区分大小写不敏感；JSON 数组请使用双引号。
> 修改 `.env` 后需要重启进程 / 容器。

### 1.3 示例 `.env`（本地 NapCat）

```dotenv
DRIVER=~websockets
ONEBOT_WS_URLS=["ws://127.0.0.1:3001"]
ONEBOT_ACCESS_TOKEN=please-change-me
LOG_LEVEL=INFO

REHEARSAL_ADMINS=["10000001"]
REHEARSAL_DB_PATH=data/rehearsal.db
REHEARSAL_COMMAND_PREFIX=#
REHEARSAL_TIMEZONE=Asia/Shanghai
REHEARSAL_ALLOW_OWNER_INIT=true
REHEARSAL_PUBLISH_CONFIRM=false
```

### 1.4 示例 `.env`（Docker Compose）

```dotenv
DRIVER=~websockets
ONEBOT_ACCESS_TOKEN=please-change-me

REHEARSAL_ADMINS=["10000001"]
REHEARSAL_DB_PATH=/data/rehearsal.db
REHEARSAL_HEARTBEAT_PATH=/data/heartbeat
```

`docker-compose.yml` 已把 `ONEBOT_WS_URLS` 固定为容器网络内的 `ws://napcat:3001`，无需在 `.env` 中重复。

---

## 2. 初始管理员

- 启动时，`REHEARSAL_ADMINS` 中的 QQ 若**从未有过管理员记录**，会写入管理员；
- 如果该 QQ 曾被取消管理员，重启**不会**自动恢复，避免撤销失效；
- 初始管理员可以继续用 `#授权管理员 QQ号` 增加其他管理员；
- 如果没有任何管理员，只有群主能通过 `#初始化剧组` 完成首次建组；建议部署时务必配置 `REHEARSAL_ADMINS`。

---

## 3. 剧组配置

在群内由导演 / 副导演 / 管理员修改，立即生效。

```text
#配置查看
#配置 默认提醒=60,30
#配置 私聊提醒=开
#配置 晚上阈值=9
#配置 显示标签=开
#配置 显示空字段=开
#配置 发布确认=开
#配置 降级范围=参与人员
#配置 时区=Asia/Shanghai
#提醒默认 30
```

| 配置 | 命令键 | 默认 | 影响 |
|---|---|---|---|
| 默认提醒 | `默认提醒` / `提醒默认` | 30 | 新建排练的提醒时间，支持多个 |
| 私聊提醒 | `私聊提醒` | 关 | 提醒时是否私聊匹配到的参与人员 |
| 晚上阈值 | `晚上阈值` | 9 | 开始小时小于该值按晚上解释 |
| 显示标签 | `显示标签` | 关 | 通知 / 提醒中显示 `@张三（湖区）` |
| 显示空字段 | `显示空字段` | 关 | 地点 / 内容 / 备注为空时显示「暂无」 |
| 发布确认 | `发布确认` | 关 | 关=无问题时直接发布，有问题才预览；开=总是预览确认 |
| 降级范围 | `降级范围` | 参与人员 | 非群管理员时逐个 @ 的目标：`参与人员` / `全体成员` |
| 时区 | `时区` | Asia/Shanghai | 该剧组所有时间解析与展示 |

布尔值支持：`开/关`、`是/否`、`true/false`、`1/0`、`on/off`。

---

## 4. 全局运行参数

仅管理员可用，存放在 `setting` 表，立即生效（无需重启）。

```text
#全局配置查看
#全局配置 发送间隔=1.0
#全局配置 重试次数=3
#全局配置 提醒宽限=90
#全局配置 分段人数=20
```

| 数据库键 | 命令键 | 默认 | 范围 | 说明 |
|---|---|---|---|---|
| `send_interval` | 发送间隔 | 1.0 | 0-30 秒 | 每条消息之间的最小间隔 |
| `max_attempts` | 重试次数 | 3 | 1-10 | 提醒失败最大尝试次数 |
| `grace_seconds` | 提醒宽限 | 90 | 0-3600 秒 | 超过该窗口不补发 |
| `at_chunk_size` | 分段人数 | 20 | 1-50 | 逐个 @ 每段人数 |

优先级：数据库值 > `.env` 默认值。删除 `setting` 表中的键即可回到 `.env` 值。

---

## 5. 配置优先级与生效范围

```text
单条排练的 提醒=...   >  剧组默认提醒  >  .env 默认提醒
数据库 setting 表     >  .env 运行参数
```

- **需要重启**：`.env` 中的连接地址、token、数据库路径、管理员、命令前缀、时区默认值；
- **立即生效**：`#配置`、`#提醒默认`、`#全局配置`；
- 时区按剧组保存，修改后新解析的时间使用新时区；已发布排练的绝对时间不变。

---

## 6. 数据与目录

| 路径 | 说明 | 是否必须持久化 |
|---|---|---|
| `data/rehearsal.db` | SQLite 主库（含 WAL 文件） | 是 |
| `data/heartbeat` | 提醒循环心跳 | 否（自动重建） |
| `data/backups/` | 默认备份目录 | 建议 |
| `logs/` | 运行日志 | 建议 |
| `deploy/napcat/config/` | NapCat 配置 | 是 |
| `deploy/napcat/qq/` | QQ 登录会话 | 是（避免反复扫码） |

备份与恢复步骤见 [OPERATIONS.md](OPERATIONS.md#3-备份与恢复)。
