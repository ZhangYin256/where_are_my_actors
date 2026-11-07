# 本地部署指南（Windows / Ubuntu / macOS）

适合个人常开电脑或不想使用 Docker 的场景。本文以“本地运行 NapCat + 本地运行机器人”为例；也可以只用 Docker 跑 NapCat、本机跑机器人（见文末）。

---

## 1. 前置条件

| 项目 | 要求 |
|---|---|
| Python | **3.11 或 3.12**（3.13 未验证） |
| 包管理 | 推荐 [uv](https://docs.astral.sh/uv/)；也可用 pip |
| 操作系统 | Windows 10/11、Ubuntu 22.04+、Debian 12+、macOS 13+ |
| NapCat | 能登录机器人 QQ，并提供 OneBot V11 正向 WebSocket |
| 网络 | 能访问 QQ 网络与 PyPI（首次安装依赖） |

---

## 2. 安装 Python 与依赖

### 2.1 安装 uv（推荐）

Windows PowerShell：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

macOS / Linux：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

验证：

```bash
uv --version
```

### 2.2 获取代码并安装依赖

```bash
git clone <本仓库地址> where_are_my_actors
cd where_are_my_actors
uv sync
```

没有 uv 时使用 Python 自带的 venv + pip：

```bash
python -m venv .venv

# Windows
.venv\Scripts\python.exe -m pip install -r requirements.txt

# macOS / Linux
.venv/bin/python -m pip install -r requirements.txt
```

---

## 3. 安装并登录 NapCat

NapCat 支持 Windows / Linux / macOS，也提供 Docker 镜像。请从官方仓库获取对应平台版本：

- 官方仓库：<https://github.com/NapNeko/NapCatQQ>
- 首次启动后使用机器人 QQ 扫码登录；
- 登录后进入 NapCat WebUI（默认 `http://127.0.0.1:6099/webui`），在「网络配置」新增 **WebSocket 服务器**：

| 配置项 | 值 |
|---|---|
| Host | `127.0.0.1` |
| 端口 | `3001` |
| Token | 自定义强口令，与 `.env` 一致 |
| 消息格式 | `array` |
| 上报自身消息 | 否 |

> 如果 NapCat 与机器人不在同一台机器，Host 改为 `0.0.0.0`，并注意防火墙与 token 安全。
> 不同 NapCat 版本界面略有差异，核心是启用 OneBot V11 正向 WebSocket。

---

## 4. 配置机器人

```bash
cp .env.example .env
```

本地 NapCat 的关键配置：

```dotenv
DRIVER=~websockets
ONEBOT_WS_URLS=["ws://127.0.0.1:3001"]
ONEBOT_ACCESS_TOKEN=与NapCat一致的token

REHEARSAL_ADMINS=["10000001"]
REHEARSAL_DB_PATH=data/rehearsal.db
REHEARSAL_HEARTBEAT_PATH=data/heartbeat
REHEARSAL_TIMEZONE=Asia/Shanghai
```

Windows 记事本保存时请选择 **UTF-8**，避免中文乱码。

---

## 5. 启动

```bash
# 使用 uv
uv run python bot.py

# 或直接使用虚拟环境
# Windows
.venv\Scripts\python.exe bot.py
# macOS / Linux
.venv/bin/python bot.py
```

看到以下日志说明启动成功：

```text
Succeeded to load plugin "rehearsal_bot" ...
QQ 账号已连接：<机器人QQ>
排练通知机器人已启动：数据库 data/rehearsal.db，提醒间隔 10s
```

另开一个终端验证健康状态：

```bash
# Windows
.venv\Scripts\python.exe scripts\healthcheck.py
# macOS / Linux
.venv/bin/python scripts/healthcheck.py
```

> 首次启动时 `data/heartbeat` 需要等提醒循环跑一轮（最多 10 秒）。

---

## 6. 设置开机自启 / 守护进程

### 6.1 Linux（systemd）

创建用户与目录（示例）：

```bash
sudo useradd -m -s /bin/bash rehearsal
sudo mkdir -p /opt/where_are_my_actors
sudo chown -R rehearsal:rehearsal /opt/where_are_my_actors
```

把项目放到 `/opt/where_are_my_actors`，安装依赖后创建 `/etc/systemd/system/rehearsal-bot.service`：

```ini
[Unit]
Description=Rehearsal QQ Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=rehearsal
WorkingDirectory=/opt/where_are_my_actors
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/where_are_my_actors/.venv/bin/python bot.py
Restart=always
RestartSec=5
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now rehearsal-bot
sudo systemctl status rehearsal-bot
sudo journalctl -u rehearsal-bot -f
```

NapCat 若也以 systemd 运行，请单独为其创建服务，并确保其先于机器人启动（可在 `[Unit]` 中 `After=`）。

### 6.2 Windows（任务计划程序）

以当前用户创建开机任务（路径按实际修改）：

```powershell
$root = "D:\Codes\where_are_my_actors"
$action = New-ScheduledTaskAction `
  -Execute "$root\.venv\Scripts\python.exe" `
  -Argument "bot.py" `
  -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -RestartCount 3 `
  -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask `
  -TaskName "RehearsalBot" `
  -Action $action `
  -Trigger $trigger `
  -Settings $settings `
  -RunLevel Highest
```

也可使用 [NSSM](https://nssm.cc/) 把 `python.exe bot.py` 注册为 Windows 服务：

```powershell
nssm install RehearsalBot "D:\Codes\where_are_my_actors\.venv\Scripts\python.exe" "bot.py"
nssm set RehearsalBot AppDirectory "D:\Codes\where_are_my_actors"
nssm start RehearsalBot
```

### 6.3 macOS（launchd）

创建 `~/Library/LaunchAgents/com.example.rehearsalbot.plist`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.example.rehearsalbot</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/you/where_are_my_actors/.venv/bin/python</string>
    <string>bot.py</string>
  </array>
  <key>WorkingDirectory</key><string>/Users/you/where_are_my_actors</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/Users/you/where_are_my_actors/logs/stdout.log</string>
  <key>StandardErrorPath</key><string>/Users/you/where_are_my_actors/logs/stderr.log</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.example.rehearsalbot.plist
```

---

## 7. 只把 NapCat 放进 Docker（可选）

如果不想在本机安装 NapCat，可以只运行 NapCat 容器，并让本机机器人连接它：

1. 修改 `docker-compose.yml`，给 napcat 增加端口映射：

```yaml
ports:
  - "127.0.0.1:3001:3001"
  - "6099:6099"
```

2. 启动 NapCat：

```bash
docker compose up -d napcat
```

3. `.env` 中使用：

```dotenv
ONEBOT_WS_URLS=["ws://127.0.0.1:3001"]
```

4. 本机运行 `python bot.py`。

---

## 8. 升级

```bash
git pull
uv sync                 # 或 pip install -r requirements.txt
# 重启机器人进程 / 服务
```

升级前先备份：

```bash
python scripts/backup_db.py --keep 14
```

---

## 9. 常见问题

| 现象 | 排查 |
|---|---|
| `NoneBot has not been initialized` | 不要直接 import 插件；用 `python bot.py` 启动 |
| 连不上 NapCat | 检查 WS 端口、token、`ONEBOT_WS_URLS`、防火墙 |
| 扫码后频繁掉线 | 校对系统时间；不要多处登录；查看 NapCat 风控日志 |
| 中文乱码 | `.env`、终端、数据库均使用 UTF-8 |
| 提醒不触发 | 检查 `data/heartbeat` 是否更新、`docker compose logs` / 控制台是否有异常、服务器时区 |
| 端口被占用 | 修改 NapCat WS / WebUI 端口，并同步 `.env` |
| 权限不足 | 用 `REHEARSAL_ADMINS` 配置的 QQ 操作，或在群内授权 |

更多运维与排障见 [OPERATIONS.md](OPERATIONS.md)。
