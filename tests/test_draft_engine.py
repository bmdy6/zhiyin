import asyncio
import copy
import json

from zhiyin.contacts import ContactStore
from zhiyin.draft import DraftEngine

ANALYSIS = {
    "summary": "对方分享了猫抢椅子的趣事",
    "stage": "friend",
    "intent": "分享",
    "mood": "轻松",
    "unknowns": [],
}
DECISION = {"action": "接话", "reason": "接住对方分享的趣事"}
GOOD = {"wait_minutes": 0, "messages": ["椅子现在归它了吧"], "reason": "顺着猫抢椅子的趣事接一句"}
CHECK = {"passed": True, "issues": [], "handoff": False}


class Model:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def complete(self, step, payload):
        self.calls.append((step, copy.deepcopy(payload)))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class Search:
    def __init__(self):
        self.calls = []

    async def search(self, message, stage=None, k=5):
        self.calls.append((message, stage, k))
        return [{"id": "synthetic", "reply": "今天猫很可爱", "goal": "接分享"}]


def engine(tmp_path, responses):
    model, search = Model(responses), Search()
    style = tmp_path / "spoken.md"
    style.write_text("合成档案哨兵，不得被输出或持久化")
    return (
        DraftEngine(model, search, ContactStore(tmp_path / "state"), style_path=style),
        model,
        search,
    )


def run(instance, **kwargs):
    return asyncio.run(
        instance.run("wechat", "合成猫友", "我: 猫睡了吗\n她: 它把我的椅子抢了", **kwargs)
    )


def test_friend_draft_and_private_style_only_sent_to_writing(tmp_path):
    instance, model, search = engine(tmp_path, [ANALYSIS, DECISION, GOOD, CHECK])
    result = run(instance)
    assert "[发送] 椅子现在归它了吧" in result.render()
    assert search.calls[0][1] is None
    assert result.analysis.stage == "friend"
    assert "合成档案哨兵" not in json.dumps(model.calls[:2], ensure_ascii=False)
    for path in (tmp_path / "state").rglob("*"):
        if path.is_file():
            assert "合成档案哨兵" not in path.read_text()
    assert "椅子现在归它" not in (tmp_path / "state/总文档.md").read_text()


def test_risk_no_model_or_search(tmp_path):
    instance, model, search = engine(tmp_path, [])
    result = asyncio.run(instance.run("wechat", "合成", "她: 你是机器人吗"))
    assert result.handoff and not result.messages
    assert not model.calls and not search.calls


def test_unknowns_handoff(tmp_path):
    instance, _model, search = engine(
        tmp_path, [dict(ANALYSIS, unknowns=["对象不明", "语境不明", "含义不明"])]
    )
    assert run(instance).handoff
    assert not search.calls


def test_rewrite_once_and_second_failure_handoff(tmp_path):
    bad = dict(GOOD, messages=["我保证每天陪你"])
    instance, model, _ = engine(tmp_path, [ANALYSIS, DECISION, bad, bad])
    result = run(instance)
    assert result.handoff and not result.messages
    assert [c[0] for c in model.calls] == ["analyze", "decide", "write", "write"]


def test_semantic_check_rewrite_then_success(tmp_path):
    instance, model, _ = engine(
        tmp_path,
        [ANALYSIS, DECISION, GOOD, dict(CHECK, passed=False, issues=["语气生硬"]), GOOD, CHECK],
    )
    assert not run(instance).handoff
    assert len([c for c in model.calls if c[0] == "write"]) == 2


def test_semantic_handoff_no_rewrite(tmp_path):
    instance, model, _ = engine(
        tmp_path,
        [ANALYSIS, DECISION, GOOD, dict(CHECK, passed=False, issues=["需本人作答"], handoff=True)],
    )
    assert run(instance).handoff
    assert len([c for c in model.calls if c[0] == "write"]) == 1


def test_provider_failure_is_redacted(tmp_path):
    instance, _, _ = engine(tmp_path, [RuntimeError("秘密请求正文与密钥")])
    result = run(instance)
    assert result.handoff
    assert "秘密" not in result.render()


def test_missing_profile_notice(tmp_path):
    instance, _, _ = engine(tmp_path, [ANALYSIS, DECISION, GOOD, CHECK])
    instance.style_path = tmp_path / "missing"
    assert "profiles/example.md" in run(instance).render()


def test_decision_handoff_and_no_reply(tmp_path):
    instance, _, search = engine(tmp_path, [ANALYSIS, dict(DECISION, action="交给我")])
    assert run(instance).handoff
    assert not search.calls
    instance, _, search = engine(tmp_path, [ANALYSIS, dict(DECISION, action="先不回")])
    result = run(instance)
    assert not result.handoff and not result.messages
    assert "[先不回]" in result.render()
    assert not search.calls


def test_rewrite_receives_actionable_semantic_feedback(tmp_path):
    instance, model, _ = engine(
        tmp_path,
        [
            ANALYSIS,
            DECISION,
            GOOD,
            dict(CHECK, passed=False, issues=["一次做了两件事：接话后又追问"]),
            GOOD,
            CHECK,
        ],
    )
    assert not run(instance).handoff
    writes = [payload for step, payload in model.calls if step == "write"]
    assert "一次做了两件事：接话后又追问" in writes[1]["previous_errors"]


def test_style_verbatim_not_exposed_even_if_model_check_misses_it(tmp_path):
    secret = "这是合成档案里面的一段完整私人说明绝对不能被输出到草稿里"
    instance, model, _ = engine(
        tmp_path, [ANALYSIS, DECISION, dict(GOOD, messages=[secret]), dict(GOOD, messages=[secret])]
    )
    instance.style_path.write_text(secret)
    result = run(instance)
    assert result.handoff and secret not in result.render()

    assert [step for step, _ in model.calls] == ["analyze", "decide", "write", "write"]


class MyReplies:
    def __init__(self, store, rows):
        self.store, self.rows = store, rows
        self.calls = []

    def search(self, message, contact, k):
        self.calls.append((message, contact, k))
        return self.rows


def wechat_engine(tmp_path, responses, *, has_personal=True):
    from test_wechat import export, msg

    from zhiyin.tone import tone_profile
    from zhiyin.wechat import WeChatStore, import_wechat

    store = WeChatStore(tmp_path / "state")
    export(
        tmp_path / "export",
        {
            "合成猫友": [
                msg(0, "them", "合成语境，猫睡了"),
                msg(1, "me", "合成口吻例子呀"),
            ]
        },
    )
    store.root.mkdir()
    (store.root / "config.toml").write_text('[relationships]\n"C1" = "朋友"')
    import_wechat(store, tmp_path / "export")
    tone_profile(store)
    style = tmp_path / "spoken.md"
    style.write_text("简短，轻松")
    rows = [{"id": "synthetic-1", "date": "2026-01-01", "her": "猫在干嘛", "reply": "它刚睡觉去了"}]
    model, samples = Model(responses), Search()
    return (
        DraftEngine(
            model,
            samples,
            replies=MyReplies(store, rows if has_personal else []),
            style_path=style,
        ),
        model,
        samples,
    )


WECHAT_GOOD = dict(GOOD, rationales=[{"reference": "R1", "rules": [2]}])
WECHAT_CHECK = dict(CHECK, rules=[{"rule": i, "passed": True} for i in range(1, 7)])


def test_wechat_draft_uses_personal_first_and_returns_per_draft_source(tmp_path):
    instance, model, samples = wechat_engine(
        tmp_path, [ANALYSIS, DECISION, WECHAT_GOOD, WECHAT_CHECK]
    )
    result = asyncio.run(instance.run_wechat("合成猫友", "它把我的椅子抢了")).as_tool_result()
    assert result["status"] == "草稿"
    assert not samples.calls
    assert result["drafts"][0]["reference"]["id"] == "synthetic-1"
    assert "2026-01-01" in result["drafts"][0]["reason"]
    assert "规则2" in result["drafts"][0]["reason"]
    payload = model.calls[2][1]
    assert "## 朋友" in payload["wechat_tone"]
    assert "合成口吻例子呀" not in payload["wechat_tone"]
    assert payload["spoken_style"] == "简短，轻松"


def test_wechat_draft_uses_public_only_if_no_personal_match(tmp_path):
    instance, _model, samples = wechat_engine(
        tmp_path, [ANALYSIS, DECISION, WECHAT_GOOD, WECHAT_CHECK], has_personal=False
    )
    result = asyncio.run(instance.run_wechat("C1", "它把我的椅子抢了"))
    assert samples.calls
    assert result.drafts[0]["reference"]["source"] == "public"


def test_wechat_draft_rewrites_after_rule_failures_then_hands_off(tmp_path):
    bad = dict(WECHAT_GOOD, messages=["考了多少分？"])
    failed = dict(
        WECHAT_CHECK,
        passed=True,
        rules=[{"rule": i, "passed": i != 1} for i in range(1, 7)],
    )
    instance, model, _ = wechat_engine(tmp_path, [ANALYSIS, DECISION, bad, WECHAT_GOOD, failed])
    result = asyncio.run(instance.run_wechat("C1", "我有点失落，这次考砸了"))
    assert result.as_tool_result()["status"] == "这条你自己回"
    assert result.drafts == []
    assert any("规则2" in check for check in result.checks)
    assert any("规则1" in check for check in result.checks)
    assert [step for step, _ in model.calls] == ["analyze", "decide", "write", "write", "check"]


def test_wechat_risk_and_exclusion_stop_before_model(tmp_path):
    instance, model, samples = wechat_engine(tmp_path, [])
    blocked = ("要转账吗", "我们见面吧", "你是不是本人", "我不想活了", "你能保证吗")
    for message in blocked:
        assert asyncio.run(instance.run_wechat("C1", message)).handoff
    (instance.replies.store.root / "config.toml").write_text('exclude_contacts=["C1"]\n')
    result = asyncio.run(instance.run_wechat("合成猫友", "熟悉的小问题"))
    assert result.handoff
    assert not model.calls and not samples.calls and not instance.replies.calls
