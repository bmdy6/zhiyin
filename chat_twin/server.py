"""Retrieval-only FastMCP stdio server. Initialization loads the local model once."""

import argparse
from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from chat_twin.retrieval import ROOT, Retriever


def create_server(retriever: Retriever) -> FastMCP:
    server = FastMCP(
        "chat-twin",
        instructions="公开样本检索参考；本轮不生成草稿、不发送消息。",
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

    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index-dir", type=Path, default=ROOT / "data/index")
    parser.add_argument("--model-cache", type=Path, default=ROOT / "data/cache/models")
    args = parser.parse_args()
    create_server(Retriever(args.index_dir, model_cache=args.model_cache)).run(transport="stdio")


if __name__ == "__main__":
    main()
