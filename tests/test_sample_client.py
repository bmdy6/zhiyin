import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

from chat_twin.sample_client import SampleClient


@pytest.mark.skipif(
    not (Path(__file__).resolve().parents[1] / "data/cache/models").is_dir(),
    reason="Prepare the local model using scripts/build_index.py",
)
def test_mcp_request_timeout_does_not_limit_session_lifetime():
    async def run():
        # Model initialization has its own generous SDK read timeout. The per-call override
        # only applies after initialization, and must not cancel the long-lived task group.
        async with SampleClient() as client:
            await client.search("今天的猫很可爱")
            client.read_timeout = timedelta(seconds=0.5)
            first = await client.search("今天的云好好看")
            await asyncio.sleep(0.6)
            second = await client.search("电影结局看懵了")
            assert first and second

    asyncio.run(run())
