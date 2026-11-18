import json

import pytest

from zhiyin.wechat import WeChatStore, import_wechat


def msg(minute, sender, text, kind=1, subtype=None):
    return {
        "time": f"2026-01-01T09:{minute:02d}:00+08:00",
        "sender": sender,
        "type": kind,
        "subtype": subtype,
        "text": text,
    }


def export(root, conversations):
    root.mkdir(exist_ok=True)
    manifest = []
    for index, (label, messages) in enumerate(conversations.items(), start=1):
        code = f"C{index}"
        (root / code).mkdir(exist_ok=True)
        (root / code / "chat.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in messages)
        )
        manifest.append(
            {"label": label, "conversation_id": f"synthetic-{index}", "code": code, "folder": code}
        )
    (root / "导出清单.json").write_text(json.dumps({"conversations": manifest}))


def test_reimport_deduplicates_but_keeps_identical_bubbles(tmp_path):
    root = tmp_path / "export"
    store = WeChatStore(tmp_path / "state")
    rows = [msg(0, "them", "茶泡好啦"), msg(0, "me", "好香"), msg(0, "me", "好香")]
    export(root, {"合成甲": rows})
    assert import_wechat(store, root)["inserted"] == 3
    assert import_wechat(store, root)["duplicates"] == 3
    export(root, {"合成甲": [*rows, msg(1, "me", "我来倒", 49, 57)]})
    result = import_wechat(store, root)
    assert result["inserted"] == 1
    assert result["reply_pairs"] == 3
    with store.connect() as db:
        assert [
            tuple(row)
            for row in db.execute("SELECT her, reply FROM reply_pairs ORDER BY time, seq")
        ] == [
            ("茶泡好啦", "好香"),
            ("茶泡好啦", "好香"),
            ("茶泡好啦", "我来倒"),
        ]
    assert (store.root / "wechat.sqlite3").stat().st_mode & 0o777 == 0o600


def test_exclusions_take_effect_before_and_after_reimport(tmp_path):
    root = tmp_path / "export"
    store = WeChatStore(tmp_path / "state")
    rows = [msg(0, "them", "合成问题"), msg(1, "me", "合成回答")]
    export(root, {"合成甲": rows, "合成乙": rows})
    import_wechat(store, root)
    (store.root / "config.toml").write_text('exclude_contacts = ["C1"]\n')
    with store.connect() as db:
        assert store.contact(db, "合成甲") is None
        assert db.execute("SELECT count(*) FROM reply_pairs").fetchone()[0] == 1
    # The excluded contact's source is never opened.
    (root / "C1/chat.jsonl").unlink()
    assert import_wechat(store, root)["excluded_contacts"] == 1
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM contacts").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 2


def test_invalid_input_rolls_back_complete_import(tmp_path):
    root = tmp_path / "export"
    store = WeChatStore(tmp_path / "state")
    export(root, {"合成甲": [msg(0, "them", "合成")], "合成乙": [{"invalid": True}]})
    with pytest.raises(KeyError):
        import_wechat(store, root)
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 0
