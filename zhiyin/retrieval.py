"""Local-only hybrid retrieval over public samples. No LLM or query persistence."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

import jieba
import numpy as np
from fastembed import TextEmbedding
from rank_bm25 import BM25Okapi

ROOT = Path(__file__).resolve().parents[1]
MODEL = "BAAI/bge-small-zh-v1.5"
INDEX_VERSION = 1
CANDIDATES = 30
RRF_CONSTANT = 60
FIELDS = ("id", "stage", "context", "her", "reply", "goal")
JIEBA = jieba.Tokenizer()
jieba.setLogLevel(logging.WARNING)


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in JIEBA.cut(text) if re.search(r"[\w]", t)]


def load_encoder(cache_dir: Path, *, offline: bool = True):
    try:
        return TextEmbedding(
            model_name=MODEL,
            cache_dir=str(cache_dir),
            threads=2,
            providers=["CPUExecutionProvider"],
            local_files_only=offline,
        )
    except ValueError as error:
        raise RuntimeError(
            "Model cache missing or invalid; run scripts/build_index.py online first"
        ) from error


def normalized(vectors) -> np.ndarray:
    matrix = np.asarray(vectors, dtype=np.float32)
    if matrix.ndim != 2 or not np.isfinite(matrix).all():
        raise ValueError("Invalid vectors")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if np.any(norms == 0):
        raise ValueError("Zero vector")
    return matrix / norms


def build_index(
    source: Path,
    target: Path,
    *,
    encoder=None,
    offline: bool = False,
    model_cache: Path = ROOT / "data/cache/models",
) -> None:
    content = source.read_bytes()
    rows = [json.loads(s) for s in content.decode().splitlines() if s.strip()]
    if not rows or len({r["id"] for r in rows}) != len(rows):
        raise ValueError("Empty corpus or duplicate IDs")
    docs = [{key: r[key] for key in (*FIELDS, "stage_name", "scene")} for r in rows]
    texts = [r["her"] + "\n" + r["context"] for r in docs]
    tokens = [tokenize(text) for text in texts]
    if not all(tokens):
        raise ValueError("Every document needs searchable her/context")
    encoder = encoder if encoder is not None else load_encoder(model_cache, offline=offline)
    vectors = normalized(list(encoder.embed(texts, batch_size=64)))
    if len(vectors) != len(rows):
        raise ValueError("Embedding count mismatch")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Prepare all files before replacing. Manifest last makes partial writes detectable.
    with tempfile.TemporaryDirectory(dir=target.parent, prefix=".index-") as temporary:
        staging = Path(temporary)
        for name, data in [("documents.json", docs), ("tokens.json", tokens)]:
            (staging / name).write_text(
                json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
        np.save(staging / "vectors.npy", vectors, allow_pickle=False)
        manifest = {
            "version": INDEX_VERSION,
            "model": MODEL,
            "count": len(rows),
            "dimension": vectors.shape[1],
            "text": "her + newline + context",
            "samples_sha256": sha256(content),
            "files": {
                name: sha256((staging / name).read_bytes())
                for name in ("documents.json", "tokens.json", "vectors.npy")
            },
        }
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        target.mkdir(parents=True, exist_ok=True)
        for name in (*manifest["files"], "manifest.json"):
            (staging / name).replace(target / name)


def reciprocal_rank_fusion(rankings: list[list[int]], *, constant: int = RRF_CONSTANT):
    scores = defaultdict(float)
    for ranking in rankings:
        for rank, item in enumerate(ranking, 1):
            scores[item] += 1 / (constant + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def hybrid_rank(eligible, lexical, semantic, *, min_similarity=None):
    positions = np.argsort(-lexical, kind="stable")[:CANDIDATES]
    lexical_rank = eligible[positions[lexical[positions] > 0]].tolist()
    if min_similarity is not None:
        meets_threshold = semantic >= min_similarity
        eligible, semantic = eligible[meets_threshold], semantic[meets_threshold]
    semantic_rank = eligible[np.argsort(-semantic, kind="stable")[:CANDIDATES]].tolist()
    return reciprocal_rank_fusion([lexical_rank, semantic_rank])


class Retriever:
    def __init__(
        self,
        index_dir: Path = ROOT / "data/index",
        *,
        source: Path | None = None,
        model_cache: Path = ROOT / "data/cache/models",
        encoder=None,
    ):
        manifest_path = index_dir / "manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError("Index missing; run uv run python -m scripts.build_index")
        self.manifest = json.loads(manifest_path.read_text())
        if self.manifest["version"] != INDEX_VERSION or self.manifest["model"] != MODEL:
            raise ValueError("Incompatible index; rebuild with scripts/build_index.py")
        source = source if source is not None else index_dir.parent / "samples.jsonl"
        if not source.is_file() or sha256(source.read_bytes()) != self.manifest["samples_sha256"]:
            raise ValueError("Index stale: source checksum changed; run scripts/build_index.py")
        expected_files = {"documents.json", "tokens.json", "vectors.npy"}
        if set(self.manifest["files"]) != expected_files:
            raise ValueError("Invalid index file list")
        for name, checksum in self.manifest["files"].items():
            if sha256((index_dir / name).read_bytes()) != checksum:
                raise ValueError(f"Index checksum mismatch: {name}; rebuild index")
        self.docs = json.loads((index_dir / "documents.json").read_text())
        tokens = json.loads((index_dir / "tokens.json").read_text())
        self.vectors = normalized(np.load(index_dir / "vectors.npy", allow_pickle=False))
        if (
            len(self.docs) != self.manifest["count"]
            or len(tokens) != len(self.docs)
            or self.vectors.shape != (len(self.docs), self.manifest["dimension"])
        ):
            raise ValueError("Index shape mismatch")
        self.bm25 = BM25Okapi(tokens)
        JIEBA.initialize()
        self.encoder = encoder if encoder is not None else load_encoder(model_cache)

    def search(
        self, message: str, stage: int | None = None, scene: str | None = None, k: int = 5
    ) -> list[dict]:
        if not isinstance(message, str) or len(message) > 10000:
            raise ValueError("message must be text up to 10000 characters")
        if stage is not None and (type(stage) is not int or not 1 <= stage <= 7):
            raise ValueError("stage must be an integer from 1 to 7")
        if scene is not None and scene not in {"online", "offline", "unknown"}:
            raise ValueError("scene must be online, offline or unknown")
        if type(k) is not int or not 1 <= k <= 50:
            raise ValueError("k must be an integer from 1 to 50")
        query_tokens = tokenize(message)
        if not query_tokens:
            return []
        eligible = np.array(
            [
                i
                for i, r in enumerate(self.docs)
                if (stage is None or abs(r["stage"] - stage) <= 1)
                and (scene is None or r["scene"] == scene)
            ],
            dtype=np.int64,
        )
        if not len(eligible):
            return []
        lexical = self.bm25.get_scores(query_tokens)[eligible]
        vector = normalized(list(self.encoder.query_embed(message)))[0]
        if len(vector) != self.vectors.shape[1]:
            raise ValueError("Query embedding dimension differs from index; rebuild index")
        semantic = self.vectors[eligible] @ vector
        combined = hybrid_rank(eligible, lexical, semantic)
        return [
            dict({key: self.docs[i][key] for key in FIELDS}, score=float(score))
            for i, score in combined[:k]
        ]

    def list_stages(self) -> list[dict]:
        counts = Counter(r["stage"] for r in self.docs)
        names = defaultdict(Counter)
        for row in self.docs:
            names[row["stage"]][row["stage_name"]] += 1
        return [
            {
                "stage": stage,
                "count": counts[stage],
                "names": [
                    {"name": name, "count": count} for name, count in sorted(names[stage].items())
                ],
            }
            for stage in sorted(counts)
        ]
