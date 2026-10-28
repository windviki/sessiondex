"""sessiondex: a local index & search layer over AI coding-agent session histories.

It never copies your session data: it builds a compact SQLite FTS5 index of
message text (truncated) plus pointers back to the original files/databases,
and reads full content on demand from the source of truth.
"""

__version__ = "0.1.0"

AGENTS = ("claude", "zcode", "kimi", "dsh", "qoder", "opencode")
