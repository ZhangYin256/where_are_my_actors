# 排练通知 QQ Bot

基于 **NoneBot2 + NapCat + SQLite** 的 QQ 群排练通知机器人：统一发布排练通知、解析模糊时间、管理多剧组权限、在排练开始前自动提醒，并支持群内 / 私聊查询个人排练安排。

- 版本：**1.1.0**（对应 `docs/SPEC.md` v1.0）
- 运行平台：Windows 10/11、Ubuntu 22.04+、Debian 12+、macOS 13+
- 测试：**195 个自动化测试全部通过**，覆盖时间解析、权限、发布、修改、提醒、查询与模拟器
- 部署：Docker Compose 优先，也支持本地进程

---

## 功能一览

| 能力 | 说明 |
|---|---|
| 发布排练通知 | 固定标题 `#排练通知`；默认无问题时直接发送，有风险时预览确认，可配置为总是预览 |
| 字段解析 | `时间/地点/人员/内容/备注/提醒/通知范围`，分隔符 `=`、`：`、`:`，内容支持多行 |
| 模糊时间 | `周五7~10`、`周一晚8:30~10:00`、`明天下午2点~4点`、`10.21`、跨天、跨周、跨年提示 |
| 人员 | @群成员、文本人员、备注标签、`@全体成员` 与降级逐个 @ |
| 修改 / 重发 | 修改先预览、保存后不自动重发；手动 `#排练重发` 再确认发送 |
| 取消 / 删除 | 取消时可选择是否发布取消通知；默认软删除，管理员可彻底删除 |
| 提醒 | 默认提前 30 分钟，支持多个提醒、关闭提醒、私聊提醒、失败重试、重启恢复、不重复发送 |
| 多剧组 | 一个群一个剧组，一个 QQ 可跨剧组担任不同角色；`#我的排练` 聚合展示 |
| 权限 | 管理员 / 导演 / 副导演 / 演员四级；群主、群管理员自动成为导演 |
| 审计 | 授权、演员、发布、修改、取消、删除、配置、提醒结果、解析失败等全记录 |
| 离线模拟 | 不装 QQ、不连 NapCat，用模拟器完整跑通全部业务流程 |

---

## 2 分钟快速体验（无需 QQ）

```bash
# 1. 安装依赖（推荐 uv；见下方 pip 方式）
uv sync

# 2. 启动离线模拟器
uv run python -m rehearsal.simulator --reset
```

进入后输入：

```text
#发布排练
时间=周五7~10
地点=一排
人员=@王五 @李四 制作组
内容=
1. 福临门开场和声
2. 一念一宇宙
备注=其他演员可以来自由练习
```

再发送 `#确认发布 P001`（示例中包含无法匹配的“制作组”，因此会先预览），即可看到机器人正式发送的 `#排练通知`。用 `/help` 查看模拟器指令，用 `/as 王五`、`/private`、`/advance 6d`、`/tick` 体验私聊查询与定时提醒。

完整对话见 **[docs/DEMO-TRANSCRIPT.md](docs/DEMO-TRANSCRIPT.md)**（由 `python scripts/demo.py` 自动生成）。

没有 uv 时：

```bash
python -m venv .venv
# Windows
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m rehearsal.simulator --reset
# macOS / Linux
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m rehearsal.simulator --reset
```

---

## 生产部署（Docker Compose）

```bash
git clone https://github.com/ZhangYin256/where_are_my_actors where_are_my_actors
cd where_are_my_actors

# 1. 准备配置
cp .env.example .env
#   必改：REHEARSAL_ADMINS（你的 QQ 号）、ONEBOT_ACCESS_TOKEN（自定义强口令）

# 2. 创建持久化目录
mkdir -p data logs deploy/napcat/config deploy/napcat/qq

# 3. 启动 NapCat，扫码登录机器人 QQ
docker compose up -d napcat
docker compose logs -f napcat

# 4. 打开 NapCat WebUI（默认 http://<服务器IP>:6099/webui），
#    在「网络配置」新增 WebSocket 服务器：端口 3001、token 与 .env 一致

# 5. 启动机器人
docker compose up -d bot
docker compose ps
docker compose logs -f bot
```

在目标 QQ 群内发送 `#初始化剧组 某某剧组`，然后拉入演员、设置机器人为群管理员（如需真正 @全体成员），即可开始使用。

详细步骤、NapCat 配置、升级与排障见 **[docs/DEPLOY-DOCKER.md](docs/DEPLOY-DOCKER.md)**；本地运行见 **[docs/DEPLOY-LOCAL.md](docs/DEPLOY-LOCAL.md)**。

---

## 常用命令速查

| 场景 | 命令 |
|---|---|
| 发布 | `#发布排练` + `时间=周五7~10` 等字段；无风险时直接发送，出现预览时按提示确认 |
| 查看 | `#排练列表`、`#排练列表 本周`、`#排练历史`、`#排练详情 3` |
| 我的安排 | 私聊 `#我的排练` / `#我的排练 本周` / `#我的排练 历史` |
| 修改 | `#排练修改 3 地点=二排` → `#确认修改 M001` |
| 重发 | `#排练重发 3` → `#确认重发 R001` |
| 取消 | `#取消排练 3` → `#确认取消 3 通知=是` |
| 删除 | `#删除排练 3` → `#确认删除 3` |
| 角色 | `#授权副导演 @张三`、`#授权导演 @张三`、`#授权管理员 QQ号` |
| 演员管理 | `#添加演员 @张三`、`#演员列表`、`#演员标签 @张三 湖区` |
| 配置 | `#配置查看`、`#配置 默认提醒=60,30`、`#提醒默认 30` |
| 审计 | `#审计日志 20` |
| 求助 | `#帮助`、`#帮助发布排练`、`#帮助提醒配置` |

完整命令、字段语法与权限矩阵见 **[docs/COMMANDS.md](docs/COMMANDS.md)**；上手教程见 **[docs/USAGE.md](docs/USAGE.md)**。

---

## 项目结构

```text
where_are_my_actors/
├── bot.py                     # NoneBot2 启动入口
├── pyproject.toml             # 依赖、pytest、ruff、NoneBot 配置
├── uv.lock / requirements.txt # 可复现依赖
├── Dockerfile                 # 应用镜像（Python 3.12 + 锁定依赖）
├── docker-compose.yml         # NapCat + Bot
├── .env.example               # 环境变量模板
├── rehearsal/                 # 与平台无关的核心库（可单测 / 可模拟）
│   ├── timeparse.py           # 中文模糊时间解析
│   ├── fields.py              # 字段、人员、提醒、标签解析
│   ├── render.py              # 通知 / 预览 / 提醒 / 列表渲染
│   ├── db.py                  # SQLite schema 与 Repository
│   ├── service.py             # 命令分发、权限、排练生命周期、提醒发送
│   ├── worker.py              # 提醒循环与心跳
│   ├── permissions.py         # 角色与能力矩阵
│   ├── onebot.py              # OneBot 消息转换
│   ├── fake.py                # 内存网关（测试 / 模拟器）
│   └── simulator.py           # 离线模拟器 CLI
├── plugins/rehearsal_bot/     # NoneBot2 适配插件
├── tests/                     # 195 个 pytest 测试
├── scripts/                   # 备份、健康检查、演示生成
├── examples/                  # 演示脚本
├── deploy/napcat/             # NapCat 配置示例
└── docs/                      # 规格、部署、使用、运维、架构、验收文档
```

---

## 文档导航

| 文档 | 内容 |
|---|---|
| [docs/COMMANDS.md](docs/COMMANDS.md) | 全量命令、字段语法、权限矩阵、错误提示 |
| [docs/USAGE.md](docs/USAGE.md) | 从初始化到日常使用的完整教程与最佳实践 |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | 环境变量、剧组配置、全局运行参数 |
| [docs/DEPLOY-DOCKER.md](docs/DEPLOY-DOCKER.md) | Docker Compose + NapCat 生产部署、升级、排障 |
| [docs/DEPLOY-LOCAL.md](docs/DEPLOY-LOCAL.md) | Windows / Ubuntu / macOS 本地部署与开机自启 |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | 日志、健康检查、备份恢复、监控、故障处理、安全 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 架构、模块、数据模型、状态机、提醒可靠性设计 |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | 开发环境、测试、扩展命令 / 时间格式、发版流程 |
| [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md) | 规格 FR/验收条目 → 代码 / 测试对照表 |
| [docs/DEMO-TRANSCRIPT.md](docs/DEMO-TRANSCRIPT.md) | 模拟器完整演示对话 |
| [docs/SPEC.md](docs/SPEC.md) | 原始需求规格说明书 |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | 版本变更记录 |

---

## 开发与测试

```bash
uv sync
uv run pytest                 # 195 passed
uv run ruff check rehearsal plugins tests bot.py
uv run python -m rehearsal.simulator --reset
uv run python scripts/demo.py # 重新生成 docs/DEMO-TRANSCRIPT.md
```

- 核心业务不依赖 NoneBot，可用 `FakeGateway` 直接构造场景；
- 时间解析、权限、提醒幂等、重启恢复均有独立测试；
- 开发约定与扩展方式见 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)。

---

## 设计要点

1. **智能发布确认**：默认无问题时 `#发布排练` 直接发送；存在时间顺延、文本人员、开始已过、错过提醒、@全体降级等风险时强制预览确认；可用 `#配置 发布确认=开` 改为总是预览。修改、重发仍必须预览确认。
2. **提醒持久化**：提醒任务写入 SQLite，进程重启后自动恢复；发送前原子抢占，正常运行时不会重复发送。
3. **时间解析显式提示**：跨周、跨年、跨天、开始时间已过、提醒已错过，都会在预览中明确说明。
4. **权限分离**：管理员全局、导演 / 副导演按剧组；群主 / 群管理员身份自动同步且与手动授权分开记录。
5. **@全体成员降级**：机器人不是群管理员时自动降级为逐个 @，可配置目标范围并分段发送。
6. **审计可追溯**：关键操作、提醒成败、解析失败全部落库，支持 `#审计日志` 查询。

---

## 已知边界

- 个人 QQ 协议存在风控 / 封号风险，请使用专用账号并控制发送频率（见规格第 13 章）。
- 私聊提醒要求机器人能向该成员发起私聊（通常需对方为好友或群临时会话可用），失败会记录审计日志。
- 人员字段中的 `@全体成员` 只发送群提醒，不逐个私聊（避免大规模私聊风控）。
- 提醒采用“至多一次”策略：进程在发送过程中崩溃时，该次提醒标记失败而不是重发，以确保不重复打扰。
- 不包含请假审批、签到、冲突检测、日历订阅、Web 管理后台等规格明确排除的功能。

---

## License

MIT，见 [LICENSE](LICENSE)。
