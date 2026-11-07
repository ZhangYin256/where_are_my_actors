# 开发指南

面向二次开发与维护者。部署与使用见 [DEPLOY-DOCKER.md](DEPLOY-DOCKER.md) / [USAGE.md](USAGE.md)。

---

## 1. 环境准备

```bash
git clone <本仓库地址> where_are_my_actors
cd where_are_my_actors
uv sync
```

`uv sync` 会：

- 创建 `.venv`；
- 按 `uv.lock` 安装运行与开发依赖；
- 不把项目本身打包（`[tool.uv] package = false`），源码直接运行。

没有 uv：

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt   # Windows
.venv/bin/python -m pip install -r requirements.txt       # macOS/Linux
.venv\Scripts\python -m pip install pytest pytest-asyncio ruff
```

运行测试与检查：

```bash
uv run pytest
uv run ruff check rehearsal plugins tests bot.py
```

---

## 2. 项目结构

```text
rehearsal/                 核心库（不依赖 NoneBot）
├── timeparse.py           中文时间解析
├── fields.py              字段 / 人员 / 提醒解析
├── render.py              消息渲染
├── db.py                  schema + Repository
├── service.py             命令分发与业务逻辑
├── permissions.py         角色与能力
├── worker.py              提醒循环
├── models.py              实体与消息段
├── onebot.py              OneBot 消息互转
├── fake.py                内存网关
└── simulator.py           离线模拟器

plugins/rehearsal_bot/     NoneBot2 适配层（事件 → 核心 → 发送）
tests/                     pytest（185 个）
scripts/                   备份 / 健康检查 / 演示生成
docs/                      文档
```

分层原则：

1. **核心层不 import NoneBot**（`onebot.py` 除外，仅为消息转换）；
2. 核心层通过 `Gateway` 协议访问平台能力；
3. 插件层只做协议转换、生命周期、发送；
4. 所有业务逻辑都可在 `FakeGateway` 下测试。

---

## 3. 测试

### 3.1 运行

```bash
uv run pytest                       # 全部
uv run pytest tests/test_timeparse.py -q
uv run pytest -k "reminder and not retry" -v
```

`pytest` 配置在 `pyproject.toml`：

```toml
asyncio_mode = "auto"
```

异步测试无需手动加 `@pytest.mark.asyncio`。

### 3.2 测试夹具

`tests/conftest.py` 提供 `app` 夹具：

```python
async def test_example(app):
    text = await app.text("#帮助")
    assert "【帮助】" in text

    preview = await app.text("#发布排练\n时间=明天7~9\n人员=@王五")
    assert "P001" in preview

    await app.text("#确认发布 P001")
    assert app.gateway.messages_to("100001")[0].plain_text().startswith("#排练通知")
```

`app` 内置：

| 属性 | 说明 |
|---|---|
| `app.repo` | 临时 SQLite Repository |
| `app.gateway` | FakeGateway（记录发送内容） |
| `app.clock` | 固定时钟（2026-11-07 13:00 周六），可 `advance()` / `set()` |
| `app.troupe_a` / `app.troupe_b` | 两个剧组 |
| `app.send(...)` | 发送命令，返回 `Reply` 列表 |
| `app.text(...)` | 发送命令并拼接回复文本 |

默认世界：群 `100001`（剧组A）、群 `100002`（剧组B），成员 10001 张三（群主）、10002 李四（群管理）、10003 王五、10004 赵六、10005 钱七；10001 是全局管理员。

### 3.3 测试分布

| 文件 | 覆盖 |
|---|---|
| `test_timeparse.py` | 时间解析与格式化（35 例） |
| `test_fields.py` | 字段、人员、标签、提醒、范围 |
| `test_publish.py` | 发布预览、确认、空值、@all、私聊发布 |
| `test_lifecycle.py` | 修改、重发、取消、删除、彻底删除 |
| `test_reminders.py` | 触发、私聊、降级、分段、重试、重启、幂等 |
| `test_queries.py` | 列表、历史、详情、我的排练、隐私 |
| `test_permissions.py` | 权限矩阵、自动导演、授权、解绑、初始化 |
| `test_admin.py` | 演员、配置、全局配置、审计、帮助 |
| `test_message.py` | 长消息拆分、OneBot 转换 |
| `test_simulator.py` | 模拟器端到端 |
| `test_worker.py` | 提醒循环、心跳、异常恢复 |
| `test_db.py` | WAL、备份、编号、唯一约束 |

---

## 4. 新增一个命令

以新增 `#排练统计` 为例。

### 4.1 在 `service._dispatch` 注册

```python
if cmd == "排练统计":
    return await self._cmd_stats(ctx, troupe)
```

### 4.2 实现处理函数

```python
async def _cmd_stats(self, ctx: CommandContext, troupe: Troupe) -> list[Reply]:
    self._require(troupe, ctx.qq, "view")
    rows = self.repo.list_rehearsals(troupe.id, statuses=("published",))
    return [self._reply(ctx, f"未来排练：{len(rows)} 条")]
```

约定：

- 用户可见错误抛 `UserError` 子类（`PermissionDenied` / `NotFound` / `StateError` / `ConfigError`）；
- 管理操作先 `_require`；
- 写操作后 `repo.add_audit(...)`；
- 返回 `[self._reply(ctx, ...)]`，不要直接调用平台 API（提醒发送除外）。

### 4.3 渲染与帮助

- 结构化输出放到 `render.py`，纯函数、可单测；
- 在 `help.py` 补充命令说明；
- 更新 `docs/COMMANDS.md`。

### 4.4 测试

在 `tests/` 增加：

```python
async def test_stats(app):
    text = await app.text("#排练统计")
    assert "未来排练" in text
```

需要权限 / 时间 / 提醒行为时，复用 `app` 与 `FakeGateway`。

---

## 5. 新增时间格式

1. 在 `timeparse.normalize()` 增加归一化规则；
2. 在 `_YMD_RE` / `_MD_RE` / `_WEEK_RE` / `_REL_RE` / `_RANGE_RE` 增加匹配；
3. 在 `parse_time()` 的日期 / 小时解析中处理；
4. 所有自动调整写入 `notes`，不要静默改变；
5. 在 `tests/test_timeparse.py` 增加正例、反例与跨周 / 跨年 / 跨天用例。

设计约束：

- 永远返回带时区的完整 `datetime`；
- 不猜测年份以外的语义；
- 结束早于开始必须按跨天处理并提示；
- 开始时间已过时，按规格区分「无日期 → 明天」「周几 → 下一周」「显式日期 → 保留」；
- 预览必须展示解析结果。

---

## 6. 数据库变更

当前 `SCHEMA_VERSION = 1`，`db.py` 的 `_create_schema` 使用 `CREATE TABLE IF NOT EXISTS`，并按 `PRAGMA user_version` 校验。

新增字段 / 表时：

1. 修改 `SCHEMA_SQL`（新库直接创建）；
2. 提升 `SCHEMA_VERSION`；
3. 在 `_create_schema` 中读取旧版本并执行 `ALTER TABLE` / 数据迁移；
4. 补充 `test_db.py` 的迁移测试；
5. 在 `CHANGELOG.md` 与 `OPERATIONS.md` 升级章节注明。

迁移原则：

- 只增不删，避免旧数据不可读；
- 大表迁移放在事务中；
- 失败时回滚并保留备份；
- 升级前先运行 `scripts/backup_db.py`。

---

## 7. 提醒相关开发注意事项

- 任何发送都必须经过 `Gateway`，不要在核心层直接调用 OneBot；
- 发送前必须 `claim_job`，保持「先抢占后发送」；
- 修改时间 / 提醒后必须 `reset_jobs` + `create_jobs`，不要只改内存；
- 已发送任务不要删除 / 重置，避免重复；
- 新增提醒类型时，考虑 `grace_seconds`、`max_attempts`、审计与重启恢复；
- 长消息 / 批量 @ 使用 `split_message` 与分段限速。

---

## 8. 调试技巧

### 8.1 模拟器

```bash
uv run python -m rehearsal.simulator --reset
uv run python -m rehearsal.simulator --now 2026-11-07T13:00:00
uv run python -m rehearsal.simulator --script examples/demo-script.txt --reset
```

模拟器支持：

```text
/as 王五        切换用户
/group 100001   切换群
/private        切换私聊
/advance 30m    推进时间
/tick           立即执行提醒检查
/now            查看模拟时间
```

### 8.2 日志

```bash
LOG_LEVEL=DEBUG uv run python bot.py
```

核心层使用标准 `logging`，插件启动时桥接到 NoneBot 的 loguru。

### 8.3 直接调用服务

```python
import asyncio
from rehearsal.config import Settings
from rehearsal.db import Database, Repository
from rehearsal.fake import FakeGateway
from rehearsal.models import Member
from rehearsal.service import RehearsalService, CommandContext

db = Database(":memory:")
repo = Repository(db)
gw = FakeGateway({"100001": [Member("1", "张三", role="owner")]})
svc = RehearsalService(repo, gw, Settings(admins=("1",), send_interval=0))
asyncio.run(svc.startup())
ctx = CommandContext(scope="group", qq="1", group_id="100001", text="帮助", sender_role="owner")
print([r.message.plain_text() for r in asyncio.run(svc.handle(ctx))])
```

### 8.4 检查数据库

```bash
sqlite3 data/rehearsal.db ".tables"
sqlite3 data/rehearsal.db "select * from reminder_job order by id desc limit 10;"
sqlite3 data/rehearsal.db "select * from pending_action order by created_at desc limit 5;"
```

---

## 9. 代码风格

- Python 3.11+，使用 `from __future__ import annotations`；
- 行宽 100（`ruff`，E501 忽略）；
- 导入排序、未使用变量、简化写法由 ruff 检查；
- 中文注释 / 提示，标识符英文；
- 公共函数写 docstring；
- 时间一律带时区；
- 用户可见文本集中在 `render.py` / `help.py` / `service.py` 的错误消息。

```bash
uv run ruff check --fix rehearsal plugins tests bot.py
uv run ruff format rehearsal plugins tests bot.py  # 可选
```

---

## 10. 发版流程

1. 更新 `pyproject.toml` 版本号与 `rehearsal/__init__.py`；
2. 更新 `docs/CHANGELOG.md`；
3. `uv lock`（依赖变化时）；
4. `uv run pytest` 全绿；
5. `uv run ruff check ...` 通过；
6. `uv run python scripts/demo.py` 更新演示文档；
7. `docker compose build bot` 本地验证（如可用）；
8. 打 tag：`git tag v1.0.0 && git push --tags`。

GitHub Actions 已配置基础 CI（见 `.github/workflows/ci.yml`）：安装依赖、ruff、pytest。

---

## 11. 常见开发问题

| 问题 | 原因 | 处理 |
|---|---|---|
| `ModuleNotFoundError: rehearsal` | 直接运行 `scripts/*.py` 时路径不对 | 用 `uv run python scripts/demo.py`，或用 `-m` 方式 |
| 插件加载两次 | `plugin_dirs` 与 `plugins` 同时配置 | 只保留 `plugin_dirs = ["plugins"]` |
| `NoneBot has not been initialized` | 直接 import 插件模块 | 通过 `nonebot.init()` + `load_from_toml`，或运行 `bot.py` |
| 测试数据库串数据 | 未使用 `tmp_path` | 每个测试用独立临时库 |
| 时间测试不稳定 | 使用真实当前时间 | 注入 `FakeClock` / 固定 `now` |
| 提醒重复发送 | 多个进程 / 未 claim | 确认单实例；检查 `claim_job` 用法 |
| SQLite locked | 多实例 / 长事务 | 保证单实例；缩短事务；查看 `busy_timeout` |
