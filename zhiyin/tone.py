"""Deterministic private voice statistics from the imported messages."""

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from statistics import median

from zhiyin.retrieval import tokenize
from zhiyin.wechat import RELATIONSHIPS, WeChatStore

PROFILE = "口吻.md"
PARTICLES = re.compile(r"(?:吧|呢|嘛|呀|啊|啦|哦|噢|诶|呗|呐|咯|哈+|嗯+)(?=[\W_]*$)")
EMOJI = re.compile(
    r"[\U0001F1E6-\U0001F1FF]{2}|[#*0-9]\uFE0F?\u20E3|"
    r"(?:[\U0001F300-\U0001FAFF\u2600-\u27BF]\uFE0F?[\U0001F3FB-\U0001F3FF]?)"
    r"(?:\u200D[\U0001F300-\U0001FAFF\u2600-\u27BF]\uFE0F?[\U0001F3FB-\U0001F3FF]?)*|"
    r"\[(?:微笑|呲牙|偷笑|调皮|捂脸|笑哭|破涕为笑|大笑|旺柴|狗头|流泪|强|鼓掌|抱拳|耶|爱心|玫瑰|疑问|好的|叹气)\]"
)


@dataclass
class Style:
    sent: int = 0
    single: int = 0
    burst: int = 0
    stickers: int = 0
    lengths: list[int] = field(default_factory=list)
    punctuation: Counter = field(default_factory=Counter)
    particles: Counter = field(default_factory=Counter)
    emoji: Counter = field(default_factory=Counter)
    words: Counter = field(default_factory=Counter)
    examples: dict[str, None] = field(default_factory=dict)

    def message(self, row, tokens: list[str]) -> None:
        self.sent += 1
        self.stickers += row["type"] == 47
        if not row["is_text"]:
            return
        text = row["text"].strip()
        length = sum(not c.isspace() for c in text)
        self.lengths.append(length)
        self.punctuation.update(c for c in text if unicodedata.category(c).startswith("P"))
        self.particles.update(PARTICLES.findall(text))
        self.emoji.update(EMOJI.findall(text))
        self.words.update(word for word in tokens if word.isalpha() and len(word) <= 16)
        if (
            2 <= length <= 24
            and len(text.splitlines()) == 1
            and not re.search(r"https?://|\d{5,}|[@<>`|]", text)
        ) and len(self.examples) < 4:
            self.examples.setdefault(text, None)

    def render(self, *, examples: bool = False) -> str:
        lengths = sorted(self.lengths)
        result = [
            f"- 发出消息 {self.sent} 条，其中可读文本 {len(lengths)} 条。",
            f"- 单条 {self.single} 轮，连发 {self.burst} 轮。",
        ]
        if lengths:
            result += [
                (
                    f"- 文本长度均值 {sum(lengths) / len(lengths):.2f}，"
                    f"中位数 {median(lengths):g}，P90 {lengths[math.ceil(0.9 * len(lengths)) - 1]}。"
                ),
                f"- 标点 {top(self.punctuation)}。",
                f"- 句末语气词 {top(self.particles)}。",
                f"- 表情 {top(self.emoji)}，微信贴图 {self.stickers} 条。",
                f"- 常用词 {top(self.words)}。",
            ]
        if examples:
            result.append("### 本地短句")
            short = list(self.examples)
            result.extend(f"> {example}\n" for example in short)
            if not short:
                result.append("暂无样本。")
        return "\n".join(result)


def top(counter: Counter) -> str:
    ranked = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    return "、".join(f"{value}（{count}）" for value, count in ranked[:12]) or "无"


def profile_revision(db) -> str:
    rows = db.execute(
        """SELECT c.id, c.relationship, count(m.id) AS n FROM visible_contacts c
           LEFT JOIN messages m ON m.contact_id=c.id GROUP BY c.id ORDER BY c.id"""
    )
    return hashlib.sha256(json.dumps([tuple(row) for row in rows]).encode()).hexdigest()


def tone_profile(store: WeChatStore) -> dict:
    overall = Style()
    groups = {name: Style() for name in RELATIONSHIPS}
    last = None
    run = 0
    current = overall

    def finish_run() -> None:
        if run == 1:
            overall.single += 1
            current.single += 1
        if run > 1:
            overall.burst += 1
            current.burst += 1

    with store.connect() as db:
        rows = db.execute(
            """SELECT m.*, c.relationship FROM messages m JOIN visible_contacts c ON c.id=m.contact_id
               WHERE type NOT IN (10000,10002) ORDER BY contact_id, time, seq, m.id"""
        )
        for row in rows:
            if last is not None and (
                row["contact_id"] != last["contact_id"]
                or row["time"] - last["time"] >= 21600
                or row["sender"] != last["sender"]
            ):
                finish_run()
                run = 0
            if row["sender"] == "me":
                current = groups[row["relationship"]]
                tokens = tokenize(row["text"]) if row["is_text"] else []
                overall.message(row, tokens)
                current.message(row, tokens)
                run += 1
            last = row
        finish_run()

        revision = profile_revision(db)
        text = [
            f"<!-- zhiyin:{revision} -->",
            "# 微信口吻",
            (
                "长度按非空白 Unicode 字符计。六小时以上间隔重开一段。"
                "轮次按相邻同一发送方合并，标点、词和短句来自可读文本。"
                "系统消息除外，微信贴图单列。"
            ),
            "## 总体\n" + overall.render(),
            *[f"## {name}\n" + group.render(examples=True) for name, group in groups.items()],
        ]
        store._write(PROFILE, "\n\n".join(text) + "\n")

    return {
        "sent": overall.sent,
        "text": len(overall.lengths),
        "single_runs": overall.single,
        "burst_runs": overall.burst,
        "stickers": overall.stickers,
        "groups": [
            {"sent": group.sent, "text": len(group.lengths), "examples": len(group.examples)}
            for group in groups.values()
        ],
    }
