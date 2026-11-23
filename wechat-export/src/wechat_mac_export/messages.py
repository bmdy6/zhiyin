"""Read numbered WeChat message shards and render private, local outputs."""
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from xml.etree import ElementTree

import zstandard

from .snapshot import SHARD

TABLE = re.compile(r'Msg_[a-fA-F0-9]{32}')
SYSTEM_ACCOUNTS = {'filehelper', 'weixin', 'newsapp', 'fmessage', 'medianote', 'floatbottle',
                   'qmessage', 'qqmail', 'notifymessage', 'weixinreminder'}
TYPES = {3: '图片', 34: '语音', 42: '名片', 43: '视频', 47: '表情', 48: '位置',
         49: '应用消息', 50: '通话', 10000: '系统', 10002: '撤回'}
TEXT_LIMIT = 8 * 1024 * 1024


def connect(path: Path):
    return closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True))


def _text(raw: str | bytes | None) -> str:
    if isinstance(raw, bytes):
        if raw.startswith(b'\x28\xb5\x2f\xfd'):
            with zstandard.ZstdDecompressor().stream_reader(raw) as stream:
                raw = stream.read(TEXT_LIMIT + 1)
            if len(raw) > TEXT_LIMIT:
                raise ValueError('单条解压正文超过 8 MiB')
        return raw.decode('utf-8', errors='replace')
    return raw or ''


def parse_message(kind: int, raw: str | bytes | None) -> tuple[str, int | None]:
    if kind == 1:
        return _text(raw), None
    if kind == 49:
        xml = _text(raw)
        if '<!DOCTYPE' not in xml and '<!ENTITY' not in xml:
            try:
                root = ElementTree.fromstring(xml)
                app = root if root.tag == 'appmsg' else root.find('appmsg')
                if app is not None and app.findtext('type') == '57':
                    return app.findtext('title') or '', 57
            except ElementTree.ParseError:
                pass
    return f'[{TYPES.get(kind, f"类型 {kind}")}]', None


def _directory(plain: Path, contact: Path | None) -> tuple[dict[str, str], set[str]]:
    labels = {}
    groups = set()
    if contact is not None:
        with connect(contact) as conn:
            for table in ('contact', 'stranger'):
                if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                    continue
                rows = conn.execute(f'SELECT username, local_type, remark, nick_name FROM "{table}"')
                for username, local_type, remark, nickname in rows:
                    if username:
                        labels[username] = remark or nickname or username
                        if local_type == 2:
                            groups.add(username)
            for (username,) in conn.execute('SELECT username FROM chat_room'):
                if username:
                    groups.add(username)
    session = plain / 'session.db'
    if session.is_file():
        with connect(session) as conn:
            for (username,) in conn.execute('SELECT username FROM SessionTable'):
                if username:
                    labels.setdefault(username, username)
    return labels, groups


def collect_messages(plain: Path, owner: str | None, contact: Path | None,
                     *, account_folder: str = '') -> tuple[dict, int]:
    shards = sorted(p for p in plain.iterdir() if SHARD.fullmatch(p.name))
    if not shards:
        raise ValueError('没有可读取的消息库')
    labels, groups = _directory(plain, contact)
    candidates = set(labels)
    shard_names = {}
    for shard in shards:
        with connect(shard) as conn:
            names = dict(conn.execute('SELECT rowid, user_name FROM Name2Id'))
            shard_names[shard] = names
            candidates.update(name for name in names.values() if name)
    if owner is None:
        matches = {name for name in candidates if account_folder == name or account_folder.startswith(name + '_')}
        if len(matches) != 1:
            raise ValueError('无法从账户目录唯一确认本人，请通过 --self-id 指定本人账号标识')
        owner = matches.pop()
    if owner not in candidates:
        raise ValueError('本人账号标识没有在快照中找到')
    reverse = {hashlib.md5(name.encode(), usedforsecurity=False).hexdigest(): name for name in candidates}
    messages = defaultdict(list)
    unresolved = set()
    for shard, names in shard_names.items():
        with connect(shard) as conn:
            tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            for (table,) in tables:
                if not TABLE.fullmatch(table):
                    continue
                cid = reverse.get(table[4:].lower())
                if cid is None:
                    if conn.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone():
                        unresolved.add(table.lower())
                    continue
                if cid in groups or cid.endswith('@chatroom') or cid.startswith('gh_') or cid in SYSTEM_ACCOUNTS:
                    continue
                rows = conn.execute(f'SELECT local_id, server_id, create_time, local_type, real_sender_id, message_content FROM "{table}"')
                for local, server, ts, raw_kind, sender_id, raw in rows:
                    kind = int(raw_kind or 0) & 0xFFFF
                    sender_id = names.get(sender_id)
                    side = 'me' if sender_id == owner else 'them' if sender_id == cid else 'unknown'
                    text, subtype = parse_message(kind, raw)
                    messages[cid].append({'timestamp': ts, 'sender': side, 'type': kind, 'subtype': subtype,
                                          'local_id': local, 'server_id': server, 'shard': shard.stem,
                                          'text': text})
    result = {cid: (labels.get(cid, cid), sorted(rows, key=lambda row: (
        row['timestamp'], row['shard'], row['local_id'] or 0))) for cid, rows in messages.items()}
    return result, len(unresolved)


def _timestamp(value: int) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec='seconds')


def write_export(root: Path, conversations: dict, unresolved: int) -> dict:
    manifest = []
    total = 0
    unknown = 0
    ranking = sorted(conversations, key=lambda cid: (-len(conversations[cid][1]), cid))
    report = ['# 私聊导出统计', '', '| 编号 | 条数 | 最早（UTC） | 最晚（UTC） |', '| --- | ---: | --- | --- |']
    for seq, cid in enumerate(ranking, 1):
        label, rows = conversations[cid]
        folder = f'{seq:03d}'
        dest = root / folder
        dest.mkdir(mode=0o700)
        first, last = (_timestamp(rows[index]['timestamp']) for index in (0, -1))
        manifest.append({'sequence': seq, 'conversation_id': cid, 'label': label, 'folder': folder,
                         'count': len(rows), 'first': first, 'last': last})
        report.append(f'| {folder} | {len(rows)} | {first} | {last} |')
        total += len(rows)
        with (dest/'chat.jsonl').open('x', encoding='utf-8') as jsonl, (dest/'chat.md').open('x', encoding='utf-8') as md:
            md.write(f'# 会话 {folder}\n\n')
            for row in rows:
                stamp = _timestamp(row['timestamp'])
                unknown += row['sender'] == 'unknown'
                jsonl.write(json.dumps({'time': stamp, **row}, ensure_ascii=False) + '\n')
                md.write(f"**{stamp} · {row['sender']}**\n\n")
                for line in row['text'].splitlines() or ['']:
                    md.write(f'    {line}\n')
                md.write('\n')
    report += ['', f'共 {len(manifest)} 个会话、{total} 条消息。未识别会话表 {unresolved} 个，发送方未知 {unknown} 条。']
    (root/'统计.md').write_text('\n'.join(report) + '\n', encoding='utf-8')
    (root/'导出清单.json').write_text(json.dumps({'conversations': manifest}, ensure_ascii=False, indent=2) + '\n',
                                    encoding='utf-8')
    return {'conversations': len(manifest), 'messages': total, 'unresolved_tables': unresolved}
