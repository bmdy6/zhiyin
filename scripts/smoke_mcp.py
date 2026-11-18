"""Exercise the actual installed stdio entry point without opening a client UI."""

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from zhiyin.retrieval import ROOT, sha256


async def run() -> dict:
    queries = [json.loads(s) for s in (ROOT / "evals/retrieval.jsonl").read_text().splitlines()]
    # Explicit absolute resource paths also exercise the wheel-install use case.
    params = StdioServerParameters(
        command=str(Path(sys.executable).parent / "zhiyin-mcp"),
        args=[
            "--index-dir",
            str(ROOT / "data/index"),
            "--model-cache",
            str(ROOT / "data/cache/models"),
        ],
        cwd=tempfile.gettempdir(),
    )
    async with (
        stdio_client(params) as (reader, writer),
        ClientSession(reader, writer) as session,
    ):
        await session.initialize()
        tools = (await session.list_tools()).tools
        assert {tool.name for tool in tools} == {"search_samples", "list_stages", "contact_brief"}
        for tool in tools:
            assert tool.annotations.readOnlyHint is True
            assert tool.annotations.openWorldHint is False
        stages = await session.call_tool("list_stages", {})
        assert not stages.isError
        assert sum(r["count"] for r in stages.structuredContent["result"]) == 788
        warmup = await session.call_tool("search_samples", {"message": "今天心情很好"})
        assert not warmup.isError
        times = []
        hits = 0
        for case in queries:
            start = time.perf_counter_ns()
            response = await session.call_tool("search_samples", {"message": case["query"]})
            times.append((time.perf_counter_ns() - start) / 1e6)
            assert not response.isError
            rows = response.structuredContent["result"]
            assert all(
                set(r) == {"id", "stage", "context", "her", "reply", "goal", "score"} for r in rows
            )
            hits += case["expected_id"] in [r["id"] for r in rows]
        filtered = await session.call_tool(
            "search_samples", {"message": "见面聊聊", "stage": 1, "scene": "offline"}
        )
        assert not filtered.isError
        assert all(r["stage"] in {1, 2} for r in filtered.structuredContent["result"])
        invalid = await session.call_tool("search_samples", {"message": "你好", "stage": 8})
        assert invalid.isError
        unknown = await session.call_tool("missing_tool", {})
        assert unknown.isError
    return {
        "transport": "stdio",
        "entrypoint": "zhiyin-mcp",
        "tools": sorted(t.name for t in tools),
        "queries": len(times),
        "hits_at_5": hits,
        "recall_at_5": hits / len(queries),
        "p95_ms": float(np.percentile(times, 95)),
        "max_ms": max(times),
        "excluded": "initialize/model load and warm-up call",
        "eval_sha256": sha256((ROOT / "evals/retrieval.jsonl").read_bytes()),
        "checks": [
            "initialize",
            "list_tools",
            "list_stages",
            "search_samples",
            "stage+scene filters",
            "invalid input",
            "unknown tool",
            "clean shutdown",
        ],
    }


def main():
    result = asyncio.run(run())
    (ROOT / "evals/mcp-smoke.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["recall_at_5"] < 0.8 or result["p95_ms"] >= 300:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
