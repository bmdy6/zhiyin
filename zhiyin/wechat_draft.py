"""WeChat-specific context, checks and source attribution for the draft engine."""

import re
from dataclasses import dataclass

from zhiyin.brief import read_brief
from zhiyin.draft_models import Candidate, Review, Rule
from zhiyin.safety import draft_errors, handoff_reason, profile_echo
from zhiyin.tone import PROFILE, profile_revision
from zhiyin.wechat import WeChatStore

RULES = {
    1: "先接住对方的感受",
    2: "控制追问，失利时不问分数、不拿别人比",
    3: "不说教，也不拿自己比较",
    4: "先回答对方的问题",
    5: "约定要么具体，要么留给本人处理",
    6: "办事时保持礼貌",
}
SETBACK = re.compile(r"没考好|没考上|没通过|没过关|落选|没录上|没选上|考砸|失败|被拒|没过|输了")
SCORE = re.compile(r"几分|多少分|考了多少|什么分数|什么名次|第几名|别人.{0,6}(?:分|名)")
VULNERABLE = re.compile(r"我.{0,5}(?:委屈|难过|焦虑|害怕|很累|失落|没信心|压力大|有点慌)")
DISMISSIVE = re.compile(r"^(?:是|不是|对|不对|嗯|行|没事|别想太多|看开点)[！!。．…\s～~]*$")
QUESTION_END = re.compile(r"(?:吗|么|嘛|呢)[。！!…～~\s]*$")


@dataclass
class WeChatContext:
    brief: dict
    tone: str

    def model_context(self) -> dict:
        return {
            "relationship": self.brief["relationship"],
            "recent": self.brief["recent"][-6:],
            "open_arrangements": [
                {key: arrangement[key] for key in ("topic", "time", "status")}
                for arrangement in self.brief["open_arrangements"]
            ],
        }


def load_context(store: WeChatStore, contact: str) -> WeChatContext | None:
    with store.connect() as db:
        matched = store.contact(db, contact)
        if matched is None:
            return None
        path = store._path(PROFILE)
        tone = ""
        if path.is_file():
            with path.open(encoding="utf-8") as handle:
                marker = handle.readline().strip()
                if marker == f"<!-- zhiyin:{profile_revision(db)} -->":
                    allowed = {"## 总体", f"## {matched['relationship']}"}
                    use_section = False
                    lines = []
                    for line in handle:
                        line = line.strip()
                        if line.startswith("## "):
                            use_section = line in allowed
                            if use_section:
                                lines.append(line)
                        if use_section and line.startswith(
                            (
                                "- 发出消息 ",
                                "- 单条 ",
                                "- 文本长度",
                                "- 标点 ",
                                "- 句末语气词 ",
                                "- 表情 ",
                            )
                        ):
                            lines.append(line)
                    tone = "\n".join(lines)
        return WeChatContext(read_brief(db, matched), tone)


def source_rows(rows: list[dict], *, personal: bool) -> tuple[list[dict], dict[str, dict]]:
    references = {}
    model_rows = []
    for index, row in enumerate(rows, 1):
        ref = f"R{index}"
        meta = {
            "reference": ref,
            "source": "personal" if personal else "public",
            "id": row["id"],
            "date": row.get("date"),
        }
        references[ref] = meta
        model_rows.append(
            {
                "reference": ref,
                "source": meta["source"],
                "date": meta["date"],
                **{key: row.get(key, "")[:500] for key in ("her", "reply", "context", "goal")},
            }
        )
    return model_rows, references


def question_count(text: str) -> int:
    explicit = len(re.findall(r"[?？]+", text))
    return explicit + bool(QUESTION_END.search(text))


def previous_questions(history: list[dict]) -> int:
    count = 0
    for row in reversed(history):
        if row["speaker"] != "我":
            break
        questions = question_count(row["text"])
        if not questions:
            break
        count += questions
    return count


def check_candidate(candidate: Candidate, chat, context, rows, references, style) -> list[str]:
    rationales = candidate.rationales or []
    if not 1 <= len(candidate.messages) <= 3 or len(rationales) != len(candidate.messages):
        return ["每条草稿须对应一条理由"]
    errors = []
    latest = chat[-1]["text"]
    history = chat[:-1]
    if not history:
        recent = context["recent"]
        if recent and recent[-1]["sender"] == "them" and recent[-1]["text"] == latest:
            recent = recent[:-1]
        history = [
            {"speaker": "我" if row["sender"] == "me" else "对方", "text": row["text"] or ""}
            for row in recent
        ]
    consecutive = previous_questions(history)
    # The current message may already answer a prior question. The semantic review sees that context.
    for text, reason in zip(candidate.messages, rationales, strict=True):
        errors.extend(draft_errors([text], rows, wechat=True))
        if reason.reference not in references or len(set(reason.rules)) != len(reason.rules):
            errors.append("理由须引用已返回的参考编号和有效规则")
        if profile_echo(text, style):
            errors.append("草稿疑似复述私密口吻档案")
        if VULNERABLE.search(latest) and DISMISSIVE.fullmatch(text.strip()):
            errors.append("规则1：先接住感受")
        questions = question_count(text)
        if questions > 2 or (questions and consecutive >= 2 and VULNERABLE.search(latest)):
            errors.append("规则2：连续追问不得超过两个问题")
        if SETBACK.search(latest) and SCORE.search(text):
            errors.append("规则2：失利后不追问分数、名次或比较")
    return list(dict.fromkeys(errors))


def check_rules(review: Review) -> list[str]:
    if review.rules is None or sorted(item.rule for item in review.rules) != list(RULES):
        return ["自检须逐条报告六条规则"]
    return [f"规则{item.rule}：{RULES[item.rule]}" for item in review.rules if not item.passed]


def rationale(meta: dict, rules: list[Rule]) -> str:
    reference = (
        f"参考 {meta['date']} 的历史回复 {meta['reference']}"
        if meta["source"] == "personal"
        else f"参考公开样本 {meta['reference']}"
    )
    return reference + "，注意" + "、".join(f"规则{number}（{RULES[number]}）" for number in rules)


def immediate_handoff(message: str, goal: str | None) -> str | None:
    return handoff_reason(message, wechat=True) or handoff_reason(goal or "", wechat=True)
