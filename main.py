"""Entry point for Cursor Chat Manager."""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running without installing the package
_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from cursor_chat_manager.app import run


if __name__ == "__main__":
    run()
