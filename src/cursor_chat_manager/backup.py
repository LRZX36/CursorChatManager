"""Backup Cursor SQLite databases before destructive edits; restore from backups."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import paths
from .process import is_cursor_running

BACKUP_FILES = (
    "state.vscdb",
    "state.vscdb-wal",
    "state.vscdb-shm",
    "conversation-search.db",
    "conversation-search.db-wal",
    "conversation-search.db-shm",
)


class CursorBusyError(RuntimeError):
    pass


class BackupNotFoundError(RuntimeError):
    pass


@dataclass
class BackupInfo:
    path: Path
    stamp: str
    created_at: datetime | None
    chat_count: int | None
    size_bytes: int

    @property
    def time_label(self) -> str:
        if self.created_at is not None:
            return self.created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        return self.stamp

    @property
    def chat_count_label(self) -> str:
        if self.chat_count is None:
            return "?"
        return str(self.chat_count)


def create_backup() -> Path:
    """Copy global state.vscdb (+ wal/shm) and conversation-search.db into a timestamped folder."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = paths.backups_dir() / stamp
    dest.mkdir(parents=True, exist_ok=True)

    gs = paths.global_storage_dir()
    for name in BACKUP_FILES:
        src = gs / name
        if src.is_file():
            shutil.copy2(src, dest / name)

    return dest


def _parse_stamp(stamp: str) -> datetime | None:
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S"):
        try:
            dt = datetime.strptime(stamp, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for p in path.iterdir():
            if p.is_file():
                total += p.stat().st_size
    except OSError:
        pass
    return total


def _count_chats_in_db(db_path: Path) -> int | None:
    """Count default-visible (main UI) conversations in a backup state.vscdb."""
    # Lazy import avoids circular dependency with store → backup.
    from .store import count_default_visible_chats

    return count_default_visible_chats(db_path)


def list_backups() -> list[BackupInfo]:
    root = paths.backups_dir()
    if not root.is_dir():
        return []
    infos: list[BackupInfo] = []
    for p in root.iterdir():
        if not p.is_dir():
            continue
        stamp = p.name
        created = _parse_stamp(stamp)
        if created is None:
            try:
                created = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
            except OSError:
                created = None
        chat_count = _count_chats_in_db(p / "state.vscdb")
        infos.append(
            BackupInfo(
                path=p,
                stamp=stamp,
                created_at=created,
                chat_count=chat_count,
                size_bytes=_dir_size(p),
            )
        )
    infos.sort(
        key=lambda b: b.created_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return infos


def restore_backup(backup_dir: Path, *, safety_backup: bool = True) -> Path | None:
    """Restore state.vscdb (+ related files) from a backup folder into Cursor globalStorage.

    Returns the path of the pre-restore safety backup, if one was created.
    """
    backup_dir = Path(backup_dir)
    if not backup_dir.is_dir():
        raise BackupNotFoundError(f"备份目录不存在: {backup_dir}")
    src_db = backup_dir / "state.vscdb"
    if not src_db.is_file():
        raise BackupNotFoundError(f"备份中缺少 state.vscdb: {backup_dir}")

    if is_cursor_running():
        raise CursorBusyError(
            "检测到 Cursor 仍在运行。请完全退出 Cursor（含托盘）后再恢复备份。"
        )

    safety: Path | None = None
    if safety_backup and paths.state_vscdb().is_file():
        safety = create_backup()

    gs = paths.global_storage_dir()
    gs.mkdir(parents=True, exist_ok=True)

    # Remove live WAL/SHM so restored main DB is not merged with stale journal
    for name in BACKUP_FILES:
        live = gs / name
        if live.is_file():
            try:
                live.unlink()
            except OSError as exc:
                raise RuntimeError(f"无法删除现有文件 {live}: {exc}") from exc

    for name in BACKUP_FILES:
        src = backup_dir / name
        if src.is_file():
            shutil.copy2(src, gs / name)

    return safety
