"""Claude Code session provider (``~/.claude/projects``)."""
from __future__ import annotations

from .claude_style import ClaudeStyleProvider


class ClaudeProvider(ClaudeStyleProvider):
    name = "claude"
    # main transcripts plus per-session subagent transcripts
    file_globs = [
        ("*/*.jsonl", False),
        ("*/*/subagents/*.jsonl", True),
    ]
