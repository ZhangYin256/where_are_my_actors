"""权限矩阵与群身份同步测试（需求第 4 章 / 验收 12.2）。"""

from __future__ import annotations

from rehearsal.models import Member


async def test_actor_cannot_manage(app):
    for command in ("#删除排练 1", "#审计日志", "#配置查看", "#演员列表"):
        text = await app.text(command, qq="10004", role="member", nickname="赵六")
        assert "权限不足" in text or "只有" in text


async def test_assistant_can_publish_and_delete(app):
    await app.text("#授权副导演 @王五", qq="10002", role="admin", nickname="李四")
    roles = [r.role for r in app.repo.get_roles(app.troupe_a.id, "10003")]
    assert "assistant" in roles

    preview = await app.text(
        "#发布排练\n时间=明天7~9\n地点=一排\n人员=@王五",
        qq="10003",
        role="member",
        nickname="王五",
    )
    assert "【发布预览】" in preview
    action = preview.split("#确认发布 ")[1].split("\n")[0].strip()
    await app.text(f"#确认发布 {action}", qq="10003", role="member", nickname="王五")
    audit = await app.text("#审计日志", qq="10003", role="member", nickname="王五")
    assert "【审计日志】" in audit

    # 副导演可以删除
    prompt = await app.text("#删除排练 1", qq="10003", role="member", nickname="王五")
    assert "确定删除" in prompt
    deleted = await app.text("#确认删除 1", qq="10003", role="member", nickname="王五")
    assert "已删除" in deleted


async def test_assistant_cannot_grant(app):
    await app.text("#授权副导演 @王五", qq="10002", role="admin", nickname="李四")
    text = await app.text(
        "#授权副导演 @赵六", qq="10003", role="member", nickname="王五"
    )
    assert "权限不足" in text


async def test_director_cannot_grant_director(app):
    text = await app.text(
        "#授权导演 @王五", qq="10002", role="admin", nickname="李四"
    )
    assert "权限不足" in text or "只有管理员" in text


async def test_director_can_grant_assistant(app):
    text = await app.text(
        "#授权副导演 @王五", qq="10002", role="admin", nickname="李四"
    )
    assert "已授予" in text
    assert any(
        r.role == "assistant" and r.source == "manual"
        for r in app.repo.get_roles(app.troupe_a.id, "10003")
    )


async def test_admin_can_grant_director(app):
    text = await app.text("#授权导演 @王五", qq="10001", role="owner", nickname="张三")
    assert "已授予" in text
    assert any(r.role == "director" for r in app.repo.get_roles(app.troupe_a.id, "10003"))


async def test_group_admin_auto_director_added(app):
    await app.text("#剧组信息", qq="10004", role="admin", nickname="赵六")
    assert any(
        r.role == "director" and r.source == "auto_group_admin"
        for r in app.repo.get_roles(app.troupe_a.id, "10004")
    )


async def test_auto_director_removed_when_group_role_lost(app):
    await app.text("#剧组信息", qq="10004", role="admin", nickname="赵六")
    await app.text("#剧组信息", qq="10004", role="member", nickname="赵六")
    assert not any(
        r.role == "director" and r.source == "auto_group_admin"
        for r in app.repo.get_roles(app.troupe_a.id, "10004")
    )


async def test_manual_role_survives_auto_revoke(app):
    await app.text("#授权副导演 @王五", qq="10002", role="admin", nickname="李四")
    # 王五曾是群管理 → 变回普通成员
    await app.text("#剧组信息", qq="10003", role="admin", nickname="王五")
    await app.text("#剧组信息", qq="10003", role="member", nickname="王五")
    roles = {r.role for r in app.repo.get_roles(app.troupe_a.id, "10003")}
    assert "assistant" in roles
    assert "director" not in roles


async def test_admin_role_is_global(app):
    text = await app.text("#授权管理员 10003", qq="10001", role="owner", nickname="张三")
    assert "已授予" in text
    assert app.repo.is_admin("10003")
    # 在另一个剧组也拥有管理员权限
    text = await app.text(
        "#剧组信息", qq="10003", group_id="100002", role="member", nickname="王五"
    )
    assert "我的角色：管理员" in text


async def test_cannot_remove_last_admin(app):
    text = await app.text("#取消管理员 10001", qq="10001", role="owner", nickname="张三")
    assert "唯一管理员" in text


async def test_cannot_revoke_owner_auto_director(app):
    text = await app.text("#取消导演 @张三", qq="10001", role="owner", nickname="张三")
    assert "群主" in text or "群管理员" in text


async def test_sync_members(app):
    app.gateway.groups["100001"] = [
        Member("10001", "张三", role="member"),
        Member("10002", "李四", role="admin"),
        Member("10004", "赵六", role="owner"),
    ]
    text = await app.text("#同步成员", qq="10001", role="member", nickname="张三")
    assert "已同步群成员" in text
    roles_10004 = [r.role for r in app.repo.get_roles(app.troupe_a.id, "10004")]
    assert "director" in roles_10004
    auto_10001 = [
        r
        for r in app.repo.get_roles(app.troupe_a.id, "10001")
        if r.role == "director" and r.source == "auto_group_admin"
    ]
    assert auto_10001 == []
    # 管理员身份仍然保留
    assert app.repo.is_admin("10001")


async def test_admin_rename_troupe(app):
    text = await app.text("#修改剧组名称 剧组A2", qq="10001", role="owner")
    assert "已修改为「剧组A2」" in text
    assert app.repo.get_troupe(app.troupe_a.id).name == "剧组A2"
    denied = await app.text("#修改剧组名称 剧组A3", qq="10002", role="admin", nickname="李四")
    assert "只有管理员" in denied or "权限不足" in denied


async def test_unbind_requires_admin_and_stops_commands(app):
    denied = await app.text("#解绑剧组", qq="10002", role="admin", nickname="李四")
    assert "只有管理员" in denied or "权限不足" in denied
    text = await app.text("#解绑剧组", qq="10001", role="owner")
    assert "已解绑" in text
    assert app.repo.get_troupe(app.troupe_a.id).status == "archived"
    blocked = await app.text("#发布排练\n时间=明天7~9\n人员=@王五")
    assert "已解绑" in blocked


async def test_init_troupe_by_owner(app):
    app.gateway.groups["100003"] = [
        Member("10006", "孙八", role="owner"),
        Member("10007", "周九", role="member"),
    ]
    app.gateway.bot_admin["100003"] = True
    text = await app.text(
        "#初始化剧组 新剧组",
        qq="10006",
        group_id="100003",
        role="owner",
        nickname="孙八",
    )
    assert "已初始化" in text
    troupe = app.repo.get_troupe_by_group("100003")
    assert troupe is not None and troupe.name == "新剧组"
    assert any(r.role == "director" for r in app.repo.get_roles(troupe.id, "10006"))
    again = await app.text(
        "#初始化剧组 另一个", qq="10006", group_id="100003", role="owner", nickname="孙八"
    )
    assert "已绑定剧组" in again


async def test_init_troupe_requires_admin_or_owner(app):
    app.gateway.groups["100003"] = [Member("10006", "孙八", role="admin")]
    text = await app.text(
        "#初始化剧组 新剧组",
        qq="10006",
        group_id="100003",
        role="admin",
        nickname="孙八",
    )
    assert "只有管理员" in text or "权限不足" in text


async def test_rebind_archived_troupe(app):
    await app.text("#解绑剧组", qq="10001", role="owner")
    text = await app.text("#初始化剧组 剧组A（回归）", qq="10001", role="owner")
    assert "重新绑定" in text
    troupe = app.repo.get_troupe_by_group("100001")
    assert troupe.status == "active"
    assert troupe.name == "剧组A（回归）"
    assert any(r.role == "director" for r in app.repo.get_roles(troupe.id, "10001"))
