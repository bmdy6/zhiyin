"""Copy message families consistently using read-only file handles."""
import hashlib
import json
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory

SHARD = re.compile(r'message_[0-9]+\.db')
OPTIONAL = (Path('contact/contact.db'), Path('session/session.db'))
SIDECARS = ('-wal', '-shm')


def databases(root: Path) -> list[Path]:
    message = root / 'message'
    if message.is_symlink():
        raise ValueError('消息目录为符号链接')
    names = sorted(p.relative_to(root) for p in message.iterdir() if SHARD.fullmatch(p.name))
    if not names:
        raise ValueError('没有找到 message_数字.db 消息库')
    for rel in OPTIONAL:
        if (root / rel.parent).is_symlink():
            raise ValueError('元数据目录为符号链接')
        if (root / rel).exists() or (root / rel).is_symlink():
            names.append(rel)
    return names


def inventory(root: Path) -> dict[str, str]:
    result = {}
    for rel in databases(root):
        for suffix in ('', *SIDECARS):
            name = str(rel) + suffix
            path = root / name
            if path.is_symlink():
                raise ValueError('快照输入包含符号链接')
            if suffix and not path.exists():
                continue
            if not path.is_file():
                raise ValueError('快照输入必须为常规文件')
            since = path.stat()
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            after = path.stat()
            if (since.st_ino, since.st_size, since.st_mtime_ns, since.st_ctime_ns) != (
                    after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError('读取期间源文件发生变化，请重新复制')
            result[name] = digest
    return result


def create_snapshot(source: Path, output: Path) -> int:
    if output.exists() or output.is_symlink() or output.is_relative_to(source):
        raise ValueError('快照目标必须是源目录以外的新目录')
    with TemporaryDirectory(dir=output.parent, prefix='.snapshot-') as work:
        stage = Path(work) / 'snapshot'
        before = inventory(source)
        for rel in before:
            dest = stage / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / rel, dest)
        if inventory(stage) != before or inventory(source) != before:
            raise ValueError('复制期间源库或 WAL 发生变化，请使用一致的副本重试')
        metadata = {'format': 1, 'account_folder': source.parent.name, 'sha256': before}
        (stage / 'snapshot.json').write_text(json.dumps(metadata), encoding='utf-8')
        if output.exists() or output.is_symlink():
            raise ValueError('快照目标已存在')
        stage.rename(output)
    return len(before)


def verify_snapshot(root: Path) -> dict:
    marker = root / 'snapshot.json'
    if marker.is_symlink():
        raise ValueError('快照清单为符号链接')
    metadata = json.loads(marker.read_text(encoding='utf-8'))
    if not isinstance(metadata, dict) or metadata.get('format') != 1:
        raise ValueError('不支持的快照清单格式')
    if metadata.get('sha256') != inventory(root):
        raise ValueError('快照文件与创建时的清单不符，请重新复制')
    return metadata
