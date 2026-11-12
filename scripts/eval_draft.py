"""Live acceptance on synthetic conversations only; never writes real contact state."""

import argparse
import asyncio
import hashlib
import json
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from chat_twin.contacts import ContactStore
from chat_twin.draft import DraftEngine
from chat_twin.llm import MODEL, DeepSeek
from chat_twin.resources import ROOT
from chat_twin.safety import draft_errors
from chat_twin.sample_client import SampleClient

FIXTURES = ROOT / "tests/fixtures/draft.jsonl"


async def evaluate() -> dict:
    if not (Path.home() / ".style-polish/spoken.md").is_file():
        raise RuntimeError("Acceptance requires the runtime spoken profile")
    cases = [json.loads(line) for line in FIXTURES.read_text().splitlines()]
    assert Counter(c["group"] for c in cases) == {"normal": 10, "handoff": 5, "friend": 5}
    results = []
    model = DeepSeek()
    async with SampleClient() as samples:
        with tempfile.TemporaryDirectory(prefix="chat-twin-eval-") as temporary:
            store = ContactStore(Path(temporary) / "state")
            engine = DraftEngine(model, samples, store)
            for case in cases:
                contact = "合成-" + case["id"]
                if case.get("context"):
                    store.update("soul", contact, case["context"], "friend")
                result = await engine.run("soul", contact, case["chat"])
                # Independently check output constraints using the exact retrieved references.
                refs = [
                    row
                    for row in json.loads((ROOT / "data/index/documents.json").read_text())
                    if row["id"] in result.sample_ids
                ]
                errors = draft_errors(result.messages, refs) if result.messages else []
                passed = (
                    result.handoff
                    if case["expected"] == "handoff"
                    else not result.handoff and bool(result.messages) and not errors
                )
                passed = passed and not result.notices
                if case["group"] == "friend":
                    passed = (
                        passed and result.analysis is not None and result.analysis.stage == "friend"
                    )
                entry = {
                    "id": case["id"],
                    "group": case["group"],
                    "expected": case["expected"],
                    "passed": passed,
                    "handoff": result.handoff,
                    "stage": result.analysis.stage if result.analysis else None,
                    "attempts": result.attempts,
                    "sample_ids": result.sample_ids,
                    "local_errors": errors,
                    "retry_checks": result.checks,
                    "output": result.render(),
                }
                results.append(entry)
                print(f"{case['id']}: {'PASS' if passed else 'FAIL'}", flush=True)
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "model": MODEL,
        "thinking": {"type": "disabled"},
        "prompts_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT / "prompts").glob("*.txt"))
        },
        "fixture_sha256": hashlib.sha256(FIXTURES.read_bytes()).hexdigest(),
        "style_source": "runtime ~/.style-polish/spoken.md; contents not persisted",
        "contact_state": "TemporaryDirectory; cleaned after evaluation",
        "transport": "official MCP stdio",
        "count": len(results),
        "passed": sum(r["passed"] for r in results),
        "results": results,
        "calls": model.metadata,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "evals/draft-results.json")
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Output exists; choose a new report path to preserve run evidence")
    result = asyncio.run(evaluate())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"Passed {result['passed']}/{result['count']}")
    if result["passed"] != result["count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
