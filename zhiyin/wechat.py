"""Read-only export ingestion and the private WeChat message index."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tomllib
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from zhiyin.context_metadata import (
    PROBLEMS,
    import_metadata,
    initialize_context_config,
    manifest_entry,
)
from zhiyin.context_metadata import SCHEMA as CONTEXT_SCHEMA
from zhiyin.local import PrivateFiles

EXPORT = Path.home() / "Documents/knowledge-mcp/private/微信聊天"
RELATIONSHIPS = ("家人", "朋友", "同事", "办事", "未分类", "其他")
LEGACY_RELATIONSHIP = "陌生人或办事"
CHAT_TIMEZONE = timezone(timedelta(hours=8))
SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id TEXT PRIMARY KEY, code TEXT NOT NULL, label TEXT NOT NULL, relationship TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY, contact_id TEXT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    time REAL NOT NULL, seq INTEGER NOT NULL, sender TEXT NOT NULL,
    type INTEGER NOT NULL, subtype INTEGER, text TEXT NOT NULL, is_text INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS reply_embeddings (
    prior_id TEXT PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
    version TEXT NOT NULL, vector BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_contact_time ON messages(contact_id, time, seq);
CREATE INDEX IF NOT EXISTS messages_sender_time ON messages(contact_id, sender, time, seq);
"""
VISIBLE = """
CREATE TEMP TABLE excluded (value TEXT PRIMARY KEY);
CREATE TEMP TABLE relationship_config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TEMP VIEW visible_contacts AS
SELECT c.id, c.code, c.label,
       coalesce(ri.value, rc.value, rl.value,
                CASE c.relationship WHEN '陌生人或办事' THEN '办事' ELSE c.relationship END
       ) AS relationship
FROM contacts c
LEFT JOIN relationship_config ri ON ri.key=c.id
-- A configured ID belongs only to its own contact, even if another contact uses it as an alias.
LEFT JOIN relationship_config rc ON rc.key=c.code
    AND NOT EXISTS (SELECT 1 FROM contacts WHERE id=rc.key)
LEFT JOIN relationship_config rl ON rl.key=c.label
    AND NOT EXISTS (SELECT 1 FROM contacts WHERE id=rl.key)
WHERE NOT EXISTS (
    SELECT 1 FROM excluded e WHERE e.value IN (c.id, c.code, c.label)
);
-- Same-second rows follow their original order. A six-hour gap is a new conversation.
CREATE TEMP VIEW reply_pairs AS
SELECT m.id, m.contact_id, m.time, m.seq, m.rowid AS position, m.text AS reply, p.id AS prior_id, p.text AS her
FROM visible_contacts c JOIN messages m ON c.id=m.contact_id
JOIN messages p ON p.id=(
    SELECT q.id FROM messages q
    WHERE q.contact_id=m.contact_id AND q.sender='them' AND q.type NOT IN (10000, 10002)
      AND (q.time, q.seq, q.rowid) < (m.time, m.seq, m.rowid) AND q.time > m.time-21600
    ORDER BY q.time DESC, q.seq DESC, q.rowid DESC LIMIT 1
)
WHERE m.sender='me' AND m.is_text AND p.is_text;
"""


def local_time(value: float) -> str:
    return datetime.fromtimestamp(value, CHAT_TIMEZONE).isoformat()


class WeChatStore(PrivateFiles):
    def config(self) -> tuple[set[str], dict[str, str]]:
        path = self._path("config.toml")
        config = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        excluded = config.get("exclude_contacts", [])
        relations = config.get("relationships", {})
        if not isinstance(excluded, list) or any(
            not isinstance(value, str) or not value.strip() for value in excluded
        ):
            raise ValueError("exclude_contacts 须为字符串列表")
        if not isinstance(relations, dict) or any(
            value not in RELATIONSHIPS and value != LEGACY_RELATIONSHIP
            for value in relations.values()
        ):
            raise ValueError("relationships 含未支持的分类")
        return {value.strip() for value in excluded}, {
            key: "办事" if value == LEGACY_RELATIONSHIP else value
            for key, value in relations.items()
        }

    @contextmanager
    def connect(self, *, write: bool = False):
        with self._locked():
            path = self._path("wechat.sqlite3")
            if not path.exists():
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
            path.chmod(0o600)
            with self._connection(path, write=write) as db:
                yield db

    @contextmanager
    def read_existing(self):
        """Read the current index without creating files or triggering legacy migration."""
        path = self._path("wechat.sqlite3")
        if not path.exists():
            yield None
            return
        with self._connection(path, readonly=True) as db:
            yield db

    @contextmanager
    def _connection(self, path: Path, *, readonly: bool = False, write: bool = False):
        uri = path.as_uri() + "?mode=ro" if readonly else path
        db = sqlite3.connect(uri, uri=readonly)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys = ON")
            if not readonly:
                db.executescript(SCHEMA + CONTEXT_SCHEMA)
            db.executescript(VISIBLE)
            excluded, relations = self.config()
            db.executemany("INSERT INTO excluded VALUES (?)", [(value,) for value in excluded])
            db.executemany("INSERT INTO relationship_config VALUES (?, ?)", relations.items())
            if write:
                db.execute("PRAGMA secure_delete = ON")
            with db:
                yield db
        finally:
            db.close()

    def contact(self, db: sqlite3.Connection, contact: str) -> sqlite3.Row | None:
        """Exact, unambiguous resolution by conversation ID, code, or display label."""
        value = contact.strip()
        exact = db.execute("SELECT * FROM visible_contacts WHERE id=?", (value,)).fetchone()
        if exact:
            return exact
        rows = db.execute(
            "SELECT * FROM visible_contacts WHERE ? IN (code, label) LIMIT 2", (value,)
        ).fetchall()
        return rows[0] if len(rows) == 1 else None


def source_messages(path: Path):
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if line.strip():
                yield {**json.loads(line), "_source_line": number}


def conversation_sources(root: Path):
    manifest = root / "导出清单.json"
    if manifest.is_file():
        seen = set()
        for entry in json.loads(manifest.read_text(encoding="utf-8"))["conversations"]:
            entry = manifest_entry(entry)
            contact_id, label, code = (
                entry[key].strip() for key in ("conversation_id", "label", "code")
            )
            folder = entry["folder"]
            if (
                not all((contact_id, label, code, folder))
                or Path(folder).name != folder
                or folder in {".", ".."}
                or contact_id in seen
            ):
                raise ValueError("导出清单中的会话字段无效")
            seen.add(contact_id)
            path = (root / folder / "chat.jsonl").resolve()
            if not path.is_relative_to(root):
                raise ValueError("会话文件超出导出目录")
            yield (contact_id, code, label, entry.get("relationship", "未分类")), path
    else:
        raise ValueError("找不到微信导出清单")


def message_row(contact_id: str, raw: dict, positions: Counter, repeats: Counter) -> tuple:
    sent = datetime.fromisoformat(raw["time"])
    time = (sent if sent.tzinfo else sent.replace(tzinfo=CHAT_TIMEZONE)).astimezone(UTC)
    timestamp = time.timestamp()
    sender, kind, subtype, text = (raw[key] for key in ("sender", "type", "subtype", "text"))
    if (
        (sender not in {"me", "them"} and not (sender == "unknown" and kind in {10000, 10002}))
        or type(kind) is not int
        or (subtype is not None and type(subtype) is not int)
        or not isinstance(text, str)
    ):
        raise ValueError("会话消息字段无效")
    signature = json.dumps([contact_id, timestamp, sender, kind, subtype, text], ensure_ascii=False)
    occurrence = repeats[signature]
    repeats[signature] += 1
    # The export has no row ID. Preserve same-second identical messages within a snapshot.
    identity = hashlib.sha256(f"{occurrence}:{signature}".encode()).hexdigest()
    seq = positions[timestamp]
    positions[timestamp] += 1
    is_text = bool(text.strip()) and (kind == 1 or (kind, subtype) == (49, 57))
    return identity, contact_id, timestamp, seq, sender, kind, subtype, text, int(is_text)


def import_wechat(store: WeChatStore, source: Path = EXPORT) -> dict[str, int]:
    """Import a manifest snapshot atomically, keeping previous messages on incremental runs."""
    source = source.expanduser().resolve(strict=True)
    with store.connect(write=True) as db:
        if not store._path("config.toml").exists():
            store._write("config.toml", "exclude_contacts = []\n\n[relationships]\n")
        excluded, _ = store.config()
        db.execute("DELETE FROM contacts WHERE id NOT IN (SELECT id FROM visible_contacts)")
        skipped = scanned = added = 0
        for contact, path in conversation_sources(source):
            if excluded.intersection(contact[:3]):
                db.execute("DELETE FROM contacts WHERE id=?", (contact[0],))
                skipped += 1
                continue
            if contact[3] not in RELATIONSHIPS and contact[3] != LEGACY_RELATIONSHIP:
                raise ValueError("导出清单中的关系分组无效")
            db.execute(
                "INSERT INTO contacts VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "code=excluded.code, label=excluded.label, relationship=excluded.relationship",
                contact,
            )
            positions, repeats = Counter(), Counter()
            raw_rows = list(source_messages(path))
            rows = [message_row(contact[0], raw, positions, repeats) for raw in raw_rows]
            scanned += len(rows)
            added += db.executemany(
                "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO NOTHING",
                rows,
            ).rowcount
            db.executemany(
                "INSERT OR REPLACE INTO source_lines VALUES (?, ?)",
                [(row[0], raw["_source_line"]) for row, raw in zip(rows, raw_rows, strict=True)],
            )
        import_metadata(db, source)
        initialize_context_config(store)
        if not store._path("我的问题.md").exists():
            store._write("我的问题.md", PROBLEMS)
        contacts = db.execute("SELECT count(*) FROM visible_contacts").fetchone()[0]
        messages = db.execute(
            "SELECT count(*) FROM messages WHERE contact_id IN (SELECT id FROM visible_contacts)"
        ).fetchone()[0]
        pairs = db.execute("SELECT count(*) FROM reply_pairs").fetchone()[0]
        return {
            "contacts": contacts,
            "messages": messages,
            "reply_pairs": pairs,
            "scanned": scanned,
            "inserted": added,
            "duplicates": scanned - added,
            "excluded_contacts": skipped,
        }
