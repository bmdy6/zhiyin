"""Configurable keyword defaults. These only locate candidates, never infer intent."""

DEFAULTS = {
    "burst_n": 5,
    "emotion_words": ["累", "烦", "难过", "唉", "哭", "分手", "伤心", "失落", "委屈", "孤独"],
    "closing_words": ["晚安", "拜拜", "再见", "回头聊", "先这样", "不打扰了"],
    "conflict_words": [
        "你总是",
        "你从来",
        "别烦我",
        "吵架",
        "争执",
        "拉黑",
        "滚",
        "闭嘴",
        "有病",
        "分手",
    ],
}
