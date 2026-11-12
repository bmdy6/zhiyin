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
