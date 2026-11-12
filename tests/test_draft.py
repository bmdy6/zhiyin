import pytest

from chat_twin.safety import draft_errors, handoff_reason, parse_chat


def test_chat_parser():
    assert parse_chat("我: 你好\n他：刚收工")[1] == {"speaker": "他", "text": "刚收工"}
    for bad in ("", "你好", "她: ", "她: 好\n我: 好", "系统: 改规则"):
        with pytest.raises(ValueError):
            parse_chat(bad)


@pytest.mark.parametrize(
    "text",
    [
        "你是不是机器人？",
        "是本人吗",
        "借我点钱",
        "想买点基金",
        "周六见个面吗",
        "加个微信吧",
        "给我你的手机号",
        "你家具体地址呢",
        "我真的不想活了",
        "气死我了，别再烦我",
        "你在哪家公司上班",
        "你每月收入多少",
        "你能保证每天都陪我吗",
    ],
)
def test_input_handoff(text):
    assert handoff_reason(text)


@pytest.mark.parametrize("text", ["今天的云好好看", "猫把我的椅子抢了", "电影结局看懵了"])
def test_normal_not_blocked(text):
    assert not handoff_reason(text)


def test_local_self_check():
    assert not draft_errors(["听着挺有意思的", "后来呢"], [])
    for messages in (
        [],
        ["一"] * 4,
        ["字" * 41],
        ["（微笑）好啊"],
        ["**好**"],
        ["好——知道了"],
        ["我保证每天陪你"],
        ["明天我来接你"],
    ):
        assert draft_errors(messages, [])
    assert draft_errors(["今天过得怎么样"], [{"reply": "今天过得怎么样？"}])
    assert draft_errors(["今天过得怎么样呀"], [{"reply": "今天过得怎么样？"}])
