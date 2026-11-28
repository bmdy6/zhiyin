"""Import private source references and identities; never expose these tables directly."""

import hashlib
import json
from collections import defaultdict
from pathlib import Path

import tomlkit

from zhiyin.context_rules import DEFAULTS

SCHEMA = """
CREATE TABLE IF NOT EXISTS message_members (
    message_id TEXT PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
    sender_id TEXT, sender_name TEXT
);
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


def manifest_entry(entry: dict) -> dict:
    """Give raw wechat-mac-export entries the code refresh_knowledge.py would assign."""
    if "code" not in entry:
        is_group = entry.get("chat_type") == "group" or entry["conversation_id"].endswith(
            "@chatroom"
        )
        if is_group:
            code = "G" + hashlib.sha256(entry["conversation_id"].encode()).hexdigest()[:12].upper()
        elif type(entry.get("sequence")) is int:
            code = f"A{entry['sequence']}"
        else:
            return entry
        return {**entry, "code": code}
    return entry


def import_metadata(db, source: Path):
    manifest = json.loads((source / "导出清单.json").read_text(encoding="utf-8"))
    manifest["conversations"] = [manifest_entry(entry) for entry in manifest["conversations"]]
    groups = defaultdict(set)
    aliases = defaultdict(set)
    identifiers = {row[0] for row in db.execute("SELECT alias FROM private_identifiers")}
    for alias, code in db.execute("SELECT alias, code FROM private_aliases"):
        aliases[alias].add(code)
    imported_codes = {entry.get("code") for entry in manifest["conversations"]}
    for code, raw_groups in db.execute(
        "SELECT c.code, g.groups_json FROM chat_groups g JOIN contacts c ON c.id=g.contact_id"
    ):
        if code not in imported_codes:
            groups[code].update(json.loads(raw_groups))
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
    for row in db.execute(
        "SELECT m.contact_id, mm.sender_id, mm.sender_name FROM message_members mm JOIN messages m ON m.id=mm.message_id"
    ):
        cid, member, name = row
        if member:
            code = "M" + hashlib.sha256((cid + ":" + member).encode()).hexdigest()[:12]
            identifiers.add(member)
            aliases[member].add(code)
            if name and name != member:
                aliases[name].add(code)
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
