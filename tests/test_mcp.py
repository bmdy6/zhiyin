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
