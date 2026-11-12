"""Evaluate the frozen queries offline; no LLM, key, or generator imports."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import random
import subprocess
import time
from pathlib import Path

import numpy as np

from chat_twin.retrieval import CANDIDATES, ROOT, RRF_CONSTANT, Retriever, sha256
from scripts.offline import deny_network


def evaluate(retriever: Retriever, queries: list[dict], repeats: int) -> dict:
    retriever.search(queries[0]["query"])  # Exclude model/Jieba warm-up from timings.
    cases = []
    for case in queries:
        results = retriever.search(case["query"], k=50)
        ids = [r["id"] for r in results]
        rank = ids.index(case["expected_id"]) + 1 if case["expected_id"] in ids else None
        cases.append(
            {
                "expected_id": case["expected_id"],
                "query": case["query"],
                "hit": rank is not None and rank <= 5,
                "rank": rank,
                "top5": ids[:5],
            }
        )
    timings = []
    sequence = queries * repeats
    random.Random(20261112).shuffle(sequence)
    for case in sequence:
        start = time.perf_counter_ns()
        retriever.search(case["query"], k=5)
        timings.append((time.perf_counter_ns() - start) / 1e6)
    hits = sum(c["hit"] for c in cases)
    return {
        "count": len(cases),
        "hits_at_5": hits,
        "recall_at_5": hits / len(cases),
        "filters": {"stage": None, "scene": None},
        "candidates_per_lane": CANDIDATES,
        "rrf_constant": RRF_CONSTANT,
        "latency": {
            "unit": "ms",
            "queries": len(timings),
            "repeats": repeats,
            "p50": float(np.percentile(timings, 50)),
            "p95": float(np.percentile(timings, 95)),
            "max": max(timings),
            "excluded": "model initialization and first warm-up search",
            "measurement": "in-process search_samples core, k=5, no filters",
        },
        "cases": cases,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "evals/results.json")
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    path = ROOT / "evals/retrieval.jsonl"
    queries = [json.loads(s) for s in path.read_text().splitlines()]
    meta = json.loads(path.with_suffix(".meta.json").read_text())
    if sha256(path.read_bytes()) != meta["eval_sha256"] or len(queries) != 50:
        raise ValueError("Frozen evaluation checksum/count mismatch")
    with deny_network() as attempts:
        retriever = Retriever()
        if retriever.manifest["samples_sha256"] != meta["samples_sha256"]:
            raise ValueError("Evaluation corpus changed")
        result = evaluate(retriever, queries, args.repeats)
    chip = subprocess.run(
        ["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, check=False
    ).stdout.strip()
    result.update(
        eval_sha256=sha256(path.read_bytes()),
        index_manifest_sha256=sha256((ROOT / "data/index/manifest.json").read_bytes()),
        samples_sha256=retriever.manifest["samples_sha256"],
        network_attempts=len(attempts),
        environment={
            "system": platform.system(),
            "release": platform.release(),
            "architecture": platform.machine(),
            "chip": chip,
            "python": platform.python_version(),
            "packages": {
                p: importlib.metadata.version(p)
                for p in ["fastembed", "onnxruntime", "jieba", "rank-bm25", "numpy", "mcp"]
            },
        },
    )
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: result[key]
                for key in ["hits_at_5", "recall_at_5", "latency", "network_attempts"]
            },
            indent=2,
        )
    )
    if result["recall_at_5"] < 0.8 or result["latency"]["p95"] >= 300:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
