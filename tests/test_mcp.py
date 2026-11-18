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
            assert brief.structuredContent["contact"] == "合成甲"
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
