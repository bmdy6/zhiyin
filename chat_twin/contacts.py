"""Private local contact ledger. Drafts are never treated as sent messages."""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from chat_twin.resources import resource


def validate_identity(platform: str, contact: str) -> None:
    for value in (platform, contact):
        if (
            not value.strip()
            or value != value.strip()
            or len(value) > 80
            or value in {".", ".."}
            or re.search(r"[/\\|\x00-\x1f\x7f]", value)
        ):
            raise ValueError("平台/昵称须为安全的单段名称，不能含路径、控制符或竖线")


def cell(text: str) -> str:
    return " ".join(text.replace("|", "／").split())[:1000]


class ContactStore:
    def __init__(self, root: Path | None = None):
        self.root = (root if root is not None else Path.home() / ".chat-twin").absolute()

    def _path(self, relative: str) -> Path:
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
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _load(self) -> list[dict]:
        path = self._path("state.json")
        if not path.exists():
            return []
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise TypeError("联系人状态损坏")
        return rows

    def _write(self, relative: str, text: str):
        path = self._path(relative)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        parent = path.parent
        while parent != self.root:
            parent.chmod(0o700)
            parent = parent.parent
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".contact-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _get(rows: list[dict], platform: str, contact: str) -> dict:
        for row in rows:
            if row["platform"] == platform and row["contact"] == contact:
                return row
        row = {
            "platform": platform,
            "contact": contact,
            "met": "未记录",
            "summary": "未记录",
            "stage": "未判断",
            "last": "",
            "days": [],
        }
        rows.append(row)
        return row

    def context(self, platform: str, contact: str) -> str:
        validate_identity(platform, contact)
        profile = self._path(f"contacts/{platform}/{contact}.md")
        if profile.exists():
            return profile.read_text(encoding="utf-8")[:16000]
        ledger = self._path("总文档.md")
        if ledger.exists():
            prefix = f"{platform} | {contact} | "
            return next(
                (line for line in ledger.read_text().splitlines() if line.startswith(prefix)), ""
            )
        return ""

    def _save(self, rows: list[dict]):
        self._write("state.json", json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
        header = "平台 | 昵称 | 认识方式 | 聊过什么 | 阶段 | 最后聊天日期\n--- | --- | --- | --- | --- | ---\n"
        lines = [
            " | ".join(
                cell(str(row[key]))
                for key in ("platform", "contact", "met", "summary", "stage", "last")
            )
            for row in rows
        ]
        self._write("总文档.md", header + "\n".join(lines) + "\n")

    def _profile(self, row: dict) -> Path:
        relative = f"contacts/{row['platform']}/{row['contact']}.md"
        path = self._path(relative)
        recent = f"{row['last'] or '尚无聊天'} · 阶段 {row['stage']}\n{row['summary']}\n仅输入摘要；草稿未发送。"
        if path.exists():
            text = path.read_text(encoding="utf-8")
            pattern = r"<!-- recent:start -->.*?<!-- recent:end -->"
            replacement = "<!-- recent:start -->\n" + recent + "\n<!-- recent:end -->"
            if re.search(pattern, text, re.DOTALL):
                text = re.sub(pattern, lambda _: replacement, text, flags=re.DOTALL)
            else:
                text += "\n\n## 最近摘要\n" + replacement + "\n"
        else:
            text = resource("profiles/contact.md").format(**row, recent=recent)
        self._write(relative, text)
        return path

    def update(
        self,
        platform: str,
        contact: str,
        summary: str,
        stage: int | str | None,
        *,
        day: date | None = None,
    ):
        validate_identity(platform, contact)
        # Preflight path before creating the lock/state.
        profile = self._path(f"contacts/{platform}/{contact}.md")
        with self._locked():
            rows = self._load()
            row = self._get(rows, platform, contact)
            today = (day or date.today()).isoformat()
            row.update(summary=cell(summary), last=today)
            if stage is not None:
                row["stage"] = stage
            row["days"] = sorted({*row["days"], today})
            if len(row["days"]) >= 3 or profile.exists():
                self._profile(row)
            self._save(rows)

    def promote(self, platform: str, contact: str) -> Path:
        validate_identity(platform, contact)
        self._path(f"contacts/{platform}/{contact}.md")
        with self._locked():
            rows = self._load()
            row = self._get(rows, platform, contact)
            path = self._profile(row)
            self._save(rows)
            return path
