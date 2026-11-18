"""Contact history and locally detected open arrangements."""

import re
from datetime import datetime

from zhiyin.wechat import WeChatStore, local_time

ACTIONS = {
    "见面": r"见面|见一面|碰面|面基",
    "吃饭": r"吃饭|吃个饭|请客|请.{0,3}(?:吃|喝)|一起吃|聚餐",
    "出门": r"看电影|看展|逛街|一起去|一起玩|一起走",
    "办事": r"寄给你|发给你|带给你|还给你|交给你",
}
TIMING = r"今天|今晚|明[天早晚]|后天|周[一二三四五六日末]|星期[一二三四五六日天]|\d{1,2}[点日号]|有空|下次|改天|有时间"
PROPOSE = re.compile(
    r"要不|要不要|行不行|可以吗|能不能|想不想|我们.{0,8}一起|约个|到时候|" + TIMING
)
AGREE = re.compile(r"^(?:好呀|好啊|好哇|好的|好|行|可以|没问题|就这么定|说定了)[！!。．\s～~]*$")
CLOSED = re.compile(
    r"取消|不去了|不来了|不吃了|约不上|先不约|不用了|改天再说|已经.{0,10}(?:完|过|到|发|寄|收)|见完|吃完|到家了|收到(了)?[！!。\s]*$"
)


def bubble(row) -> dict:
    return {
        "time": local_time(row["time"]),
        "sender": row["sender"],
        "text": row["text"][:500] if row["is_text"] else None,
    }


def arrangements(rows: list) -> list[dict]:
    pending = {}
    for row in rows:
        text = row["text"]
        topics = [topic for topic, pattern in ACTIONS.items() if re.search(pattern, text)]
        for topic, (prior, agreed) in list(pending.items()):
            nearby = row["time"] - prior["time"] < 21600
            if CLOSED.search(text) and (topic in topics or (nearby and len(pending) == 1)):
                del pending[topic]
            elif nearby and row["sender"] != prior["sender"] and AGREE.fullmatch(text.strip()):
                pending[topic] = (prior, True)
        if PROPOSE.search(text) and not CLOSED.search(text):
            for topic in topics:
                pending[topic] = (row, False)
    return [
        dict(bubble(row), topic=topic, status="已回应，未见落实" if agreed else "提及，待确认")
        for topic, (row, agreed) in sorted(
            pending.items(), key=lambda item: item[1][0]["time"], reverse=True
        )
    ]


def read_brief(db, contact) -> dict:
    contact_id = contact["id"]
    base = "FROM messages WHERE contact_id=? AND type NOT IN (10000,10002)"
    first, last, count = db.execute(
        "SELECT min(time), max(time), count(*) " + base, (contact_id,)
    ).fetchone()
    recent = db.execute(
        "SELECT * " + base + " ORDER BY time DESC, seq DESC, id DESC LIMIT 8", (contact_id,)
    ).fetchall()
    recent_text = db.execute(
        "SELECT * " + base + " AND is_text ORDER BY time DESC, seq DESC, id DESC LIMIT 600",
        (contact_id,),
    ).fetchall()
    first_time = local_time(first) if first is not None else None
    last_time = local_time(last) if last is not None else None
    duration_days = (
        (datetime.fromisoformat(last_time).date() - datetime.fromisoformat(first_time).date()).days
        if first_time is not None and last_time is not None
        else None
    )
    return {
        "found": True,
        "contact": contact["label"],
        "relationship": contact["relationship"],
        "first_time": first_time,
        "last_time": last_time,
        "duration_days": duration_days,
        "message_count": count,
        "recent": [bubble(row) for row in reversed(recent)],
        "open_arrangements": arrangements(list(reversed(recent_text))),
    }


def contact_brief(store: WeChatStore, contact: str) -> dict:
    with store.connect() as db:
        matched = store.contact(db, contact)
        return read_brief(db, matched) if matched else {"found": False}
