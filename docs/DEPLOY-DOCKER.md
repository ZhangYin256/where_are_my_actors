# Docker Compose 部署指南

面向长期在服务器 / 常开电脑上运行的推荐部署方式。整套系统由两个容器组成：

- `napcat`：登录机器人 QQ，提供 OneBot V11 WebSocket；
- `bot`：NoneBot2 应用，处理命令、SQLite 数据与定时提醒。

---

## 1. 前置条件

| 项目 | 要求 |
|---|---|
| Docker | Docker Engine 24+ / Docker Desktop 4.x |
| Docker Compose | v2（`docker compose` 子命令） |
| 操作系统 | Linux（推荐 Ubuntu 22.04+ / Debian 12+）、Windows 10/11、macOS 13+ |
| 内存 | 建议 ≥ 1 GB 可用 |
| 磁盘 | 建议 ≥ 2 GB（QQ 会话 + 数据 + 日志） |
| QQ | 一个专用机器人 QQ 号（不建议使用主账号） |
| 网络 | 服务器可访问 QQ 网络；部署时需能访问 `6099` 端口完成扫码 |

---

## 2. 获取代码与目录

```bash
git clone <本仓库地址> where_are_my_actors
cd where_are_my_actors
```

创建持久化目录（Linux/macOS）：

```bash
mkdir -p data logs deploy/napcat/config deploy/napcat/qq
```

Windows PowerShell：

```powershell
New-Item -ItemType Directory -Force data, logs, deploy/napcat/config, deploy/napcat/qq | Out-Null
```

目录用途：

```text
data/                    SQLite 数据库、心跳、备份
logs/                    运行日志
deploy/napcat/config/    NapCat 配置
deploy/napcat/qq/        QQ 登录会话（务必保留，避免反复扫码）
```

---

## 3. 配置 `.env`

```bash
cp .env.example .env
```

至少修改：

```dotenv
# 你的管理员 QQ（用于授权、解绑、全局配置）
REHEARSAL_ADMINS=["10000001"]

# 与 NapCat 网络配置中的 token 保持一致，建议 16 位以上随机串
ONEBOT_ACCESS_TOKEN=please-change-me-to-a-long-random-token

# Docker 内部心跳路径（compose 已注入，保留即可）
REHEARSAL_HEARTBEAT_PATH=/data/heartbeat
```

完整变量说明见 [CONFIGURATION.md](CONFIGURATION.md)。

> `.env` 含敏感信息，不要提交到 Git，也不要发到群里。

---

## 4. 启动 NapCat 并登录 QQ

```bash
docker compose up -d napcat
docker compose logs -f napcat
```

首次启动后：

1. **扫码登录**：日志中会出现二维码 / 登录提示。用机器人 QQ 手机端扫码确认；部分版本可在 WebUI 中查看二维码。
2. **打开 WebUI**：浏览器访问 `http://<服务器IP>:6099/webui`。
   - 登录 token 通常打印在 `docker compose logs napcat` 中；
   - 若端口被占用，修改 `docker-compose.yml` 中的 `6099:6099` 映射。
3. 登录成功后，QQ 会话保存在 `deploy/napcat/qq/`，后续重启一般无需再次扫码。

### 配置 OneBot WebSocket 服务

在 NapCat WebUI 中进入「网络配置」→ 新建 / 编辑 **WebSocket 服务器**（正向 WS）：

| 配置项 | 值 |
|---|---|
| 名称 | `rehearsal-bot` |
| 启用 | 是 |
| Host | `0.0.0.0` |
| 端口 | `3001` |
| Token | 与 `.env` 的 `ONEBOT_ACCESS_TOKEN` 完全一致 |
| 消息格式 | `array` |
| 上报自身消息 | 否 |

保存并启用。容器网络内地址为 `ws://napcat:3001`，`docker-compose.yml` 已通过环境变量注入。

> NapCat 不同版本的配置界面与字段可能略有差异，核心是「开启 OneBot V11 正向 WebSocket 服务器，端口 3001，token 一致」。
> 参考：[NapCatQQ 官方仓库](https://github.com/NapNeko/NapCatQQ)。
> 配置示例（仅结构参考）：[`deploy/napcat/onebot11.example.json`](../deploy/napcat/onebot11.example.json)。

### 验证 NapCat

```bash
docker compose logs --tail=100 napcat
```

应能看到 WebSocket 服务已启动、QQ 已登录。此时先不要关闭日志窗口，等机器人连上后再确认。

---

## 5. 启动机器人

```bash
docker compose up -d bot
docker compose ps
docker compose logs -f bot
```

首次构建会拉取 Python 镜像并安装依赖，通常 1-3 分钟。

预期日志：

```text
Succeeded to load plugin "rehearsal_bot" ...
QQ 账号已连接：<机器人QQ>
排练通知机器人已启动：数据库 /data/rehearsal.db，提醒间隔 10s
```

检查健康状态：

```bash
docker inspect --format '{{.State.Health.Status}}' rehearsal-bot
# healthy
```

或：

```bash
docker compose exec -T bot python scripts/healthcheck.py
```

健康检查包含：

- SQLite 数据库可读且 `quick_check` 通过；
- 提醒循环心跳在 180 秒内更新。

> 启动后前 60 秒处于 `starting` 状态属于正常（`start-period`）。

---

## 6. 首次使用

1. 把机器人 QQ 拉进目标群；
2. 如需真正 `@全体成员`，将机器人设为**群管理员**（非管理员会自动降级逐个 @）；
3. 在群内发送：

```text
#初始化剧组 某某剧组
```

4. 群主自动成为导演；管理员可用 `#授权副导演 @张三` 添加协作者；
5. 发送第一条通知：

```text
#发布排练
时间=周五7~10
地点=一排
人员=@张三 @李四
```

6. 按预览提示发送 `#确认发布 P001`。

完整使用流程见 [USAGE.md](USAGE.md)。

---

## 7. 日常运维命令

```bash
# 查看状态
docker compose ps

# 查看日志（应用）
docker compose logs -f --tail=200 bot
docker compose logs -f --tail=200 napcat

# 重启
docker compose restart bot

# 停止（保留数据）
docker compose down

# 重新构建应用（修改代码后）
docker compose up -d --build bot

# 进入容器排查
docker compose exec bot sh
docker compose exec bot python -c "import sqlite3;print(sqlite3.connect('/data/rehearsal.db').execute('select count(*) from rehearsal').fetchone())"
```

日志也会写入 `logs/nonebot.log`（按 10 MB 轮转，保留 14 天）。

---

## 8. 升级

```bash
# 1. 先备份
docker compose exec -T bot python scripts/backup_db.py --keep 14

# 2. 拉取新代码
git pull

# 3. 重建并启动
docker compose build bot
docker compose up -d bot

# 4. 验证
docker compose logs -f --tail=100 bot
docker compose exec -T bot python scripts/healthcheck.py
```

- 数据库结构通过 `PRAGMA user_version` 管理，启动时自动完成兼容初始化；
- 升级前建议查看 [CHANGELOG.md](CHANGELOG.md)；
- 若需回滚：`git checkout <上一个版本>`，`docker compose build bot && docker compose up -d bot`，必要时用备份恢复数据库。

---

## 9. 网络、端口与安全

| 端口 | 用途 | 是否对公网开放 |
|---|---|---|
| `6099` | NapCat WebUI（扫码、网络配置） | 仅部署 / 维护时开放，建议用完关闭或加防火墙白名单 |
| `3001` | OneBot WebSocket | **不开放**，只在 Compose 内部网络使用 |

建议：

- `ONEBOT_ACCESS_TOKEN` 使用长随机串，不要使用默认值；
- WebUI 使用强密码 / token，并限制来源 IP；
- 服务器防火墙只放行必要端口；
- 定期备份 `data/` 与 `deploy/napcat/qq/`；
- 机器人使用专用 QQ，控制发送频率（默认 1 秒间隔）；
- `.env`、NapCat 配置与 QQ 会话目录权限设为仅服务账号可读。

---

## 10. 常见问题

### 机器人日志显示未连接 / 反复重连

```bash
docker compose logs --tail=100 napcat
docker compose logs --tail=100 bot
```

逐项检查：

1. NapCat 是否已登录 QQ；
2. WebSocket 服务器是否已启用，端口是否 3001；
3. `.env` 的 `ONEBOT_ACCESS_TOKEN` 与 NapCat 是否一致（注意空格 / 引号）；
4. `docker compose exec bot python -c "import socket;print(socket.gethostbyname('napcat'))"` 是否能解析服务名；
5. 是否误改了 `docker-compose.yml` 中的 `ONEBOT_WS_URLS`。

### 机器人日志持续显示 `Error while setup websocket ...`

这是 NoneBot 在 NapCat 未启动 / 未登录时的正常重连提示。先按上一节检查 NapCat 与 token；连接成功后会自动停止报错并输出 `QQ 账号已连接`。

### bot 容器反复重启，日志显示 `FileNotFoundError: pyproject.toml`

v1.0.1 已修复：`bot.py` 不再依赖运行时的 `pyproject.toml`，改为显式从 `plugins/` 加载插件；镜像也会复制该文件。

如果你是旧镜像，请重新构建：

```bash
docker compose build --no-cache bot
docker compose up -d bot
docker compose logs -f --tail=100 bot
```

确认镜像内文件：

```bash
docker compose exec bot ls -l /app/pyproject.toml /app/plugins/rehearsal_bot/__init__.py
```

### 扫码后立即掉线

- 检查服务器时间是否准确（误差过大会导致登录失败）；
- 查看 NapCat 日志中的风控提示；
- 删除 `deploy/napcat/qq/` 后重新扫码（会丢失登录会话，谨慎）；
- 避免频繁重启 / 多设备同时登录同一 QQ。

### 健康检查 unhealthy

```bash
docker compose exec -T bot python scripts/healthcheck.py
```

- “数据库不存在”：检查 `data/` 挂载与 `REHEARSAL_DB_PATH`；
- “心跳文件不存在 / 超时”：提醒循环未启动，查看 bot 日志是否异常退出；
- “数据库完整性检查失败”：停止机器人，用备份恢复（见 [OPERATIONS.md](OPERATIONS.md#3-备份与恢复)）。

### 端口 6099 被占用

修改 `docker-compose.yml`：

```yaml
ports:
  - "16099:6099"
```

然后访问 `http://<IP>:16099/webui`。

### Windows / macOS 文件权限

Docker Desktop 需要允许共享项目所在磁盘；Linux 下如遇权限问题：

```bash
sudo chown -R "$(id -u):$(id -g)" data logs deploy/napcat
```

`docker-compose.yml` 中 `NAPCAT_UID` / `NAPCAT_GID` 默认 1000，可按实际用户修改。

---

## 11. 备份与监控

- 备份：`docker compose exec -T bot python scripts/backup_db.py --keep 14`
- 定时备份、恢复、监控 SQL 见 [OPERATIONS.md](OPERATIONS.md)
- 配置项说明见 [CONFIGURATION.md](CONFIGURATION.md)
- 架构与可靠性设计见 [ARCHITECTURE.md](ARCHITECTURE.md)
