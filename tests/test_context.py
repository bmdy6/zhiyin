"""Synthetic-only acceptance for the Agent context tools."""

import json
from datetime import datetime, timedelta

import pytest
from test_wechat import export

from zhiyin.wechat import WeChatStore, import_wechat


@pytest.fixture
def context_store(tmp_path):
    from zhiyin.context import ChatContext

    source = tmp_path / "export"
    start = datetime.fromisoformat("2026-01-01T09:00:00+08:00")
    entries = [
        (0, "them", "合成甲今天很难过"),
        (1, "me", "嗯"),
        (2, "them", "我最近在学画画"),
        (3, "me", "我今天也很忙"),
        (4, "me", "第一条合成补充"),
        (5, "me", "第二条合成补充"),
        (6, "me", "第三条合成补充"),
        (7, "me", "合成乙 13800138000 a@example.com 123456 wxid_synthetic"),
        (8, "them", "你总是这样，别烦我"),
        (368, "me", "合成问句吗？"),
        (728, "them", "合成新段"),
        (729, "me", "晚安"),
    ]
    rows = [
        {
            "time": (start + timedelta(minutes=t)).isoformat(),
            "sender": s,
            "text": text,
            "type": 1,
            "subtype": None,
        }
        for t, s, text in entries
    ]
    export(source, {"合成甲": rows, "合成乙": rows[:2], "合成服务": rows})
    manifest_path = source / "导出清单.json"
    manifest = json.loads(manifest_path.read_text())
    for entry, group in zip(manifest["conversations"], ["朋友", "同学", "商家 / 服务"]):
        entry["groups"] = [group]
    manifest_path.write_text(json.dumps(manifest))
    (source / "代号映射-第9轮.json").write_text(
        json.dumps(
            {
                "codes": {"wxid_synthetic": "C2"},
                "people": {"wxid_synthetic": {"username": "wxid_synthetic", "nick_name": "合成乙"}},
            }
        )
    )
    store = WeChatStore(tmp_path / "state")
    import_wechat(store, source)
    return ChatContext(store), store


def test_list_privacy_filters_and_no_writes(context_store):
    ctx, store = context_store
    before = {p.name: p.read_bytes() for p in store.root.iterdir()}
    rows = ctx.list_chats()
    assert [r["chat"] for r in rows] == ["C1", "C2"]
    assert rows[0]["messages"] == 12
    assert rows[0]["recent90"] == 12
    assert len(ctx.list_chats(group="同学")) == 1
    assert len(ctx.list_chats(min_messages=3)) == 1
    assert len(ctx.list_chats(active_days=1)) == 2
    assert ctx.list_chats(reveal_names=True)[0]["name"] == "合成甲"
    assert {p.name: p.read_bytes() for p in store.root.iterdir()} == before


def test_ambiguous_imported_contacts_preserve_body_and_labels(tmp_path):
    from test_wechat import msg

    from zhiyin.context import ChatContext

    source = tmp_path / "export"
    body = "我在等同学（明天见），合成甲说他很难过"
    export(
        source,
        {name: [msg(0, "them", body)] for name in ["我", "在", "同学", "（", "合成甲", "朋友"]},
    )
    path = source / "导出清单.json"
    manifest = json.loads(path.read_text())
    for entry in manifest["conversations"]:
        entry["groups"] = ["同学", "哥们 / 人脉（前同事）", "合成甲"]
        entry["relationship"] = "朋友"
    path.write_text(json.dumps(manifest))
    store = WeChatStore(tmp_path / "state")
    import_wechat(store, source)
    ctx = ChatContext(store)
    before = {p.name: p.read_bytes() for p in store.root.iterdir()}
    expected_groups = sorted(manifest["conversations"][0]["groups"])
    for row in ctx.list_chats():
        assert row["groups"] == expected_groups
        assert row["relationship"] == "朋友"
    assert ctx.chat_stats("C1")["relationship"] == "朋友"
    window = ctx.read_window("C1", line=1)
    assert window["groups"] == expected_groups
    assert window["messages"][0]["text"] == body.replace("合成甲", "C5")
    assert ctx.search_messages("难过", "C1")[0]["text"] == body.replace("合成甲", "C5")
    assert {p.name: p.read_bytes() for p in store.root.iterdir()} == before


def test_statistics_have_explicit_denominators(context_store):
    ctx, _ = context_store
    stats = ctx.chat_stats("C1")
    assert stats["segments"] == 3
    assert stats["me_open_ratio"] == pytest.approx(1 / 3)
    assert stats["burst_ge3"] == stats["burst_ge5"] == 1
    assert stats["ends_with_me"] == 2
    assert stats["ends_with_me_ratio"] == pytest.approx(2 / 3)
    assert stats["me_reply_median_seconds"] == 60
    assert stats["them_reply_median_seconds"] == 60
    assert stats["me_question_ratio"] == pytest.approx(1 / 8)
    assert ctx.chat_stats()["chats"] == 2


def test_search_window_lines_redaction_and_six_hour_boundary(context_store):
    ctx, _ = context_store
    hits = ctx.search_messages("13800138000", chat="C1", sender="me")
    assert len(hits) == 1 and hits[0]["line"] == 8
    serialized = json.dumps(hits, ensure_ascii=False)
    for private in ["合成乙", "13800138000", "a@example.com", "123456", "wxid_synthetic"]:
        assert private not in serialized
    assert "C2" in serialized
    window = ctx.read_window("C1", line=10, before=1, after=1)
    assert [r["line"] for r in window["messages"]] == [9, 10, 11]
    assert window["messages"][1]["segment_start"] is True
    assert window["messages"][2]["segment_start"] is True
    assert (
        ctx.read_window("C1", time=hits[0]["time"], before=0, after=0)["messages"][0]["line"] == 8
    )
    assert len(ctx.search_messages("合成", since="2026-01-02", until="2026-01-03")) == 0
    with pytest.raises(ValueError):
        ctx.read_window("C1", line=1, time="2026-01-01")


@pytest.mark.parametrize(
    "kind", ["emotion_bid", "burst", "unanswered_end", "self_pivot", "conflict"]
)
def test_each_candidate_is_bounded_and_marked_as_heuristic(context_store, kind):
    ctx, _ = context_store
    moments = ctx.find_moments(kind, chat="C1", limit=1)
    assert len(moments) == 1
    assert "规则候选，需看原文判断" in moments[0]["rule"]
    assert all("line" in m for m in moments[0]["messages"])
    if kind == "emotion_bid":
        assert moments[0]["short_reply"] is True
        assert moments[0]["reply_line"] == 2
    if kind == "unanswered_end":
        assert moments[0]["line"] == 10


@pytest.mark.parametrize(
    "kind", ["emotion_bid", "burst", "unanswered_end", "self_pivot", "conflict"]
)
def test_all_candidate_kinds_preserve_ambiguous_words(context_store, kind):
    ctx, store = context_store
    ambiguous = ["我", "在", "同学", "（"]
    with store.connect(write=True) as db:
        db.executemany(
            "INSERT INTO private_aliases VALUES (?, ?)",
            [(word, f"P90{i}") for i, word in enumerate(ambiguous)],
        )
    with ctx.snapshot("C1") as (_, _, messages, _):
        original = {row["line"]: row["text"] for rows in messages.values() for row in rows}
    moments = ctx.find_moments(kind, chat="C1", limit=1)
    assert len(moments) == 1
    for message in moments[0]["messages"]:
        for word in ambiguous:
            assert message["text"].count(word) == original[message["line"]].count(word)
    assert not any(f"P90{i}" in json.dumps(moments) for i in range(len(ambiguous)))


def test_config_rules_and_readonly_reply_context(context_store):
    ctx, store = context_store
    store._write("config.toml", '[context]\nburst_n = 6\nemotion_words = ["无匹配"]\n')
    assert ctx.find_moments("burst") == []
    assert ctx.find_moments("emotion_bid") == []
    store._write("我的问题.md", "合成手改问题")
    before = {p.name: p.read_bytes() for p in store.root.iterdir()}
    result = ctx.reply_context("C1", "合成甲今天很难过")
    assert result["chat"] == "C1"
    assert result["problems"] == "合成手改问题"
    assert result["historical_replies"][0]["reply"] == "嗯"
    assert "合成甲" not in json.dumps(result, ensure_ascii=False)
    assert {p.name: p.read_bytes() for p in store.root.iterdir()} == before


def test_missing_archive_is_readonly(tmp_path):
    from zhiyin.context import ChatContext

    store = WeChatStore(tmp_path / "absent")
    ctx = ChatContext(store)
    assert ctx.list_chats() == []
    assert ctx.chat_stats()["messages"] == 0
    assert ctx.find_moments("conflict") == []
    assert not store.root.exists()


def test_default_reveal_is_off_and_schema_is_readonly(context_store):
    import asyncio

    from zhiyin.server import create_server

    _, store = context_store
    server = create_server(store=store)
    tools = asyncio.run(server.list_tools())
    assert "draft_reply" not in {t.name for t in tools}
    assert "set_relationship" not in {t.name for t in tools}
    assert all(t.annotations.readOnlyHint and not t.annotations.openWorldHint for t in tools)
    for tool in tools:
        props = tool.inputSchema.get("properties", {})
        if tool.name in {
            "list_chats",
            "chat_stats",
            "search_messages",
            "read_window",
            "find_moments",
            "reply_context",
        }:
            assert props["reveal_names"]["default"] is False
            assert "reveal_names=true" in tool.description


def test_reveal_names_does_not_reveal_phone_or_email(context_store):
    ctx, _ = context_store
    hit = ctx.search_messages("13800138000", chat="C1", reveal_names=True)[0]
    assert "合成乙" in hit["text"]
    assert "13800138000" not in hit["text"]
    assert "a@example.com" not in hit["text"]
    assert ctx.list_chats()[0]["first_time"].startswith("2026-")


def test_group_chats_unknown_names_and_exclusions_are_not_returned(context_store):
    ctx, store = context_store
    store._write("config.toml", 'exclude_contacts = ["C1"]\n')
    assert ctx.chat_stats("C1")["found"] is False
    assert ctx.search_messages("合成", chat="C1") == []
    assert ctx.reply_context("C3", "合成")["found"] is False
    assert ctx.search_my_replies("合成甲今天很难过", "C3") == []
    with store.connect(write=True) as db:
        db.execute("INSERT INTO contacts VALUES ('synthetic@chatroom','G1','合成群','朋友')")
    assert "G1" not in {c["chat"] for c in ctx.list_chats()}


def test_same_second_source_lines_and_empty_records_are_preserved(tmp_path):
    from test_wechat import msg

    from zhiyin.context import ChatContext

    source = tmp_path / "export"
    export(source, {"合成": [msg(0, "them", "合成问题"), msg(0, "me", "合成回答")]})
    p = source / "C1/chat.jsonl"
    p.write_text("\n" + p.read_text())
    store = WeChatStore(tmp_path / "state")
    import_wechat(store, source)
    ctx = ChatContext(store)
    rows = ctx.read_window("C1", line=2)["messages"]
    assert [row["line"] for row in rows] == [2, 3]
    assert [row["sender"] for row in rows] == ["them", "me"]
    assert ctx.chat_stats()["me_reply_median_seconds"] == 0


def test_errors_do_not_echo_input_or_storage_details(context_store, caplog):
    import asyncio

    from mcp.server.fastmcp.exceptions import ToolError

    from zhiyin.server import create_server

    _, store = context_store
    server = create_server(store=store)
    with pytest.raises(ToolError) as error:
        asyncio.run(server.call_tool("read_window", {"chat": "C1", "time": "合成私密哨兵"}))
    assert "合成私密哨兵" not in str(error.value)
    assert "合成私密哨兵" not in caplog.text


def test_long_code_survives_text_redaction():
    from zhiyin.context_privacy import Redactor

    assert Redactor({"合成甲": "P8608"}).text("合成甲 123456") == "P8608 [隐私]"


def test_emotion_bid_keeps_next_reply_after_large_incoming_run(tmp_path):
    from test_wechat import msg

    from zhiyin.context import ChatContext

    source = tmp_path / "export"
    rows = (
        [msg(0, "them", "我很难过")]
        + [msg(0, "them", "合成补充") for _ in range(110)]
        + [msg(1, "me", "嗯")]
    )
    export(source, {"合成": rows})
    store = WeChatStore(tmp_path / "state")
    import_wechat(store, source)
    moment = ChatContext(store).find_moments("emotion_bid")[0]
    assert moment["reply_line"] == moment["next_reply"]["line"] == 112
    assert moment["next_reply"]["text"] == "嗯"
    assert moment["window_truncated"] is True


def test_legacy_brief_keeps_fields_without_names(context_store):
    ctx, _ = context_store
    brief = ctx.contact_brief("C1")
    assert brief["contact"] == "C1"
    assert brief["message_count"] == 12
    assert "recent" in brief and "open_arrangements" in brief
    assert "合成甲" not in json.dumps(brief, ensure_ascii=False)


def test_startup_error_does_not_echo_private_config(tmp_path):
    import subprocess
    import sys

    state = tmp_path / "state"
    state.mkdir()
    (state / "config.toml").write_text('exclude_contacts = ["合成配置私密哨兵"\n')
    result = subprocess.run(
        [sys.executable, "-m", "zhiyin.server", "--state-dir", str(state)],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1
    assert "合成配置私密哨兵" not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr


def test_reveal_names_never_reveals_custom_wechat_identifiers():
    from zhiyin.context_privacy import Redactor

    redactor = Redactor({"合成甲": "C1", "custom_wechat_id": "C1"}, {"custom_wechat_id"})
    assert redactor.text("合成甲 custom_wechat_id", reveal_names=True) == "合成甲 C1"


def test_numeric_secrets_are_masked_inside_other_words():
    from zhiyin.context_privacy import Redactor

    assert "123456" not in Redactor({}).text("验证码code123456")
    assert Redactor({"合成甲": "P8608"}).text("合成甲") == "P8608"


def test_readonly_retrieval_does_not_create_tokenizer_cache(context_store, tmp_path, monkeypatch):
    import jieba

    from zhiyin import retrieval

    ctx, _ = context_store
    tokenizer = jieba.Tokenizer()
    cache_dir = tmp_path / "empty-cache"
    cache_dir.mkdir()
    tokenizer.tmp_dir = str(cache_dir)
    monkeypatch.setattr(retrieval, "JIEBA", tokenizer)
    assert ctx.search_my_replies("合成甲今天很难过", "C1")
    assert list(cache_dir.iterdir()) == []


def test_import_initializes_rule_config_without_overwriting_customizations(context_store, tmp_path):
    import tomllib

    from zhiyin.context import DEFAULTS

    ctx, store = context_store
    config = store._path("config.toml")
    parsed = tomllib.loads(config.read_text())
    assert parsed["context"]["emotion_words"] == DEFAULTS["emotion_words"]
    assert parsed["enable_draft_reply"] is False
    config.write_text('# 合成保留注释\n[context]\nburst_n=8\nemotion_words=["合成情绪"]\n')
    store._write("我的问题.md", "合成手改问题")
    import_wechat(store, tmp_path / "export")
    assert ctx.settings()["burst_n"] == 8
    assert ctx.settings()["emotion_words"] == ["合成情绪"]
    assert "# 合成保留注释" in config.read_text()
    assert store._path("我的问题.md").read_text() == "合成手改问题"
