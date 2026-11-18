"""Local relationship hints and explicit edits to the private configuration."""

import re
from typing import Literal, get_args

import tomlkit

from zhiyin.wechat import WeChatStore

Relationship = Literal["家人", "朋友", "同事", "办事", "其他"]
FAMILY = r"妈妈|爸爸|母亲|父亲|奶奶|爷爷|外婆|外公|姥姥|姥爷|舅舅|姑姑|姨妈"
REMARKS = {
    "家人": re.compile(rf"家人|亲戚|{FAMILY}"),
    "朋友": re.compile(r"朋友|发小|闺蜜|室友|同学"),
    "同事": re.compile(r"同事|部门|组员|主管"),
    "办事": re.compile(r"客服|快递|外卖|物业|维修|送货|中介|售后"),
}
VOCATIVES = {
    group: re.compile(rf"^\s*(?:{words})(?=$|[\s，,。.!！?？：:~～])")
    for group, words in {
        "家人": rf"爸|妈|{FAMILY}",
        "朋友": r"老同学|老朋友|发小|闺蜜",
        "同事": r"组长|主管",
        "办事": r"客服|师傅",
    }.items()
}

ACTIVITY = """
WITH latest AS (
    SELECT max(m.time) AS time
    FROM messages m JOIN visible_contacts c ON c.id=m.contact_id
    WHERE m.type NOT IN (10000,10002)
)
SELECT c.id, c.code, c.label,
       count(DISTINCT date(m.time, 'unixepoch', '+8 hours')) AS active_days
FROM visible_contacts c CROSS JOIN latest
LEFT JOIN messages m ON m.contact_id=c.id AND m.type NOT IN (10000,10002)
    AND m.time >= CAST(strftime(
        '%s', date(latest.time, 'unixepoch', '+8 hours', '-89 days'), '-8 hours'
    ) AS INTEGER)
WHERE c.relationship='未分类'
GROUP BY c.id
"""


def suggest_relationships(store: WeChatStore) -> list[dict]:
    suggestions = []
    with store.read_existing() as db:
        if db is None:
            return []
        for row in db.execute(ACTIVITY):
            remark_hits = {
                group for group, pattern in REMARKS.items() if pattern.search(row["label"])
            }
            recent = db.execute(
                """SELECT text FROM messages WHERE contact_id=? AND sender='me' AND is_text
                   ORDER BY time DESC, seq DESC, id DESC LIMIT 30""",
                (row["id"],),
            )
            vocative_hits = {
                group
                for message in recent
                for group, pattern in VOCATIVES.items()
                if pattern.match(message["text"])
            }
            matches = remark_hits | vocative_hits
            if len(matches) != 1:
                continue
            (relationship,) = matches
            evidence = []
            if remark_hits:
                evidence.append("备注关键词")
            if vocative_hits:
                evidence.append("聊天称呼")
            suggestions.append(
                {
                    "contact_id": row["id"],
                    "code": row["code"],
                    "relationship": relationship,
                    "evidence": evidence,
                    "active_days": row["active_days"],
                }
            )
    return sorted(
        suggestions,
        key=lambda row: (-row["active_days"], -len(row["evidence"]), row["contact_id"]),
    )


def set_relationship(store: WeChatStore, contact: str, relationship: str) -> dict[str, str]:
    if relationship not in get_args(Relationship):
        raise ValueError("不支持的关系分组")
    # Use the same lock as imports, and persist the unambiguous ID instead of the input alias.
    with store.connect() as db:
        matched = store.contact(db, contact)
        if matched is None:
            raise ValueError("找不到唯一的可见联系人")
        contact_id = matched["id"]
        path = store._path("config.toml")
        config = (
            tomlkit.parse(path.read_text(encoding="utf-8")) if path.exists() else tomlkit.document()
        )
        relations = config.setdefault("relationships", tomlkit.table())
        if relations.get(contact_id) != relationship:
            relations[contact_id] = relationship
            store._write("config.toml", config.as_string())
    return {"contact_id": contact_id, "relationship": relationship}
