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
        from zhiyin.retrieval import Retriever
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
        from zhiyin.retrieval import load_encoder
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


def test_all_context_tools_are_offline_readonly_in_a_fresh_process():
    run_child("""
        import asyncio
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from zhiyin.server import create_server
        from zhiyin.wechat import WeChatStore, import_wechat
        from scripts.offline import deny_network
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'export'
            source.mkdir()
            (source / 'C1').mkdir()
            (source / '导出清单.json').write_text(json.dumps({'conversations': [{
                'label':'合成联系人','conversation_id':'synthetic-1','code':'C1','folder':'C1',
                'groups':['朋友']}]}))
            rows = [dict(time=f'2026-01-01T09:0{i}:00+08:00', sender='them' if i == 0 else 'me',
                type=1, subtype=None, text='我难过，别烦我' if i == 0 else '我也在合成测试') for i in range(7)]
            (source / 'C1/chat.jsonl').write_text(''.join(json.dumps(r)+'\\n' for r in rows))
            store = WeChatStore(root / 'state')
            import_wechat(store, source)
            before = {p.name:p.read_bytes() for p in store.root.iterdir()}
            async def check():
                with deny_network() as attempts:
                    server = create_server(store=store, model_cache=root / 'missing-models')
                    for name, args in [
                        ('list_chats',{}), ('chat_stats',{}),
                        ('search_messages',{'query':'难过'}),
                        ('read_window',{'chat':'C1','line':1}),
                        ('reply_context',{'chat':'C1','message':'我难过'}),
                        *[('find_moments',{'kind':k}) for k in
                          ['emotion_bid','burst','unanswered_end','self_pivot','conflict']],
                    ]:
                        result = await server.call_tool(name,args)
                        assert result
                assert attempts == []
            asyncio.run(check())
            assert {p.name:p.read_bytes() for p in store.root.iterdir()} == before
    """)
