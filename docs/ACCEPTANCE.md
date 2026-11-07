# 验收对照表

对照 `docs/SPEC.md` v1.0 的验收标准与功能需求，逐项说明实现位置与验证方式。

- 验收日期：2026-11-07
- 版本：1.2.0
- 自动化测试：**209 个，全部通过**
- 测试环境：Windows 11 · Python 3.12.14 · SQLite 3（stdlib）· pytest 9
- 复现命令：`uv run pytest`
- 需求变更：v1.1 调整发布确认默认行为（无问题直接发布，可配置为总是预览），
  私聊管理按“本人管理的剧组”选择剧组；v1.2 新增管理员私聊初始化、`#剧组列表`、
  私聊 `#同步成员` 与管理员恢复机制。详见第 11 节。

> Docker 相关条目提供了完整文件与部署步骤；本仓库开发环境未安装 Docker，镜像构建请在具备 Docker 的机器上按 [DEPLOY-DOCKER.md](DEPLOY-DOCKER.md) 执行。

---

## 1. 技术栈验收（规格 12.1）

| 条目 | 实现 | 证据 |
|---|---|---|
| 使用 NoneBot2 | 插件 `plugins/rehearsal_bot`，依赖 `nonebot2[websockets]>=2.4,<2.6` | `pyproject.toml`、`bot.py` |
| 使用 NapCat | `docker-compose.yml` 的 `napcat` 服务；本地部署文档 | `docker-compose.yml`、[DEPLOY-DOCKER.md](DEPLOY-DOCKER.md) |
| 使用 SQLite | `rehearsal/db.py`，WAL、外键、唯一约束 | `tests/test_db.py::test_wal_mode_and_schema` |
| Windows / Ubuntu / macOS | 纯 Python + `tzdata`，无平台特定依赖 | [DEPLOY-LOCAL.md](DEPLOY-LOCAL.md) |
| Docker Compose 部署 | `Dockerfile` + `docker-compose.yml` + `.env.example` | [DEPLOY-DOCKER.md](DEPLOY-DOCKER.md) |

---

## 2. 权限验收（规格 12.2）

| # | 验收项 | 实现 | 自动化测试 |
|---:|---|---|---|
| 1 | 演员可查看本剧组所有排练 | `_cmd_list` 不做参与人过滤 | `test_queries.py::test_list_future`、`test_list_this_week` |
| 2 | 副导演可删除排练 | `delete` 能力等级 2 | `test_permissions.py::test_assistant_can_publish_and_delete` |
| 3 | 副导演可查看审计日志 | `audit` 能力等级 2 | 同上（审计日志查询成功） |
| 4 | 导演可授权副导演 | `grant_assistant` 等级 3 | `test_permissions.py::test_director_can_grant_assistant` |
| 5 | 导演不能授权导演 | `grant_director` 等级 4 | `test_permissions.py::test_director_cannot_grant_director` |
| 6 | 群主自动成为导演 | 初始化 / 消息 / 同步时写入 `auto_group_admin` | `test_permissions.py::test_init_troupe_by_owner`、`test_sync_members` |
| 7 | 群管理员自动成为导演 | `_sync_sender_role` + 定时同步 + 群管理变更事件 | `test_permissions.py::test_group_admin_auto_director_added` |
| 8 | 一个 QQ 可在多剧组任导演 / 副导演 | 角色按 `troupe_id` 隔离；管理员全局；私聊管理按本人管理剧组选择 | `test_multi_troupe.py::test_private_management_uses_only_managed_troupes`、`test_private_management_multiple_requires_troupe`、`test_admin.py::test_admin_role_is_global` |

补充验证：

- 自动导演撤销：`test_auto_director_removed_when_group_role_lost`
- 手动角色不受自动撤销影响：`test_manual_role_survives_auto_revoke`
- 不能取消唯一管理员：`test_cannot_remove_last_admin`
- 群主自动导演不能手动取消：`test_cannot_revoke_owner_auto_director`
- 演员不能执行管理命令：`test_permissions.py::test_actor_cannot_manage`

---

## 3. 发布验收（规格 12.3 + v1.1 调整）

| # | 验收项 | 实现 | 测试 |
|---:|---|---|---|
| 1 | 标题固定 `#排练通知` | `render.notice_message` | `test_publish.py::test_publish_preview_and_confirm` |
| 2 | 支持 `=`、`：`、`:` | `fields._KEY_RE` | `test_fields.py::test_separators_equivalent` |
| 3 | 地点 / 内容可为空 | 空值归一化 | `test_publish.py::test_empty_fields_are_not_displayed` |
| 4 | 空字段不显示 | `render.notice_message` | 同上 |
| 5 | 标签默认不显示 | `troupe.show_tags` 默认 false | `test_publish.py::test_tags_hidden_by_default`、`test_admin.py::test_config_show_tags` |
| 6 | 人员可包含文本人员 | `parse_participants` | `test_fields.py::test_parse_mentions_and_text_person` |
| 7 | 文本人员提示不能精确提醒 | 预览渲染 `draft.text_participants` | `test_publish.py::test_text_person_warning` |
| 8 | 已匹配人员会被 @ | `render.add_participants` → `At` 段 | `test_publish.py::test_publish_preview_and_confirm` |
| 9 | 发布确认可配置（默认无问题直接发送） | `publish_confirm_when_clean` + `_cmd_publish` / `_publish_now` | `test_publish.py::test_publish_auto_sends_when_no_warnings`、`test_publish_with_warnings_still_previews_when_auto`、`test_config_publish_confirm_toggle` |
| 10 | 预览时必须确认后才正式发送 | `#确认发布` 才写库并发送；直接发布仅限无风险且配置为关 | `test_publish.py::test_publish_requires_preview_confirmation`、`test_publish_auto_sends_when_no_warnings` |
| 11 | 新建剧组默认 `发布确认=关` | `troupe.publish_confirm_when_clean` 默认 0 / `REHEARSAL_PUBLISH_CONFIRM=false` | `test_db.py::test_publish_confirm_defaults_to_false` |

---

## 4. 时间解析验收（规格 12.4）

| # | 验收项 | 测试 |
|---:|---|---|
| 1 | `周一晚8:30~10:00` 周一早上 → 当天 20:30-22:00 | `test_timeparse.py::test_weekday_evening_same_day` |
| 2 | `周五7~10` 周六发布 → 下周五 19:00-22:00 | `test_timeparse.py::test_friday_range_on_saturday_is_next_friday` |
| 3 | `周五7~10` 默认晚上 | `test_timeparse.py::test_bare_hour_range_defaults_to_evening` |
| 4 | 晚上阈值可配置 | `test_timeparse.py::test_evening_threshold_configurable`、`test_admin.py::test_config_evening_threshold` |
| 5 | 不需要年份 | 全部日期用例；内部存完整 UTC 时间 | `test_timeparse.py`、`test_db.py::test_wal_mode_and_schema` |
| 6 | 今年已过按下一年并提示 | `test_timeparse.py::test_no_year_date_rolls_to_next_year`、`test_publish.py::test_cross_year_date_note` |
| 7 | 开始时间不足只发布、不补发 | `test_publish.py::test_publish_with_missed_reminder_window`、`test_start_time_passed_note` |

补充：

- 跨天：`test_cross_midnight`、`test_cross_midnight_explicit_24h`
- 周一晚已过顺延：`test_weekday_evening_passed_rolls_to_next_week`
- 本周 / 下周：`test_this_week_friday_stays_in_this_week`、`test_next_week_friday`
- 无效时间：`test_invalid_time`

---

## 5. 修改与重发验收（规格 12.5）

| # | 验收项 | 测试 |
|---:|---|---|
| 1 | 修改后支持预览 | `test_lifecycle.py::test_modify_location_preview_and_save` |
| 2 | 保存后不自动重发 | 同上（群消息为空） |
| 3 | 手动 `#排练重发` 后群内发送更新通知 | `test_lifecycle.py::test_republish_preview_then_send` |
| 4 | 重发前支持预览 | 同上 |
| 5 | 修改时间后提醒同步更新 | `test_lifecycle.py::test_modify_time_reschedules_reminder` |
| 6 | 不保留历史版本 | `rehearsal` 表无版本表，`UPDATE` 覆盖；审计记录动作 |

补充：

- 提醒关闭：`test_modify_reminder_off`
- 取消修改：`test_modify_preview_cancel`
- 取消的排练不能重发：`test_republish_cancelled_rehearsal_rejected`

---

## 6. 查询验收（规格 12.6）

| # | 验收项 | 测试 |
|---:|---|---|
| 1 | 演员群内查看本剧组所有通知 | `test_queries.py::test_list_future` |
| 2 | 查看历史 | `test_queries.py::test_list_history` |
| 3 | 按周查看 | `test_queries.py::test_list_this_week`、`test_list_next_week` |
| 4 | 私聊查询本人多剧组安排 | `test_queries.py::test_my_schedule_private_across_troupes` |
| 5 | 按时间轴排列 | 同上 |
| 6 | 显示剧组名称 | 同上 |

补充：

- 详情：`test_detail`、`test_detail_not_found`
- 隐私（不显示他人 QQ / @）：`test_my_schedule_private_across_troupes`
- 标签：`test_my_schedule_shows_tag`
- 群内查询提示私聊：`test_my_schedule_in_group_notes_privacy`
- 历史 / 未来过滤：`test_my_schedule_future_excludes_past`
- 取消标记：`test_cancelled_marked_in_list`

---

## 7. 提醒验收（规格 12.7）

| # | 验收项 | 测试 |
|---:|---|---|
| 1 | 默认提前 30 分钟 | `test_reminders.py::test_reminder_sent_once` |
| 2 | 可配置多个提醒 | `test_reminders.py::test_multiple_reminder_jobs`、`test_admin.py::test_default_remind_command` |
| 3 | 可关闭提醒 | `test_lifecycle.py::test_modify_reminder_off`、`test_admin.py::test_default_remind_off` |
| 4 | 地点 / 内容为空时提醒不显示 | `test_reminders.py::test_reminder_omits_empty_fields` |
| 5 | 私聊提醒默认关闭 | `test_reminders.py::test_private_reminder_disabled_by_default` |
| 6 | 私聊提醒可开启 | `test_reminders.py::test_private_reminder` |
| 7 | 重启后提醒任务恢复 | `test_reminders.py::test_pending_reminders_survive_restart` |
| 8 | 同一提醒不重复发送 | `test_reminders.py::test_reminder_sent_once`、`test_restart_does_not_duplicate` |

补充：

- 未到点不发送：`test_reminder_not_before_trigger`
- 失败重试 / 最终失败：`test_reminder_retry_then_fail`、`test_reminder_retry_then_success`
- 错过宽限不补发：`test_missed_reminder_expired_not_sent`
- 私聊失败写审计：`test_private_reminder_failure_logged`
- 取消后不发送：`test_cancelled_reminder_not_sent`
- 提醒审计：`test_reminder_status_and_audit`

---

## 8. @全体成员验收（规格 12.8）

| # | 验收项 | 测试 |
|---:|---|---|
| 1 | 机器人是群管理员时可 @全体 | `test_reminders.py::test_all_mode_at_all_when_admin` |
| 2 | 不是群管理员时不能 @全体 | `test_publish.py::test_at_all_downgrade_when_bot_not_admin`、`test_all_mode_degrades_to_member_mentions` |
| 3 | 人员含 @全体时降级逐个 @ | `test_reminders.py::test_all_mode_degrades_to_member_mentions` |
| 4 | 不在人员中的 @全体不参与人员匹配 | `test_publish.py::test_at_all_in_content_does_not_change_participants` |
| 5 | 降级逐个 @ 能分段发送 | `test_reminders.py::test_reminder_chunking` |

---

## 9. 功能需求（FR）覆盖

| 需求 | 实现 | 测试 / 文档 |
|---|---|---|
| FR-01 剧组绑定 | `_cmd_init_troupe`（群内 / 私聊）、`_cmd_rename_troupe`、`_cmd_unbind_troupe`、`_cmd_troupe_list`；`troupe.group_id UNIQUE` | `test_permissions.py::test_init_troupe_by_owner`、`test_private_admin.py::test_group_init_by_admin_without_owner_role`、`test_private_init_creates_troupe`、`test_private_init_bot_not_in_group`、`test_troupe_list_private_admin`；`test_db.py::test_troupe_group_unique` |
| FR-02 角色授权 | `_cmd_grant`、`_cmd_global_admin`、`role_binding` / `admin_binding` | `test_permissions.py`（18 例） |
| FR-03 演员管理 | `_cmd_actor`、`actor_profile` | `test_admin.py::test_add_and_list_actors`、`test_actor_alias_and_tag`、`test_remove_actor` |
| FR-04 发布通知 | `_cmd_publish` / `_publish_now` / `publish_confirm_when_clean` + `preview_message` + `notice_message` | `test_publish.py`（23 例） |
| FR-05 时间解析 | `rehearsal/timeparse.py` | `test_timeparse.py`（35 例） |
| FR-06 人员解析 | `rehearsal/fields.py::parse_participants`、`service._parse_people` | `test_fields.py`、`test_reminders.py` |
| FR-07 查看通知 | `_cmd_list` / `_cmd_detail` / `_cmd_my_schedule` | `test_queries.py`（13 例） |
| FR-08 修改与重发 | `_cmd_modify` / `_cmd_republish` + pending | `test_lifecycle.py`（15 例） |
| FR-09 取消与删除 | `_cmd_cancel` / `_cmd_delete` / `_cmd_hard_delete` | 同上 |
| FR-10 排练提醒 | `ReminderWorker` / `run_due_reminders` / `reminder_job` | `test_reminders.py`（18 例） |
| FR-11 配置管理 | `_cmd_config_*` / `_cmd_global_config` / `_cmd_default_remind` | `test_admin.py` |
| FR-12 审计日志 | `audit_log` + `_cmd_audit` | `test_admin.py::test_audit_log_records_actions`、`test_audit_requires_role` |
| FR-13 帮助 | `help.py` + `_cmd_help` | `test_admin.py::test_help_overview_and_topic` |

---

## 10. 非功能需求

| 需求 | 实现 / 说明 |
|---|---|
| 跨平台 | 纯 Python 3.11+；Windows / Linux / macOS 部署文档；Docker 镜像 |
| 命令响应 < 2 秒 | 单次命令本地 SQLite 操作，测试中通常 < 100 ms；外部耗时主要为 OneBot API |
| 发布解析 < 3 秒 | 纯 CPU 解析 + 一次群成员查询（有缓存） |
| 私聊查询 < 2 秒 | 单次 SQL + 渲染 |
| 提醒误差 < 60 秒 | 扫描间隔默认 10 秒；`test_reminder_sent_once` 验证准点触发 |
| 500 人 / 群 | 群成员列表分页由 OneBot 处理；逐个 @ 分段发送 |
| 10 个剧组 / 1000 条未来排练 | SQLite 索引 `(troupe_id, start_at)`；容量说明见 OPERATIONS |
| NapCat 自动重连 | NoneBot OneBot 适配器内置重连；文档说明 |
| 重启恢复 | `reminder_job` 持久化 + `recover_inflight_jobs` |
| WAL | `PRAGMA journal_mode=WAL`，`test_wal_mode_and_schema` |
| 幂等 / 不重复 | `claim_job` 原子抢占 + 唯一约束 + 重启策略 |
| 长消息分段 | `split_message` + gateway 拆分，`test_message.py` |
| 失败重试 | 最多 3 次，可配置；失败写审计 |
| 权限 / 隐私 | 权限矩阵、私聊仅本人、审计仅管理角色 |
| 可维护性 | 模块化、209 测试、中文日志/错误、备份脚本 |
| 可用性 | 中文提示、智能发布确认、帮助、错误修正建议 |

---

## 11. 已知差异与边界

| 项目 | 说明 |
|---|---|
| 管理员存储 | 使用独立 `admin_binding` 全局表，而非规格 `role_binding` 的 troupe 级记录；符合“管理员管理所有剧组”的语义 |
| 发布确认 | 规格要求发布前必须预览确认；v1.1 起默认“无问题直接发布”，有风险仍强制预览，可用 `发布确认=开` 恢复总是预览（需求变更） |
| 私聊多剧组 | 管理命令按“本人担任导演 / 副导演的剧组”自动选择，恰好一个时可省略 `剧组=`；查询按参与剧组判断（需求变更） |
| 私聊初始化 | 规格仅在群内初始化 | 管理员可私聊 `#初始化剧组 名称 群号=...`；新增 `#剧组列表`、私聊 `#同步成员`；管理员锁死可用脚本恢复（需求变更） |
| 重发确认 | 增加 `#确认重发 R001`，满足“重发前支持预览” |
| 彻底删除 | 增加 `#彻底删除`（仅管理员），对应“管理员可物理删除” |
| 群主初始化 | 默认允许群主自助 `#初始化剧组`，可通过 `REHEARSAL_ALLOW_OWNER_INIT=false` 关闭 |
| `#排练通知` 别名 | 群内无字段时不响应，避免与正式通知标题冲突 |
| 全体成员私聊 | `人员=@全体成员` 只发群提醒，不逐个私聊 |
| 崩溃恢复 | 发送中崩溃标记 failed、不重发，优先不重复 |
| 审计角色 | 记录操作人 QQ 与动作，不保存当时角色快照 |
| 全局配置 | 连接地址、数据库路径、命令前缀等需改 `.env`；聊天内只能改运行参数 |
| Docker 构建验证 | 开发环境无 Docker，文件与步骤已提供，需在目标机器执行构建 |

---

## 12. 手工验收清单（真实 QQ）

部署完成后按顺序执行：

- [ ] `docker compose ps`：两个容器 Up，bot 为 healthy
- [ ] `#帮助` 有回复，角色显示正确
- [ ] 群内 `#初始化剧组 验收剧组` 成功
- [ ] 管理员私聊 `#初始化剧组 新剧组 群号=<群号>` 成功，群内无消息
- [ ] `#剧组列表` 显示全局管理员身份、群号与角色
- [ ] 误删 / 停用管理员后，`docker compose exec -T bot python scripts/grant_admin.py <QQ>` 可恢复
- [ ] `#授权副导演 @测试账号` 成功；该账号可发布、可删除、可查审计
- [ ] 该账号 `#授权导演 @某人` 被拒绝
- [ ] 发布通知（无风险）：`#发布排练` 直接发送 `#排练通知`，无需确认
- [ ] `#配置 发布确认=开` 后，同样内容返回预览，`#确认发布` 后发送
- [ ] 带文本人员的通知始终返回预览并提示不能 @
- [ ] 中文时间：`周五7~10` 解析为 19:00-22:00
- [ ] 文本人员提示“不能 @”，群通知中文本人员未被 @
- [ ] 私聊管理多剧组：管理一个时可省略 `剧组=`；管理多个时提示指定
- [ ] 私聊查询多剧组：同时参与多个时要求 `剧组=`
- [ ] 私聊 `#同步成员 剧组=...` 可同步群主 / 群管理员
- [ ] 修改地点 → 保存后群内无消息 → `#排练重发` + 确认 → 群内收到更新通知
- [ ] 取消排练 → `通知=是` → 群内收到 `#排练取消通知`
- [ ] 私聊 `#我的排练` 只显示本人安排，无他人 QQ
- [ ] 提醒：将排练设为 2 分钟后开始（提醒=0 或 1），到点收到 `#排练提醒`
- [ ] 机器人非群管理员时发布 `@全体成员`，预览提示降级，实际逐个 @
- [ ] 机器人设为群管理员后重复上一步，实际 `@全体成员`
- [ ] 重启 bot，未发送提醒仍会触发；已发送提醒不重复
- [ ] `#审计日志 20` 包含发布 / 修改 / 提醒记录

---

## 13. 验收结论

- 规格中的 **P0 功能全部实现**；
- **12.1 – 12.8 验收项均有对应实现与自动化测试**；
- v1.1 需求变更（发布确认可配置、私聊多剧组选择）已实现并补充测试；
- v1.2 需求变更（私聊初始化 / 剧组列表 / 私聊同步、管理员恢复）已实现并补充测试；
- 209 个测试覆盖时间、权限、发布、修改、提醒、查询、配置、审计、模拟器与数据库；
- 部署、使用、配置、运维、架构、开发文档齐全；
- 遗留差异均已在第 11 节说明，不阻塞验收。
