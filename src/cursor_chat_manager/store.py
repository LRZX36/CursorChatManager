"""Read/list/delete Cursor conversations from local SQLite stores."""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable
from pathlib import Path

from . import backup as backup_mod
from . import paths
from .process import is_cursor_running


class ChatKind(str, Enum):
    MAIN = "main"
    SUBAGENT = "subagent"
    ARCHIVED_EMPTY = "zombie"
    SYSTEM_DRAFT = "draft"
    EMPTY_TAB = "empty"
    ORPHAN = "orphan"


KIND_LABELS = {
    ChatKind.MAIN: "主对话",
    ChatKind.SUBAGENT: "子代理",
    ChatKind.ARCHIVED_EMPTY: "僵尸残留",
    ChatKind.SYSTEM_DRAFT: "系统草稿",
    ChatKind.EMPTY_TAB: "空标签",
    ChatKind.ORPHAN: "孤立正文",
}


@dataclass
class ChatSummary:
    composer_id: str
    name: str
    workspace_id: str | None
    workspace_path: str | None
    kind: ChatKind
    is_archived: bool
    is_subagent: bool
    bubble_count: int
    size_bytes: int
    created_at: int | None
    last_updated_at: int | None
    unified_mode: str | None = None
    has_composer_data: bool = True

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind.value)

    @property
    def is_default_hidden(self) -> bool:
        return self.kind in {
            ChatKind.SYSTEM_DRAFT,
            ChatKind.ARCHIVED_EMPTY,
            ChatKind.EMPTY_TAB,
        }


@dataclass
class BubblePreview:
    bubble_id: str
    type: int | None
    text: str


@dataclass
class DeleteResult:
    deleted_ids: list[str] = field(default_factory=list)
    backup_path: Path | None = None
    vacuumed: bool = False
    errors: list[str] = field(default_factory=list)


class CursorBusyError(RuntimeError):
    pass


class StoreNotFoundError(RuntimeError):
    pass


def _decode_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _json_loads(value: Any) -> Any:
    raw = _decode_value(value)
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    return json.loads(raw)


def _copy_db_trio(src: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    shutil.copy2(src, dest)
    for suffix in ("-wal", "-shm"):
        side = Path(str(src) + suffix)
        if side.is_file():
            shutil.copy2(side, dest_dir / side.name)
    return dest


def _open_readonly_snapshot(db_path: Path) -> sqlite3.Connection:
    """Copy DB + WAL to temp and open so we get a consistent snapshot while Cursor may hold locks."""
    if not db_path.is_file():
        raise StoreNotFoundError(f"Database not found: {db_path}")
    tmp = Path(tempfile.mkdtemp(prefix="ccm-read-"))
    copied = _copy_db_trio(db_path, tmp)
    # Opening the copy applies WAL into the main file for this connection.
    con = sqlite3.connect(str(copied))
    con.row_factory = sqlite3.Row
    return con


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (name,)
    ).fetchone()
    return row is not None


def _bubble_count(con: sqlite3.Connection, composer_id: str) -> int:
    return con.execute(
        "SELECT COUNT(*) FROM cursorDiskKV WHERE key LIKE ?",
        (f"bubbleId:{composer_id}:%",),
    ).fetchone()[0]


def _size_bytes(con: sqlite3.Connection, composer_id: str) -> int:
    return con.execute(
        """
        SELECT IFNULL(SUM(LENGTH(value)), 0) FROM cursorDiskKV
        WHERE key = ? OR key LIKE ? OR key LIKE ? OR key LIKE ?
        """,
        (
            f"composerData:{composer_id}",
            f"bubbleId:{composer_id}:%",
            f"checkpointId:{composer_id}:%",
            f"messageRequestContext:{composer_id}:%",
        ),
    ).fetchone()[0]


def _has_composer_data(con: sqlite3.Connection, composer_id: str) -> bool:
    return (
        con.execute(
            "SELECT 1 FROM cursorDiskKV WHERE key=? LIMIT 1",
            (f"composerData:{composer_id}",),
        ).fetchone()
        is not None
    )


def _classify(
    composer_id: str,
    *,
    is_archived: bool,
    is_subagent: bool,
    bubble_count: int,
    name: str | None,
    has_composer_data: bool,
) -> ChatKind:
    if composer_id == "empty-state-draft":
        return ChatKind.SYSTEM_DRAFT
    if is_subagent:
        return ChatKind.SUBAGENT
    if is_archived and bubble_count == 0:
        return ChatKind.ARCHIVED_EMPTY
    if bubble_count == 0 and not (name and name.strip()):
        return ChatKind.EMPTY_TAB
    if not has_composer_data and bubble_count == 0:
        return ChatKind.EMPTY_TAB
    return ChatKind.MAIN


def _headers_from_table(con: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _table_exists(con, "composerHeaders"):
        return []
    rows = con.execute(
        """
        SELECT composerId, workspaceId, createdAt, lastUpdatedAt,
               isArchived, isSubagent, recency, value
        FROM composerHeaders
        """
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        meta: dict[str, Any] = {}
        try:
            meta = _json_loads(row["value"]) or {}
        except (json.JSONDecodeError, TypeError):
            meta = {}
        out.append(
            {
                "composerId": row["composerId"],
                "workspaceId": row["workspaceId"],
                "createdAt": row["createdAt"] or meta.get("createdAt"),
                "lastUpdatedAt": row["lastUpdatedAt"] or meta.get("lastUpdatedAt"),
                "isArchived": bool(row["isArchived"]),
                "isSubagent": bool(row["isSubagent"] or meta.get("isSubagent")),
                "name": meta.get("name"),
                "unifiedMode": meta.get("unifiedMode"),
                "meta": meta,
            }
        )
    return out


def _headers_from_itemtable(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Legacy Cursor ≤3.0: ItemTable key composer.composerHeaders JSON."""
    row = con.execute(
        "SELECT value FROM ItemTable WHERE key='composer.composerHeaders'"
    ).fetchone()
    if not row:
        return []
    try:
        data = _json_loads(row["value"] if isinstance(row, sqlite3.Row) else row[0])
    except (json.JSONDecodeError, TypeError):
        return []
    composers = []
    if isinstance(data, dict):
        composers = data.get("allComposers") or []
    elif isinstance(data, list):
        composers = data
    out: list[dict[str, Any]] = []
    for c in composers:
        if not isinstance(c, dict):
            continue
        cid = c.get("composerId")
        if not cid:
            continue
        wi = c.get("workspaceIdentifier") or {}
        wid = wi.get("id") if isinstance(wi, dict) else None
        uri = (wi.get("uri") or {}) if isinstance(wi, dict) else {}
        fs_path = uri.get("fsPath") if isinstance(uri, dict) else None
        out.append(
            {
                "composerId": cid,
                "workspaceId": wid,
                "createdAt": c.get("createdAt"),
                "lastUpdatedAt": c.get("lastUpdatedAt"),
                "isArchived": bool(c.get("isArchived")),
                "isSubagent": bool(c.get("isSubagent")),
                "name": c.get("name"),
                "unifiedMode": c.get("unifiedMode"),
                "workspacePathHint": fs_path,
                "meta": c,
            }
        )
    return out


def list_chats(*, include_hidden: bool = False) -> list[ChatSummary]:
    db = paths.state_vscdb()
    con = _open_readonly_snapshot(db)
    try:
        ws_map = paths.load_workspace_map()
        headers = _headers_from_table(con)
        if not headers:
            headers = _headers_from_itemtable(con)

        seen: set[str] = set()
        results: list[ChatSummary] = []

        for h in headers:
            cid = h["composerId"]
            seen.add(cid)
            bubbles = _bubble_count(con, cid)
            has_cd = _has_composer_data(con, cid)
            name = h.get("name") or ""
            # Prefer name from composerData if header has none
            if not name and has_cd:
                name = _composer_data_name(con, cid) or ""
            kind = _classify(
                cid,
                is_archived=h["isArchived"],
                is_subagent=h["isSubagent"],
                bubble_count=bubbles,
                name=name,
                has_composer_data=has_cd,
            )
            wid = h.get("workspaceId")
            wpath = h.get("workspacePathHint") or (ws_map.get(wid) if wid else None)
            summary = ChatSummary(
                composer_id=cid,
                name=name.strip() or "(无标题)",
                workspace_id=wid,
                workspace_path=wpath,
                kind=kind,
                is_archived=h["isArchived"],
                is_subagent=h["isSubagent"],
                bubble_count=bubbles,
                size_bytes=_size_bytes(con, cid),
                created_at=h.get("createdAt"),
                last_updated_at=h.get("lastUpdatedAt"),
                unified_mode=h.get("unifiedMode"),
                has_composer_data=has_cd,
            )
            if include_hidden or not summary.is_default_hidden:
                results.append(summary)

        # Orphan composerData without header
        for (key,) in con.execute(
            "SELECT key FROM cursorDiskKV WHERE key LIKE 'composerData:%'"
        ):
            cid = key.split(":", 1)[1]
            if cid in seen:
                continue
            bubbles = _bubble_count(con, cid)
            name = _composer_data_name(con, cid) or "(无标题)"
            summary = ChatSummary(
                composer_id=cid,
                name=name,
                workspace_id=None,
                workspace_path=None,
                kind=ChatKind.ORPHAN,
                is_archived=False,
                is_subagent=False,
                bubble_count=bubbles,
                size_bytes=_size_bytes(con, cid),
                created_at=None,
                last_updated_at=None,
                has_composer_data=True,
            )
            if include_hidden or not summary.is_default_hidden:
                results.append(summary)

        results.sort(
            key=lambda c: (c.last_updated_at or c.created_at or 0),
            reverse=True,
        )
        return results
    finally:
        con.close()


def count_default_visible_chats(db_path: Path) -> int | None:
    """Count chats shown by default in the main UI (exclude subagents / zombies / drafts / empty)."""
    if not db_path.is_file():
        return None
    try:
        # Open in place so companion -wal/-shm in the same folder are applied.
        con = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
    except sqlite3.Error:
        try:
            con = _open_readonly_snapshot(db_path)
        except (StoreNotFoundError, OSError, sqlite3.Error):
            return None
    try:
        headers = _headers_from_table(con)
        if not headers:
            headers = _headers_from_itemtable(con)

        seen: set[str] = set()
        count = 0
        for h in headers:
            cid = h["composerId"]
            seen.add(cid)
            bubbles = _bubble_count(con, cid)
            has_cd = _has_composer_data(con, cid)
            name = h.get("name") or ""
            if not name and has_cd:
                name = _composer_data_name(con, cid) or ""
            kind = _classify(
                cid,
                is_archived=h["isArchived"],
                is_subagent=h["isSubagent"],
                bubble_count=bubbles,
                name=name,
                has_composer_data=has_cd,
            )
            if kind == ChatKind.SUBAGENT:
                continue
            if kind in {
                ChatKind.SYSTEM_DRAFT,
                ChatKind.ARCHIVED_EMPTY,
                ChatKind.EMPTY_TAB,
            }:
                continue
            count += 1

        for (key,) in con.execute(
            "SELECT key FROM cursorDiskKV WHERE key LIKE 'composerData:%'"
        ):
            cid = key.split(":", 1)[1]
            if cid in seen:
                continue
            if _bubble_count(con, cid) == 0:
                continue
            count += 1
        return count
    except sqlite3.Error:
        return None
    finally:
        con.close()


def _composer_data_name(con: sqlite3.Connection, composer_id: str) -> str | None:
    row = con.execute(
        "SELECT value FROM cursorDiskKV WHERE key=?",
        (f"composerData:{composer_id}",),
    ).fetchone()
    if not row:
        return None
    try:
        data = _json_loads(row[0] if not isinstance(row, sqlite3.Row) else row["value"])
        if isinstance(data, dict):
            return data.get("name")
    except (json.JSONDecodeError, TypeError):
        return None
    return None


def get_preview(composer_id: str, *, limit: int = 12) -> list[BubblePreview]:
    con = _open_readonly_snapshot(paths.state_vscdb())
    try:
        order: list[str] = []
        row = con.execute(
            "SELECT value FROM cursorDiskKV WHERE key=?",
            (f"composerData:{composer_id}",),
        ).fetchone()
        if row:
            try:
                data = _json_loads(row[0] if not isinstance(row, sqlite3.Row) else row["value"])
                headers = (data or {}).get("fullConversationHeadersOnly") or []
                for h in headers:
                    if isinstance(h, dict) and h.get("bubbleId"):
                        order.append(h["bubbleId"])
            except (json.JSONDecodeError, TypeError, AttributeError):
                pass

        previews: list[BubblePreview] = []
        if order:
            for bid in order[:limit]:
                brow = con.execute(
                    "SELECT value FROM cursorDiskKV WHERE key=?",
                    (f"bubbleId:{composer_id}:{bid}",),
                ).fetchone()
                if not brow:
                    continue
                try:
                    bdata = _json_loads(
                        brow[0] if not isinstance(brow, sqlite3.Row) else brow["value"]
                    )
                except (json.JSONDecodeError, TypeError):
                    continue
                text = (bdata or {}).get("text") or ""
                if not text and isinstance(bdata, dict):
                    # thinking-only / tool bubbles
                    text = (bdata.get("richText") or "")[:500]
                previews.append(
                    BubblePreview(
                        bubble_id=bid,
                        type=(bdata or {}).get("type"),
                        text=str(text)[:2000],
                    )
                )
        else:
            # Fallback: any bubbles for this composer
            rows = con.execute(
                "SELECT key, value FROM cursorDiskKV WHERE key LIKE ? LIMIT ?",
                (f"bubbleId:{composer_id}:%", limit),
            ).fetchall()
            for r in rows:
                key = r[0] if not isinstance(r, sqlite3.Row) else r["key"]
                bid = key.rsplit(":", 1)[-1]
                try:
                    bdata = _json_loads(r[1] if not isinstance(r, sqlite3.Row) else r["value"])
                except (json.JSONDecodeError, TypeError):
                    continue
                previews.append(
                    BubblePreview(
                        bubble_id=bid,
                        type=(bdata or {}).get("type"),
                        text=str((bdata or {}).get("text") or "")[:2000],
                    )
                )
        return previews
    finally:
        con.close()


def list_zombie_ids() -> list[str]:
    return [
        c.composer_id
        for c in list_chats(include_hidden=True)
        if c.kind == ChatKind.ARCHIVED_EMPTY
    ]


def delete_chats(
    composer_ids: Iterable[str],
    *,
    vacuum: bool = True,
    allow_while_cursor_running: bool = False,
) -> DeleteResult:
    ids = [i for i in dict.fromkeys(composer_ids) if i]
    result = DeleteResult()
    if not ids:
        return result

    if is_cursor_running() and not allow_while_cursor_running:
        raise CursorBusyError(
            "Cursor 正在运行。请完全退出 Cursor 后再删除，否则可能损坏对话库。"
        )

    if not paths.state_vscdb().is_file():
        raise StoreNotFoundError(f"找不到 {paths.state_vscdb()}")

    result.backup_path = backup_mod.create_backup()

    con = sqlite3.connect(str(paths.state_vscdb()))
    try:
        con.execute("PRAGMA busy_timeout=5000")
        # Merge WAL so deletes land in main DB consistently
        try:
            con.execute("PRAGMA wal_checkpoint(FULL)")
        except sqlite3.Error:
            pass

        has_headers_table = _table_exists(con, "composerHeaders")

        for cid in ids:
            try:
                _delete_one(con, cid, has_headers_table=has_headers_table)
                result.deleted_ids.append(cid)
            except sqlite3.Error as exc:
                result.errors.append(f"{cid}: {exc}")

        # Legacy ItemTable composer.composerHeaders JSON cleanup
        _strip_from_legacy_headers(con, result.deleted_ids)

        con.commit()

        if vacuum and result.deleted_ids:
            con.execute("VACUUM")
            result.vacuumed = True
    finally:
        con.close()

    _cleanup_search_index(result.deleted_ids)
    _cleanup_transcripts(result.deleted_ids)
    return result


def _delete_one(con: sqlite3.Connection, composer_id: str, *, has_headers_table: bool) -> None:
    if has_headers_table:
        con.execute("DELETE FROM composerHeaders WHERE composerId=?", (composer_id,))

    con.execute("DELETE FROM cursorDiskKV WHERE key=?", (f"composerData:{composer_id}",))
    con.execute(
        "DELETE FROM cursorDiskKV WHERE key LIKE ?",
        (f"bubbleId:{composer_id}:%",),
    )
    con.execute(
        "DELETE FROM cursorDiskKV WHERE key LIKE ?",
        (f"checkpointId:{composer_id}:%",),
    )
    con.execute(
        "DELETE FROM cursorDiskKV WHERE key LIKE ?",
        (f"messageRequestContext:{composer_id}:%",),
    )
    # Agent checkpoint roots keyed by composer id (do not touch shared agentKv:blob:*)
    con.execute(
        "DELETE FROM cursorDiskKV WHERE key LIKE ?",
        (f"agentKv:checkpoint:{composer_id}%",),
    )
    # Glass / panel keys sometimes live in ItemTable
    if _table_exists(con, "ItemTable"):
        con.execute(
            "DELETE FROM ItemTable WHERE key LIKE ?",
            (f"glass/cursor.editorPanelVisibility.agent/{composer_id}",),
        )
        con.execute(
            "DELETE FROM ItemTable WHERE key LIKE ?",
            (f"workbench.panel.composerChatViewPane.%{composer_id}%",),
        )


def _strip_from_legacy_headers(con: sqlite3.Connection, composer_ids: list[str]) -> None:
    if not composer_ids or not _table_exists(con, "ItemTable"):
        return
    row = con.execute(
        "SELECT value FROM ItemTable WHERE key='composer.composerHeaders'"
    ).fetchone()
    if not row:
        return
    try:
        data = _json_loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return
    id_set = set(composer_ids)
    changed = False
    if isinstance(data, dict) and isinstance(data.get("allComposers"), list):
        before = len(data["allComposers"])
        data["allComposers"] = [
            c
            for c in data["allComposers"]
            if not (isinstance(c, dict) and c.get("composerId") in id_set)
        ]
        changed = len(data["allComposers"]) != before
    elif isinstance(data, list):
        before = len(data)
        data = [
            c
            for c in data
            if not (isinstance(c, dict) and c.get("composerId") in id_set)
        ]
        changed = len(data) != before
    if changed:
        con.execute(
            "UPDATE ItemTable SET value=? WHERE key='composer.composerHeaders'",
            (json.dumps(data, ensure_ascii=False),),
        )


def _cleanup_search_index(composer_ids: list[str]) -> None:
    db = paths.conversation_search_db()
    if not composer_ids or not db.is_file():
        return
    try:
        con = sqlite3.connect(str(db))
    except sqlite3.Error:
        return
    try:
        con.execute("PRAGMA busy_timeout=5000")
        tables = {
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for cid in composer_ids:
            if "conversations" in tables:
                # FTS may be content-linked; delete from conversations first
                row = con.execute(
                    "SELECT fts_rowid FROM conversations WHERE id=?", (cid,)
                ).fetchone()
                fts_rowid = row[0] if row else None
                con.execute("DELETE FROM conversations WHERE id=?", (cid,))
                if fts_rowid is not None and "conversation_fts" in tables:
                    try:
                        con.execute(
                            "DELETE FROM conversation_fts WHERE rowid=?", (fts_rowid,)
                        )
                    except sqlite3.Error:
                        pass
            if "conversation_search_candidates" in tables:
                con.execute(
                    "DELETE FROM conversation_search_candidates WHERE id=?", (cid,)
                )
        con.commit()
    except sqlite3.Error:
        pass
    finally:
        con.close()


def _cleanup_transcripts(composer_ids: list[str]) -> None:
    root = paths.projects_dir()
    if not root.is_dir():
        return
    for cid in composer_ids:
        for path in root.glob(f"**/agent-transcripts/{cid}.*"):
            try:
                path.unlink()
            except OSError:
                pass
        # Also plain .jsonl / .txt without nested glob quirks
        for path in root.rglob(f"{cid}.jsonl"):
            if "agent-transcripts" in path.parts:
                try:
                    path.unlink()
                except OSError:
                    pass
        for path in root.rglob(f"{cid}.txt"):
            if "agent-transcripts" in path.parts:
                try:
                    path.unlink()
                except OSError:
                    pass


def format_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.2f} MB"


def format_ts(ms: int | None) -> str:
    if not ms:
        return "-"
    from datetime import datetime, timezone

    try:
        # Cursor sometimes stores ms; guard absurd values
        if ms > 10_000_000_000:
            dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone()
        else:
            dt = datetime.fromtimestamp(ms, tz=timezone.utc).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return str(ms)
