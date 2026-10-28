"""Qoder session provider (``~/.qoder-cn/projects``).

Qoder stores Claude-Code-style JSONL transcripts; only the discovery root
and the absence of a separate subagent transcript layout differ.
"""
from __future__ import annotations

from .claude_style import ClaudeStyleProvider


class QoderProvider(ClaudeStyleProvider):
    name = "qoder"
    file_globs = [("*/*.jsonl", False)]
