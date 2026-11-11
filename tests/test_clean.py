import json
from pathlib import Path

import numpy as np
import pytest

from scripts.clean import (
    apply_label,
    deduplicate,
    parse_candidates,
    parse_examples,
    rule_reason,
    validate_label,
)


def sample(**kw):
    return dict(
        id="x",
        stage=3,
        stage_name="建立好感",
        context="普通聊天",
        her="你好",
        reply="今天过得怎么样？",
        goal="自然接话",
        why=None,
        bad=[],
        scene="unknown",
        tags=[],
        **kw,
    )


def test_candidates_both_stage_fives_and_fields():
    def raw(i, name):
        return {
            "id": i,
            "prompt": f"【情境】酒吧聊天\n【当前阶段】阶段5 {name}\n\n她刚发来：“你好”",
            "expected_output": '### 核心策略目标（最重要的评分维度）\n接话\n### 参考优秀答案\n“你好呀”\n### 不好的选项（避免出现类似内容）\n - "哦（敷衍）"\n### 评分说明\nend',
        }

    rows = parse_candidates({"evals": [raw(1, "升温推进"), raw(2, "约会实战")]})
    assert [r["stage"] for r in rows] == [5, 5]
    assert [r["stage_name"] for r in rows] == ["升温推进", "约会实战"]
    assert rows[0]["her"] == "你好" and rows[0]["reply"] == "你好呀"
    assert rows[0]["bad"] == ["哦（敷衍）"] and rows[0]["scene"] == "offline"


def test_examples_no_speech_and_unmapped_stage():
    text = "## 阶段1：开场搭讪\n**场景**：同事下班\n她：（无台词，准备离开）\n→ 嗨，之前还没打招呼。\n为什么：自然开场\n别这样：拦住她\n## 挽回/冷激活\n**场景**：再次见面\n她：你好\n→ 好久不见\n为什么：自然回应\n别这样：指责"
    a, b = parse_examples(text)
    assert a["her"] == "" and "准备离开" in a["context"]
    assert a["why"] == "自然开场" and a["reply"] == "嗨，之前还没打招呼。"
    assert b["stage"] == 7 and b["stage_name"] == "挽回/冷激活" and b["review"]


@pytest.mark.parametrize(
    "reason,field,value",
    [
        ("vulgar", "reply", "我的下面已经竖起来了"),
        ("vulgar", "reply", "今晚来开房上床吧"),
        ("manipulative", "reply", "你这样的丑八怪还挑什么"),
        ("manipulative", "goal", "故意制造不安让她倒追"),
        ("not_sendable", "reply", "你好（转头微笑）"),
        ("not_sendable", "reply", "你" * 61),
        ("not_sendable", "reply", "继续坐着吃完，不追出去。"),
    ],
)
def test_rules(reason, field, value):
    row = sample()
    row[field] = value
    assert rule_reason(row)[0] == reason


def test_boundary_good_reply_not_bad_or_her():
    row = sample()
    row["bad"] = ["今晚开房"]
    row["her"] = "你说的废物测试我不喜欢"
    assert rule_reason(row) is None
    row["reply"] = "你" * 60
    assert rule_reason(row) is None


def test_broken():
    row = sample()
    row["reply"] = ""
    assert rule_reason(row)[0] == "broken"


def test_duplicate_first_retained_and_strict_threshold():
    rows = [sample(), sample(), sample()]
    for i, row in enumerate(rows):
        row["id"] = str(i)
    vectors = np.array([[1.0, 0.0], [0.99, 0.01], [0.0, 1.0]])
    kept, dropped = deduplicate(rows, vectors)
    assert [r["id"] for r in kept] == ["0", "2"]
    assert dropped[0]["reason"] == "duplicate" and dropped[0]["duplicate_of"] == "0"
    a = np.array([[1.0, 0.0], [0.92, np.sqrt(1 - 0.92**2)]])
    assert len(deduplicate(rows[:2], a)[0]) == 2


def test_duplicate_does_not_chain_through_dropped():
    rows = [sample(), sample(), sample()]
    for i, r in enumerate(rows):
        r["id"] = str(i)
    vectors = np.array([[1.0, 0.0], [0.94, np.sqrt(1 - 0.94**2)], [0.8, 0.6]])
    assert [r["id"] for r in deduplicate(rows, vectors)[0]] == ["0", "2"]


def test_uncertain_drop_is_kept_for_review():
    row = sample()
    label = {
        "tags": ["接话"],
        "scene": "unknown",
        "reason": "manipulative",
        "review": True,
        "confidence": 0.6,
        "evidence": "可能在打压",
    }
    kept = apply_label(row, label)
    assert kept["review"] and "reason" not in kept


@pytest.mark.parametrize(
    "change",
    [
        {"review": "false"},
        {"reason": "other"},
        {"confidence": True},
        {"scene": "elsewhere"},
        {"tags": "接话"},
    ],
)
def test_bad_label_contract(change):
    label = {
        "tags": ["接话"],
        "scene": "unknown",
        "reason": None,
        "review": False,
        "confidence": 0.98,
        "evidence": "自然回应",
    }
    label.update(change)
    with pytest.raises(ValueError):
        validate_label(label)


def test_real_sources_when_cached():
    root = Path("data/raw")
    if not (root / "evals_candidates_v1.json").exists():
        pytest.skip("raw not cached")
    rows = parse_candidates(json.loads((root / "evals_candidates_v1.json").read_text()))
    examples = parse_examples((root / "examples-library.md").read_text())
    assert len(rows) == 1395 and len(examples) == 42
    assert all(r["reply"] and 1 <= r["stage"] <= 7 for r in rows + examples)
    assert len({r["id"] for r in rows + examples}) == 1437


def test_llm_uses_disabled_thinking_and_cache(monkeypatch, tmp_path):
    import httpx

    from scripts import clean

    calls = []
    label = {
        "tags": ["接话"],
        "scene": "online",
        "reason": None,
        "review": False,
        "confidence": 0.98,
        "evidence": "自然回应",
    }

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert payload["model"] == "deepseek-flash"
        assert payload["thinking"] == {"type": "disabled"}
        assert json.loads(payload["messages"][1]["content"])["goal"] == sample()["goal"]
        assert request.headers["Authorization"] == "Bearer synthetic-test-key"
        return httpx.Response(
            200,
            json={
                "model": "deepseek-flash",
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(label)}}],
                "usage": {"total_tokens": 100},
            },
        )

    original = httpx.Client
    monkeypatch.setattr(clean, "read_key", lambda: "synthetic-test-key")
    monkeypatch.setattr(
        clean.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handler), **kw)
    )
    first = clean.classify(sample(), "prompt", tmp_path, False)
    second = clean.classify(sample(), "prompt", tmp_path, True)
    assert first == second and len(calls) == 1
    assert "synthetic-test-key" not in next(tmp_path.glob("*.json")).read_text()
    with pytest.raises(ValueError, match="Missing label cache"):
        clean.classify(sample(), "changed prompt", tmp_path, True)


def test_invalid_llm_response_retains_for_review(monkeypatch, tmp_path):
    import httpx

    from scripts import clean

    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": "not JSON"}}]}
        )

    original = httpx.Client
    monkeypatch.setattr(clean, "read_key", lambda: "synthetic-test-key")
    monkeypatch.setattr(clean.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        clean.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handler), **kw)
    )
    audit = clean.classify(sample(), "prompt", tmp_path, False)
    result = clean.apply_label(sample(), audit["label"])
    assert len(calls) == 3 and "error" in audit
    assert result["review"] and "reason" not in result
    assert not list(tmp_path.glob("*.json"))  # Failed results never poison success caches.


def test_source_checksum_checked_before_download(tmp_path, monkeypatch):
    from scripts import clean

    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "evals_candidates_v1.json").write_text("changed")
    monkeypatch.setattr(clean.httpx, "get", lambda *a, **kw: pytest.fail("unexpected request"))
    with pytest.raises(ValueError, match="checksum mismatch"):
        clean.download_sources(tmp_path)


def test_pilot_is_twenty_and_covers_both_sources_and_stage_fives():
    from scripts import clean

    raw = Path("data/raw")
    if not raw.exists():
        pytest.skip("raw not cached")
    rows = clean.parse_candidates(json.loads((raw / "evals_candidates_v1.json").read_text()))
    rows += clean.parse_examples((raw / "examples-library.md").read_text())
    picked = clean.pilot_rows(rows)
    assert len(picked) == len({r["id"] for r in picked}) == 20
    assert {r["source"] for r in picked} == {"qingsheng/candidates", "qingsheng/examples"}
    assert {"升温推进", "约会实战"} <= {r["stage_name"] for r in picked}
    assert picked == clean.pilot_rows(rows)


def test_negated_manipulation_in_goal_is_not_a_rule_hit():
    row = sample()
    row["goal"] = "展示真实兴趣，而非假装不在乎后快速离开"
    assert rule_reason(row) is None
    row["goal"] = "不要打压自信，尊重对方"
    assert rule_reason(row) is None


def test_full_requires_exact_reviewed_pilot(tmp_path):
    from scripts import clean

    prompt = "test prompt"
    (tmp_path / "pilot.jsonl").write_text("a reviewed pilot\n")
    approval = {
        "count": 20,
        "prompt_sha256": clean.digest(prompt.encode()),
        "pilot_sha256": clean.digest((tmp_path / "pilot.jsonl").read_bytes()),
        "status": "reviewed-before-full",
    }
    (tmp_path / "pilot-approved.json").write_text(json.dumps(approval))
    clean.validate_pilot(tmp_path, prompt)
    (tmp_path / "pilot.jsonl").write_text("changed pilot\n")
    with pytest.raises(ValueError, match="Pilot review missing or stale"):
        clean.validate_pilot(tmp_path, prompt)


def test_mismatched_cache_is_rejected(tmp_path):
    from scripts import clean

    row = sample()
    prompt = "prompt"
    key = clean.label_key(row, prompt)
    entry = {
        "id": "wrong-id",
        "input_sha256": key,
        "model": clean.MODEL,
        "thinking": "disabled",
        "label": {
            "tags": ["接话"],
            "scene": "unknown",
            "reason": None,
            "review": False,
            "confidence": 0.98,
            "evidence": "自然回应",
        },
    }
    (tmp_path / f"{key}.json").write_text(json.dumps(entry))
    with pytest.raises(ValueError, match="Label cache identity mismatch"):
        clean.classify(row, prompt, tmp_path, True)


@pytest.mark.parametrize(
    "reply", ["阿笨，刚看到只丑萌的斗牛犬，像你失散的表亲", "因为你不是个乖女孩"]
)
def test_context_dependent_teasing_is_kept_for_review(reply):
    row = sample()
    row["reply"] = reply
    label = {
        "tags": ["幽默"],
        "scene": "unknown",
        "reason": None,
        "review": False,
        "confidence": 0.95,
        "evidence": "轻松玩笑",
    }
    result = apply_label(row, label)
    assert result["review"] and "reason" not in result


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("reply", "想起你了。[附上故事原文]", "not_sendable"),
        ("goal", "拒绝成本极高，随后过渡到接吻", "manipulative"),
        ("goal", "不给对方思考要不要拒绝的空间", "manipulative"),
        ("goal", "不是接受拒绝，而是继续推进号码交换", "manipulative"),
    ],
)
def test_explicit_placeholder_and_boundary_bypass(field, value, expected):
    row = sample()
    row[field] = value
    assert rule_reason(row)[0] == expected


def test_unbracketed_action_script_is_not_sendable():
    row = sample()
    row["reply"] = '笑一下，然后说："最近怎么样？"'
    assert rule_reason(row)[0] == "not_sendable"


def test_repackaging_phone_refusal_is_manipulative():
    row = sample()
    row["her"] = "我不习惯给陌生人电话"
    row["reply"] = "说得对，绝不能习惯！就留这一次，下不为例。"
    assert rule_reason(row)[0] == "manipulative"


@pytest.mark.parametrize(
    "reply,cleaned,annotation",
    [
        ("我绝对没对早餐店的阿姨这样过（幽默化解）", "我绝对没对早餐店的阿姨这样过", "幽默化解"),
        (
            "我知道，这完全是我们两个人之间的事（轻松语气，不进不退）",
            "我知道，这完全是我们两个人之间的事",
            "轻松语气，不进不退",
        ),
        ("明晚7点有家店会把你变成海盗（眨眼表情）", "明晚7点有家店会把你变成海盗", "眨眼表情"),
        ("(微笑)你好", "你好", "微笑"),
        ("（微笑）你好（眨眼）", "你好", "微笑\n眨眼"),
        ("你好（语气轻松自然，带一点俏皮和笑意）", "你好", "语气轻松自然，带一点俏皮和笑意"),
    ],
)
def test_strip_only_short_edge_annotations(reply, cleaned, annotation):
    from scripts.clean import strip_annotations

    row = sample()
    row["reply"] = reply
    result = strip_annotations(row)
    assert result["reply"] == cleaned
    assert result["stripped_annotation"] == annotation
    assert result["original_reply"] == reply
    assert row["reply"] == reply  # Parsing input is not mutated.
    assert strip_annotations(result) == result


@pytest.mark.parametrize(
    "reply",
    [
        "你最近怎么样（她说有）那就好",
        "你好（确认位置后）再见",
        "（她说有）那就好",
        "你好（确认位置后）",
        "你好（语气轻松自然，带一点俏皮和笑意呀）",  # 16 chars is too long.
        "我吃了饺子（韭菜馅的）",
        "你好（我也喜欢微笑）",
        "（微笑）",  # No spoken reply remains.
        "你好（微笑)",  # Mismatched parentheses are never guessed at.
    ],
)
def test_do_not_strip_scripts_long_annotations_or_spoken_asides(reply):
    from scripts.clean import strip_annotations

    row = sample()
    row["reply"] = reply
    assert strip_annotations(row) == row
    assert "stripped_annotation" not in strip_annotations(row)


@pytest.mark.parametrize(
    "goal",
    [
        "无需征求同意，就可以继续行动",
        "去掉说不的空间",
        "让她体面接受",
        "她需要的不是事实，而是一个能让自己体面接受的心理框架。这个回应提供了借口。",
        "故意制造焦虑来吸引她",
        "贬低对方来抬高自己",
    ],
)
def test_goal_can_make_an_ordinary_reply_manipulative(goal):
    row = sample()
    row["goal"] = goal
    assert rule_reason(row)[0] == "manipulative"


@pytest.mark.parametrize(
    "goal",
    [
        "幽默接话",
        "主导话题但尊重她的选择",
        "保持自信",
        "不要故意制造焦虑",
        "避免贬低对方来抬高自己",
    ],
)
def test_goal_does_not_confuse_confidence_or_negation_with_manipulation(goal):
    row = sample()
    row["goal"] = goal
    assert rule_reason(row) is None


def test_strip_spaced_annotation_preserves_exact_note():
    from scripts.clean import strip_annotations

    row = sample()
    row["reply"] = "你好（ 微笑 ）"
    result = strip_annotations(row)
    assert result["reply"] == "你好"
    assert result["stripped_annotation"] == " 微笑 "


@pytest.mark.parametrize(
    "reply",
    [
        "你好（停顿等她反应，随即拉过来舌吻）",
        "你好（停顿，等她再次邀请）",
    ],
)
def test_even_a_short_edge_note_cannot_hide_a_conditional_script(reply):
    from scripts.clean import strip_annotations

    row = sample()
    row["reply"] = reply
    assert strip_annotations(row) == row
    assert rule_reason(strip_annotations(row))[0] == "not_sendable"


@pytest.mark.parametrize(
    "reply,cleaned,annotation",
    [
        ("（送三个拥抱表情）你好", "你好", "送三个拥抱表情"),
        ("你好（苦笑，示意她去看那对情侣）", "你好", "苦笑，示意她去看那对情侣"),
        ("（立刻打回去）刚才是我不对", "刚才是我不对", "立刻打回去"),
    ],
)
def test_compound_edge_expressions_and_simple_actions(reply, cleaned, annotation):
    from scripts.clean import strip_annotations

    row = sample()
    row["reply"] = reply
    result = strip_annotations(row)
    assert result["reply"] == cleaned and result["stripped_annotation"] == annotation


def test_phone_number_placeholder_is_not_directly_sendable():
    row = sample()
    row["reply"] = "如需人工服务请拨 [自己号码]，谢谢合作"
    assert rule_reason(row)[0] == "not_sendable"
