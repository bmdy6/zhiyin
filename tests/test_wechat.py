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
    from zhiyin.relationships import set_relationship

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

    set_relationship(store, "C1", "办事")
    set_relationship(store, "C3", "办事")
    rows = search.search("泡茶", "合成甲")
    assert [row["contact"] for row in rows] == ["合成甲", "合成丙", "合成乙"]
    assert encoder.embeds == 3
    config.write_text('exclude_contacts = ["C1"]\n')
    assert search.search("泡茶", "合成甲") == []
    assert [row["contact"] for row in search.search("泡茶")] == ["合成丙", "合成乙"]


def test_confirmed_relationship_rebuilds_tone_and_preserves_config(tmp_path):
    import tomllib

    from zhiyin.relationships import set_relationship
    from zhiyin.tone import PROFILE, tone_profile

    store = WeChatStore(tmp_path / "state")
    root = tmp_path / "export"
    export(
        root,
        {
            '合成备注 "甲"': [msg(0, "me", "合成办事短句")],
            "合成乙": [msg(1, "me", "合成未分组短句")],
        },
    )
    import_wechat(store, root)
    tone_profile(store)
    profile = store.root / PROFILE
    assert "合成办事短句" in profile.read_text().split("## 未分类\n")[1].split("\n## ")[0]

    config = store.root / "config.toml"
    config.write_text(
        '# 合成设置\nexclude_contacts = ["C9"]\n'
        "[relationships]\n'C1' = '陌生人或办事' # 合成旧配置\n"
        "[settings]\nenabled = true\n"
    )
    assert set_relationship(store, '合成备注 "甲"', "办事") == {
        "contact_id": "synthetic-1",
        "relationship": "办事",
    }
    saved = config.read_text()
    assert "# 合成旧配置" in saved
    assert "# 合成设置" in saved
    assert tomllib.loads(saved) == {
        "exclude_contacts": ["C9"],
        "relationships": {"C1": "陌生人或办事", "synthetic-1": "办事"},
        "settings": {"enabled": True},
    }
    assert config.stat().st_mode & 0o777 == 0o600
    tone_profile(store)
    grouped = profile.read_text()
    assert "合成办事短句" in grouped.split("## 办事\n")[1].split("\n## ")[0]
    assert "合成未分组短句" in grouped.split("## 未分类\n")[1].split("\n## ")[0]
    assert "## 其他\n" in grouped

    for contact, relationship in [("不存在的合成编号", "其他"), ("C1", "未分类")]:
        with pytest.raises(ValueError):
            set_relationship(store, contact, relationship)
        assert config.read_text() == saved
    set_relationship(store, "C1", "其他")
    with store.connect() as db:
        assert store.contact(db, "C1")["relationship"] == "其他"


def test_suggest_relationships_leaves_config_and_index_untouched(tmp_path):
    from zhiyin.relationships import suggest_relationships

    store = WeChatStore(tmp_path / "state")
    assert suggest_relationships(store) == []
    assert not store.root.exists()

    root = tmp_path / "export"
    export(
        root,
        {
            "合成妈妈": [msg(0, "me", "妈，今天做合成实验")],
            "合成甲": [msg(0, "me", "老同学，好久不见")],
            "合成物业": [msg(0, "me", "合成请问")],
            "合成同事": [msg(0, "me", "发小，合成集合时间")],
            "合成客服": [msg(0, "me", "合成请求")],
            "合成乙": [msg(0, "me", "合成普通聊天")],
            "合成发小": [
                msg(0, "me", "合成早上好"),
                {**msg(0, "them", "合成问候"), "time": "2026-01-02T09:00:00+08:00"},
            ],
        },
    )
    import_wechat(store, root)
    config = store.root / "config.toml"
    config.write_text('exclude_contacts = ["C5"]\n[relationships]\n"C3" = "办事"\n')
    before = {path.name: path.read_bytes() for path in store.root.iterdir()}
    suggestions = suggest_relationships(store)
    assert suggestions == [
        {
            "contact_id": "synthetic-7",
            "code": "C7",
            "relationship": "朋友",
            "evidence": ["备注关键词"],
            "active_days": 2,
        },
        {
            "contact_id": "synthetic-1",
            "code": "C1",
            "relationship": "家人",
            "evidence": ["备注关键词", "聊天称呼"],
            "active_days": 1,
        },
        {
            "contact_id": "synthetic-2",
            "code": "C2",
            "relationship": "朋友",
            "evidence": ["聊天称呼"],
            "active_days": 1,
        },
    ]
    assert {path.name: path.read_bytes() for path in store.root.iterdir()} == before


def test_legacy_relationships_work_without_reimport(tmp_path):
    root = tmp_path / "export"
    store = WeChatStore(tmp_path / "state")
    export(root, {"合成办事": [], "合成未分组": []})
    manifest_path = root / "导出清单.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["conversations"][0]["relationship"] = "陌生人或办事"
    manifest_path.write_text(json.dumps(manifest))
    import_wechat(store, root)
    with store.connect() as db:
        stored = db.execute("SELECT relationship FROM contacts WHERE code='C1'").fetchone()[0]
        assert stored == "陌生人或办事"
        assert store.contact(db, "C1")["relationship"] == "办事"
        assert store.contact(db, "C2")["relationship"] == "未分类"
    (store.root / "config.toml").write_text('[relationships]\n"合成未分组" = "陌生人或办事"\n')
    with store.connect() as db:
        assert store.contact(db, "C2")["relationship"] == "办事"


def test_set_relationship_only_targets_the_resolved_id(tmp_path):
    from zhiyin.relationships import set_relationship

    store = WeChatStore(tmp_path / "state")
    root = tmp_path / "export"
    export(root, {"合成甲": [], "synthetic-1": [], "C2": []})
    import_wechat(store, root)
    config = store.root / "config.toml"
    before = config.read_bytes()
    # C2 is both the second contact's code and the third contact's label.
    with pytest.raises(ValueError):
        set_relationship(store, "C2", "朋友")
    assert config.read_bytes() == before

    set_relationship(store, "synthetic-1", "其他")
    with store.connect() as db:
        assert store.contact(db, "synthetic-1")["relationship"] == "其他"
        assert store.contact(db, "synthetic-2")["relationship"] == "未分类"


def test_export_unknown_direction_is_only_allowed_for_system_messages(tmp_path):
    from zhiyin.context import ChatContext

    root = tmp_path / "export"
    store = WeChatStore(tmp_path / "state")
    export(root, {"合成": [msg(0, "unknown", "合成系统提示", 10000), msg(1, "me", "合成文本")]})
    stats = import_wechat(store, root)
    assert stats["messages"] == 2
    assert ChatContext(store).chat_stats()["messages"] == 1
    assert ChatContext(store).read_window("C1", line=2)["messages"][0]["line"] == 2
    export(root, {"合成": [msg(0, "unknown", "合成普通文本")]})
    with pytest.raises(ValueError):
        import_wechat(store, root)
