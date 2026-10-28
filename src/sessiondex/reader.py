"""On-demand reader: fetch full message content from the original storage.

The index only keeps truncated text; anything richer (full message, raw
record, surrounding conversation context) is read from the agent's own
files/databases via locators.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from .index import Index
from .model import MessageRecord, SessionMeta
from .provider import provider_for


def _meta_from_row(row: dict) -> SessionMeta:
    import orjson

    return SessionMeta(
        agent=row["agent"],
        session_id=row["session_id"],
        title=row.get("title") or "",
        cwd=row.get("cwd") or "",
        file_path=row.get("file_path") or "",
        subagent=bool(row.get("subagent")),
        sources=orjson.loads(row.get("sources") or "[]"),
    )


def get_session(idx: Index, agent: str, session_id: str) -> dict | None:
    return idx.get_session(agent, session_id)


def session_messages(
    idx: Index,
    agent: str,
    session_id: str,
    *,
    role: str | None = None,
    around_message_id: int | None = None,
    context: int = 0,
    limit: int | None = None,
    offset: int = 0,
    include_raw: bool = False,
) -> dict[str, Any] | None:
    """Return normalized messages of a session, optionally a window around a
    matched message id, reading from the original storage."""
    row = idx.get_session(agent, session_id)
    if row is None:
        return None
    provider = provider_for(agent)
    meta = _meta_from_row(row)

    target_key: str | None = None
    if around_message_id is not None:
        mrow = idx.get_message(int(around_message_id))
        if mrow is None or mrow["agent"] != agent or mrow["session_rowid"] != row["id"]:
            raise ValueError("around_message_id does not belong to this session")
        target_key = mrow["msg_key"]

    records: list[tuple[MessageRecord, dict | None]] = []
    for item in provider.iter_messages(meta, include_raw=True):
        rec, raw = item  # type: ignore[misc]
        if role and rec.role != role:
            continue
        records.append((rec, raw))

    out_records = records
    if target_key is not None:
        center = next(
            (i for i, (rec, _) in enumerate(records)
             if _rec_key(rec, agent) == target_key),
            None,
        )
        if center is not None:
            lo = max(0, center - context)
            hi = center + context + 1
            out_records = records[lo:hi]
            offset = 0
            limit = None
    if limit is not None:
        out_records = out_records[offset : offset + limit]
    else:
        out_records = out_records[offset:]

    messages = []
    for rec, raw in out_records:
        item = rec.to_dict()
        item["ts"] = (
            _dt.datetime.fromtimestamp(rec.ts_ms / 1000).astimezone().isoformat(
                timespec="seconds"
            )
            if rec.ts_ms
            else None
        )
        if include_raw:
            item["raw"] = raw
        messages.append(item)
    return {
        "agent": agent,
        "session_id": session_id,
        "session": {
            "title": row.get("title") or "",
            "cwd": row.get("cwd") or "",
            "subagent": bool(row.get("subagent")),
            "msg_count": row.get("msg_count"),
        },
        "total": len(records),
        "returned": len(messages),
        "messages": messages,
    }


def _rec_key(rec: MessageRecord, agent: str) -> str:
    loc = rec.locator
    if loc.get("kind") == "sqlite":
        return f"{agent}:{loc.get('message_id')}"
    return f"{loc.get('path')}:{loc.get('line')}"


def read_raw(agent: str, locator: dict[str, Any]) -> dict[str, Any]:
    """Read the complete original record addressed by a search-result locator."""
    provider = provider_for(agent)
    return provider.read_raw(locator)


_SKIP_KEYS = {"signature", "uuid", "parentUuid", "sessionId", "tool_use_id"}


def _walk_strings(obj: Any, out: list[str], budget: int) -> None:
    if budget[0] <= 0:
        return
    if isinstance(obj, str):
        if len(obj) > 1 and not obj.startswith(("data:", "base64:")):
            out.append(obj)
            budget[0] -= len(obj)
    elif isinstance(obj, dict):
        for key, value in obj.items():
            if key in _SKIP_KEYS:
                continue
            _walk_strings(value, out, budget)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _walk_strings(item, out, budget)


def context_text(
    agent: str, locator: dict[str, Any], *, max_chars: int = 200_000
) -> str:
    """Best-effort full searchable text of the original record addressed by
    *locator* (the hit message plus, for JSONL agents, the records right
    after it — where tool outputs live). Used to regenerate snippets for
    hits that fall outside the stored excerpts."""
    provider = provider_for(agent)
    kind = locator.get("kind")
    out: list[str] = []
    budget = [max_chars]
    if kind in ("jsonl", "jsonl_zst"):
        import orjson

        path = locator["path"]
        start = int(locator.get("line", 0))
        if kind == "jsonl":
            with open(path, "rb") as fh:
                for idx, raw in enumerate(fh, start=1):
                    if idx < start:
                        continue
                    if idx > start + 80 or budget[0] <= 0:
                        break
                    try:
                        _walk_strings(orjson.loads(raw), out, budget)
                    except orjson.JSONDecodeError:
                        continue
        else:
            from .provider.dsh import _decompress_lines

            lines = _decompress_lines(path)
            for idx in range(start - 1, min(start + 80, len(lines))):
                if budget[0] <= 0:
                    break
                try:
                    _walk_strings(orjson.loads(lines[idx]), out, budget)
                except orjson.JSONDecodeError:
                    continue
    else:
        _walk_strings(read_raw(agent, locator), out, budget)
    return "\n".join(out)
