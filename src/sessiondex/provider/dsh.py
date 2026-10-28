"""dsh (DeepSeek CLI) session provider (``~/.dsh``).

Layout::

    sessions/<cwd-with-dashes-as-slashes>/
        session-<uuid>/session.v4.jsonl.zstd    # main sessions
        <uuid>/session.v4.jsonl.zstd            # subagent sessions (bare uuid)

Each file is a zstd-compressed JSONL stream (often multiple concatenated
frames). The first record is a header ``{"type": "session", "version": 4,
"id": ..., "createdAt": ms, "cwd": ..., "origin": ...}``; every other record
is an envelope ``{"type": ..., "seq": N, "time": epoch-ms, "data": {...}}``
with types such as ``user/message``, ``assistant/message``, ``tool/call``
and ``tool/result``. Assistant messages embed ``tool-call`` blocks whose
``arguments`` field is a JSON-encoded string.

Compressed streams cannot be seeked incrementally, so any change reparses
the whole (small) file; decompressed copies are kept in a bounded LRU cache.
"""
from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Iterator

import orjson
import zstandard

from ..model import MessageRecord, SessionMeta, ToolCall
from .base import Provider, summarize_input

_DECOMP_CACHE_MAX_BYTES = 128 * 1024 * 1024
_cache: OrderedDict[str, tuple[tuple[int, int], list[bytes]]] = OrderedDict()
_cache_bytes = 0


def _decompress_lines(path: str) -> list[bytes]:
    """Decompress *path* fully and return its JSONL lines (cached)."""
    st = Path(path).stat()
    fp = (st.st_size, st.st_mtime_ns)
    hit = _cache.get(path)
    if hit is not None and hit[0] == fp:
        _cache.move_to_end(path)
        return hit[1]
    dctx = zstandard.ZstdDecompressor()
    chunks: list[bytes] = []
    with open(path, "rb") as fh:
        reader = dctx.stream_reader(fh, read_across_frames=True)
        while True:
            chunk = reader.read(1 << 20)
            if not chunk:
                break
            chunks.append(chunk)
    lines = b"".join(chunks).splitlines()
    _cache[path] = (fp, lines)
    _cache.move_to_end(path)
    global _cache_bytes
    _cache_bytes += sum(len(x) for x in lines)
    while _cache_bytes > _DECOMP_CACHE_MAX_BYTES and len(_cache) > 1:
        _, (_, old) = _cache.popitem(last=False)
        _cache_bytes -= sum(len(x) for x in old)
    return lines


def _parse_dsh_lines(
    lines: Iterator[tuple[int, bytes]],
    *,
    agent: str,
    path: str,
    session: SessionMeta,
    for_index: bool,
    include_raw: bool,
) -> Iterator[MessageRecord | tuple[MessageRecord, dict]]:
    last_call: dict[str, ToolCall] = {}
    for line_no, raw in lines:
        try:
            rec = orjson.loads(raw)
        except orjson.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        rtype = rec.get("type")
        ts = int(rec.get("time") or 0)

        if rtype == "session":
            session.cwd = str(rec.get("cwd") or session.cwd)
            if rec.get("origin") == "subagent":
                session.subagent = True
            continue

        data = rec.get("data") or {}
        if not isinstance(data, dict):
            continue
        role: str | None = None
        blocks: Any = None
        if rtype == "user/message":
            role, blocks = "user", data.get("content")
        elif rtype == "assistant/message":
            msg = data.get("message") or {}
            role = msg.get("role")
            blocks = msg.get("content")
        elif rtype == "tool/result" and not for_index:
            msg = data.get("message") or {}
            call = last_call.get(str(msg.get("toolCallId") or ""))
            preview = ""
            for block in msg.get("content") or []:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    preview = block["text"][:500]
                    break
            if call is not None:
                call.result_preview = preview
            continue
        else:
            continue
        if role not in ("user", "assistant"):
            continue

        texts: list[str] = []
        reasoning: list[str] = []
        tools: list[ToolCall] = []
        if isinstance(blocks, str):
            texts.append(blocks)
        elif isinstance(blocks, list):
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text" and isinstance(block.get("text"), str):
                    texts.append(block["text"])
                elif btype == "reasoning" and isinstance(block.get("text"), str):
                    reasoning.append(block["text"])
                elif btype == "tool-call":
                    args = block.get("arguments")
                    if isinstance(args, str):
                        try:
                            args = orjson.loads(args)
                        except orjson.JSONDecodeError:
                            pass
                    call = ToolCall(
                        name=str(block.get("name") or ""),
                        input_summary=summarize_input(args),
                    )
                    tools.append(call)
                    last_call[str(block.get("id") or "")] = call

        record = MessageRecord(
            agent=agent,
            session_id=session.session_id,
            ts_ms=ts,
            role=role,
            text="\n".join(t for t in texts if t),
            reasoning="\n".join(r for r in reasoning if r),
            tools=tools,
            locator={"kind": "jsonl_zst", "path": path, "line": line_no},
            subagent=session.subagent,
            cwd=session.cwd,
        )
        if for_index and not record.text and not record.reasoning and not record.tools:
            continue
        if include_raw:
            yield record, rec  # type: ignore[misc]
        else:
            yield record


class DshProvider(Provider):
    name = "dsh"
    kind = "jsonl_zst"
    supports_append = False  # compressed streams are reparsed whole

    def discover(self) -> list[SessionMeta]:
        sessions_root = self.root / "sessions"
        if not sessions_root.is_dir():
            return []
        out: list[SessionMeta] = []
        for project_dir in sorted(sessions_root.iterdir()):
            if not project_dir.is_dir():
                continue
            for session_dir in sorted(project_dir.iterdir()):
                if not session_dir.is_dir():
                    continue
                source = None
                for name in ("session.v4.jsonl.zstd", "session.jsonl.zstd"):
                    candidate = session_dir / name
                    if candidate.is_file():
                        source = candidate
                        break
                if source is None:
                    continue
                out.append(
                    SessionMeta(
                        agent=self.name,
                        session_id=session_dir.name,
                        file_path=str(source),
                        subagent=not session_dir.name.startswith("session-"),
                        sources=[str(source)],
                    )
                )
        return out

    def scan_source(
        self,
        path: str,
        session: SessionMeta,
        state: dict | None,
        emit: Callable[[MessageRecord], None],
    ) -> dict:
        lines = _decompress_lines(path)
        indexed = 0
        for rec in _parse_dsh_lines(
            ((i, line) for i, line in enumerate(lines, start=1)),
            agent=self.name,
            path=path,
            session=session,
            for_index=True,
            include_raw=False,
        ):
            emit(rec)
            indexed += 1
        return {"lines": len(lines), "messages": indexed}

    def iter_messages(
        self, session: SessionMeta, include_raw: bool = False
    ) -> Iterator[MessageRecord | tuple[MessageRecord, dict]]:
        for source in session.sources or ([session.file_path] if session.file_path else []):
            lines = _decompress_lines(source)
            yield from _parse_dsh_lines(
                ((i, line) for i, line in enumerate(lines, start=1)),
                agent=self.name,
                path=source,
                session=session,
                for_index=False,
                include_raw=include_raw,
            )

    def read_raw(self, locator: dict[str, Any]) -> dict[str, Any]:
        lines = _decompress_lines(locator["path"])
        return orjson.loads(lines[int(locator.get("line", 0)) - 1])
