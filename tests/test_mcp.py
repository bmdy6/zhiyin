import asyncio
from pathlib import Path

import pytest

from scripts.smoke_mcp import run


def test_real_stdio_server_contract():
    if not (Path(__file__).resolve().parents[1] / "data/cache/models").is_dir():
        pytest.skip("Run scripts/build_index.py once to download the local model")
    result = asyncio.run(run())
    assert result["hits_at_5"] == 42
    assert result["transport"] == "stdio"


def test_stdio_wechat_tools_use_only_synthetic_inputs(tmp_path):
    import sys
    import tempfile

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from test_wechat import export, msg

    from zhiyin.wechat import WeChatStore, import_wechat

    model_cache = Path(__file__).resolve().parents[1] / "data/cache/models"
    if not model_cache.is_dir():
        pytest.skip("Run scripts/build_index.py once to download the local model")
    state = tmp_path / "state"
    root = tmp_path / "export"
    export(root, {"合成甲": [msg(0, "them", "绿茶泡好了"), msg(1, "me", "闻起来真香")]})
    import_wechat(WeChatStore(state), root)
    (state / "config.toml").write_text("enable_draft_reply = true\n")

    async def check():
        params = StdioServerParameters(
            command=sys.executable,
            env={"TMPDIR": tempfile.gettempdir()},
            args=[
                "-m",
                "zhiyin.server",
                "--state-dir",
                str(state),
                "--model-cache",
                str(model_cache),
                "--index-dir",
                str(tmp_path / "absent-public-sample-index"),
            ],
        )
        async with (
            stdio_client(params) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            ready = await session.initialize()
            assert ready.serverInfo.name == "zhiyin"
            brief = await session.call_tool("contact_brief", {"contact": "C1"})
            assert not brief.isError
            assert brief.structuredContent["chat"] == "C1"
            assert "合成甲" not in str(brief.structuredContent)
            matches = await session.call_tool(
                "search_my_replies", {"message": "绿茶泡好了", "contact": "C1"}
            )
            assert not matches.isError
            assert matches.structuredContent["result"][0]["reply"] == "闻起来真香"
            direct = await session.call_tool(
                "draft_reply", {"contact": "C1", "message": "今天能转账吗"}
            )
            assert not direct.isError
            assert direct.structuredContent["status"] == "这条你自己回"
            invalid = await session.call_tool(
                "draft_reply", {"contact": ["合成隐私占位"], "message": "有效的合成内容"}
            )
            assert invalid.isError
            assert "合成隐私占位" not in str(invalid.content)

    asyncio.run(check())


def test_stdio_relationship_tools_use_only_synthetic_inputs(tmp_path):
    import subprocess
    import sys
    import tempfile

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from test_wechat import export, msg

    from zhiyin.tone import PROFILE
    from zhiyin.wechat import WeChatStore, import_wechat

    state = tmp_path / "state"
    root = tmp_path / "export"
    export(root, {"合成同事": [msg(0, "me", "合成排期")]})
    import_wechat(WeChatStore(state), root)
    config = state / "config.toml"
    config.write_text("enable_relationship_writes = true\n")
    before = config.read_bytes()

    async def check():
        params = StdioServerParameters(
            command=sys.executable,
            env={"TMPDIR": tempfile.gettempdir()},
            args=["-m", "zhiyin.server", "--state-dir", str(state)],
        )
        async with (
            stdio_client(params) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            assert tools["suggest_relationships"].annotations.readOnlyHint is True
            assert tools["set_relationship"].annotations.readOnlyHint is False

            suggestions = await session.call_tool("suggest_relationships", {})
            assert not suggestions.isError
            assert suggestions.structuredContent["result"][0]["relationship"] == "同事"
            assert config.read_bytes() == before

            applied = await session.call_tool(
                "set_relationship", {"contact": "C1", "relationship": "同事"}
            )
            assert not applied.isError
            assert applied.structuredContent["contact_id"] == "synthetic-1"
            brief = await session.call_tool("contact_brief", {"contact": "C1"})
            assert brief.structuredContent["relationship"] == "同事"

    asyncio.run(check())
    subprocess.run(
        [sys.executable, "-m", "zhiyin.cli", "tone_profile", "--state-dir", str(state)],
        check=True,
        capture_output=True,
    )
    assert "合成排期" in (state / PROFILE).read_text().split("## 同事\n")[1].split("\n## ")[0]


def test_stdio_agent_context_tools(context_store):
    import sys
    import tempfile

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    _, store = context_store

    async def check():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "zhiyin.server", "--state-dir", str(store.root)],
            env={"TMPDIR": tempfile.gettempdir()},
        )
        async with (
            stdio_client(params) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            tools = {t.name: t for t in (await session.list_tools()).tools}
            assert "draft_reply" not in tools and "set_relationship" not in tools
            for name, arguments in [
                ("list_chats", {}),
                ("chat_stats", {}),
                ("search_messages", {"query": "难过", "sender": "them"}),
                ("read_window", {"chat": "C1", "line": 1}),
                ("reply_context", {"chat": "C1", "message": "合成甲今天很难过"}),
                *[
                    ("find_moments", {"kind": kind, "chat": "C1"})
                    for kind in ["emotion_bid", "burst", "unanswered_end", "self_pivot", "conflict"]
                ],
            ]:
                assert tools[name].annotations.readOnlyHint
                result = await session.call_tool(name, arguments)
                assert not result.isError
                assert "合成甲" not in str(result.structuredContent)
                assert "合成乙" not in str(result.structuredContent)
            revealed = await session.call_tool("list_chats", {"reveal_names": True})
            assert "合成甲" in str(revealed.structuredContent)
            for arguments in [{"chat": "C1"}, {"chat": "C1", "line": 1, "time": "2026-01-01"}]:
                invalid = await session.call_tool("read_window", arguments)
                assert invalid.isError
            invalid = await session.call_tool("find_moments", {"kind": "合成私密哨兵"})
            assert invalid.isError and "合成私密哨兵" not in str(invalid.content)

    asyncio.run(check())
