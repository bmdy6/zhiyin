"""Import private source references and identities; never expose these tables directly."""

import json
from collections import defaultdict
from pathlib import Path

import tomlkit

from zhiyin.context_rules import DEFAULTS

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_lines (
    message_id TEXT PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
    line INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_groups (
    contact_id TEXT PRIMARY KEY REFERENCES contacts(id) ON DELETE CASCADE,
    groups_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS private_aliases (alias TEXT PRIMARY KEY, code TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS private_identifiers (alias TEXT PRIMARY KEY);
"""
PROBLEMS = """# 我的问题

- 对方说感受时只接事情或回得太短
- 话题很快转回自己
- 一次连发太多太长
- 段落常停在我这里
"""


def import_metadata(db, source: Path):
    manifest = json.loads((source / "导出清单.json").read_text(encoding="utf-8"))
    groups = defaultdict(set)
    aliases = defaultdict(set)
    identifiers = set()
    for entry in manifest["conversations"]:
        groups[entry["code"]].update(entry.get("groups", []))
    index = source / "分组索引.json"
    if index.exists():
        for group, entries in (
            json.loads(index.read_text(encoding="utf-8")).get("groups", {}).items()
        ):
            for entry in entries:
                groups[entry["code"]].add(group)
    mapping = source / "代号映射-第9轮.json"
    if mapping.exists():
        data = json.loads(mapping.read_text(encoding="utf-8"))
        for identity, code in data.get("codes", {}).items():
            aliases[identity].add(code)
            identifiers.add(identity)
            person = data.get("people", {}).get(identity, {})
            for field in ("username", "alias", "remark", "nick_name"):
                value = person.get(field)
                if isinstance(value, str) and value.strip():
                    aliases[value.strip()].add(code)
                    if field in {"username", "alias"}:
                        identifiers.add(value.strip())
    for contact in db.execute("SELECT * FROM contacts"):
        identifiers.add(contact["id"])
        for value in (contact["id"], contact["label"]):
            aliases[value].add(contact["code"])
        db.execute(
            "INSERT OR REPLACE INTO chat_groups VALUES (?, ?)",
            (contact["id"], json.dumps(sorted(groups[contact["code"]]), ensure_ascii=False)),
        )
    # Ambiguous names are masked rather than assigned to the wrong person.
    db.execute("DELETE FROM private_identifiers")
    db.executemany("INSERT INTO private_identifiers VALUES (?)", [(v,) for v in identifiers])
    db.execute("DELETE FROM private_aliases")
    db.executemany(
        "INSERT INTO private_aliases VALUES (?, ?)",
        [
            (alias, next(iter(codes)) if len(codes) == 1 else "[联系人]")
            for alias, codes in aliases.items()
            if alias
        ],
    )


def initialize_context_config(store):
    """Only the explicit import/setup path writes defaults; MCP readers never do."""
    path = store._path("config.toml")
    document = tomlkit.parse(path.read_text(encoding="utf-8"))
    before = document.as_string()
    document.setdefault("enable_draft_reply", False)
    document.setdefault("enable_relationship_writes", False)
    document.setdefault("enable_public_samples", False)
    document.setdefault("contact_notes", tomlkit.table())
    rules = document.setdefault("context", tomlkit.table())
    for key, value in DEFAULTS.items():
        rules.setdefault(key, value)
    if document.as_string() != before:
        store._write("config.toml", document.as_string())
