"""OpenCode session provider (``~/.local/share/opencode/opencode.db``)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ..config import default_provider_paths
from .sqlite_provider import SqliteProvider


class OpencodeProvider(SqliteProvider):
    name = "opencode"

    def __init__(self, root: Path | str | None = None, cache_dir: Path | None = None):
        if root is None:
            root = default_provider_paths()["opencode"]
        super().__init__(root, cache_dir)

    def _is_subagent(self, row: sqlite3.Row) -> bool:
        keys = row.keys()
        return "parent_id" in keys and bool(row["parent_id"])
