"""Deterministic masking at the context boundary, including nested text."""

import re

DETAILS = re.compile(
    r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|"
    r"(?i:wxid_[a-z0-9_-]+)|"
    r"(?<!\w)(?:\+?\d[\d ()-]{5,}\d)(?!\w)|"
    r"\d{4,}|"
    r"(?:微信号|微信ID|wechat\s*id)\s*[:：=]?\s*[A-Za-z0-9_-]+",
    re.IGNORECASE,
)


class Redactor:
    def __init__(self, aliases: dict[str, str], identifiers: set[str] | None = None):
        self.aliases = aliases
        self.identifiers = (
            re.compile("|".join(re.escape(s) for s in sorted(identifiers, key=len, reverse=True)))
            if identifiers
            else None
        )
        self.pattern = (
            re.compile("|".join(re.escape(s) for s in sorted(aliases, key=len, reverse=True)))
            if aliases
            else None
        )

    def text(self, text: str, reveal_names: bool = False) -> str:
        if reveal_names and self.identifiers:
            text = self.identifiers.sub(lambda match: self.aliases.get(match[0], "[隐私]"), text)
        if not reveal_names and self.pattern:
            text = self.pattern.sub(lambda match: self.aliases[match[0]], text)
        codes = set(self.aliases.values())
        code_spans = [
            m.span()
            for m in re.finditer(r"(?<![A-Za-z0-9])[A-Z]+\d+(?![A-Za-z0-9])", text)
            if m[0] in codes
        ]

        def mask(match):
            if match[0].isdigit() and any(
                start <= match.start() and match.end() <= end for start, end in code_spans
            ):
                return match[0]
            return "[隐私]"

        return DETAILS.sub(mask, text)

    def value(self, value, reveal_names: bool = False):
        if isinstance(value, str):
            return self.text(value, reveal_names)
        if isinstance(value, list):
            return [self.value(item, reveal_names) for item in value]
        if isinstance(value, dict):
            return {
                key: item
                if key in {"chat", "date", "time", "first_time", "last_time", "kind", "retrieval"}
                else self.value(item, reveal_names)
                for key, item in value.items()
            }
        return value
