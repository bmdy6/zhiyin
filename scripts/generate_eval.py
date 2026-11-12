"""One-time public-sample paraphrasing; evaluation never imports or calls this module."""

from __future__ import annotations

import argparse
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from scripts.clean import MODEL, ROOT, digest, encoded, read_key, write_jsonl

SEED = 2026111202
PROMPT = (
    "把用户给出的一句中文聊天消息改写成意思相同、说法不同的一句口语。"
    "保留全部事实、情绪、语气、疑问或请求；不要回答消息，不加情节、称呼、场景或解释。"
    '不要仅改标点或空格。只返回JSON对象，格式：{"query":"改写后的消息"}。'
)


def select_samples(rows: list[dict]) -> list[dict]:
    # Speechless samples have no her to paraphrase, so exclude them before uniform sampling.
    return random.Random(SEED).sample([r for r in rows if r["her"].strip()], 50)


def rewrite(row: dict, cache: Path) -> dict:
    identity = digest((MODEL + PROMPT + row["her"]).encode())
    path = cache / f"{identity}.json"
    if path.exists():
        return json.loads(path.read_text())
    for attempt in range(3):
        try:
            with httpx.Client(timeout=120) as client:
                response = client.post(
                    "https://api.deepseek.com/chat/completions",
                    headers={"Authorization": "Bearer " + read_key()},
                    json={
                        "model": MODEL,
                        "thinking": {"type": "disabled"},
                        "temperature": 0,
                        "response_format": {"type": "json_object"},
                        "max_tokens": 300,
                        "messages": [
                            {"role": "system", "content": PROMPT},
                            {"role": "user", "content": row["her"]},
                        ],
                    },
                )
                response.raise_for_status()
                body = response.json()
            choice = body["choices"][0]
            if choice["finish_reason"] != "stop" or choice["message"].get("reasoning_content"):
                raise ValueError("Incomplete or thinking response")
            query = json.loads(choice["message"]["content"])["query"]
            if (
                not isinstance(query, str)
                or not query.strip()
                or query.strip() == row["her"].strip()
            ):
                raise ValueError("Missing or unchanged paraphrase")
            result = {
                "query": query.strip(),
                "input_sha256": identity,
                "served_model": body.get("model"),
                "usage": body.get("usage"),
            }
            cache.mkdir(parents=True, exist_ok=True)
            path.write_text(encoded(result) + "\n")
            return result
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as error:
            kind = type(error).__name__
            if attempt < 2:
                time.sleep(2**attempt)
    raise RuntimeError(f"Paraphrase failed for {row['id']}: {kind}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "evals/retrieval.jsonl")
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Frozen evaluation already exists; refusing to overwrite")
    source = ROOT / "data/samples.jsonl"
    picked = select_samples([json.loads(s) for s in source.read_text().splitlines()])
    with ThreadPoolExecutor(max_workers=5) as pool:
        rewrites = list(pool.map(lambda r: rewrite(r, ROOT / "data/cache/eval-rewrites"), picked))
    rows = [
        dict(expected_id=r["id"], original_her=r["her"], stage=r["stage"], **p)
        for r, p in zip(picked, rewrites)
    ]
    write_jsonl(args.output, rows)
    args.output.with_suffix(".meta.json").write_text(
        json.dumps(
            {
                "seed": SEED,
                "count": 50,
                "eligible_count": sum(
                    bool(json.loads(s)["her"].strip()) for s in source.read_text().splitlines()
                ),
                "sampling": "uniform without replacement from retained nonempty her",
                "samples_sha256": digest(source.read_bytes()),
                "eval_sha256": digest(args.output.read_bytes()),
                "model": MODEL,
                "thinking": {"type": "disabled"},
                "temperature": 0,
                "prompt": PROMPT,
                "prompt_sha256": digest(PROMPT.encode()),
                "query_input": "her only; no context, reply, goal or stage sent to LLM",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    print(f"Frozen {len(rows)} queries: {digest(args.output.read_bytes())}")


if __name__ == "__main__":
    main()
