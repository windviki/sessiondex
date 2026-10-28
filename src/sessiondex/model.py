"""Normalized data model shared by all providers."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


def _cap_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


TEXT_CAP = _cap_env("SESSIONDEX_TEXT_CAP", 2048)
REASONING_CAP = _cap_env("SESSIONDEX_REASONING_CAP", 1024)
TOOL_INPUT_CAP = _cap_env("SESSIONDEX_TOOL_INPUT_CAP", 200)


@dataclass
class ToolCall:
    name: str
    input_summary: str = ""
    result_preview: str = ""  # reader-only; never stored in the index


@dataclass
class MessageRecord:
    """One user or assistant message, normalized across agent formats."""

    agent: str
    session_id: str
    ts_ms: int  # epoch milliseconds
    role: str  # "user" | "assistant"
    text: str = ""
    reasoning: str = ""
    tools: list[ToolCall] = field(default_factory=list)
    locator: dict[str, Any] = field(default_factory=dict)
    subagent: bool = False
    cwd: str = ""
    #: reader-only previews of tool results; never stored in the index
    tool_results: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "session_id": self.session_id,
            "ts_ms": self.ts_ms,
            "role": self.role,
            "text": self.text,
            "reasoning": self.reasoning,
            "tools": [
                {
                    "name": t.name,
                    "input_summary": t.input_summary,
                    **({"result_preview": t.result_preview} if t.result_preview else {}),
                }
                for t in self.tools
            ],
            "tool_results": self.tool_results,
            "locator": self.locator,
            "subagent": self.subagent,
            "cwd": self.cwd,
        }


@dataclass
class SessionMeta:
    """A conversation session discovered on disk."""

    agent: str
    session_id: str
    title: str = ""
    cwd: str = ""
    file_path: str = ""  # primary storage path (file or sqlite db)
    first_ts: int = 0
    last_ts: int = 0
    subagent: bool = False
    sources: list[str] = field(default_factory=list)  # all files feeding this session

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "session_id": self.session_id,
            "title": self.title,
            "cwd": self.cwd,
            "file_path": self.file_path,
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
            "subagent": self.subagent,
            "sources": self.sources,
        }
