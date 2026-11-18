"""Zhiyin stdio tools for local WeChat context and reply drafts."""

import argparse
import asyncio
import sys
from functools import cache
from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field

from zhiyin.brief import contact_brief as build_contact_brief
from zhiyin.draft import DraftEngine
from zhiyin.llm import DeepSeek
from zhiyin.local import temporary_state
from zhiyin.my_replies import ReplySearch
from zhiyin.relationships import Relationship
from zhiyin.relationships import set_relationship as save_relationship
from zhiyin.relationships import suggest_relationships as find_relationships
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
    retriever: Retriever | None = None,
    store: WeChatStore | None = None,
    *,
    model_cache: Path = ROOT / "data/cache/models",
    index_dir: Path = ROOT / "data/index",
    model=None,
) -> FastMCP:
    store = store if store is not None else WeChatStore()
    replies = ReplySearch(store, model_cache, encoder=retriever.encoder if retriever else None)

    @cache
    def public_index() -> Retriever:
        return retriever if retriever is not None else Retriever(index_dir, model_cache=model_cache)

    class AuxiliarySamples:
        async def search(self, message, stage=None, k=5):
            return await asyncio.to_thread(lambda: public_index().search(message, stage, k=k))

    server = ZhiyinMCP(
        "zhiyin",
        instructions=(
            "知音支持微信联系人近况、历史回复检索和回复草稿。"
            "草稿会调用 DeepSeek，返回后由本人决定是否使用。"
            "关系分组建议只读，必须由本人确认具体联系人和关系后，才能调用 set_relationship。"
        ),
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
        return public_index().search(message, stage, scene, k)

    @server.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
    def list_stages() -> list[dict]:
        """返回各数字阶段的总条数及每个上游原名的条数（名称不合并）。"""
        return public_index().list_stages()

    @server.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
    def contact_brief(
        contact: Annotated[str, Field(min_length=1, max_length=200)],
    ) -> dict[str, object]:
        """按微信昵称、备注、会话代号或会话 ID 查看聊天近况和待确认约定。"""
        return build_contact_brief(store, contact)

    @server.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
    def suggest_relationships() -> list[dict]:
        """依据本地备注、称呼和聊天活跃天数列出待确认的关系分组建议。"""
        return find_relationships(store)

    @server.tool(
        annotations={"readOnlyHint": False, "openWorldHint": False, "idempotentHint": True}
    )
    def set_relationship(
        contact: Annotated[str, Field(min_length=1, max_length=200)],
        relationship: Relationship,
    ) -> dict[str, str]:
        """仅在本人明确确认后，按会话 ID、代号或唯一备注保存一个联系人关系。"""
        return save_relationship(store, contact, relationship)

    @server.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
    async def search_my_replies(
        message: Annotated[str, Field(min_length=1, max_length=10000)],
        contact: Annotated[str | None, Field(min_length=1, max_length=200)] = None,
        k: Annotated[int, Field(ge=1, le=50)] = 5,
    ) -> list[dict]:
        """检索自己的微信回复，优先同一联系人和已知的同类关系，返回历史原句及日期。"""
        return await asyncio.to_thread(replies.search, message, contact, k)

    @server.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
    async def draft_reply(
        contact: Annotated[str, Field(min_length=1, max_length=200)],
        message: Annotated[str, Field(min_length=1, max_length=10000)],
        goal: Annotated[str | None, Field(min_length=1, max_length=500)] = None,
    ) -> dict[str, object]:
        """结合本地微信历史起草 1–3 条可选回复或交回本人；会调用 DeepSeek。"""
        engine = DraftEngine(
            model if model is not None else DeepSeek(), AuxiliarySamples(), replies=replies
        )
        return (await engine.run_wechat(contact, message, goal=goal)).as_tool_result()

    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index-dir", type=Path, default=ROOT / "data/index")
    parser.add_argument("--model-cache", type=Path, default=ROOT / "data/cache/models")
    parser.add_argument("--state-dir", type=temporary_state)
    args = parser.parse_args()
    create_server(
        store=WeChatStore(args.state_dir),
        index_dir=args.index_dir,
        model_cache=args.model_cache,
    ).run(transport="stdio")


def legacy_main():
    print("chat-twin-mcp 已更名为 zhiyin-mcp", file=sys.stderr)
    main()


if __name__ == "__main__":
    main()
