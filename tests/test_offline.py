"""Real-model tests in fresh processes: fail even on swallowed network attempts."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_child(code):
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=ROOT,
        check=False,
        env={**os.environ, "HF_HUB_OFFLINE": "0"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_model_load_and_query_never_attempt_network():
    if not (ROOT / "data/cache/models").is_dir():
        pytest.skip("Run scripts/build_index.py once to download the local model")
    run_child("""
        from chat_twin.retrieval import Retriever
        from scripts.offline import deny_network
        with deny_network() as attempts:
            retriever = Retriever()
            assert retriever.search('今天心情还不错呢')
            assert retriever.search('你最近忙不忙？', stage=3, scene='unknown')
            assert len(retriever.list_stages()) == 7
        assert attempts == []
        print('cold process: model load + queries + stages, zero network attempts')
    """)


def test_missing_cache_fails_offline_without_download():
    run_child("""
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from chat_twin.retrieval import load_encoder
        from scripts.offline import deny_network
        with TemporaryDirectory() as directory, deny_network() as attempts:
            try:
                load_encoder(Path(directory))
            except RuntimeError as error:
                assert 'build_index' in str(error)
            else:
                raise AssertionError('Missing cache must fail')
        assert attempts == []
    """)


def test_network_guard_detects_swallowed_attempt():
    run_child("""
        import socket
        from scripts.offline import deny_network
        try:
            with deny_network():
                try:
                    socket.getaddrinfo('example.com', 443)
                except AssertionError:
                    pass
        except AssertionError as error:
            assert 'possibly swallowed' in str(error)
        else:
            raise AssertionError('Swallowed network attempt was missed')
    """)
