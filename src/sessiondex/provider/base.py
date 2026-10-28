"""Provider abstraction: one adapter per agent storage format.

A provider discovers sessions on disk and turns them into normalized
:class:`~sessiondex.model.MessageRecord` objects plus locators that can be
used later to re-read the full original record from the source of truth.
"""
from __future__ import annotations

import abc
import datetime as _dt
import json
from pathlib import Path
from typing import Any, Callable, Iterator

from ..model import TOOL_INPUT_CAP, MessageRecord, SessionMeta, ToolCall


def parse_iso_ms(value: Any) -> int:
    """Parse an ISO-8601 timestamp (or epoch ms int) to epoch milliseconds."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip()
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    try:
        if s.endswith(("Z", "z")):
            s = s[:-1] + "+00:00"
        dt = _dt.datetime.fromisoformat(s)
    except ValueError:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return int(dt.timestamp() * 1000)


_INPUT_PRIORITY_KEYS = (
    "command",
    "cmd",
    "file_path",
    "filePath",
    "path",
    "pattern",
    "url",
    "query",
    "prompt",
    "name",
    "description",
)


def summarize_input(raw: Any, cap: int = TOOL_INPUT_CAP) -> str:
    """Short human-readable summary of a tool call input (never the output)."""
    if raw is None:
        return ""
    if isinstance(raw, str):
        text = raw
    elif isinstance(raw, dict):
        text = ""
        for key in _INPUT_PRIORITY_KEYS:
            if key in raw and raw[key]:
                text = str(raw[key])
                break
        else:
            try:
                text = json.dumps(raw, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                text = str(raw)
    else:
        try:
            text = json.dumps(raw, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(raw)
    text = " ".join(text.split())
    return text[:cap]


def first_text(blocks: Any, text_key: str = "text") -> str:
    """Concatenate text blocks from a content array; pass through strings."""
    if isinstance(blocks, str):
        return blocks
    if not isinstance(blocks, list):
        return ""
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and isinstance(block.get(text_key), str):
            parts.append(block[text_key])
    return "\n".join(p for p in parts if p)


class Provider(abc.ABC):
    """Base class for agent storage adapters."""

    #: provider name, equals the agent key ("claude", "zcode", ...)
    name: str = ""
    #: "jsonl", "jsonl_zst" or "sqlite"
    kind: str = ""
    #: whether incremental appends can resume from a byte offset
    supports_append: bool = False

    def __init__(self, root: Path | str | None = None):
        if root is not None:
            self.root = Path(root)

    # -- discovery / iteration -------------------------------------------

    @abc.abstractmethod
    def discover(self) -> list[SessionMeta]:
        """Find all sessions for this agent."""

    @abc.abstractmethod
    def iter_messages(
        self, session: SessionMeta, include_raw: bool = False
    ) -> Iterator[MessageRecord | tuple[MessageRecord, dict]]:
        """Yield every message of *session* in chronological file order.

        With ``include_raw=True`` yields ``(record, raw_record)`` tuples where
        ``raw_record`` is the original parsed JSON of the underlying storage.
        """

    @abc.abstractmethod
    def scan_source(
        self,
        path: str,
        session: SessionMeta,
        state: dict | None,
        emit: Callable[[MessageRecord], None],
    ) -> dict:
        """(Re)index one source file, emitting normalized messages.

        *state* is the previously stored watermark for this source (or None
        for a first scan). Returns the new watermark dict to persist.
        """

    @abc.abstractmethod
    def read_raw(self, locator: dict[str, Any]) -> dict[str, Any]:
        """Read the full original record addressed by *locator*."""

    # -- helpers ---------------------------------------------------------

    def source_fingerprint(self, path: str) -> tuple[int, int]:
        """(size, mtime_ns) used to detect changes; sqlite providers also
        account for the -wal sidecar file."""
        st = Path(path).stat()
        return st.st_size, st.st_mtime_ns

    def emit_title(self) -> bool:
        return True
