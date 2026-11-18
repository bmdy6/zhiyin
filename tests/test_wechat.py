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


def test_tone_uses_all_own_bubbles_and_respects_groups_and_exclusions(tmp_path):
    from zhiyin.tone import PROFILE, tone_profile

    root = tmp_path / "export"
    store = WeChatStore(tmp_path / "state")
    export(
        root,
        {
            "合成甲": [
                msg(0, "them", "不要统计对方这句"),
                msg(1, "me", "今天泡茶呀！"),
                msg(2, "me", "刚泡好🙂"),
                msg(3, "them", "喝茶"),
                msg(4, "me", "[表情]", 47),
                msg(5, "me", "[系统]", 10000),
            ],
            "合成乙": [msg(0, "me", "排除用的长句")],
        },
    )
    import_wechat(store, root)
    (store.root / "config.toml").write_text(
        'exclude_contacts = ["C2"]\n[relationships]\n"C1" = "朋友"\n'
    )
    stats = tone_profile(store)
    assert (stats["sent"], stats["text"], stats["single_runs"], stats["burst_runs"]) == (3, 2, 1, 1)
    assert stats["groups"][1] == {"sent": 3, "text": 2, "examples": 2}
    path = store.root / PROFILE
    first = path.read_bytes()
    assert "今天泡茶呀！".encode() in first
    assert "排除用的长句".encode() not in first
    assert "不要统计对方这句".encode() not in first
    assert path.stat().st_mode & 0o777 == 0o600
    assert tone_profile(store) == stats
    assert path.read_bytes() == first


def test_brief_has_recent_context_and_open_arrangements(tmp_path):
    from zhiyin.brief import contact_brief

    root = tmp_path / "export"
    store = WeChatStore(tmp_path / "state")
    export(
        root,
        {
            "合成甲": [
                msg(0, "them", "周末一起吃饭吗"),
                msg(1, "me", "好呀"),
                msg(2, "them", "不吃了，改天再说"),
                msg(3, "me", "下次一起看展？"),
                msg(4, "them", "好"),
                msg(5, "them", "[贴图]", 47),
            ],
        },
    )
    import_wechat(store, root)
    result = contact_brief(store, "C1")
    assert result["contact"] == "合成甲"
    assert result["duration_days"] == 0
    assert result["message_count"] == 6
    assert result["recent"][-1]["text"] is None
    assert [item["topic"] for item in result["open_arrangements"]] == ["出门"]
    assert result["open_arrangements"][0]["status"] == "已回应，未见落实"
    (store.root / "config.toml").write_text('exclude_contacts = ["C1"]\n')
    assert contact_brief(store, "合成甲") == {"found": False}


def test_reply_search_prefers_contact_then_relationship_and_forgets_exclusion(tmp_path):
    from zhiyin.my_replies import ReplySearch

    class Encoder:
        embeds = 0

        def embed(self, texts, *, batch_size):
            self.embeds += len(texts)
            return ([1, 0] for _ in texts)

        def query_embed(self, text):
            return [[0, 1] if text == "合成不相关" else [1, 0]]

    root = tmp_path / "export"
    store = WeChatStore(tmp_path / "state")
    export(
        root,
        {
            "合成甲": [msg(0, "them", "泡茶"), msg(1, "me", "选绿茶"), msg(2, "me", "少泡会")],
            "合成乙": [msg(0, "them", "喝茶"), msg(3, "me", "我喜欢红茶")],
            "合成丙": [msg(0, "them", "泡新茶"), msg(5, "me", "我喜欢乌龙")],
        },
    )
    import_wechat(store, root)
    config = store.root / "config.toml"
    config.write_text('[relationships]\n"C1" = "朋友"\n"C2" = "朋友"\n')
    encoder = Encoder()
    search = ReplySearch(store, encoder=encoder)
    rows = search.search("泡茶", "合成甲")
    assert [row["contact"] for row in rows] == ["合成甲", "合成乙", "合成丙"]
    assert (rows[0]["date"], rows[0]["reply"]) == ("2026-01-01", "选绿茶")
    assert encoder.embeds == 3
    assert ReplySearch(store, encoder=encoder).search("合成不相关") == []
    assert encoder.embeds == 3
    config.write_text('exclude_contacts = ["C1"]\n')
    assert search.search("泡茶", "合成甲") == []
    assert [row["contact"] for row in search.search("泡茶")] == ["合成丙", "合成乙"]
