"""Synthetic regressions for ambiguous aliases and structural labels."""

import pytest

from zhiyin.context_privacy import Redactor


@pytest.mark.parametrize(
    "alias",
    [
        "我",
        "在",
        "要",
        "同学",
        "朋友",
        "你好",
        "谢谢",
        "明天见",
        "北京",
        "上海",
        "12",
        "（",
        "？！",
        "😊",
        "❤️",
        "AB",
        "Echo",
    ],
)
def test_ambiguous_aliases_only_mask_explicit_addresses(alias):
    redactor = Redactor({alias: "P1"})
    ordinary = f"我在等同学，要去北京。（合成正文 {alias}）"
    assert redactor.text(ordinary) == ordinary
    for prefix in ["昵称：", "昵称: ", "备注：", "姓名：", "@", "＠ "]:
        assert redactor.text(f"{prefix}{alias}，你好") == f"{prefix}P1，你好"


@pytest.mark.parametrize("text", ["@我在这里", "昵称：同学们", "@ABCD", "昵称：北京路"])
def test_address_must_match_the_whole_alias(text):
    assert Redactor({"我": "P1", "同学": "P2", "AB": "P3", "北京": "P4"}).text(text) == text


def test_uncommon_names_and_identifiers_still_mask_in_body():
    redactor = Redactor({"合成甲": "P1", "synthetic_id": "P1"}, {"synthetic_id"})
    assert redactor.text("合成甲在等我 synthetic_id") == "P1在等我 P1"
    assert redactor.text("合成甲 synthetic_id", reveal_names=True) == "合成甲 P1"


def test_reveal_names_keeps_ambiguous_addresses_but_not_private_details():
    redactor = Redactor({"同学": "P1"})
    assert redactor.text("@同学 13800138000 a@example.com", True) == "@同学 [隐私] [隐私]"


def test_labels_skip_contact_replacement_but_keep_other_privacy_masks():
    redactor = Redactor(
        {"我": "P1", "在": "P2", "同学": "P3", "（": "P4", "合成甲": "P5"}, {"合成甲"}
    )
    value = {
        "groups": ["同学", "哥们 / 人脉（前同事）", "合成甲"],
        "relationship": "合成甲在同学群",
        "nested": {"groups": ["同学", "合成甲", "13800138000"]},
        "text": "合成甲说我在等同学（明天见）",
    }
    masked = redactor.value(value)
    assert masked["groups"] == value["groups"]
    assert masked["relationship"] == value["relationship"]
    assert masked["nested"]["groups"] == ["同学", "合成甲", "[隐私]"]
    assert masked["text"] == "P5说我在等同学（明天见）"
    assert redactor.value(value, reveal_names=True)["groups"] == value["groups"]


def test_numeric_alias_does_not_disable_secret_masking_or_damage_codes():
    redactor = Redactor({"12": "P1", "合成甲": "P8608"})
    assert redactor.text("共12次，合成甲的验证码123456") == "共12次，P8608的验证码[隐私]"
