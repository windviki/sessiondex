"""Kimi Code session provider (``~/.kimi-code``).

Layout::

    sessions/wd_<name>_<hash>/session_<uuid>/
        state.json                     # session metadata (id, cwd, timestamps)
        agents/main/wire.jsonl         # main conversation event stream
        agents/agent-<N>/wire.jsonl    # subagent streams
        logs/ ...

``wire.jsonl`` is an event stream. Records of interest:

* ``agent.message.appended`` -- ``message.message`` holds ``{role, content}``
  with blocks ``{"type": "text"|"think", ...}`` (note: Kimi uses ``think``).
* ``context.append_loop_event`` wrapping ``event.type == "tool.call"`` with
  the tool name in ``event.name`` (and ``tool.result`` for outputs).

Tool calls are attached to the most recent assistant message of the same
agent stream, mirroring how they appear in the conversation.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterator

import orjson

from ..model import MessageRecord, SessionMeta, ToolCall
from .base import Provider, parse_iso_ms, summarize_input


def _iter_wire_lines(path: str) -> Iterator[tuple[int, bytes]]:
    try:
        fh = open(path, "rb")
    except OSError:
        return
    with fh:
        for idx, raw in enumerate(fh, start=1):
            yield idx, raw


def _parse_events(
    lines: Iterator[tuple[int, bytes]],
    *,
    agent: str,
    path: str,
    session: SessionMeta,
    subagent: bool,
    for_index: bool,
    include_raw: bool,
) -> Iterator[MessageRecord | tuple[MessageRecord, dict]]:
    """Buffer one wire.jsonl stream so tool calls can attach to messages."""
    pending: list[MessageRecord] = []
    last_assistant: dict[str, MessageRecord] = {}
    last_call: dict[str, ToolCall] = {}
    raw_by_line: dict[int, dict] = {}

    def flush() -> Iterator[MessageRecord | tuple[MessageRecord, dict]]:
        for rec in pending:
            if include_raw:
                yield rec, raw_by_line.get(rec.locator.get("line", 0), {})
            else:
                yield rec
        pending.clear()

    for line_no, raw in lines:
        try:
            rec = orjson.loads(raw)
        except orjson.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        rtype = rec.get("type")
        ts = int(rec.get("time") or 0)
        agent_id = str(rec.get("agentId") or "main")

        if rtype == "agent.message.appended":
            envelope = rec.get("message") or {}
            msg = envelope.get("message") or {}
            role = msg.get("role")
            if role not in ("user", "assistant"):
                continue
            texts: list[str] = []
            thinks: list[str] = []
            content = msg.get("content")
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text" and isinstance(block.get("text"), str):
                        texts.append(block["text"])
                    elif block.get("type") == "think" and isinstance(block.get("think"), str):
                        thinks.append(block["think"])
            ts_ms = parse_iso_ms(envelope.get("createdAt")) or ts
            record = MessageRecord(
                agent=agent,
                session_id=session.session_id,
                ts_ms=ts_ms,
                role=role,
                text="\n".join(t for t in texts if t),
                reasoning="\n".join(t for t in thinks if t),
                locator={"kind": "jsonl", "path": path, "line": line_no},
                subagent=subagent,
                cwd=session.cwd,
            )
            pending.append(record)
            if include_raw:
                raw_by_line[line_no] = rec
            if role == "assistant":
                last_assistant[agent_id] = record

        elif rtype == "context.append_loop_event":
            event = rec.get("event") or {}
            etype = event.get("type")
            if etype == "tool.call":
                call = ToolCall(
                    name=str(event.get("name") or ""),
                    input_summary=summarize_input(event.get("args")),
                )
                target = last_assistant.get(agent_id)
                if target is not None:
                    target.tools.append(call)
                last_call[str(event.get("toolCallId") or "")] = call
            elif etype == "tool.result":
                from .. import model

                call = last_call.get(str(event.get("toolCallId") or ""))
                result = event.get("result") or {}
                output = str(result.get("output") or "")
                if model.TOOL_OUTPUT_CAP:
                    output = output[: model.TOOL_OUTPUT_CAP]
                if call is not None:
                    call.output = output
                    call.result_preview = output[:500]
                else:
                    target = last_assistant.get(agent_id)
                    if target is not None and output:
                        target.tool_results.append({"preview": output[:500]})
    yield from flush()


class KimiProvider(Provider):
    name = "kimi"
    kind = "jsonl"
    supports_append = True

    def discover(self) -> list[SessionMeta]:
        sessions_root = self.root / "sessions"
        if not sessions_root.is_dir():
            return []
        out: list[SessionMeta] = []
        for state_file in sorted(sessions_root.glob("*/*/state.json")):
            try:
                data = orjson.loads(state_file.read_bytes())
            except (OSError, orjson.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            session_dir = state_file.parent
            sources: list[str] = []
            agents = data.get("agents") or {}
            for agent_name in agents if isinstance(agents, dict) else []:
                wire = session_dir / "agents" / str(agent_name) / "wire.jsonl"
                if wire.is_file():
                    sources.append(str(wire))
            if not sources:
                for wire in sorted(session_dir.glob("agents/*/wire.jsonl")):
                    sources.append(str(wire))
            # main stream first
            sources.sort(key=lambda p: 0 if Path(p).parent.name == "main" else 1)
            out.append(
                SessionMeta(
                    agent=self.name,
                    session_id=str(data.get("id") or session_dir.name),
                    cwd=str(data.get("cwd") or ""),
                    file_path=str(session_dir),
                    first_ts=parse_iso_ms(data.get("createdAt")),
                    last_ts=parse_iso_ms(data.get("updatedAt")),
                    sources=sources,
                )
            )
        return out

    def _subagent_flag(self, path: str) -> bool:
        return Path(path).parent.name != "main"

    def scan_source(
        self,
        path: str,
        session: SessionMeta,
        state: dict | None,
        emit: Callable[[MessageRecord], None],
    ) -> dict:
        offset = int((state or {}).get("offset", 0))
        line_no = int((state or {}).get("line", 0))
        subagent = self._subagent_flag(path)

        def line_iter():
            nonlocal offset, line_no
            with open(path, "rb") as fh:
                fh.seek(offset)
                for raw in fh:
                    if not raw.endswith(b"\n"):
                        break
                    line_no += 1
                    offset += len(raw)
                    yield line_no, raw

        for rec in _parse_events(
            line_iter(),
            agent=self.name,
            path=path,
            session=session,
            subagent=subagent,
            for_index=True,
            include_raw=False,
        ):
            if rec.text or rec.reasoning or rec.tools:
                emit(rec)
        return {"offset": offset, "line": line_no}

    def iter_messages(
        self, session: SessionMeta, include_raw: bool = False
    ) -> Iterator[MessageRecord | tuple[MessageRecord, dict]]:
        for source in session.sources:
            yield from _parse_events(
                _iter_wire_lines(source),
                agent=self.name,
                path=source,
                session=session,
                subagent=self._subagent_flag(source),
                for_index=False,
                include_raw=include_raw,
            )

    def read_raw(self, locator: dict[str, Any]) -> dict[str, Any]:
        for idx, raw in _iter_wire_lines(locator["path"]):
            if idx == int(locator.get("line", 0)):
                return orjson.loads(raw)
        raise KeyError(f"line {locator.get('line')} not found in {locator['path']}")
