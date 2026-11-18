"""Deterministic input and draft guards; semantic review is also required."""

import re
import unicodedata
from difflib import SequenceMatcher

RISK_PATTERNS = (
    ("涉及身份确认", r"机器人|是不是.{0,6}(本人|AI|人工智能)|是本人|你是真人|代聊|自动回复"),
    ("涉及钱或投资", r"钱|转账|借款|收款|红包|投资|基金|股票|理财|银行卡|支付宝|比特币|贷款"),
    (
        "涉及见面或联系方式",
        r"见[个一]?面|约会|出来.{0,5}(吃|喝|玩|走)|一起.{0,6}(吃饭|看展|看电影)|微信|微.?信号|手机号|电话|地址|住哪|住哪里|定位|加.{0,3}V|加.{0,3}v",
    ),
    (
        "对方情绪很差，需要本人处理",
        r"不想活|自杀|自残|伤害自己|割腕|崩溃|绝望|难过|伤心|气死|生气|别再烦|活着.{0,3}(没|没有)意义",
    ),
    (
        "涉及承诺或私人信息",
        r"保证|承诺|答应|发誓|工作单位|哪家.{0,2}公司|在哪.{0,4}(公司|上班)|收入|工资|住址|身份证",
    ),
)


def parse_chat(text: str) -> list[dict]:
    if not text.strip() or len(text) > 10000:
        raise ValueError("聊天输入须为1–10000字符")
    messages = []
    for line in text.splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"\s*(我|她|他)\s*[:：]\s*(\S.*?)\s*", line)
        if not match:
            raise ValueError("每行必须是 我: / 她: / 他: 加非空消息")
        messages.append({"speaker": match[1], "text": match[2]})
    if not messages or messages[-1]["speaker"] == "我" or len(messages) > 100:
        raise ValueError("最多100条消息，最后一条必须来自对方")
    return messages


def handoff_reason(text: str, *, wechat: bool = False) -> str | None:
    for reason, pattern in RISK_PATTERNS:
        if wechat and reason == "涉及见面或联系方式":
            pattern = pattern.replace("|微信|", "|加.{0,4}微信|")
            pattern += r"|约饭|请你(?:吃|喝)|下次.{0,4}一起.{0,4}(?:吃|喝|聚)|抽空聚聚"
        if wechat and reason == "对方情绪很差，需要本人处理":
            pattern = pattern.replace("|难过|伤心|", "|(?:太|特别|非常|好|很)(?:难过|伤心|生气)|")
            pattern = pattern.replace("|气死|生气|", "|气死|")
        if re.search(pattern, text, re.IGNORECASE):
            return reason
    return None


def normalized_text(text: str) -> str:
    return "".join(c.lower() for c in unicodedata.normalize("NFKC", text) if c.isalnum())


def draft_errors(messages: list[str], samples: list[dict], *, wechat: bool = False) -> list[str]:
    errors = []
    if not 1 <= len(messages) <= 3:
        errors.append("必须为1–3条消息")
    for text in messages:
        if not text.strip() or len(text) > 40:
            errors.append("每条须为1–40字")
        if re.search(r"[\r\n()（）\[\]【】{}<>*_`#~|—–]|!\[|^\s*[-+>]\s", text):
            errors.append("不能带括号动作、Markdown或破折号")
        if handoff_reason(text, wechat=wechat) or re.search(
            r"我.{0,5}(一定|肯定|会|来|帮你|陪你).{0,8}(接你|陪你|给你|做到|处理|买|每天)|明天我来",
            text,
        ):
            errors.append("可能涉及敏感信息或替本人承诺")
        norm = normalized_text(text)
        for row in samples:
            reference = normalized_text(row["reply"])
            if reference and (
                norm == reference or SequenceMatcher(None, norm, reference).ratio() > 0.85
            ):
                errors.append("与检索样本相似度>0.85，疑似照抄")
    # Splitting a copied sample into two messages must not bypass comparison.
    combined = normalized_text("".join(messages))
    if len(messages) > 1:
        for row in samples:
            ref = normalized_text(row["reply"])
            if ref and (ref in combined or SequenceMatcher(None, combined, ref).ratio() > 0.85):
                errors.append("分条后仍照抄样本")
    return list(dict.fromkeys(errors))


def profile_echo(text: str, style: str) -> bool:
    """Detect long verbatim excerpts without logging/storing the private profile."""
    norm, private = normalized_text(text), normalized_text(style)
    return any(norm[index : index + 16] in private for index in range(max(0, len(norm) - 15)))


def review_categories(issues: list[str]) -> list[str]:
    """Only fixed labels can reach diagnostics; raw semantic feedback stays in memory."""
    labels = []
    patterns = {
        "动作不一致": "动作|两件|一件|追问|接话|分享自己|推进",
        "口吻或套路": "口吻|文风|档案|油腻|套路|稀缺|操控|生硬|自然|称呼|调侃|夸张",
        "事实或承诺": "事实|经历|编造|承诺|保证|私人|隐私",
        "格式或相似": "字数|40|格式|括号|破折|Markdown|照抄|相似",
    }
    for label, pattern in patterns.items():
        if any(re.search(pattern, issue) for issue in issues):
            labels.append(label)
    return labels or ["其他语义问题"]
