"""Detect whether Cursor IDE is running."""

from __future__ import annotations

CURSOR_PROCESS_NAMES = frozenset(
    {
        "cursor.exe",
        "cursor helper.exe",
        "cursor helper (gpu).exe",
        "cursor helper (renderer).exe",
        "cursor helper (plugin).exe",
    }
)


def is_cursor_running() -> bool:
    try:
        import psutil
    except ImportError:
        return _is_cursor_running_tasklist()

    for proc in psutil.process_iter(["name"]):
        name = (proc.info.get("name") or "").lower()
        if name in CURSOR_PROCESS_NAMES or name == "cursor.exe":
            return True
    return False


def cursor_process_count() -> int:
    try:
        import psutil
    except ImportError:
        return 1 if _is_cursor_running_tasklist() else 0

    count = 0
    for proc in psutil.process_iter(["name"]):
        name = (proc.info.get("name") or "").lower()
        if name == "cursor.exe":
            count += 1
    return count


def _is_cursor_running_tasklist() -> bool:
    """Fallback without psutil (Windows tasklist)."""
    import subprocess

    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq Cursor.exe", "/NH"],
            text=True,
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return "cursor.exe" in out.lower()
