"""Stdio context acceptance with synthetic data, no models and no private output."""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from zhiyin.wechat import WeChatStore, import_wechat


def synthetic_archive(root: Path) -> WeChatStore:
    source = root / "export"
    (source / "C1").mkdir(parents=True)
    (source / "导出清单.json").write_text(
        json.dumps(
            {
                "conversations": [
                    {
                        "label": "合成联系人",
                        "conversation_id": "synthetic-1",
                        "code": "C1",
                        "folder": "C1",
                        "groups": ["朋友"],
                    }
                ]
            }
        )
    )
    rows = [
        {
            "time": f"2026-01-01T09:0{i}:00+08:00",
            "sender": "them" if i == 0 else "me",
            "type": 1,
            "subtype": None,
            "text": "我难过，别烦我" if i == 0 else "我也在做合成测试",
        }
        for i in range(7)
    ]
    (source / "C1/chat.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    store = WeChatStore(root / "state")
    import_wechat(store, source)
    return store


async def run() -> dict:
    with tempfile.TemporaryDirectory() as directory:
        store = synthetic_archive(Path(directory))
        before = {p.name: p.read_bytes() for p in store.root.iterdir()}
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "zhiyin.server", "--state-dir", str(store.root)],
            env={"TMPDIR": tempfile.gettempdir(), "PATH": os.environ.get("PATH", "")},
        )
        checked = []
        async with (
            stdio_client(params) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            tools = (await session.list_tools()).tools
            assert all(
                t.annotations.readOnlyHint and not t.annotations.openWorldHint for t in tools
            )
            for name, arguments in [
                ("list_chats", {}),
                ("chat_stats", {}),
                ("search_messages", {"query": "难过"}),
                ("read_window", {"chat": "C1", "line": 1}),
                ("reply_context", {"chat": "C1", "message": "我难过，别烦我"}),
                *[
                    ("find_moments", {"kind": kind})
                    for kind in ["emotion_bid", "burst", "unanswered_end", "self_pivot", "conflict"]
                ],
            ]:
                response = await session.call_tool(name, arguments)
                assert not response.isError
                assert "合成联系人" not in str(response.structuredContent)
                if name == "find_moments":
                    assert response.structuredContent["result"]
                checked.append(name)
        assert before == {p.name: p.read_bytes() for p in store.root.iterdir()}
        return {
            "transport": "stdio",
            "calls": len(checked),
            "tools": sorted(set(checked)),
            "candidate_kinds": 5,
            "readonly": True,
            "synthetic": True,
        }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), ensure_ascii=False))
