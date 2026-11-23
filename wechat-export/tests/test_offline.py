from contextlib import closing
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest

from Crypto.Cipher import AES
import zstandard

from wechat_mac_export._wal import _checksum_bytes
from wechat_mac_export.cipher import DatabaseError, decrypt_database, verify_database
from wechat_mac_export.messages import collect_messages, parse_message, write_export


def cipher_fixture():
    master = bytes(range(32))
    salt = b'S' * 16
    page_key = hashlib.pbkdf2_hmac('sha512', master, salt, 256000, 32)
    mac_key = hashlib.pbkdf2_hmac('sha512', page_key, bytes(x ^ 0x3A for x in salt), 2, 32)

    def page(number, body):
        iv = b'I' * 16
        payload = AES.new(page_key, AES.MODE_CBC, iv).encrypt(body) + iv
        mac = hmac.digest(mac_key, payload + struct.pack('<I', number), 'sha512')
        return (salt if number == 1 else b'') + payload + mac

    sqlite_header = b'SQLite format 3\0' + bytes.fromhex('1000010150402020')
    first_body = sqlite_header[16:].ljust(4000, b'\0')
    return master, page, sqlite_header[:16] + first_body + bytes(80), page(1, first_body)


class CipherTests(unittest.TestCase):
    def test_accepts_supplied_master_key_and_rejects_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            encrypted = Path(tmp) / 'encrypted.db'
            output = Path(tmp) / 'plain.db'
            master, _, expected, first = cipher_fixture()
            encrypted.write_bytes(first)
            verify_database(encrypted, master)
            with self.assertRaises(DatabaseError):
                verify_database(encrypted, bytes(32))
            decrypt_database(encrypted, master, output)
            self.assertEqual(output.read_bytes(), expected)
            encrypted.write_bytes(first + b'corrupt')
            with self.assertRaises(DatabaseError):
                decrypt_database(encrypted, master, output.with_name('bad.db'))
            self.assertFalse(output.with_name('bad.db').exists())


    def test_applies_only_committed_authenticated_wal_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            master, encrypt, first_plain, first = cipher_fixture()
            root = Path(tmp)
            encrypted = root / 'message_0.db'
            encrypted.write_bytes(first)
            wal = root / 'message_0.db-wal'
            salt = b'saltsalt'
            header = struct.pack('>IIII', 0x377F0682, 3007000, 4096, 0) + salt

            def write_wal(committed):
                checksum = _checksum_bytes(header, big_endian=False)
                contents = header + struct.pack('>II', *checksum)
                for size, page in [(2, committed), (0, encrypt(2, b'U' * 4016))]:
                    prefix = struct.pack('>II', 2, size)
                    checksum = _checksum_bytes(prefix + page, checksum, big_endian=False)
                    contents += prefix + salt + struct.pack('>II', *checksum) + page
                wal.write_bytes(contents)

            payload = encrypt(2, b'C' * 4016)
            write_wal(payload)
            output = root / 'result.db'
            self.assertEqual(decrypt_database(encrypted, master, output), 1)
            self.assertEqual(output.read_bytes(), first_plain + b'C' * 4016 + bytes(80))
            write_wal(payload[:-1] + bytes([payload[-1] ^ 1]))
            rejected = root / 'rejected.db'
            with self.assertRaises(DatabaseError):
                decrypt_database(encrypted, master, rejected)
            self.assertFalse(rejected.exists())


class MessageTests(unittest.TestCase):
    def test_parse_text_quote_and_media(self):
        zap = zstandard.ZstdCompressor().compress('测试消息'.encode())
        self.assertEqual(parse_message(1, zap), ('测试消息', None))
        quote = '<msg><appmsg><type>57</type><title>回复</title><refermsg><content>旧消息</content></refermsg></appmsg></msg>'
        self.assertEqual(parse_message(49, quote), ('回复', 57))
        self.assertEqual(parse_message(3, '<token>synthetic</token>'), ('[图片]', None))

    def test_reads_shards_and_exports_numbered_conversations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            table = 'Msg_' + hashlib.md5(b'fixture-peer', usedforsecurity=False).hexdigest()
            group = 'Msg_' + hashlib.md5(b'fixture-group', usedforsecurity=False).hexdigest()
            with closing(sqlite3.connect(root / 'contact.db')) as conn, conn:
                conn.execute('CREATE TABLE contact (username TEXT, local_type INTEGER, remark TEXT, nick_name TEXT)')
                conn.execute('INSERT INTO contact VALUES (?, 2, NULL, NULL)', ('fixture-group',))
                conn.execute('CREATE TABLE chat_room (username TEXT)')
            for index in range(2):
                with closing(sqlite3.connect(root / f'message_{index}.db')) as conn, conn:
                    conn.execute('CREATE TABLE Name2Id (user_name TEXT)')
                    conn.executemany('INSERT INTO Name2Id VALUES (?)',
                                     [('fixture-owner',), ('fixture-peer',), ('fixture-group',)])
                    for name in (table, group):
                        conn.execute(f'CREATE TABLE "{name}" (local_id INTEGER, server_id INTEGER, create_time INTEGER, local_type INTEGER, real_sender_id INTEGER, message_content TEXT)')
                        conn.execute(f'INSERT INTO "{name}" VALUES (?, ?, ?, ?, ?, ?)',
                                     (index + 1, index + 10, 1700000001 - index, 1, index + 1, '合成消息'))
            conversations, unresolved = collect_messages(root, 'fixture-owner', root / 'contact.db')
            self.assertEqual(unresolved, 0)
            summary = write_export(root, conversations, unresolved)
            self.assertEqual(summary, {'conversations': 1, 'messages': 2, 'unresolved_tables': 0})
            rows = [json.loads(s) for s in (root/'001/chat.jsonl').read_text().splitlines()]
            self.assertEqual([r['sender'] for r in rows], ['them', 'me'])
            self.assertEqual([r['server_id'] for r in rows], [11, 10])
            self.assertIn('合成消息', (root/'001/chat.md').read_text())
            self.assertIn('001', (root/'统计.md').read_text())
            self.assertEqual(json.loads((root/'导出清单.json').read_text())['conversations'][0]['sequence'], 1)
