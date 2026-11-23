"""Command-line entry points for snapshot, verification, export, and handoff."""
import argparse
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from tempfile import TemporaryDirectory

import zstandard

from .cipher import DatabaseError, decrypt_database, verify_database
from .messages import collect_messages, connect, write_export
from .snapshot import create_snapshot, databases, verify_snapshot

CONTAINER = Path.home() / 'Library/Containers/com.tencent.xinWeChat'
HANDOFF = {
    'user': ('需要用户.txt', '请准备自己的加密数据库副本和已授权的配套密钥文件。密钥通过本地文件传入。\n'),
    'scan': ('需要扫码.txt', '若官方微信界面要求扫码，请本人核对设备后在官方客户端完成验证。回到终端后手动继续离线步骤。\n'),
}


def _outside_live(path: Path) -> Path:
    path = path.expanduser().resolve()
    if path.is_relative_to(CONTAINER.resolve()):
        raise ValueError('此路径需要在微信容器之外')
    return path


def _new_output(path: Path) -> Path:
    if path.expanduser().is_symlink():
        raise ValueError('目标不能是符号链接')
    path = _outside_live(path)
    if path.exists():
        raise ValueError('目标目录已存在')
    return path


def _master(path: Path) -> bytes:
    try:
        with path.expanduser().open('rb') as stream:
            data = stream.read(257)
    except OSError as exc:
        raise DatabaseError('密钥文件不存在或不可读，请检查文件路径及权限') from exc
    if len(data) == 32:
        return data
    if re.fullmatch(rb'[a-fA-F0-9]{64}', data.strip()):
        return bytes.fromhex(data.strip().decode('ascii'))
    raise DatabaseError('密钥文件应为 32 字节二进制或 64 位十六进制文本')


def _notice(directory: Path, action: str) -> None:
    directory = _outside_live(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    name, message = HANDOFF[action]
    try:
        with (directory / name).open('x', encoding='utf-8') as stream:
            stream.write(message)
    except FileExistsError:
        pass


def _verify_sources(root: Path, key: bytes, selected: list[Path]) -> None:
    for rel in selected:
        try:
            verify_database(root / rel, key)
        except DatabaseError as exc:
            raise DatabaseError(f'{rel.name}：{exc}') from exc


def _export(root: Path, key: bytes, selected: list[Path], owner: str | None, folder: str, output: Path) -> dict:
    if output.is_relative_to(root) or root.is_relative_to(output):
        raise ValueError('导出目录和快照必须独立')
    with TemporaryDirectory(dir=output.parent, prefix='.export-') as directory:
        temp = Path(directory)
        plain = temp / 'plain'
        plain.mkdir(mode=0o700)
        frames = 0
        for rel in selected:
            dest = plain / rel.name
            try:
                frames += decrypt_database(root / rel, key, dest)
            except DatabaseError as exc:
                raise DatabaseError(f'{rel.name}：{exc}') from exc
            with connect(dest) as conn:
                if conn.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                    raise DatabaseError(f'{rel.name}：SQLite quick_check 未通过')
        contact = plain / 'contact.db'
        chats, unresolved = collect_messages(plain, owner, contact if contact.is_file() else None, account_folder=folder)
        verify_snapshot(root)
        stage = temp / 'export'
        stage.mkdir(mode=0o700)
        result = write_export(stage, chats, unresolved)
        if output.exists() or output.is_symlink():
            raise ValueError('导出目标已存在')
        stage.rename(output)
        return {**result, 'verified_databases': len(selected), 'committed_wal_frames': frames}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='使用已有密钥离线导出 macOS 微信 4.2.0 数据库副本')
    commands = parser.add_subparsers(dest='command', required=True)
    snapshot = commands.add_parser('snapshot', help='复制消息库、元数据及 WAL 文件')
    snapshot.add_argument('--source', required=True, type=Path, help='某一个账号的 db_storage 目录')
    snapshot.add_argument('--output', required=True, type=Path, help='新建的加密快照目录')
    for action, help_text in [('verify', '检查快照与所有数据库首页 HMAC'), ('export', '导出私聊和统计')]:
        command = commands.add_parser(action, help=help_text)
        command.add_argument('--snapshot', required=True, type=Path, help='snapshot 命令生成的目录')
        command.add_argument('--key-file', required=True, type=Path, help='配套的已有本地密钥文件')
        command.add_argument('--handoff-dir', type=Path, help='密钥不可用时写入需要用户.txt')
        if action == 'export':
            command.add_argument('--output', required=True, type=Path, help='新建的导出目录')
            command.add_argument('--self-id', help='无法从账户目录识别时指定微信数据库中的本人账号标识')
    handoff = commands.add_parser('handoff', help='写入人工接力提示文件')
    handoff.add_argument('--directory', required=True, type=Path)
    handoff.add_argument('--action', required=True, choices=tuple(HANDOFF))
    return parser


def _run(args) -> dict:
    if args.command == 'handoff':
        _notice(args.directory, args.action)
        return {'notice': HANDOFF[args.action][0]}
    if args.command == 'snapshot':
        source = args.source.expanduser().resolve(strict=True)
        return {'snapshot_files': create_snapshot(source, _new_output(args.output))}
    root = _outside_live(args.snapshot)
    metadata = verify_snapshot(root)
    selected = databases(root)
    try:
        key = _master(args.key_file)
        _verify_sources(root, key, selected)
    except (DatabaseError, OSError):
        if args.handoff_dir is not None:
            _notice(args.handoff_dir, 'user')
        raise
    if args.command == 'verify':
        return {'verified_databases': len(selected)}
    return _export(root, key, selected, args.self_id, metadata.get('account_folder', ''), _new_output(args.output))


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    args = _parser().parse_args(argv)
    try:
        print(json.dumps(_run(args), ensure_ascii=False))
        return 0
    except (ValueError, OSError, sqlite3.Error, zstandard.ZstdError) as exc:
        if isinstance(exc, (OSError, sqlite3.Error, zstandard.ZstdError, json.JSONDecodeError)):
            detail = type(exc).__name__
        else:
            detail = str(exc)
        print(f'操作停止（{detail}）', file=sys.stderr)
        return 1
