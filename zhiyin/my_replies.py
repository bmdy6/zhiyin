"""Hybrid retrieval over the user's messages, using the round-2 local encoder."""

from collections import defaultdict
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from zhiyin.retrieval import (
    MODEL,
    ROOT,
    hybrid_rank,
    load_encoder,
    normalized,
    tokenize,
    tokenize_readonly,
)
from zhiyin.tone import profile_revision
from zhiyin.wechat import WeChatStore, local_time

MIN_SIMILARITY = 0.72
TEXT_LIMIT = 1000
VECTOR_VERSION = f"{MODEL}:prefix-{TEXT_LIMIT}"
COMMON = frozenset(
    [
        "我",
        "你",
        "他",
        "她",
        "它",
        "的",
        "了",
        "是",
        "有",
        "和",
        "在",
        "吗",
        "呢",
        "啊",
        "就",
        "都",
        "也",
        "说",
        "这个",
        "那个",
        "什么",
        "怎么",
    ]
)
LATEST_FIRST = """
WITH ranked AS (
    SELECT p.*, c.relationship, c.label,
           row_number() OVER (
              PARTITION BY p.prior_id ORDER BY p.time, p.seq, p.position
           ) AS n
    FROM reply_pairs p JOIN visible_contacts c ON p.contact_id=c.id
)
SELECT ranked.*, e.vector FROM ranked
LEFT JOIN reply_embeddings e ON ranked.prior_id=e.prior_id AND e.version=?
WHERE n=1 ORDER BY time DESC, seq DESC, position DESC
"""


class ReplySearch:
    def __init__(
        self,
        store: WeChatStore,
        model_cache: Path = ROOT / "data/cache/models",
        *,
        encoder=None,
    ):
        self.store, self.model_cache = store, model_cache
        self.encoder = encoder
        self.revision = None

    def _encoder(self):
        if self.encoder is None:
            self.encoder = load_encoder(self.model_cache)
        return self.encoder

    def _load(self, db) -> None:
        revision = profile_revision(db)
        if revision == self.revision:
            return
        rows = [dict(row) for row in db.execute(LATEST_FIRST, (VECTOR_VERSION,))]
        missing = defaultdict(list)
        known = {}
        tokens = []
        for row in rows:
            query = row["her"][:TEXT_LIMIT]
            tokens.append(tokenize(query))
            if row["vector"] is None:
                missing[query].append(row)
            else:
                known[query] = row["vector"]

        to_embed = [text for text in missing if text not in known]
        if to_embed:
            vectors = normalized(list(self._encoder().embed(to_embed, batch_size=64)))
            if len(vectors) != len(to_embed):
                raise ValueError("历史消息向量数量不匹配")
            known.update(
                (text, vector.astype("<f4").tobytes())
                for text, vector in zip(to_embed, vectors, strict=True)
            )
        cached = []
        for text, pending in missing.items():
            for row in pending:
                row["vector"] = known[text]
                cached.append((row["prior_id"], VECTOR_VERSION, row["vector"]))
        db.executemany(
            "INSERT INTO reply_embeddings VALUES (?, ?, ?) ON CONFLICT(prior_id) DO UPDATE SET "
            "version=excluded.version, vector=excluded.vector",
            cached,
        )
        # BM25Okapi needs a non-empty vocabulary even for an emoji-only archive.
        self.bm25 = BM25Okapi(tokens) if any(tokens) else None
        self.docs = rows
        self.vectors = (
            normalized([np.frombuffer(row.pop("vector"), "<f4") for row in rows]) if rows else None
        )
        self.revision = revision

    def prepare(self) -> int:
        with self.store.connect(write=True) as db:
            self._load(db)
            return len(self.docs)

    def search_readonly(self, db, message, matched=None, k=5, allowed=None):
        """Use existing reply pairs without embeddings, model loading or cache writes."""
        if not message.strip() or len(message) > 10000 or not 1 <= k <= 50:
            raise ValueError("历史检索参数无效")
        tokens = set(tokenize_readonly(message[:TEXT_LIMIT])) - COMMON
        if not tokens:
            return []
        rows = []
        seen = set()
        for raw in db.execute(LATEST_FIRST, (VECTOR_VERSION,)):
            row = dict(raw)
            if allowed is not None and row["contact_id"] not in allowed:
                continue
            # One result per prior incoming message, same as hybrid retrieval.
            if row["prior_id"] in seen:
                continue
            seen.add(row["prior_id"])
            words = set(tokenize_readonly(row["her"][:TEXT_LIMIT])) - COMMON
            overlap = len(tokens & words)
            exact = row["her"].strip() == message.strip()
            if not overlap and not exact:
                continue
            tier = 2
            if matched is not None:
                if row["contact_id"] == matched["id"]:
                    tier = 0
                elif (
                    matched["relationship"] != "未分类"
                    and row["relationship"] == matched["relationship"]
                ):
                    tier = 1
            score = 1.0 if exact else overlap / max(len(tokens | words), 1)
            rows.append((tier, -score, -row["time"], row))
        return [
            {
                "contact_id": r["contact_id"],
                "date": local_time(r["time"])[:10],
                "her": r["her"],
                "reply": r["reply"],
                "score": -score,
                "retrieval": "local lexical overlap",
            }
            for _, score, _, r in sorted(rows, key=lambda item: item[:3])[:k]
        ]

    def search(self, message: str, contact: str | None = None, k: int = 5) -> list[dict]:
        if not isinstance(message, str) or not message.strip() or len(message) > 10000:
            raise ValueError("消息须为1–10000字符")
        if type(k) is not int or not 1 <= k <= 50:
            raise ValueError("k 必须为1–50")
        query = message[:TEXT_LIMIT]
        tokens = tokenize(query)
        if not tokens:
            return []
        with self.store.connect(write=True) as db:
            matched = self.store.contact(db, contact) if contact is not None else None
            if contact is not None and matched is None:
                return []
            self._load(db)
            if not self.docs:
                return []
            lexical = (
                self.bm25.get_scores([token for token in tokens if token not in COMMON])
                if self.bm25
                else np.zeros(len(self.docs))
            )
            vector = normalized(list(self._encoder().query_embed(query)))[0]
            if len(vector) != self.vectors.shape[1]:
                raise ValueError("历史消息与查询向量维度不匹配")
            semantic = self.vectors @ vector
            # Exact matches stay eligible even for short, high-frequency utterances.
            exact = np.array([row["her"].strip() == message.strip() for row in self.docs])
            lexical[exact] = max(1.0, float(lexical.max()) + 1)
            tiers = [[], [], []]
            for i, row in enumerate(self.docs):
                if matched is not None and row["contact_id"] == matched["id"]:
                    tiers[0].append(i)
                elif (
                    matched is not None
                    and matched["relationship"] != "未分类"
                    and row["relationship"] == matched["relationship"]
                ):
                    tiers[1].append(i)
                else:
                    tiers[2].append(i)
            results = []
            for tier in tiers:
                indices = np.array(tier, dtype=np.int64)
                for index, score in hybrid_rank(
                    indices, lexical[indices], semantic[indices], min_similarity=MIN_SIMILARITY
                ):
                    row = self.docs[index]
                    results.append(
                        {
                            "id": row["id"],
                            "contact": row["label"],
                            "relationship": row["relationship"],
                            "date": local_time(row["time"])[:10],
                            "her": row["her"],
                            "reply": row["reply"],
                            "score": score,
                        }
                    )
                    if len(results) == k:
                        return results
            return results
