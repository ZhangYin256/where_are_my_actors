"""私聊初始化 / 剧组列表 / 私聊同步与管理员引导测试。"""

from __future__ import annotations

from rehearsal.models import Member


async def test_group_init_by_admin_without_owner_role(app):
    app.gateway.groups["100003"] = [
        Member("10006", "孙八", role="owner"),
        Member("10007", "周九", role="member"),
    ]
    text = await app.text(
        "#初始化剧组 群内新剧组",
        qq="10001",
        group_id="100003",
        role="member",
        nickname="张三",
    )
    assert "已初始化" in text
    troupe = app.repo.get_troupe_by_group("100003")
    assert troupe is not None and troupe.name == "群内新剧组"
    assert any(
        r.role == "director" and r.source == "auto_group_admin"
        for r in app.repo.get_roles(troupe.id, "10006")
    )


async def test_private_init_creates_troupe(app):
    app.gateway.groups["100003"] = [
        Member("10006", "孙八", role="owner"),
        Member("10007", "周九", role="admin"),
        Member("10008", "吴十", role="member"),
    ]
    text = await app.text(
        "#初始化剧组 私聊新剧组 群号=100003",
        qq="10001",
        scope="private",
        group_id=None,
        nickname="张三",
    )
    assert "已初始化并绑定群 100003" in text
    assert "群主 / 群管理员已同步为导演" in text
    troupe = app.repo.get_troupe_by_group("100003")
    assert troupe is not None
    assert troupe.name == "私聊新剧组"
    directors = {
        r.qq_id for r in app.repo.list_roles(troupe.id) if r.role == "director"
    }
    assert directors == {"10006", "10007"}


async def test_private_init_accepts_bare_group_number(app):
    app.gateway.groups["100004"] = [Member("10009", "郑十一", role="owner")]
    text = await app.text(
        "#初始化剧组 裸群号剧组 100004",
        qq="10001",
        scope="private",
        group_id=None,
        nickname="张三",
    )
    assert "已初始化" in text
    assert app.repo.get_troupe_by_group("100004").name == "裸群号剧组"


async def test_private_init_requires_group_number(app):
    text = await app.text(
        "#初始化剧组 新剧组",
        qq="10001",
        scope="private",
        group_id=None,
        nickname="张三",
    )
    assert "需要提供剧组群号" in text


async def test_private_init_requires_admin(app):
    text = await app.text(
        "#初始化剧组 新剧组 群号=100001",
        qq="10002",
        scope="private",
        group_id=None,
        nickname="李四",
    )
    assert "只有管理员" in text


async def test_private_init_bot_not_in_group(app):
    text = await app.text(
        "#初始化剧组 新剧组 群号=199999",
        qq="10001",
        scope="private",
        group_id=None,
        nickname="张三",
    )
    assert "无法读取群 199999" in text
    assert app.repo.get_troupe_by_group("199999") is None


async def test_private_init_rejects_bound_group(app):
    text = await app.text(
        "#初始化剧组 重复剧组 群号=100001",
        qq="10001",
        scope="private",
        group_id=None,
        nickname="张三",
    )
    assert "已绑定剧组" in text


async def test_private_init_rebinds_archived_group(app):
    await app.text("#解绑剧组", qq="10001", role="owner")
    assert app.repo.get_troupe(app.troupe_a.id).status == "archived"
    text = await app.text(
        "#初始化剧组 剧组A回归 群号=100001",
        qq="10001",
        scope="private",
        group_id=None,
        nickname="张三",
    )
    assert "已重新绑定" in text
    troupe = app.repo.get_troupe(app.troupe_a.id)
    assert troupe.status == "active"
    assert troupe.name == "剧组A回归"


async def test_troupe_list_private_admin(app):
    app.gateway.groups["100003"] = [Member("10006", "孙八", role="owner")]
    await app.text(
        "#初始化剧组 新剧组 群号=100003",
        qq="10001",
        scope="private",
        group_id=None,
        nickname="张三",
    )
    text = await app.text(
        "#剧组列表", qq="10001", scope="private", group_id=None, nickname="张三"
    )
    assert "【剧组列表】" in text
    assert "全局管理员：是" in text
    assert "剧组A（群 100001）" in text
    assert "剧组B（群 100002）" in text
    assert "新剧组（群 100003）" in text
    assert "添加剧组" in text


async def test_troupe_list_for_director_and_actor(app):
    text = await app.text(
        "#剧组列表", qq="10002", scope="private", group_id=None, nickname="李四"
    )
    assert "全局管理员：否" in text
    assert "导演" in text
    assert "你管理多个剧组" in text

    app.repo.upsert_actor(app.troupe_b.id, "10004", "赵六")
    text = await app.text(
        "#剧组列表", qq="10004", scope="private", group_id=None, nickname="赵六"
    )
    assert "剧组B（群 100002）" in text
    assert "演员" in text
    assert "剧组A" not in text


async def test_troupe_list_in_group(app):
    text = await app.text(
        "#剧组列表", qq="10004", group_id="100001", role="member", nickname="赵六"
    )
    assert "【剧组列表】当前群" in text
    assert "剧组A（群 100001）" in text
    assert "剧组B" not in text


async def test_private_sync_members(app):
    app.gateway.groups["100001"] = [
        Member("10001", "张三", role="member"),
        Member("10002", "李四", role="admin"),
        Member("10004", "赵六", role="owner"),
    ]
    text = await app.text(
        "#同步成员 剧组=剧组A",
        qq="10001",
        scope="private",
        group_id=None,
        nickname="张三",
    )
    assert "已同步群成员" in text
    assert any(
        r.role == "director" and r.source == "auto_group_admin"
        for r in app.repo.get_roles(app.troupe_a.id, "10004")
    )
    assert not any(
        r.role == "director" and r.source == "auto_group_admin"
        for r in app.repo.get_roles(app.troupe_a.id, "10001")
    )


async def test_startup_restores_config_admin_when_none_active(app):
    app.repo.remove_admin("10001")
    assert not app.repo.is_admin("10001")
    await app.service.startup()
    assert app.repo.is_admin("10001")


async def test_startup_keeps_revoked_config_admin_when_other_admin(app):
    app.repo.add_admin("10002", granted_by="10001", source="manual")
    app.repo.remove_admin("10001")
    await app.service.startup()
    assert not app.repo.is_admin("10001")
    assert app.repo.is_admin("10002")
