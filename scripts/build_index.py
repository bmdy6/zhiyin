"""Build the public her+context index. Only this command may download the model."""

import argparse
from pathlib import Path

from zhiyin.retrieval import ROOT, build_index


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--model-cache", type=Path, default=ROOT / "data/cache/models")
    args = parser.parse_args()
    build_index(
        ROOT / "data/samples.jsonl",
        ROOT / "data/index",
        offline=args.offline,
        model_cache=args.model_cache,
    )
    print("Index built: data/index/ (her + context; no reply/goal embedded)")


if __name__ == "__main__":
    main()
