import json
from pathlib import Path

import httpx

from scripts import generate_eval
from zhiyin.retrieval import sha256

ROOT = Path(__file__).resolve().parents[1]


def test_frozen_eval_provenance_and_sampling():
    source = ROOT / "data/samples.jsonl"
    path = ROOT / "evals/retrieval.jsonl"
    meta = json.loads(path.with_suffix(".meta.json").read_text())
    samples = [json.loads(s) for s in source.read_text().splitlines()]
    cases = [json.loads(s) for s in path.read_text().splitlines()]
    assert len(cases) == len({r["expected_id"] for r in cases}) == 50
    assert [r["expected_id"] for r in cases] == [
        r["id"] for r in generate_eval.select_samples(samples)
    ]
    assert meta["samples_sha256"] == sha256(source.read_bytes())
    assert meta["eval_sha256"] == sha256(path.read_bytes())
    assert meta["model"] == "deepseek-flash"
    assert meta["thinking"] == {"type": "disabled"}
    assert all(r["served_model"] == "deepseek-flash" for r in cases)
    assert all(r["query"] and r["query"] != r["original_her"] for r in cases)


def test_generation_uses_her_only_and_cache(monkeypatch, tmp_path):
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert payload["model"] == "deepseek-flash"
        assert payload["thinking"] == {"type": "disabled"}
        assert payload["messages"][1]["content"] == "你好呀"
        assert request.headers["authorization"] == "Bearer synthetic-key"
        return httpx.Response(
            200,
            json={
                "model": "deepseek-flash",
                "usage": {"total_tokens": 1},
                "choices": [{"finish_reason": "stop", "message": {"content": '{"query":"嗨"}'}}],
            },
        )

    real_client = httpx.Client
    monkeypatch.setattr(generate_eval, "read_key", lambda: "synthetic-key")
    monkeypatch.setattr(
        generate_eval.httpx,
        "Client",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    row = {
        "id": "public-test",
        "her": "你好呀",
        "reply": "do not send to LLM",
        "context": "neither this",
    }
    first = generate_eval.rewrite(row, tmp_path)
    assert first["query"] == "嗨"
    assert generate_eval.rewrite(row, tmp_path) == first
    assert len(calls) == 1
