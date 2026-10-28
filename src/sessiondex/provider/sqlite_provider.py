"""Shared implementation for agents that store sessions in SQLite.

ZCode (``~/.zcode/cli/db/db.sqlite``) and OpenCode
(``~/.local/share/opencode/opencode.db``) both use a ``session`` /
``message`` / ``part`` schema where ``message.data`` and ``part.data`` are
JSON columns: the message carries the role, and its parts carry the text /
reasoning / tool blocks (tool name in ``data.tool``).

Databases are only ever opened read-only. If a read-only open fails (e.g. a
stale WAL that needs recovery), a best-effort snapshot copy is made in the
cache directory and recovered there — the original database is never
written to.

Incremental scanning uses rowid watermarks on ``message`` and ``part``:
any message either new itself or touched by a new part is re-emitted and
upserted by its stable message id.
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Any, Callable, Iterator

import orjson

from ..model import MessageRecord, SessionMeta, ToolCall
from .base import Provider, summarize_input


def connect_readonly(db_path: Path, cache_dir: Path | None = None) -> sqlite3.Connection:
    """Open *db_path* read-only, falling back to a recovered snapshot copy."""
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=3000")
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        conn.execute("SELECT rowid FROM message LIMIT 1").fetchall()
        return conn
    except sqlite3.DatabaseError:
        conn.close()
    if cache_dir is None:
        raise
    return _open_snapshot(db_path, cache_dir)


def _open_snapshot(db_path: Path, cache_dir: Path) -> sqlite3.Connection:
    snap_dir = cache_dir / "snapshots" / f"{db_path.stem}-{int(db_path.stat().st_mtime)}"
    snap_dir.mkdir(parents=True, exist_ok=True)
    target = snap_dir / db_path.name
    if not target.exists():
        shutil.copy2(db_path, target)
    wal = db_path.with_name(db_path.name + "-wal")
    if wal.is_file():
        shutil.copy2(wal, target.with_name(target.name + "-wal"))
    conn = sqlite3.connect(str(target), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=3000")
    # drop older snapshots of the same database
    for old in (cache_dir / "snapshots").glob(f"{db_path.stem}-*"):
        if old != snap_dir:
            shutil.rmtree(old, ignore_errors=True)
    return conn


class SqliteProvider(Provider):
    name = ""
    kind = "sqlite"
    supports_append = True

    def __init__(self, root: Path | str | None = None, cache_dir: Path | None = None):
        super().__init__(root)
        self._db_path = Path(root) if root is not None else None
        self.cache_dir = cache_dir

    @property
    def db_path(self) -> Path:
        return self._db_path or Path.home()  # overridden via constructor in practice

    # -- schema hooks ------------------------------------------------------

    def _is_subagent(self, row: sqlite3.Row) -> bool:
        return False

    # -- Provider API ------------------------------------------------------

    def source_fingerprint(self, path: str) -> tuple[int, int]:
        """Combine db and -wal/-shm metadata so WAL-only writes are seen."""
        p = Path(path)
        st = p.stat()
        size, mtime = st.st_size, st.st_mtime_ns
        for suffix in ("-wal", "-shm"):
            side = p.with_name(p.name + suffix)
            if side.is_file():
                s2 = side.stat()
                size += s2.st_size
                mtime = max(mtime, s2.st_mtime_ns)
        return size, mtime

    def discover(self) -> list[SessionMeta]:
        if not self.db_path.is_file():
            return []
        conn = connect_readonly(self.db_path, self.cache_dir)
        try:
            cols = {
                r["name"] for r in conn.execute("PRAGMA table_info(session)")
            }
            wanted = [
                c
                for c in ("id", "directory", "title", "time_created", "time_updated",
                          "task_type", "parent_id")
                if c in cols
            ]
            rows = conn.execute(f"SELECT {', '.join(wanted)} FROM session").fetchall()
        finally:
            conn.close()
        out = []
        for row in rows:
            out.append(
                SessionMeta(
                    agent=self.name,
                    session_id=str(row["id"]),
                    title=str(row["title"] or "") if "title" in row.keys() else "",
                    cwd=str(row["directory"] or "") if "directory" in row.keys() else "",
                    file_path=str(self.db_path),
                    first_ts=int(row["time_created"] or 0) if "time_created" in row.keys() else 0,
                    last_ts=int(row["time_updated"] or 0) if "time_updated" in row.keys() else 0,
                    subagent=self._is_subagent(row),
                    sources=[str(self.db_path)],
                )
            )
        return out

    def _connect(self) -> sqlite3.Connection:
        return connect_readonly(self.db_path, self.cache_dir)

    def _session_index(self, conn: sqlite3.Connection) -> dict[str, dict]:
        out: dict[str, dict] = {}
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(session)")}
        wanted = [c for c in ("id", "directory", "task_type", "parent_id") if c in cols]
        for row in conn.execute(f"SELECT {', '.join(wanted)} FROM session"):
            out[str(row["id"])] = {
                "cwd": str(row["directory"] or "") if "directory" in row.keys() else "",
                "subagent": self._is_subagent(row),
            }
        return out

    def load_message(
        self,
        conn: sqlite3.Connection,
        message_id: str,
        sessions: dict[str, dict],
        db_path: str,
        for_index: bool,
    ) -> MessageRecord | None:
        row = conn.execute(
            "SELECT rowid, id, session_id, time_created, data FROM message WHERE id = ?",
            (message_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            data = orjson.loads(row["data"]) if row["data"] else {}
        except orjson.JSONDecodeError:
            data = {}
        role = data.get("role")
        if role not in ("user", "assistant"):
            return None
        ts = 0
        time_obj = data.get("time")
        if isinstance(time_obj, dict):
            ts = int(time_obj.get("created") or 0)
        if not ts and row["time_created"] is not None:
            ts = int(row["time_created"])
        session_id = str(row["session_id"])
        sinfo = sessions.get(session_id, {})
        cwd = ""
        path = data.get("path")
        if isinstance(path, dict):
            cwd = str(path.get("cwd") or "")
        snapshot = data.get("contextSnapshot")
        if not cwd and isinstance(snapshot, dict):
            env = snapshot.get("envInfo")
            if isinstance(env, dict):
                cwd = str(env.get("cwd") or "")
        if not cwd:
            cwd = sinfo.get("cwd", "")

        texts: list[str] = []
        reasoning: list[str] = []
        tools: list[ToolCall] = []
        parts = conn.execute(
            "SELECT data FROM part WHERE message_id = ? ORDER BY rowid", (message_id,)
        ).fetchall()
        for prow in parts:
            try:
                pdata = orjson.loads(prow["data"]) if prow["data"] else {}
            except orjson.JSONDecodeError:
                continue
            if not isinstance(pdata, dict):
                continue
            ptype = pdata.get("type")
            if ptype == "text" and isinstance(pdata.get("text"), str):
                texts.append(pdata["text"])
            elif ptype == "reasoning" and isinstance(pdata.get("text"), str):
                reasoning.append(pdata["text"])
            elif ptype == "tool":
                state = pdata.get("state") or {}
                call = ToolCall(
                    name=str(pdata.get("tool") or ""),
                    input_summary=summarize_input(state.get("input") if isinstance(state, dict) else None),
                )
                if not for_index and isinstance(state, dict):
                    out = state.get("output")
                    if isinstance(out, str):
                        call.result_preview = out[:500]
                    elif out is not None:
                        call.result_preview = str(out)[:500]
                tools.append(call)

        record = MessageRecord(
            agent=self.name,
            session_id=session_id,
            ts_ms=ts,
            role=role,
            text="\n".join(t for t in texts if t),
            reasoning="\n".join(r for r in reasoning if r),
            tools=tools,
            locator={
                "kind": "sqlite",
                "agent": self.name,
                "db": db_path,
                "message_id": message_id,
            },
            subagent=bool(sinfo.get("subagent")),
            cwd=cwd,
        )
        if for_index and not record.text and not record.reasoning and not record.tools:
            return None
        return record

    def scan_source(
        self,
        path: str,
        session: SessionMeta,
        state: dict | None,
        emit: Callable[[MessageRecord], None],
    ) -> dict:
        conn = self._connect()
        try:
            state = state or {}
            msg_wm = int(state.get("msg_wm", 0))
            part_wm = int(state.get("part_wm", 0))
            max_msg = conn.execute(
                "SELECT COALESCE(MAX(rowid), 0) FROM message"
            ).fetchone()[0]
            max_part = conn.execute(
                "SELECT COALESCE(MAX(rowid), 0) FROM part"
            ).fetchone()[0]
            affected: set[str] = set()
            for row in conn.execute(
                "SELECT id FROM message WHERE rowid > ? ORDER BY rowid", (msg_wm,)
            ):
                affected.add(str(row["id"]))
            for row in conn.execute(
                "SELECT DISTINCT message_id FROM part WHERE rowid > ?", (part_wm,)
            ):
                if row["message_id"]:
                    affected.add(str(row["message_id"]))
            if affected:
                sessions = self._session_index(conn)
                ids = sorted(affected)
                for i in range(0, len(ids), 500):
                    chunk = ids[i : i + 500]
                    placeholders = ",".join("?" * len(chunk))
                    for row in conn.execute(
                        f"SELECT id FROM message WHERE id IN ({placeholders}) ORDER BY rowid",
                        chunk,
                    ):
                        rec = self.load_message(
                            conn, str(row["id"]), sessions, path, for_index=True
                        )
                        if rec is not None:
                            emit(rec)
            return {"msg_wm": max_msg, "part_wm": max_part}
        finally:
            conn.close()

    def iter_messages(
        self, session: SessionMeta, include_raw: bool = False
    ) -> Iterator[MessageRecord | tuple[MessageRecord, dict]]:
        db = session.file_path or str(self.db_path)
        conn = connect_readonly(Path(db), self.cache_dir)
        try:
            sessions = self._session_index(conn)
            for row in conn.execute(
                "SELECT id, rowid FROM message WHERE session_id = ? ORDER BY rowid",
                (session.session_id,),
            ):
                rec = self.load_message(
                    conn, str(row["id"]), sessions, db, for_index=False
                )
                if rec is None:
                    continue
                if include_raw:
                    raw = self._raw_message(conn, str(row["id"]))
                    yield rec, raw  # type: ignore[misc]
                else:
                    yield rec
        finally:
            conn.close()

    def _raw_message(self, conn: sqlite3.Connection, message_id: str) -> dict:
        row = conn.execute(
            "SELECT * FROM message WHERE id = ?", (message_id,)
        ).fetchone()
        parts = conn.execute(
            "SELECT * FROM part WHERE message_id = ? ORDER BY rowid", (message_id,)
        ).fetchall()
        return {
            "message": {k: row[k] for k in row.keys()} if row else {},
            "parts": [{k: p[k] for k in p.keys()} for p in parts],
        }

    def read_raw(self, locator: dict[str, Any]) -> dict[str, Any]:
        conn = connect_readonly(Path(locator["db"]), self.cache_dir)
        try:
            return self._raw_message(conn, str(locator["message_id"]))
        finally:
            conn.close()
