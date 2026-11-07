"""多剧组与私聊剧组选择测试。"""

from __future__ import annotations


async def test_private_management_uses_only_managed_troupes(app):
    """在多个剧组有身份，但只管理一个时，私聊发布不需要 剧组=。"""
    app.repo.add_role(app.troupe_a.id, "10003", "assistant", source="manual")
    app.repo.upsert_actor(app.troupe_b.id, "10003", "王五")

    preview = await app.text(
        "#发布排练\n时间=明天7~9\n人员=@王五",
        qq="10003",
        scope="private",
        group_id=None,
        nickname="王五",
    )
    assert "【发布预览】" in preview
    action = preview.split("#确认发布 ")[1].split("\n")[0].strip()
    await app.text(
        f"#确认发布 {action}",
        qq="10003",
        scope="private",
        group_id=None,
        nickname="王五",
    )
    assert app.repo.get_rehearsal(app.troupe_a.id, 1) is not None
    assert app.repo.get_rehearsal(app.troupe_b.id, 1) is None


async def test_private_management_multiple_requires_troupe(app):
    text = await app.text(
        "#发布排练\n时间=明天7~9\n人员=@王五",
        qq="10002",
        scope="private",
        group_id=None,
        nickname="李四",
    )
    assert "你管理多个剧组" in text
    assert "剧组A" in text and "剧组B" in text

    preview = await app.text(
        "#发布排练 剧组=剧组B\n时间=明天7~9\n人员=@王五",
        qq="10002",
        scope="private",
        group_id=None,
        nickname="李四",
    )
    assert "【发布预览】" in preview
    action = preview.split("#确认发布 ")[1].split("\n")[0].strip()
    await app.text(
        f"#确认发布 {action}",
        qq="10002",
        scope="private",
        group_id=None,
        nickname="李四",
    )
    assert app.repo.get_rehearsal(app.troupe_b.id, 1) is not None


async def test_private_view_multiple_requires_troupe(app):
    app.repo.add_role(app.troupe_a.id, "10003", "assistant", source="manual")
    app.repo.upsert_actor(app.troupe_b.id, "10003", "王五")

    text = await app.text(
        "#排练列表",
        qq="10003",
        scope="private",
        group_id=None,
        nickname="王五",
    )
    assert "你参与了多个剧组" in text

    selected = await app.text(
        "#排练列表 剧组=剧组A",
        qq="10003",
        scope="private",
        group_id=None,
        nickname="王五",
    )
    assert "【排练列表】剧组A" in selected


async def test_private_management_without_managed_troupe(app):
    """只在剧组 B 有演员记录、没有管理角色时，管理命令应提示无管理剧组。"""
    app.repo.upsert_actor(app.troupe_b.id, "10004", "赵六")
    text = await app.text(
        "#发布排练\n时间=明天7~9\n人员=@王五",
        qq="10004",
        scope="private",
        group_id=None,
        nickname="赵六",
    )
    assert "你还没有管理任何剧组" in text

    # 查询类命令仍可按参与的剧组自动选择
    listing = await app.text(
        "#排练列表",
        qq="10004",
        scope="private",
        group_id=None,
        nickname="赵六",
    )
    assert "【排练列表】剧组B" in listing


async def test_private_management_config_and_audit_use_managed_troupe(app):
    app.repo.add_role(app.troupe_a.id, "10003", "assistant", source="manual")
    app.repo.upsert_actor(app.troupe_b.id, "10003", "王五")

    config = await app.text(
        "#配置查看",
        qq="10003",
        scope="private",
        group_id=None,
        nickname="王五",
    )
    assert "【剧组配置】剧组A" in config
    assert "剧组B" not in config

    audit = await app.text(
        "#审计日志",
        qq="10003",
        scope="private",
        group_id=None,
        nickname="王五",
    )
    assert "【审计日志】" in audit
