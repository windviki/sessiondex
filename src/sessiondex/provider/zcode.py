"""ZCode session provider (``~/.zcode/cli/db/db.sqlite``)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ..config import default_provider_paths
from .sqlite_provider import SqliteProvider


class ZcodeProvider(SqliteProvider):
    name = "zcode"

    def __init__(self, root: Path | str | None = None, cache_dir: Path | None = None):
        if root is None:
            root = default_provider_paths()["zcode"]
        super().__init__(root, cache_dir)

    def _is_subagent(self, row: sqlite3.Row) -> bool:
        keys = row.keys()
        if str(row["id"]).startswith("sess_subagent"):
            return True
        return "task_type" in keys and row["task_type"] == "subagent_child"
