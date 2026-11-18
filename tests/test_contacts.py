from datetime import date
from pathlib import Path

import pytest

from zhiyin.contacts import ContactStore


def test_three_distinct_days_and_preserve_profile(tmp_path):
    store = ContactStore(tmp_path)
    for day in (1, 1, 2):
        store.update("wechat", "合成小鱼", "聊了猫", "friend", day=date(2026, 11, day))
    profile = tmp_path / "contacts/wechat/合成小鱼.md"
    assert not profile.exists()
    store.update("wechat", "合成小鱼", "聊了音乐", "friend", day=date(2026, 11, 3))
    assert profile.exists()
    profile.write_text(
        profile.read_text().replace("## 长期记忆", "手工补充：喜欢猫\n\n## 长期记忆")
    )
    store.update("wechat", "合成小鱼", "聊了电影", "friend", day=date(2026, 11, 4))
    assert "手工补充：喜欢猫" in store.context("wechat", "合成小鱼")
    assert "聊了电影" in profile.read_text()
    assert (tmp_path / "总文档.md").read_text().count("wechat | 合成小鱼") == 1
    assert profile.stat().st_mode & 0o777 == 0o600


def test_manual_promote(tmp_path):
    store = ContactStore(tmp_path)
    path = store.promote("wechat", "合成熟人")
    assert path.exists()
    assert "## 说话分寸" in path.read_text()
    assert "合成熟人" in store.context("wechat", "合成熟人")
    assert store.promote("wechat", "合成熟人").read_text() == path.read_text()


@pytest.mark.parametrize("name", ["../escape", "/tmp/escape", "..", "a/b", "a\\b", "x\ny"])
def test_contact_path_rejected(tmp_path, name):
    with pytest.raises(ValueError):
        ContactStore(tmp_path).promote("wechat", name)
    assert not list(tmp_path.iterdir())


def test_symlink_not_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "state"
    root.mkdir()
    (root / "contacts").symlink_to(outside)
    with pytest.raises(ValueError):
        ContactStore(root).promote("wechat", "合成")
    assert not list(outside.iterdir())


def test_missing_analysis_preserves_known_stage(tmp_path):
    store = ContactStore(tmp_path)
    store.update("wechat", "合成", "老朋友聊猫", "friend")
    store.update("wechat", "合成", "本次需本人查看", None)
    assert "| friend |" in store.context("wechat", "合成")


def test_default_store_copies_legacy_data_without_overwriting(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    old = tmp_path / ".chat-twin"
    legacy = ContactStore(old)
    legacy.update("wechat", "示例联系人", "合成记录", "friend")
    legacy.promote("wechat", "示例联系人")

    current = ContactStore()
    assert "合成记录" in current.context("wechat", "示例联系人")
    copied = current.root / "contacts/wechat/示例联系人.md"
    assert current.root.stat().st_mode & 0o777 == 0o700
    assert copied.stat().st_mode & 0o777 == 0o600
    current.update("wechat", "示例联系人", "更新后的合成记录", "friend")
    assert "更新后的合成记录" in copied.read_text()
    assert "更新后的合成记录" not in legacy.context("wechat", "示例联系人")
