# 运维手册

覆盖日志、健康检查、备份恢复、升级、监控、故障处理与安全。

---

## 1. 日常检查清单

| 检查项 | 命令 | 期望 |
|---|---|---|
| 容器状态 | `docker compose ps` | `rehearsal-bot`、`rehearsal-napcat` 均为 `Up` |
| 应用健康 | `docker compose exec -T bot python scripts/healthcheck.py` | `healthy` |
| QQ 在线 | `docker compose logs --tail=50 napcat` | 已登录、WS 服务正常 |
| 机器人连接 | `docker compose logs --tail=100 bot` | `QQ 账号已连接` |
| 提醒循环 | 心跳文件 `data/heartbeat` | 10 秒内更新 |
| 待发送提醒 | 见第 5 节 SQL | 数量与近期排练匹配 |
| 备份 | `ls -lh data/backups` | 最近 24 小时内有新备份 |

建议把健康检查接入 Uptime Kuma / 云监控：

```bash
# Docker 已内置 HEALTHCHECK
docker inspect --format '{{.State.Health.Status}}' rehearsal-bot

# 外部探针（返回码 0/1）
docker compose exec -T bot python scripts/healthcheck.py
```

---

## 2. 日志

| 位置 | 内容 |
|---|---|
| `logs/nonebot.log` | 应用日志（10 MB 轮转，保留 14 天） |
| `docker compose logs bot` | 容器 stdout（loguru 输出） |
| `docker compose logs napcat` | NapCat 登录、WS、风控日志 |
| 数据库 `audit_log` 表 | 业务操作审计（结构化、可查询） |

调整日志级别：

```dotenv
LOG_LEVEL=DEBUG
```

重启后生效。生产环境建议保持 `INFO`；排查问题时临时开 `DEBUG`。

常用命令：

```bash
docker compose logs -f --tail=200 bot
docker compose logs -f --tail=200 napcat
tail -f logs/nonebot.log
grep -i "error\|exception" logs/nonebot.log | tail -50
```

---

## 3. 备份与恢复

### 3.1 备份内容

| 内容 | 路径 | 重要性 |
|---|---|---|
| SQLite 数据库 + WAL | `data/rehearsal.db*` | 最高 |
| 备份文件 | `data/backups/` | 中 |
| NapCat 配置 | `deploy/napcat/config/` | 高 |
| QQ 登录会话 | `deploy/napcat/qq/` | 高（丢失需重新扫码） |
| `.env` | 项目根 | 高（含 token / 管理员） |
| 日志 | `logs/` | 低 |

### 3.2 在线备份（推荐）

脚本使用 SQLite 在线备份 API，兼容 WAL，不会阻塞正常读写：

```bash
# 容器内（写入 ./data/backups）
docker compose exec -T bot python scripts/backup_db.py --keep 14

# 本地部署
python scripts/backup_db.py --db data/rehearsal.db --out-dir data/backups --keep 14
```

参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--db` | `data/rehearsal.db` 或 `REHEARSAL_DB_PATH` | 源数据库 |
| `--out-dir` | 数据库同级的 `backups/` | 输出目录 |
| `--keep` | 14 | 保留最近 N 份 |
| `--quiet` | 关闭 | 仅输出错误 |

脚本会：

1. 以只读方式打开数据库；
2. 备份到 `rehearsal-YYYYmmdd-HHMMSS.db`；
3. 对备份做 `PRAGMA quick_check`；
4. 删除超出保留数量的旧备份。

### 3.3 定时备份

Linux cron（每天 03:00）：

```cron
0 3 * * * cd /opt/where_are_my_actors && docker compose exec -T bot python scripts/backup_db.py --keep 14 >> logs/backup.log 2>&1
```

systemd timer 示例：

```ini
# /etc/systemd/system/rehearsal-backup.service
[Unit]
Description=Backup rehearsal bot database

[Service]
Type=oneshot
WorkingDirectory=/opt/where_are_my_actors
ExecStart=/usr/bin/docker compose exec -T bot python scripts/backup_db.py --keep 14
```

```ini
# /etc/systemd/system/rehearsal-backup.timer
[Unit]
Description=Daily rehearsal bot backup

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl enable --now rehearsal-backup.timer
```

Windows 任务计划程序（每天 03:00）：

```powershell
$root = "D:\Codes\where_are_my_actors"
$action = New-ScheduledTaskAction `
  -Execute "docker" `
  -Argument "compose exec -T bot python scripts/backup_db.py --keep 14" `
  -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At 03:00
Register-ScheduledTask -TaskName "RehearsalBotBackup" -Action $action -Trigger $trigger
```

### 3.4 校验备份

```bash
# 用 sqlite3 打开并检查
sqlite3 data/backups/rehearsal-20261107-030000.db "PRAGMA quick_check; select count(*) from rehearsal;"
```

容器内没有 `sqlite3` 命令时，可用 Python：

```bash
docker compose exec -T bot python -c "
import sqlite3,sys
db=sqlite3.connect('data/backups/xxx.db')
print(db.execute('PRAGMA quick_check').fetchone())
print(db.execute('select count(*) from rehearsal').fetchone())
"
```

### 3.5 恢复

> 恢复会覆盖当前数据。先停止机器人，再操作。

```bash
# 1. 停止应用（NapCat 可继续运行）
docker compose stop bot

# 2. 备份当前数据库（以防恢复后发现问题）
cp data/rehearsal.db data/rehearsal.db.before-restore

# 3. 删除 WAL / SHM 残留（必须）
rm -f data/rehearsal.db-wal data/rehearsal.db-shm

# 4. 覆盖为备份文件
cp data/backups/rehearsal-20261107-030000.db data/rehearsal.db

# 5. 启动并验证
docker compose start bot
docker compose exec -T bot python scripts/healthcheck.py
docker compose logs --tail=100 bot
```

本地部署把 `docker compose stop/start` 换成停止 / 启动进程即可，路径对应 `data/rehearsal.db`。

恢复后建议在群内发送 `#排练列表`、`#审计日志` 抽查数据。

### 3.6 整机迁移

1. 在新机器部署代码与 `.env`；
2. 复制 `data/`、`deploy/napcat/config/`、`deploy/napcat/qq/`；
3. 启动 NapCat（会话在，通常无需扫码）；
4. 启动 bot，验证健康状态与 `#剧组信息`；
5. 如 token / 端口变化，同步修改 `.env` 与 NapCat 配置。

---

## 4. 升级与回滚

### 升级

```bash
docker compose exec -T bot python scripts/backup_db.py --keep 14
git pull
docker compose build bot
docker compose up -d bot
docker compose logs -f --tail=100 bot
```

数据库版本由 `PRAGMA user_version` 管理，当前为 `2`。程序启动时会自动执行增量迁移（v1 → v2 增加 `publish_confirm_when_clean` 列，不改动已有数据）。如果程序检测到数据库版本高于自身支持版本，会拒绝启动，避免写坏数据。升级前仍建议先备份。

### 回滚

```bash
git checkout <上一个版本>
docker compose build bot
docker compose up -d bot
# 如数据库结构不兼容，用升级前备份恢复
```

---

## 5. 监控与巡检 SQL

```bash
docker compose exec -T bot python - <<'PY'
import sqlite3
db = sqlite3.connect('/data/rehearsal.db')
db.row_factory = sqlite3.Row
for sql in [
    "select status, count(*) n from rehearsal group by status",
    "select status, count(*) n from reminder_job group by status",
    "select count(*) n from pending_action where status='pending'",
    "select count(*) n from rehearsal where status='published' and start_at >= datetime('now')",
]:
    print(sql)
    for row in db.execute(sql):
        print('  ', dict(row))
PY
```

关键指标：

| 指标 | 健康范围 | 异常处理 |
|---|---|---|
| `reminder_job.status = pending` | 与未来排练数量匹配 | 持续增长且不发送：检查提醒循环 |
| `failed` | 0 | 查看 `last_error`；确认 NapCat 在线后重发 / 重建提醒 |
| `sending` | 0 | 重启后会自动标记失败，不会重复发送 |
| `pending_action.pending` | 少量 | 30 分钟后自动过期 |
| 数据库大小 | 通常 < 100 MB | `VACUUM` 回收空间 |

找回失败提醒：

```text
#排练重发 3
#确认重发 R001
```

重发会重建未发送的提醒任务（已发送的不会重复）。

---

## 6. 审计日志导出

```bash
# 最近 100 条
docker compose exec -T bot python -c "
import sqlite3
db=sqlite3.connect('/data/rehearsal.db'); db.row_factory=sqlite3.Row
for row in db.execute('select * from audit_log order by id desc limit 100'):
    print(dict(row))
"
```

导出 CSV（在宿主机执行，需安装 sqlite3）：

```bash
sqlite3 data/rehearsal.db -header -csv \
  "select created_at, qq_id, action, target, summary, result, message from audit_log order by id desc" \
  > logs/audit-export.csv
```

审计数据包含 QQ 号，导出文件请按内部资料管理。

---

## 7. 故障处理

| 现象 | 可能原因 | 处理 |
|---|---|---|
| 机器人完全不回复 | 进程未启动 / NapCat 未连接 / token 错误 | 看 `logs`；检查 `ONEBOT_WS_URLS` 与 token；确认 QQ 在线 |
| 只有 @ 才回复 | QQ 设置 / 群内屏蔽 / 消息被拦截 | 检查群设置、机器人是否被禁言 |
| 提示“本群尚未绑定剧组” | 未初始化 | `#初始化剧组 名称` |
| 提示权限不足 | 角色不够 / 管理员未配置 | 检查 `REHEARSAL_ADMINS`；用管理员授权 |
| 发布后没发群消息 | 未确认 / 发送失败 | 看预览 ID；`#排练重发 N` 重试；看审计 `fail` |
| 提醒没发 | 开始时间已过 / 提醒关闭 / 循环异常 | `#配置查看`；看 `heartbeat`；看 `reminder_job` |
| 提醒重复发送 | 多实例同时运行 / 手动重发 | 确认只有一个 bot 进程；检查 `sending` 状态 |
| 提醒延迟大 | 扫描间隔 / 队列积压 | 默认 10 秒扫描；检查 NapCat 网络 |
| @全体成员失败 | 机器人非群管理员 | 设置群管理员，或接受降级逐个 @ |
| 数据库 locked | 备份 / 多实例 / 磁盘慢 | 只运行一个实例；避免直接写库；`PRAGMA busy_timeout` 已设 |
| 时间不对 | 时区 / 服务器时钟 | 核对 `TZ`、`#剧组信息`、`date`；开启 NTP |
| 中文乱码 | 编码不是 UTF-8 | `.env`、终端、数据库均使用 UTF-8 |
| 磁盘满 | 日志 / 备份 / QQ 缓存 | 清理旧备份与日志；检查 `deploy/napcat` |
| NapCat 掉线 | 风控 / 网络 / 客户端更新 | 重新扫码；查看 NapCat 日志；保持客户端版本兼容 |

### 7.1 机器人不回复的排查顺序

```text
1. docker compose ps                     # 容器是否 Up
2. docker compose logs --tail=200 bot    # 是否加载插件、是否连接 QQ
3. docker compose logs --tail=200 napcat # QQ 是否在线、WS 是否启动
4. 用另一个 QQ 私聊机器人 #帮助          # 排除群设置问题
5. 检查 .env 的 ONEBOT_ACCESS_TOKEN      # 与 NapCat 是否一致
6. 检查命令前缀                          # 默认 #
```

### 7.2 管理员初始化被拒绝 / 管理员锁死

现象：管理员发送 `#初始化剧组`，提示“只有管理员或群主可以初始化剧组”。

排查：

1. 私聊发送 `#剧组列表`，查看“全局管理员：是/否”；
2. 检查容器环境变量是否包含正确的管理员 QQ：

```bash
docker compose exec -T bot env | grep REHEARSAL_ADMINS
```

3. 格式必须是 JSON 数组，例如：

```dotenv
REHEARSAL_ADMINS=["123456789"]
```

4. 修改 `.env` 后重启：

```bash
docker compose up -d bot
```

如果已经没有任何有效管理员，使用恢复脚本（立即生效，无需重启）：

```bash
docker compose exec -T bot python scripts/grant_admin.py 123456789
```

脚本会写入 `admin_binding` 并打印当前管理员列表。启动时也会自动记录：

```text
当前有效管理员：123456789
```

如果配置了管理员但数据库里的记录被停用、且没有任何其他有效管理员，启动会自动按配置恢复；
如果仍有其他管理员，则不会覆盖“取消管理员”的结果。

### 7.3 私聊初始化剧组

管理员可私聊执行，避免群内刷屏：

```text
#初始化剧组 某某剧组 群号=123456789
```

要求：

- 执行者是全局管理员；
- 机器人已加入目标群，且能读取群成员；
- 该群尚未绑定其他剧组（已解绑的可以重新绑定）。

失败提示与处理：

| 提示 | 原因 |
|---|---|
| 只有管理员可以在私聊初始化剧组 | 当前 QQ 不是全局管理员 |
| 需要提供剧组群号 | 未写 `群号=` |
| 无法读取群 N 的成员列表 | 群号错误 / 机器人未入群 |
| 群 N 已绑定剧组 | 已绑定，请使用 `#修改剧组名称` 或先解绑 |

### 7.4 提醒不发送的排查顺序

```text
1. #排练详情 N                  # 开始时间是否已过？提醒是否关闭？
2. #配置查看                    # 默认提醒 / 私聊设置
3. 看 data/heartbeat 是否更新    # 提醒循环是否存活
4. 查询 reminder_job 表          # pending / expired / failed / cancelled
5. 看 audit_log 的 reminder_failed
6. #排练重发 N 重建提醒
```

---

## 8. 安全

1. **token**：`ONEBOT_ACCESS_TOKEN` 使用长随机串，泄露后立即更换并重启两端；
2. **WebUI**：6099 端口仅在部署时开放，用完关闭或限制来源 IP；
3. **`.env`**：权限设为 `600`（Linux），不要提交 Git、不要截图发群；
4. **数据**：备份含 QQ 号、排练安排，按内部资料管理；必要时加密存储；
5. **管理员**：只授予可信人员；定期审查 `#全局配置查看` 中的管理员列表；
6. **QQ 账号**：使用专用账号，避免主账号；控制发送频率；重要通知保留人工备份渠道；
7. **多实例**：SQLite 与提醒任务按单实例设计，不要同时启动多个 bot 进程；
8. **依赖**：定期 `uv sync` 更新安全补丁，升级前备份。

---

## 9. 容量与维护

| 项目 | 建议 |
|---|---|
| SQLite 规模 | 10 个剧组、每剧组数千条排练无压力 |
| 数据库清理 | 软删除记录可保留；历史数据量大时可归档或 `VACUUM` |
| WAL 大小 | 正常会 checkpoint；异常增大时执行 `PRAGMA wal_checkpoint(TRUNCATE)` |
| 日志 | 10 MB × 14 天轮转；如需长期保留请外送 |
| 备份 | 至少每日一次，保留 14 天；重要演出前手动备份 |
| NapCat 缓存 | 定期检查 `deploy/napcat` 大小，清理旧图片 / 文件缓存 |

手动 `VACUUM`（停机器人后执行）：

```bash
docker compose stop bot
docker compose run --rm bot python -c "import sqlite3;db=sqlite3.connect('/data/rehearsal.db');db.execute('VACUUM');db.close()"
docker compose start bot
```

---

## 10. 应急流程

### 10.1 机器人异常但马上要发通知

1. 先人工在群内发布通知；
2. 修复机器人后补发 `#发布排练`（或将人工通知的关键信息补录）；
3. 检查提醒任务是否创建；
4. 在 `#审计日志` 中确认操作记录。

### 10.2 数据库损坏

1. 停止 bot；
2. 尝试 `PRAGMA integrity_check`；
3. 如损坏，恢复最近备份；
4. 无备份时：保留损坏文件，使用 `sqlite3 .recover` 尝试恢复；
5. 恢复后验证 `#排练列表`、`#审计日志`、提醒任务。

### 10.3 误删 / 误取消

- 软删除 / 取消的通知仍在数据库，可请管理员用 SQL 或后续工具恢复；
- 当前版本没有“恢复”命令，必要时用备份恢复到新文件后对比；
- 彻底删除无法恢复。

---

## 11. 相关文档

- [DEPLOY-DOCKER.md](DEPLOY-DOCKER.md) / [DEPLOY-LOCAL.md](DEPLOY-LOCAL.md)
- [CONFIGURATION.md](CONFIGURATION.md)
- [ARCHITECTURE.md](ARCHITECTURE.md)
- [COMMANDS.md](COMMANDS.md#16-常见错误提示)
