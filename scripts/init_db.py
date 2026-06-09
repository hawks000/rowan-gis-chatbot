#!/usr/bin/env python3
"""Initialize the chat history SQLite database."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.chat_log import get_db_path, init_db  # noqa: E402


def main() -> None:
    path = get_db_path()
    init_db(path)
    print(f"Initialized chat database at {path}")


if __name__ == "__main__":
    main()
