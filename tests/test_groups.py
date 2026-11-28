"""Synthetic group ingestion and opt-in MCP acceptance."""

import asyncio
import json

from test_wechat import export, msg

from zhiyin.context import ChatContext
from zhiyin.server import create_server
from zhiyin.wechat import WeChatStore, import_wechat


def test_groups_are_opt_in_and_never_reply_samples(tmp_path):
    root = tmp_path / "export"
    rows = [
        dict(
            msg(0, "them", "合成群内容"),
            chat_type="group",
            sender_id="member-a",
            sender_name="合成成员甲",
        ),
        dict(
            msg(0, "them", "合成群内容"),
            chat_type="group",
            sender_id="member-b",
            sender_name="合成成员乙",
        ),
        dict(msg(1, "me", "合成回应"), chat_type="group", sender_id="owner"),
        dict(msg(2, "unknown", "未知成员"), chat_type="group", sender_id=None),
    ]
    export(
        root, {"合成群": rows, "合成私聊": [msg(0, "them", "私聊内容"), msg(1, "me", "私聊回应")]}
    )
    path = root / "导出清单.json"
    manifest = json.loads(path.read_text())
    manifest["conversations"][0].update(conversation_id="synthetic@chatroom", chat_type="group")
    del manifest["conversations"][0]["code"]
    path.write_text(json.dumps(manifest))
    store = WeChatStore(tmp_path / "state")
    assert import_wechat(store, root)["inserted"] == 6
    assert import_wechat(store, root)["inserted"] == 0
    ctx = ChatContext(store)
    assert len(ctx.list_chats()) == 1
    groups = ctx.list_chats(chat_type="group", reveal_names=True)
    assert len(groups) == 1 and groups[0]["name"] == "合成群"
    assert len(ctx.list_chats(chat_type="all")) == 2
    code = groups[0]["chat"]
    assert ctx.read_window(code, line=1)["found"] is False
    result = ctx.read_window(code, line=2, before=1, after=2, chat_type="group")
    bubbles = result["messages"]
    assert [m["line"] for m in bubbles] == [1, 2, 3, 4]
    assert bubbles[0]["member"] != bubbles[1]["member"]
    assert bubbles[3]["member"] == "unknown"
    assert "合成成员甲" not in json.dumps(result, ensure_ascii=False)
    named = ctx.read_window(code, line=1, before=0, after=0, chat_type="group", reveal_names=True)
    assert named["messages"][0]["member_name"] == "合成成员甲"
    assert len(ctx.search_messages("合成群", chat_type="group")) == 2
    assert not ctx.search_messages("合成群")
    assert ctx.chat_stats(code)["found"] is False
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM reply_pairs").fetchone()[0] == 1
    server = create_server(store=store)
    tools = asyncio.run(server.list_tools())
    for tool in tools:
        if tool.name in {"list_chats", "search_messages", "read_window"}:
            assert tool.inputSchema["properties"]["chat_type"]["default"] == "private"
    reply = asyncio.run(
        server.call_tool("read_window", {"chat": code, "line": 1, "chat_type": "group"})
    )
    assert reply
    store._write("config.toml", f'exclude_contacts = ["{code}"]\n')
    assert ctx.list_chats(chat_type="group") == []


def test_partial_group_import_keeps_private_metadata(tmp_path):
    private = tmp_path / "private-export"
    export(private, {"合成朋友": [msg(0, "them", "合成私聊")]})
    path = private / "导出清单.json"
    manifest = json.loads(path.read_text())
    manifest["conversations"][0]["groups"] = ["朋友 / 读书"]
    manifest["conversations"][0]["sequence"] = 1
    path.write_text(json.dumps(manifest))
    store = WeChatStore(tmp_path / "state")
    import_wechat(store, private)
    group = tmp_path / "group-export"
    export(
        group, {"合成群": [dict(msg(1, "them", "讨论主题"), chat_type="group", sender_id="member")]}
    )
    path = group / "导出清单.json"
    manifest = json.loads(path.read_text())
    manifest["conversations"][0].update(
        conversation_id="group@chatroom", code="G1", chat_type="group"
    )
    path.write_text(json.dumps(manifest))
    import_wechat(store, group)
    ctx = ChatContext(store)
    assert ctx.list_chats()[0]["groups"] == ["朋友 / 读书"]

    async def check():
        import os
        import sys

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "zhiyin.server"],
            env={**os.environ, "ZHIYIN_STATE_DIR": str(store.root)},
        )
        async with (
            stdio_client(params) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            result = await session.call_tool(
                "read_window", {"chat": "G1", "line": 1, "chat_type": "group"}
            )
            assert not result.isError
            payload = result.structuredContent or json.loads(result.content[0].text)
            assert payload["found"] is True
            assert payload["messages"][0]["text"] == "讨论主题"
            hidden = await session.call_tool("list_chats", {})
            assert "G1" not in str(hidden.structuredContent)

    asyncio.run(check())


def test_raw_private_export_uses_same_code_for_metadata(tmp_path):
    source = tmp_path / "export"
    export(source, {"合成联系人": [msg(0, "them", "测试正文")]})
    path = source / "导出清单.json"
    manifest = json.loads(path.read_text())
    del manifest["conversations"][0]["code"]
    manifest["conversations"][0]["groups"] = ["朋友 / 读书"]
    manifest["conversations"][0]["sequence"] = 1
    path.write_text(json.dumps(manifest))
    store = WeChatStore(tmp_path / "state")
    import_wechat(store, source)
    chat = ChatContext(store).list_chats()[0]
    assert chat["chat"].startswith("A")
    assert chat["groups"] == ["朋友 / 读书"]
