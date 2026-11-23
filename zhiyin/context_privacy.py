"""Deterministic masking at the context boundary, including nested text."""

import re
import unicodedata
from functools import lru_cache

import jieba

DETAILS = re.compile(
    r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|"
    r"(?i:wxid_[a-z0-9_-]+)|"
    r"(?<!\w)(?:\+?\d[\d ()-]{5,}\d)(?!\w)|"
    r"\d{4,}|"
    r"(?:微信号|微信ID|wechat\s*id)\s*[:：=]?\s*[A-Za-z0-9_-]+",
    re.IGNORECASE,
)
ADDRESS_PREFIX = re.compile(
    r"(?:(?:昵称|备注|姓名|名字|联系人|称呼)\s*[:：]|[@＠])\s*[\"'“‘「『]?$"
)
ADDRESS_END = re.compile(r"$|[\s,，。.!！?？:：;；、…()（）\[\]【】<>《》\"'“”‘’「」『』]")


@lru_cache(maxsize=1)
def common_aliases() -> frozenset[str]:
    """Reuse the bundled lexicon without initializing a tokenizer or writing its cache."""
    words = {"你好", "谢谢", "再见", "晚安", "明天见"}
    with jieba.get_dict_file() as source:
        for line in source:
            word, frequency, tag = line.decode("utf-8").split()
            # Place names are ambiguous at any frequency. Keep dictionary person names private.
            if tag == "ns" or (not tag.startswith("nr") and int(frequency) >= 100):
                words.add(word)
    return frozenset(words)


def contextual_alias(alias: str) -> bool:
    return (
        len(alias) == 1
        or alias.isnumeric()
        or not any(unicodedata.category(char)[0] in "LN" for char in alias)
        or bool(re.fullmatch(r"[A-Za-z]{1,4}", alias))
        or alias in common_aliases()
    )


class Redactor:
    def __init__(self, aliases: dict[str, str], identifiers: set[str] | None = None):
        self.aliases = aliases
        self.contextual = {alias for alias in aliases if contextual_alias(alias)}
        self.identifier_aliases = identifiers or set()
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

    def text(self, text: str, reveal_names: bool = False, *, replace_aliases: bool = True) -> str:
        if replace_aliases and reveal_names and self.identifiers:
            text = self.identifiers.sub(lambda match: self.aliases.get(match[0], "[隐私]"), text)
        if replace_aliases and not reveal_names and self.pattern:

            def replace(match):
                alias = match[0]
                if (
                    alias in self.contextual
                    and alias not in self.identifier_aliases
                    and not (
                        ADDRESS_PREFIX.search(match.string[: match.start()])
                        and ADDRESS_END.match(match.string, match.end())
                    )
                ):
                    return alias
                return self.aliases[alias]

            text = self.pattern.sub(replace, text)
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

    def value(self, value, reveal_names: bool = False, *, replace_aliases: bool = True):
        if isinstance(value, str):
            return self.text(value, reveal_names, replace_aliases=replace_aliases)
        if isinstance(value, list):
            return [
                self.value(item, reveal_names, replace_aliases=replace_aliases) for item in value
            ]
        if isinstance(value, dict):
            return {
                key: item
                if key in {"chat", "date", "time", "first_time", "last_time", "kind", "retrieval"}
                else self.value(
                    item,
                    reveal_names,
                    replace_aliases=replace_aliases and key not in {"groups", "relationship"},
                )
                for key, item in value.items()
            }
        return value
