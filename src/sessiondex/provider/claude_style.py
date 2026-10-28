"""Shared parser for Claude-Code-style JSONL transcripts.

Claude Code and Qoder both store one JSONL file per session under
``projects/<sanitized-cwd>/<session-uuid>.jsonl`` with records shaped like
``{"type": "user"|"assistant", "timestamp": ISO, "cwd": ..., "message":
{"role": ..., "content": str | [blocks]}}``. Tool calls are ``tool_use``
blocks; tool results come back inside subsequent ``user`` records as
``tool_result`` blocks.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterator

import orjson

from ..model import MessageRecord, SessionMeta, ToolCall
from .base import Provider, parse_iso_ms, summarize_input


def parse_claude_line(
    raw: bytes | str,
    *,
    agent: str,
    path: str,
    line_no: int,
    session: SessionMeta,
    for_index: bool,
) -> tuple[MessageRecord | None, list[tuple[str, ToolCall]], list[tuple[str, str]]]:
    """Parse one JSONL record.

    Returns ``(record, tool_uses, tool_results)`` where *tool_uses* is a list
    of ``(tool_use_id, call)`` from this record and *tool_results* a list of
    ``(tool_use_id, output_text)``. The record may be None (records with no
    indexable content, e.g. pure tool_result carriers in index mode) while
    the tool lists are still populated so callers can attach outputs.
    """
    try:
        rec = orjson.loads(raw)
    except orjson.JSONDecodeError:
        return None, [], []
    if not isinstance(rec, dict):
        return None, [], []
    rtype = rec.get("type")
    if rtype not in ("user", "assistant"):
        return None, [], []

    msg = rec.get("message") or {}
    if not isinstance(msg, dict):
        return None, [], []
    role = msg.get("role") or rtype
    content = msg.get("content")

    texts: list[str] = []
    reasoning: list[str] = []
    tools: list[ToolCall] = []
    tool_results: list[dict[str, Any]] = []
    uses: list[tuple[str, ToolCall]] = []
    results: list[tuple[str, str]] = []

    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                if isinstance(block.get("text"), str):
                    texts.append(block["text"])
            elif btype == "thinking":
                if isinstance(block.get("thinking"), str):
                    reasoning.append(block["thinking"])
            elif btype == "tool_use":
                call = ToolCall(
                    name=str(block.get("name") or ""),
                    input_summary=summarize_input(block.get("input")),
                )
                tools.append(call)
                uses.append((str(block.get("id") or ""), call))
            elif btype == "tool_result":
                preview = block.get("content")
                if isinstance(preview, list):
                    preview = " ".join(
                        b.get("text", "") for b in preview if isinstance(b, dict)
                    )
                text = str(preview or "")
                results.append((str(block.get("tool_use_id") or ""), text))
                if not for_index:
                    tool_results.append(
                        {
                            "tool_use_id": block.get("tool_use_id"),
                            "preview": text[:500],
                            "is_error": bool(block.get("is_error")),
                        }
                    )

    record = MessageRecord(
        agent=agent,
        session_id=str(rec.get("sessionId") or session.session_id),
        ts_ms=parse_iso_ms(rec.get("timestamp")) or session.first_ts,
        role=role if role in ("user", "assistant") else rtype,
        text="\n".join(t for t in texts if t),
        reasoning="\n".join(r for r in reasoning if r),
        tools=tools,
        locator={"kind": "jsonl", "path": path, "line": line_no},
        subagent=bool(rec.get("isSidechain")) or session.subagent,
        cwd=str(rec.get("cwd") or session.cwd),
        tool_results=tool_results,
    )
    if for_index and not record.text and not record.reasoning and not record.tools:
        return None, uses, results  # carrier records still carry results
    return record, uses, results


class ClaudeStyleProvider(Provider):
    """Base implementation for JSONL transcripts in Claude Code layout."""

    name = ""
    kind = "jsonl"
    supports_append = True

    #: glob patterns relative to ``<root>/projects`` for session files; the
    #: second element of each tuple marks subagent transcripts.
    file_globs: list[tuple[str, bool]] = [("*/*.jsonl", False)]

    def __init__(self, root: Path | str | None = None):
        super().__init__(root)

    def _projects_dir(self) -> Path:
        return self.root / "projects"

    def discover(self) -> list[SessionMeta]:
        projects = self._projects_dir()
        sessions: dict[str, SessionMeta] = {}
        for pattern, is_sub in self.file_globs:
            for path in sorted(projects.glob(pattern)):
                if not path.is_file():
                    continue
                if is_sub:
                    session_id = path.parent.parent.name
                else:
                    session_id = path.stem
                meta = sessions.get(session_id)
                if meta is None:
                    meta = SessionMeta(
                        agent=self.name,
                        session_id=session_id,
                        file_path=str(path),
                        subagent=False,
                    )
                    sessions[session_id] = meta
                meta.sources.append(str(path))
        return list(sessions.values())

    def _session_of(self, path: str) -> SessionMeta:
        p = Path(path)
        if "subagents" in p.parts:
            session = SessionMeta(
                agent=self.name, session_id=p.parent.parent.name, subagent=True
            )
        else:
            session = SessionMeta(agent=self.name, session_id=p.stem)
        return session

    def scan_source(
        self,
        path: str,
        session: SessionMeta,
        state: dict | None,
        emit: Callable[[MessageRecord], None],
    ) -> dict:
        """Index mode: buffer the file so tool results (which arrive in
        later ``user`` records) can be attached back to the assistant
        message that issued the call, then emit each record once."""
        offset = int((state or {}).get("offset", 0))
        line_no = int((state or {}).get("line", 0))
        local = self._session_of(path)
        local.cwd, local.subagent = session.cwd, session.subagent or local.subagent
        local.first_ts = session.first_ts
        pending: list[MessageRecord] = []
        calls: dict[str, ToolCall] = {}
        from .. import model

        with open(path, "rb") as fh:
            fh.seek(offset)
            for raw in fh:
                if not raw.endswith(b"\n"):
                    break  # partially written trailing line; retry next scan
                line_no += 1
                offset += len(raw)
                rec, uses, results = parse_claude_line(
                    raw,
                    agent=self.name,
                    path=path,
                    line_no=line_no,
                    session=local,
                    for_index=True,
                )
                for use_id, call in uses:
                    if use_id:
                        calls[use_id] = call
                for res_id, text in results:
                    call = calls.get(res_id)
                    if call is not None and text:
                        call.output = text[: model.TOOL_OUTPUT_CAP] \
                            if model.TOOL_OUTPUT_CAP else text
                        call.result_preview = call.output[:500]
                if rec is not None:
                    if rec.cwd and not session.cwd:
                        session.cwd = rec.cwd  # enrich session meta while parsing
                    if rec.ts_ms and not session.first_ts:
                        session.first_ts = rec.ts_ms
                    if rec.ts_ms and rec.ts_ms > session.last_ts:
                        session.last_ts = rec.ts_ms
                    pending.append(rec)
        for rec in pending:
            emit(rec)
        return {"offset": offset, "line": line_no}

    def iter_messages(
        self, session: SessionMeta, include_raw: bool = False
    ) -> Iterator[MessageRecord | tuple[MessageRecord, dict]]:
        for source in session.sources or ([session.file_path] if session.file_path else []):
            local = self._session_of(source)
            local.cwd, local.subagent = session.cwd, session.subagent
            local.first_ts = session.first_ts
            try:
                fh = open(source, "rb")
            except OSError:
                continue
            line_no = 0
            calls: dict[str, ToolCall] = {}
            with fh:
                for raw in fh:
                    line_no += 1
                    rec, uses, results = parse_claude_line(
                        raw,
                        agent=self.name,
                        path=source,
                        line_no=line_no,
                        session=local,
                        for_index=False,
                    )
                    for use_id, call in uses:
                        if use_id:
                            calls[use_id] = call
                    for res_id, text in results:
                        call = calls.get(res_id)
                        if call is not None and text:
                            call.result_preview = text[:500]
                    if rec is None:
                        continue
                    if include_raw:
                        try:
                            yield rec, orjson.loads(raw)  # type: ignore[misc]
                        except orjson.JSONDecodeError:
                            yield rec, {}  # type: ignore[misc]
                    else:
                        yield rec

    def read_raw(self, locator: dict[str, Any]) -> dict[str, Any]:
        path = locator["path"]
        want = int(locator.get("line", 0))
        with open(path, "rb") as fh:
            for idx, raw in enumerate(fh, start=1):
                if idx == want:
                    return orjson.loads(raw)
        raise KeyError(f"line {want} not found in {path}")
