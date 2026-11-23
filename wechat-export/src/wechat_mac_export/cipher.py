"""Authenticate SQLCipher 4 pages and replay committed frames on a private copy."""
from dataclasses import dataclass
import hashlib
import hmac
from pathlib import Path
import struct
from tempfile import NamedTemporaryFile

from Crypto.Cipher import AES

from . import _wal

PAGE_SIZE = 4096
SALT_SIZE = 16
MAC_START = PAGE_SIZE - 64
IV_START = MAC_START - 16
SQLITE_HEADER = b'SQLite format 3\0'


class DatabaseError(ValueError):
    """An encrypted input does not pass format or authentication checks."""


@dataclass(frozen=True)
class PageCipher:
    encryption_key: bytes
    mac_key: bytes

    def authenticated(self, page: bytes, number: int) -> bool:
        if len(page) != PAGE_SIZE:
            return False
        offset = SALT_SIZE if number == 1 else 0
        data = page[offset:MAC_START] + struct.pack('<I', number)
        return hmac.compare_digest(hmac.digest(self.mac_key, data, 'sha512'), page[MAC_START:])

    def decrypt(self, page: bytes, number: int) -> bytes | None:
        if not self.authenticated(page, number):
            return None
        offset = SALT_SIZE if number == 1 else 0
        prefix = SQLITE_HEADER if number == 1 else b''
        cipher = AES.new(self.encryption_key, AES.MODE_CBC, page[IV_START:MAC_START])
        return prefix + cipher.decrypt(page[offset:IV_START]) + bytes(PAGE_SIZE - IV_START)


def _page_cipher(first: bytes, master: bytes) -> PageCipher:
    if len(master) != 32 or len(first) != PAGE_SIZE:
        raise DatabaseError('密钥长度或数据库首页长度无效')
    salt = first[:SALT_SIZE]
    page_key = hashlib.pbkdf2_hmac('sha512', master, salt, 256000, 32)
    mac_key = hashlib.pbkdf2_hmac('sha512', page_key, bytes(b ^ 0x3A for b in salt), 2, 32)
    cipher = PageCipher(page_key, mac_key)
    if not cipher.authenticated(first, 1):
        raise DatabaseError('首页 HMAC 不匹配，请核对密钥、账号及快照版本')
    return cipher


def verify_database(source: Path, master: bytes) -> None:
    with source.open('rb') as stream:
        _page_cipher(stream.read(PAGE_SIZE), master)


def _apply_wal(source: Path, dest: Path, cipher: PageCipher) -> int:
    wal = source.with_name(source.name + '-wal')
    try:
        indexed = _wal.inspect_wal(wal, expected_page_size=PAGE_SIZE)
    except _wal.WalIndexValidationError:
        return _wal._apply_wal_without_index(wal, dest, cipher.decrypt, expected_page_size=PAGE_SIZE)
    if indexed.used_shm and indexed.physical_frames > indexed.frames_to_apply:
        recovered = _wal._inspect_wal_without_index(wal, expected_page_size=PAGE_SIZE)
        if recovered.frames_to_apply > indexed.frames_to_apply:
            return _wal._apply_wal_without_index(wal, dest, cipher.decrypt, expected_page_size=PAGE_SIZE)
    return _wal.apply_wal(wal, dest, cipher.decrypt, expected_page_size=PAGE_SIZE)


def _decrypt_main(source: Path, output, master: bytes) -> PageCipher:
    with source.open('rb') as stream:
        first = stream.read(PAGE_SIZE)
        cipher = _page_cipher(first, master)
        header = cipher.decrypt(first, 1)
        if header is None or header[16:18] != b'\x10\x00' or header[20] != PAGE_SIZE - IV_START:
            raise DatabaseError('解密后 SQLite 页头格式无效')
        output.write(header)
        header_pages = int.from_bytes(header[28:32], 'big')
        logical_pages = header_pages if header_pages and header[24:28] == header[92:96] else None
        count = 1
        for number, page in enumerate(iter(lambda: stream.read(PAGE_SIZE), b''), 2):
            if logical_pages is not None and number > logical_pages and len(page) == PAGE_SIZE and not any(page):
                continue
            if logical_pages is not None and number > logical_pages:
                raise DatabaseError('加密库超过认证页头所记录的大小')
            plain = cipher.decrypt(page, number)
            if plain is None:
                raise DatabaseError(f'第 {number} 页不完整或认证失败')
            output.write(plain)
            count += 1
        if logical_pages is not None and count != logical_pages:
            raise DatabaseError('加密库缺少页头所记录的数据页')
    return cipher


def decrypt_database(source: Path, master: bytes, output: Path) -> int:
    """Write to a new output only after page and WAL validation succeeds."""
    if output.exists() or output.is_symlink():
        raise FileExistsError('明文目标已存在')
    temp = None
    try:
        with NamedTemporaryFile(dir=output.parent, prefix='.pages-', delete=False) as stream:
            temp = Path(stream.name)
            cipher = _decrypt_main(source, stream, master)
        frames = _apply_wal(source, temp, cipher)
        output.hardlink_to(temp)
        return frames
    except _wal.WalValidationError as exc:
        raise DatabaseError(f'WAL 校验失败（{exc}）') from exc
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
