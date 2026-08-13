"""Resolve Cursor and app data paths on Windows."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse


def appdata_roaming() -> Path:
    return Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))


def cursor_user_dir() -> Path:
    return appdata_roaming() / "Cursor" / "User"


def global_storage_dir() -> Path:
    return cursor_user_dir() / "globalStorage"


def workspace_storage_dir() -> Path:
    return cursor_user_dir() / "workspaceStorage"


def state_vscdb() -> Path:
    return global_storage_dir() / "state.vscdb"


def conversation_search_db() -> Path:
    return global_storage_dir() / "conversation-search.db"


def cursor_home() -> Path:
    return Path.home() / ".cursor"


def projects_dir() -> Path:
    return cursor_home() / "projects"


def manager_home() -> Path:
    return Path.home() / ".cursor-chat-manager"


def backups_dir() -> Path:
    return manager_home() / "backups"


def folder_uri_to_path(uri: str | None) -> str | None:
    """Convert file:/// or vscode-remote URI to a display path."""
    if not uri:
        return None
    if uri.startswith("vscode-remote:"):
        return uri
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        path = unquote(parsed.path)
        # Windows: /C:/Users/... → C:/Users/...
        if len(path) >= 3 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        return path.replace("/", "\\") if os.name == "nt" else path
    return uri


def load_workspace_map() -> dict[str, str | None]:
    """Map workspaceStorage folder name → project folder path."""
    root = workspace_storage_dir()
    mapping: dict[str, str | None] = {}
    if not root.is_dir():
        return mapping
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        wj = entry / "workspace.json"
        folder: str | None = None
        if wj.is_file():
            try:
                import json

                data = json.loads(wj.read_text(encoding="utf-8"))
                uri = data.get("folder") or data.get("workspace")
                folder = folder_uri_to_path(uri) if isinstance(uri, str) else None
            except (OSError, ValueError, TypeError):
                folder = None
        mapping[entry.name] = folder
    return mapping
