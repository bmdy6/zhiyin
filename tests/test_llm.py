import asyncio
import json

import httpx
import pytest

from zhiyin.llm import DeepSeek


def test_disabled_thinking_no_prompt_persistence(monkeypatch):
    monkeypatch.setattr("zhiyin.llm.read_key", lambda: "synthetic-key")
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        assert request.headers["Authorization"] == "Bearer synthetic-key"
        return httpx.Response(
            200,
            json={
                "model": "deepseek-flash",
                "usage": {"total_tokens": 10},
                "choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}],
            },
        )

    model = DeepSeek(transport=httpx.MockTransport(respond))
    assert asyncio.run(model.complete("analyze", {"chat": "synthetic"})) == {"ok": True}
    assert calls[0]["model"] == "deepseek-flash"
    assert calls[0]["thinking"] == {"type": "disabled"}
    assert model.metadata == [
        {"step": "analyze", "model": "deepseek-flash", "usage": {"total_tokens": 10}}
    ]


@pytest.mark.parametrize(
    "message,finish",
    [
        ({"content": '{"ok":true}', "reasoning_content": "thinking"}, "stop"),
        ({"content": "{}"}, "length"),
        ({"content": "[]"}, "stop"),
    ],
)
def test_invalid_provider_output_rejected(monkeypatch, message, finish):
    monkeypatch.setattr("zhiyin.llm.read_key", lambda: "synthetic-key")
    model = DeepSeek(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"choices": [{"finish_reason": finish, "message": message}]}
            )
        )
    )
    with pytest.raises((ValueError, TypeError)):
        asyncio.run(model.complete("write", {}))
