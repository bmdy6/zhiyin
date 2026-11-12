"""Official MCP stdio client for the round-2 server, reused for all drafts in a run."""

import sys
from contextlib import AsyncExitStack
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from chat_twin.resources import ROOT


class SampleClient:
    def __init__(
        self, index_dir: Path = ROOT / "data/index", model_cache: Path = ROOT / "data/cache/models"
    ):
        self.index_dir, self.model_cache = index_dir, model_cache
        self.stack = AsyncExitStack()
        self.session = None
        self.read_timeout = timedelta(seconds=45)

    async def __aenter__(self):
        return self

    async def _connect(self):
        # Lazy initialization: sensitive input can hand off without loading any retrieval resources.
        params = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "chat_twin.server",
                "--index-dir",
                str(self.index_dir.resolve()),
                "--model-cache",
                str(self.model_cache.resolve()),
            ],
        )
        reader, writer = await self.stack.enter_async_context(stdio_client(params))
        self.session = await self.stack.enter_async_context(
            ClientSession(reader, writer, read_timeout_seconds=self.read_timeout)
        )
        await self.session.initialize()

    async def search(self, message: str, stage: int | None = None, k: int = 5) -> list[dict]:
        if self.session is None:
            await self._connect()
        response = await self.session.call_tool(
            "search_samples",
            {"message": message, "stage": stage, "k": k},
            read_timeout_seconds=self.read_timeout,
        )
        if response.isError or not response.structuredContent:
            raise RuntimeError("样本 MCP 不可用")
        rows = response.structuredContent["result"]
        if not isinstance(rows, list) or not rows:
            raise ValueError("样本 MCP 未返回参考")
        return rows

    async def __aexit__(self, *args):
        await self.stack.aclose()
