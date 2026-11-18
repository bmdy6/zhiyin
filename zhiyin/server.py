"""Retrieval-only FastMCP stdio server. Initialization loads the local model once."""

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field

from zhiyin.brief import contact_brief as build_contact_brief
from zhiyin.my_replies import ReplySearch
from zhiyin.retrieval import ROOT, Retriever
from zhiyin.wechat import WeChatStore


class ZhiyinMCP(FastMCP):
    async def call_tool(self, name, arguments):
        try:
            return await super().call_tool(name, arguments)
        except ToolError:
            # Parameter validation and downstream errors may include conversation text.
            raise ToolError("知音工具调用失败，请检查参数和本地配置") from None


def create_server(
    retriever: Retriever,
    store: WeChatStore | None = None,
    *,
    model_cache=ROOT / "data/cache/models",
) -> FastMCP:
    store = store if store is not None else WeChatStore()
    replies = ReplySearch(store, model_cache, encoder=retriever.encoder)
    server = ZhiyinMCP(
        "zhiyin",
        instructions="知音提供本地微信聊天参考和公开辅助样本。",
        log_level="WARNING",
    )

    @server.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
    def search_samples(
        message: Annotated[str, Field(max_length=10000)],
        stage: Annotated[int | None, Field(ge=1, le=7)] = None,
        scene: Annotated[str | None, Field(pattern="^(online|offline|unknown)$")] = None,
        k: Annotated[int, Field(ge=1, le=50)] = 5,
    ) -> list[dict]:
        """按对方消息搜索样本；stage限本阶段±1，scene精确过滤，score是RRF分数。"""
        return retriever.search(message, stage, scene, k)

    @server.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
    def list_stages() -> list[dict]:
        """返回各数字阶段的总条数及每个上游原名的条数（名称不合并）。"""
        return retriever.list_stages()

    @server.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
    def contact_brief(contact: Annotated[str, Field(min_length=1, max_length=200)]) -> dict:
        """按微信昵称、备注、会话代号或会话 ID 查看聊天近况和待确认约定。"""
        return build_contact_brief(store, contact)

    @server.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
    async def search_my_replies(
        message: Annotated[str, Field(min_length=1, max_length=10000)],
        contact: Annotated[str | None, Field(min_length=1, max_length=200)] = None,
        k: Annotated[int, Field(ge=1, le=50)] = 5,
    ) -> list[dict]:
        """检索自己的微信回复，优先同一联系人和已知的同类关系，返回历史原句及日期。"""
        return await asyncio.to_thread(replies.search, message, contact, k)

    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index-dir", type=Path, default=ROOT / "data/index")
    parser.add_argument("--model-cache", type=Path, default=ROOT / "data/cache/models")
    from zhiyin.cli import temporary_state

    parser.add_argument("--state-dir", type=temporary_state)
    args = parser.parse_args()
    create_server(
        Retriever(args.index_dir, model_cache=args.model_cache),
        WeChatStore(args.state_dir),
        model_cache=args.model_cache,
    ).run(transport="stdio")


def legacy_main():
    print("chat-twin-mcp 已更名为 zhiyin-mcp", file=sys.stderr)
    main()


if __name__ == "__main__":
    main()
