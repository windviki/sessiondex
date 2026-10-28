"""SQLite storage for the search index.

Design guarantees (the "index layer only" contract):

* ``messages.text`` / ``reasoning`` hold at most :data:`TEXT_CAP` characters
  of the original message — enough for snippets — never tool outputs.
* Tool calls are stored as name + short input summary only.
* Full content is always fetched from the original files via locators.

FTS5 setup: a standalone ``messages_fts`` table over pre-tokenized token
streams (see :mod:`sessiondex.tokenize`); ``messages.id`` is the FTS rowid,
so deletes are plain ``DELETE ... WHERE rowid = ?``. Storage caps live in
:mod:`sessiondex.model` and can be tuned with SESSIONDEX_*_CAP env vars.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import orjson

from . import tokenize
from .model import (
    REASONING_CAP,
    TEXT_CAP,
    TOOL_INPUT_CAP,
    MessageRecord,
    SessionMeta,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions(
  id INTEGER PRIMARY KEY,
  agent TEXT NOT NULL,
  session_id TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  cwd TEXT NOT NULL DEFAULT '',
  file_path TEXT NOT NULL DEFAULT '',
  sources TEXT NOT NULL DEFAULT '[]',
  subagent INTEGER NOT NULL DEFAULT 0,
  first_ts INTEGER NOT NULL DEFAULT 0,
  last_ts INTEGER NOT NULL DEFAULT 0,
  msg_count INTEGER NOT NULL DEFAULT 0,
  UNIQUE(agent, session_id)
);
CREATE INDEX IF NOT EXISTS idx_sessions_agent ON sessions(agent, last_ts);
CREATE INDEX IF NOT EXISTS idx_sessions_cwd ON sessions(cwd);
CREATE TABLE IF NOT EXISTS sources(
  path TEXT PRIMARY KEY,
  agent TEXT NOT NULL,
  session_key TEXT NOT NULL,
  size INTEGER NOT NULL,
  mtime_ns INTEGER NOT NULL,
  state TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY,
  agent TEXT NOT NULL,
  session_rowid INTEGER NOT NULL,
  src TEXT NOT NULL,
  msg_key TEXT NOT NULL UNIQUE,
  role TEXT NOT NULL,
  ts_ms INTEGER NOT NULL,
  subagent INTEGER NOT NULL DEFAULT 0,
  tools TEXT NOT NULL DEFAULT '[]',
  locator TEXT NOT NULL DEFAULT '{}',
  snippet TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL DEFAULT '',
  reasoning TEXT NOT NULL DEFAULT '',
  tools_text TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_rowid);
CREATE INDEX IF NOT EXISTS idx_messages_agent_ts ON messages(agent, ts_ms);
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts_ms);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
  text, reasoning, tools_text,
  tokenize='unicode61'
);
"""


def message_key(rec: MessageRecord) -> str:
    loc = rec.locator
    if loc.get("kind") == "sqlite":
        return f"{rec.agent}:{loc.get('message_id')}"
    return f"{loc.get('path')}:{loc.get('line')}"


class Index:
    """Thin data-access layer over the index database."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=15, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # -- meta --------------------------------------------------------------

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # -- sessions ----------------------------------------------------------

    def upsert_session(self, meta: SessionMeta) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO sessions(agent, session_id, title, cwd, file_path, sources,
                                 subagent, first_ts, last_ts)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(agent, session_id) DO UPDATE SET
              title=excluded.title, cwd=excluded.cwd, file_path=excluded.file_path,
              sources=excluded.sources, subagent=excluded.subagent,
              first_ts=excluded.first_ts, last_ts=excluded.last_ts
            """,
            (
                meta.agent,
                meta.session_id,
                meta.title,
                meta.cwd,
                meta.file_path,
                orjson.dumps(meta.sources).decode(),
                int(meta.subagent),
                int(meta.first_ts),
                int(meta.last_ts),
            ),
        )
        row = self.conn.execute(
            "SELECT id FROM sessions WHERE agent=? AND session_id=?",
            (meta.agent, meta.session_id),
        ).fetchone()
        return int(row["id"])

    def get_session(self, agent: str, session_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM sessions WHERE agent=? AND session_id=?",
            (agent, session_id),
        ).fetchone()
        return dict(row) if row else None

    def refresh_session_stats(self, session_rowid: int) -> None:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS n, COALESCE(MIN(ts_ms),0) AS first_ts,
                   COALESCE(MAX(ts_ms),0) AS last_ts
            FROM messages WHERE session_rowid=?
            """,
            (session_rowid,),
        ).fetchone()
        self.conn.execute(
            "UPDATE sessions SET msg_count=?, first_ts=?, last_ts=? WHERE id=?",
            (row["n"], row["first_ts"], row["last_ts"], session_rowid),
        )

    def fill_session_title(self, session_rowid: int) -> None:
        """If a session has no title, use its first user message text."""
        sess = self.conn.execute(
            "SELECT title FROM sessions WHERE id=?", (session_rowid,)
        ).fetchone()
        if not sess or sess["title"]:
            return
        row = self.conn.execute(
            "SELECT snippet FROM messages WHERE session_rowid=? AND role='user'"
            " ORDER BY ts_ms LIMIT 1",
            (session_rowid,),
        ).fetchone()
        if row and row["snippet"]:
            self.conn.execute(
                "UPDATE sessions SET title=? WHERE id=?",
                (row["snippet"][:120], session_rowid),
            )

    # -- messages ----------------------------------------------------------

    def upsert_message(self, rec: MessageRecord, session_rowid: int) -> int | None:
        key = message_key(rec)
        text = rec.text[:TEXT_CAP]
        reasoning = rec.reasoning[:REASONING_CAP]
        tools = [
            {"name": t.name, "input_summary": t.input_summary} for t in rec.tools
        ]
        tools_text = " ".join(
            f"{t.name} {t.input_summary}"[:TOOL_INPUT_CAP] for t in rec.tools
        )[:800]
        fts_text = tokenize.index_text(text)[: TEXT_CAP * 3]
        fts_reasoning = tokenize.index_text(reasoning)[: REASONING_CAP * 3]
        fts_tools = tokenize.index_text(tools_text)[:2000]
        snippet = " ".join((text or reasoning).split())[:200]

        old = self.conn.execute(
            "SELECT id FROM messages WHERE msg_key=?", (key,)
        ).fetchone()
        with self.conn:
            if old:
                mid = int(old["id"])
                self.conn.execute(
                    "DELETE FROM messages_fts WHERE rowid=?", (mid,)
                )
                self.conn.execute("DELETE FROM messages WHERE id=?", (mid,))
            else:
                mid = None
            cur = self.conn.execute(
                """
                INSERT INTO messages(agent, session_rowid, src, msg_key, role, ts_ms,
                                     subagent, tools, locator, snippet, text,
                                     reasoning, tools_text)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    rec.agent,
                    session_rowid,
                    str(rec.locator.get("path") or rec.locator.get("db") or ""),
                    key,
                    rec.role,
                    int(rec.ts_ms),
                    int(rec.subagent),
                    orjson.dumps(tools).decode(),
                    orjson.dumps(rec.locator).decode(),
                    snippet,
                    text,
                    reasoning,
                    tools_text,
                ),
            )
            mid = int(cur.lastrowid)
            self.conn.execute(
                "INSERT INTO messages_fts(rowid, text, reasoning, tools_text)"
                " VALUES(?,?,?,?)",
                (mid, fts_text, fts_reasoning, fts_tools),
            )
        return mid

    def delete_messages_for_src(self, src: str) -> int:
        rows = self.conn.execute(
            "SELECT id FROM messages WHERE src=?", (src,)
        ).fetchall()
        with self.conn:
            for row in rows:
                self.conn.execute(
                    "DELETE FROM messages_fts WHERE rowid=?", (row["id"],)
                )
            self.conn.execute("DELETE FROM messages WHERE src=?", (src,))
        return len(rows)

    def get_message(self, msg_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM messages WHERE id=?", (msg_id,)
        ).fetchone()
        return dict(row) if row else None

    # -- sources (scan watermarks) ------------------------------------------

    def get_source(self, path: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM sources WHERE path=?", (path,)
        ).fetchone()
        if not row:
            return None
        out = dict(row)
        try:
            out["state"] = json.loads(out.get("state") or "{}")
        except json.JSONDecodeError:
            out["state"] = {}
        return out

    def set_source(
        self, path: str, agent: str, session_key: str, size: int, mtime_ns: int, state: dict
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO sources(path, agent, session_key, size, mtime_ns, state)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(path) DO UPDATE SET agent=excluded.agent,
              session_key=excluded.session_key, size=excluded.size,
              mtime_ns=excluded.mtime_ns, state=excluded.state
            """,
            (path, agent, session_key, size, mtime_ns, json.dumps(state)),
        )

    # -- misc ---------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        def one(sql: str, params: tuple = ()) -> int:
            return int(self.conn.execute(sql, params).fetchone()[0])

        agents = [
            dict(r)
            for r in self.conn.execute(
                """
                SELECT s.agent AS agent, COUNT(DISTINCT s.id) AS sessions,
                       SUM(s.msg_count) AS messages, MAX(s.last_ts) AS last_ts
                FROM sessions s GROUP BY s.agent ORDER BY s.agent
                """
            )
        ]
        return {
            "index_path": str(self.path),
            "index_size_bytes": self.path.stat().st_size if self.path.exists() else 0,
            "sessions": one("SELECT COUNT(*) FROM sessions"),
            "messages": one("SELECT COUNT(*) FROM messages"),
            "agents": agents,
            "last_scan": int(self.get_meta("last_scan", "0") or 0),
        }

    def mark_scan(self) -> None:
        self.set_meta("last_scan", str(int(time.time() * 1000)))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def rebuild(self) -> None:
        """Drop all index content (keeps the database file)."""
        with self.conn:
            self.conn.execute("DELETE FROM messages_fts")
            self.conn.execute("DELETE FROM messages")
            self.conn.execute("DELETE FROM sessions")
            self.conn.execute("DELETE FROM sources")
            self.conn.execute("DELETE FROM meta WHERE key='last_scan'")
