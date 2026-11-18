import json

import numpy as np
import pytest

from zhiyin.retrieval import Retriever, build_index, reciprocal_rank_fusion, tokenize


class Encoder:
    def embed(self, texts, **kwargs):
        for text in texts:
            yield np.array([1.0, float("咖啡" in text), float("散步" in text)], dtype=np.float32)

    def query_embed(self, text):
        return self.embed([text])


@pytest.fixture
def index(tmp_path):
    source = tmp_path / "samples.jsonl"
    rows = [
        {
            "id": f"s{i}",
            "stage": i,
            "stage_name": f"名称{i}",
            "scene": scene,
            "context": context,
            "her": her,
            "reply": "好呀",
            "goal": "接话",
        }
        for i, scene, context, her in [
            (1, "online", "初次聊天", "喝咖啡吗"),
            (2, "offline", "咖啡馆聊天", "咖啡好喝"),
            (3, "unknown", "街头", "散步去吗"),
            (4, "online", "聊天", "你好呀"),
            (5, "online", "聊天", "在忙吗"),
            (6, "offline", "聊天", "下班了吗"),
            (7, "unknown", "聊天", "晚安"),
        ]
    ]
    source.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    target = tmp_path / "index"
    build_index(source, target, encoder=Encoder())
    return source, target


def test_tokenizer_and_rrf():
    assert "咖啡" in tokenize("今天喝一杯咖啡！")
    assert tokenize("ABC，123") == ["abc", "123"]
    assert not tokenize(" ！？")
    fused = reciprocal_rank_fusion([[2, 1], [1, 3]], constant=60)
    assert fused[0][0] == 1
    assert fused[0][1] == pytest.approx(1 / 62 + 1 / 61)


def test_search_contract_and_filters(index):
    source, target = index
    retriever = Retriever(target, source=source, encoder=Encoder())
    rows = retriever.search("咖啡", k=3)
    assert len(rows) == 3
    assert set(rows[0]) == {"id", "stage", "context", "her", "reply", "goal", "score"}
    assert all(isinstance(r["score"], float) for r in rows)
    assert {r["stage"] for r in retriever.search("咖啡", stage=1, k=50)} <= {1, 2}
    assert {r["stage"] for r in retriever.search("咖啡", stage=7, k=50)} <= {6, 7}
    assert [r["id"] for r in retriever.search("咖啡", stage=1, scene="online")] == ["s1"]
    assert retriever.search("咖啡", stage=1, scene="unknown") == []
    assert [r["id"] for r in retriever.search("散步", scene="unknown")][:1] == ["s3"]
    assert retriever.search("") == []
    assert retriever.search("！？") == []
    assert sum(s["count"] for s in retriever.list_stages()) == 7


@pytest.mark.parametrize(
    "kwargs", [{"stage": 0}, {"stage": 8}, {"scene": "bad"}, {"k": 0}, {"k": 51}]
)
def test_invalid_arguments(index, kwargs):
    source, target = index
    with pytest.raises(ValueError):
        Retriever(target, source=source, encoder=Encoder()).search("你好", **kwargs)


def test_index_detects_stale_and_corrupt_data(index):
    source, target = index
    original = source.read_bytes()
    source.write_bytes(original + b"\n")
    with pytest.raises(ValueError, match="stale"):
        Retriever(target, source=source, encoder=Encoder())
    source.write_bytes(original)
    with (target / "vectors.npy").open("ab") as f:
        f.write(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        Retriever(target, source=source, encoder=Encoder())


def test_missing_index_is_actionable(tmp_path):
    with pytest.raises(FileNotFoundError, match="build_index"):
        Retriever(tmp_path / "missing", encoder=Encoder())


def test_two_lanes_take_thirty_after_filtering(tmp_path):
    source = tmp_path / "samples.jsonl"
    rows = [
        {
            "id": str(i),
            "stage": 1 if i < 40 else 6,
            "stage_name": "x",
            "scene": "online",
            "context": "咖啡",
            "her": "咖啡",
            "reply": "好",
            "goal": "接话",
        }
        for i in range(80)
    ]
    source.write_text("".join(json.dumps(r) + "\n" for r in rows))
    build_index(source, tmp_path / "index", encoder=Encoder())
    retriever = Retriever(tmp_path / "index", source=source, encoder=Encoder())
    assert len(retriever.search("咖啡", stage=6, k=50)) == 30
    assert all(r["stage"] == 6 for r in retriever.search("咖啡", stage=6))


def test_committed_index_only_contains_retained_public_samples():
    from zhiyin.retrieval import ROOT, sha256

    source = ROOT / "data/samples.jsonl"
    samples = [json.loads(s) for s in source.read_text().splitlines()]
    target = ROOT / "data/index"
    manifest = json.loads((target / "manifest.json").read_text())
    docs = json.loads((target / "documents.json").read_text())
    assert manifest["samples_sha256"] == sha256(source.read_bytes())
    assert [r["id"] for r in docs] == [r["id"] for r in samples]
    assert "qs-c-3386" in {r["id"] for r in docs}
    assert "qs-c-3044" not in {r["id"] for r in docs}
    retriever = Retriever(target, encoder=Encoder())
    stage5 = next(r for r in retriever.list_stages() if r["stage"] == 5)
    assert {"升温推进", "约会实战"} <= {r["name"] for r in stage5["names"]}
    assert sum(r["count"] for r in stage5["names"]) == stage5["count"]


def test_index_embeds_only_her_and_context(index, tmp_path):
    class Recorder(Encoder):
        def embed(self, texts, **kwargs):
            self.texts = list(texts)
            return super().embed(self.texts, **kwargs)

    source, _ = index
    encoder = Recorder()
    build_index(source, tmp_path / "recorded", encoder=encoder)
    originals = [json.loads(s) for s in source.read_text().splitlines()]
    assert encoder.texts == [r["her"] + "\n" + r["context"] for r in originals]
