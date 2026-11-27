"""Permissions, locking and copy-only migration for private local files."""

from __future__ import annotations

import argparse
import fcntl
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path


def temporary_state(value: str) -> Path:
    path = Path(value).expanduser().absolute()
    resolved = path.resolve()
    roots = {Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve()}
    if not any(resolved != root and resolved.is_relative_to(root) for root in roots):
        raise argparse.ArgumentTypeError("--state-dir仅供测试，必须是系统临时目录下的子目录")
    return path


class PrivateFiles:
    def __init__(self, root: Path | None = None):
        env_root = os.environ.get("ZHIYIN_STATE_DIR") if root is None else None
        self.root = (
            root
            if root is not None
            else Path(env_root).expanduser()
            if env_root
            else Path.home() / ".zhiyin"
        ).absolute()
        self.legacy = Path.home() / ".chat-twin" if root is None and not env_root else None

    def _path(self, relative: str | Path) -> Path:
        path = self.root / relative
        # Reject symlinks in the entire ancestry, including the state root.
        for item in (path, *path.parents):
            if item.is_symlink() and not (
                str(item) in {"/var", "/tmp"} and str(item.resolve()) == "/private" + str(item)
            ):
                raise ValueError("联系人存储不能经过符号链接")
        return path

    @contextmanager
    def _locked(self):
        lock = self._path(".lock")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        with lock.open("a") as handle:
            lock.chmod(0o600)
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                self._migrate()
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _write(self, relative: str | Path, text: str):
        self._write_bytes(relative, text.encode("utf-8"))

    def _write_bytes(self, relative: str | Path, data: bytes):
        path = self._path(relative)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        parent = path.parent
        while parent != self.root:
            parent.chmod(0o700)
            parent = parent.parent
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".zhiyin-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _migrate(self) -> None:
        if self.legacy is None or self._path(".migrated").exists():
            return
        if self.legacy.is_symlink():
            raise ValueError("旧目录不能是符号链接")
        if not self.legacy.exists():
            return
        if not self.legacy.is_dir():
            raise ValueError("旧存储路径不是目录")
        for source in self.legacy.rglob("*"):
            relative = source.relative_to(self.legacy)
            if relative.parts[0].startswith("."):
                continue
            mode = source.lstat().st_mode
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode):
                raise ValueError("旧存储含有不支持的文件类型")
            if not self._path(relative).exists():
                self._write_bytes(relative, source.read_bytes())
        self._write(".migrated", "")
