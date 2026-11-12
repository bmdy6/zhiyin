"""Acceptance invariants for committed artifacts; no API or raw downloads."""

import json
import re
from collections import Counter
from pathlib import Path

from scripts.clean import REASONS, validate_label

DATA = Path(__file__).resolve().parents[1] / "data"


def load(name):
    return [json.loads(line) for line in (DATA / name).read_text().splitlines()]


def test_partition_schema_and_counts():
    kept, dropped = load("samples.jsonl"), load("samples.dropped.jsonl")
    rows = kept + dropped
    assert len(rows) == len({r["id"] for r in rows}) == 1437
    assert Counter(r["source"] for r in rows) == {
        "qingsheng/candidates": 1395,
        "qingsheng/examples": 42,
    }
    assert sum(r["reason"] == "broken" for r in dropped) / 1437 < 0.01
    assert all(r["reason"] in REASONS for r in dropped)
    for row in kept:
        assert row["reply"] and len(row["reply"]) <= 60
        assert type(row["stage"]) is int and 1 <= row["stage"] <= 7
        assert row["stage_name"] and row["tags"]
        assert row["scene"] in {"online", "offline", "unknown"}
        assert {"her", "context", "bad", "goal", "why", "source_id"} <= row.keys()
        assert not re.search(r"[（(][^）)]+[）)]", row["reply"])
    targets = {r["id"] for r in kept}
    for row in dropped:
        if row["reason"] == "duplicate":
            assert row["duplicate_of"] in targets
            assert row["similarity"] > 0.92
    assert {r["stage_name"] for r in rows if r["stage"] == 5} >= {"升温推进", "约会实战"}


def test_all_records_have_valid_audits_and_uncertainty_is_visible():
    kept, dropped, audits = (
        load("samples.jsonl"),
        load("samples.dropped.jsonl"),
        load("labels.jsonl"),
    )
    assert len(audits) == len({a["id"] for a in audits}) == 1437
    assert {a["id"] for a in audits} == {r["id"] for r in kept + dropped}
    kept_by_id = {r["id"]: r for r in kept}
    for audit in audits:
        assert audit["model"] == "deepseek-flash" and audit["thinking"] == "disabled"
        assert re.fullmatch(r"[0-9a-f]{64}", audit["input_sha256"])
        label = validate_label(audit["label"])
        if audit["id"] in kept_by_id and (label["review"] or label["confidence"] < 0.9):
            assert kept_by_id[audit["id"]]["review"]


def test_report_includes_exactly_thirty_originals_from_each_partition():
    text = (DATA / "REPORT.md").read_text()
    keep_section, drop_section = text.split("## 随机30条保留")[1].split("## 随机30条删除")
    for section, name in [(keep_section, "samples.jsonl"), (drop_section, "samples.dropped.jsonl")]:
        excerpts = [json.loads(s) for s in re.findall(r"```json\n(.*?)\n```", section, re.DOTALL)]
        source = {r["id"]: r for r in load(name)}
        assert len(excerpts) == len({r["id"] for r in excerpts}) == 30
        assert all(row == source[row["id"]] for row in excerpts)


def test_rework_annotation_provenance_and_named_regressions():
    rows = load("samples.jsonl") + load("samples.dropped.jsonl")
    annotated = [r for r in rows if "stripped_annotation" in r]
    assert annotated
    for row in annotated:
        assert row["original_reply"] != row["reply"]
        assert all(0 < len(note) <= 15 for note in row["stripped_annotation"].splitlines())
    by_id = {r["id"]: r for r in rows}
    for sample_id, annotation in [
        ("qs-c-3388", "幽默化解"),
        ("qs-c-2545", "轻松语气，不进不退"),
        ("qs-c-2781", "眨眼表情"),
    ]:
        row = by_id[sample_id]
        assert row["stripped_annotation"] == annotation
        assert row["reply"] == row["original_reply"].removesuffix(f"（{annotation}）")
        assert row.get("reason") != "not_sendable"
    assert by_id["qs-c-2418"]["reason"] == "manipulative"


def test_report_uses_the_new_rework_seed():
    import random

    from scripts.clean import REPORT_SEED

    assert REPORT_SEED != 20261112
    text = (DATA / "REPORT.md").read_text()
    assert str(REPORT_SEED) in text
    sections = text.split("## 随机30条保留")[1].split("## 随机30条删除")
    for section, name in zip(sections, ("samples.jsonl", "samples.dropped.jsonl")):
        actual = [
            json.loads(s)["id"] for s in re.findall(r"```json\n(.*?)\n```", section, re.DOTALL)
        ]
        expected = [r["id"] for r in random.Random(REPORT_SEED).sample(load(name), 30)]
        assert actual == expected


def test_round2_review_decisions():
    kept = {r["id"]: r for r in load("samples.jsonl")}
    dropped = {r["id"]: r for r in load("samples.dropped.jsonl")}
    assert kept["qs-c-3386"]["reply"] == "哦是阿，不过他还没到"
    assert kept["qs-c-3386"]["stripped_annotation"] == "坚持目送"
    assert dropped["qs-c-3044"]["reason"] == "vulgar"
