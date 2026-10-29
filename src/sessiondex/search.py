"""Query layer: combined filters + FTS5 full-text matching over the index."""
from __future__ import annotations

import datetime as _dt
import html
import re
from typing import Any

from . import tokenize
from .index import Index

_CJK_CHAR = re.compile(r"[\u2e80-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]")


def has_cjk(s: str) -> bool:
    return bool(_CJK_CHAR.search(s))


# -- date parsing -----------------------------------------------------------


def parse_date(value: str | None, *, end: bool = False) -> int | None:
    """Resolve a date filter to epoch ms. Supports today / yesterday /
    -Nd / -Nw / YYYY-MM-DD / ISO datetime. Naive datetimes use local time."""
    if not value:
        return None
    v = value.strip().lower()
    now = _dt.datetime.now().astimezone()
    day = None
    if v == "today":
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif v == "yesterday":
        day = (now - _dt.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    elif re.fullmatch(r"-\d+[dw]", v):
        n, unit = int(v[1:-1]), v[-1]
        delta = _dt.timedelta(days=n if unit == "d" else n * 7)
        return int((now - delta).timestamp() * 1000)
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        day = _dt.datetime.strptime(v, "%Y-%m-%d").astimezone()
    else:
        s = value.strip()
        if s.endswith(("Z", "z")):
            s = s[:-1] + "+00:00"
        try:
            dt = _dt.datetime.fromisoformat(s)
        except ValueError:
            raise ValueError(f"invalid date filter: {value!r}") from None
        if dt.tzinfo is None:
            dt = dt.astimezone()
        return int(dt.timestamp() * 1000)
    if end:
        day = day + _dt.timedelta(days=1, milliseconds=-1)
    return int(day.timestamp() * 1000)


# -- filter building ----------------------------------------------------------


class Filters:
    """Shared SQL filter builder for message-level queries."""

    def __init__(
        self,
        agents: list[str] | None = None,
        roles: list[str] | None = None,
        ts_from: int | None = None,
        ts_to: int | None = None,
        tools: list[str] | None = None,
        cwd: str | None = None,
        session_id: str | None = None,
        subagent: bool | None = None,
    ):
        self.agents = [a.strip().lower() for a in agents or [] if a.strip()]
        self.roles = [r.strip().lower() for r in roles or [] if r.strip()]
        self.ts_from = ts_from
        self.ts_to = ts_to
        self.tools = [t.strip() for t in tools or [] if t.strip()]
        self.cwd = (cwd or "").strip() or None
        self.session_id = (session_id or "").strip() or None
        self.subagent = subagent

    def sql(self, msg_alias: str = "m", sess_alias: str = "s") -> tuple[str, list]:
        where: list[str] = []
        params: list = []
        if self.agents:
            where.append(f"{msg_alias}.agent IN ({','.join('?' * len(self.agents))})")
            params.extend(self.agents)
        if self.roles:
            where.append(f"{msg_alias}.role IN ({','.join('?' * len(self.roles))})")
            params.extend(self.roles)
        if self.ts_from is not None:
            where.append(f"{msg_alias}.ts_ms >= ?")
            params.append(self.ts_from)
        if self.ts_to is not None:
            where.append(f"{msg_alias}.ts_ms <= ?")
            params.append(self.ts_to)
        if self.tools:
            marks = ",".join("?" * len(self.tools))
            where.append(
                f"EXISTS (SELECT 1 FROM json_each({msg_alias}.tools) je"
                f" WHERE lower(je.value->>'name') IN ({marks}))"
            )
            params.extend(t.lower() for t in self.tools)
        if self.cwd:
            glob = self.cwd if any(c in self.cwd for c in "*?[") else self.cwd + "*"
            where.append(f"{sess_alias}.cwd GLOB ?")
            params.append(glob)
        if self.session_id:
            where.append(f"{sess_alias}.session_id = ?")
            params.append(self.session_id)
        if self.subagent is not None:
            where.append(f"{msg_alias}.subagent = ?")
            params.append(int(self.subagent))
        return (" AND ".join(where), params) if where else ("1=1", params)


# -- snippets -----------------------------------------------------------------


def make_snippet(text: str, query: str | None, width: int = 160) -> str:
    """HTML snippet of *text* around the first query match, ``<mark>``-ed."""
    if not text:
        return ""
    escaped = html.escape(text)
    if query:
        terms = tokenize.tokenize(query)
        patterns = []
        for term in terms:
            if has_cjk(term):
                patterns.append(re.escape(term))
            else:
                patterns.append(rf"\b{re.escape(term)}")
        if patterns:
            rx = re.compile("|".join(patterns), re.IGNORECASE)
            m = rx.search(escaped)
            if m:
                start = max(0, m.start() - width // 2)
                end = min(len(escaped), start + width)
                segment = escaped[start:end]
                segment = rx.sub(lambda mm: f"<mark>{mm.group(0)}</mark>", segment)
                prefix = "…" if start > 0 else ""
                suffix = "…" if end < len(escaped) else ""
                return f"{prefix}{segment}{suffix}"
    return escaped[:width] + ("…" if len(escaped) > width else "")


def _iso(ms: int | None) -> str | None:
    if not ms:
        return None
    return (
        _dt.datetime.fromtimestamp(ms / 1000).astimezone().isoformat(timespec="seconds")
    )


def _msg_row(row: Any, query: str | None) -> dict:
    import orjson

    d = dict(row)
    session = {
        "session_id": d.pop("session_id", None),
        "title": d.pop("title", None),
        "cwd": d.pop("session_cwd", None),
        "subagent": bool(d.pop("session_sub", 0)),
    }
    text = d.get("text") or ""
    reasoning = d.get("reasoning") or ""
    tools = orjson.loads(d.get("tools") or "[]")
    locator = orjson.loads(d.get("locator") or "{}")

    snippet = make_snippet(text, query)
    if query and "<mark>" not in snippet:
        r_snip = make_snippet(reasoning, query)
        if r_snip:
            snippet = "[thinking] " + r_snip
    if query and "<mark>" not in snippet:
        previews = " ".join(
            f"{t.get('name', '')} {t.get('input_summary', '')}"
            f" {t.get('output_preview', '')}"
            for t in tools
        )
        t_snip = make_snippet(previews, query)
        if t_snip and "<mark>" in t_snip:
            snippet = "[tools] " + t_snip
    if query and "<mark>" not in snippet:
        # the hit is beyond the stored excerpts — regenerate from source
        deep = _deep_snippet(d.get("agent"), locator, query)
        if deep:
            snippet = deep
    return {
        "id": d["id"],
        "agent": d["agent"],
        "role": d["role"],
        "ts": _iso(d.get("ts_ms")),
        "ts_ms": d.get("ts_ms"),
        "subagent": bool(d.get("subagent")),
        "tools": tools,
        "locator": locator,
        "text": text,
        "reasoning": reasoning,
        "snippet": snippet,
        "score": round(d["score"], 3) if d.get("score") is not None else None,
        "session": session,
    }


def _deep_snippet(agent: str | None, locator: dict, query: str) -> str:
    """Snippet from the original storage for hits outside stored excerpts."""
    if not agent or not locator:
        return ""
    from . import reader

    try:
        context = reader.context_text(agent, locator)
    except Exception:  # noqa: BLE001 - snippet is best-effort
        return ""
    snip = make_snippet(context, query)
    return f"[source] {snip}" if "<mark>" in snip else ""


# -- public search API ----------------------------------------------------------


def search_messages(
    idx: Index,
    q: str | None = None,
    *,
    agents: list[str] | None = None,
    roles: list[str] | None = None,
    ts_from: int | None = None,
    ts_to: int | None = None,
    tools: list[str] | None = None,
    cwd: str | None = None,
    session_id: str | None = None,
    subagent: bool | None = None,
    order: str = "bm25",
    prefer: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Message-level search.

    ``order``: ``bm25`` (relevance, default with a query) or ``ts`` (newest
    first). ``prefer`` ("user"/"assistant") does NOT filter — it moves the
    preferred roles to the top of the result list ("search everything, but
    rank these first").
    """
    f = Filters(agents, roles, ts_from, ts_to, tools, cwd, session_id, subagent)
    where, params = f.sql()
    match = tokenize.fts_query(q) if q and q.strip() else None
    join = (
        "FROM messages_fts JOIN messages m ON m.id = messages_fts.rowid"
        if match
        else "FROM messages m"
    )
    join += " JOIN sessions s ON s.id = m.session_rowid"
    cond = ("messages_fts MATCH ? AND " + where) if match else where
    mparams = ([match] if match else []) + params
    # column weights: text > tool calls > reasoning > tool outputs
    rank = (
        "bm25(messages_fts, 1.0, 0.5, 0.8, 0.2)"
        if match
        else "m.ts_ms"
    )
    prefer_clause = ""
    if prefer:
        wanted = [p.lower() for p in prefer if p.lower() in ("user", "assistant")]
        if wanted:
            cases = " ".join(f"WHEN m.role='{r}' THEN 0" for r in wanted)
            prefer_clause = f"CASE {cases} ELSE 1 END ASC, "
    order_sql = prefer_clause + (
        f"{rank} ASC" if (match and order != "ts") else "m.ts_ms DESC"
    )
    total = idx.conn.execute(
        f"SELECT COUNT(*) {join} WHERE {cond}", mparams
    ).fetchone()[0]
    rows = idx.conn.execute(
        f"""
        SELECT m.*, s.session_id, s.title, s.cwd AS session_cwd,
               s.subagent AS session_sub
               {f', {rank} AS score' if match else ''}
        {join} WHERE {cond}
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?
        """,
        mparams + [int(limit), int(offset)],
    ).fetchall()
    return {"total": int(total), "items": [_msg_row(r, q) for r in rows]}


def search_sessions(
    idx: Index,
    q: str | None = None,
    *,
    agents: list[str] | None = None,
    ts_from: int | None = None,
    ts_to: int | None = None,
    cwd: str | None = None,
    session_id: str | None = None,
    subagent: bool | None = None,
    prefer: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Session-level search: sessions are returned when at least one message
    matches every filter (including full-text *q*).

    Implemented on top of :func:`search_messages` (FTS5's bm25 cannot be
    aggregated in one statement); up to 2000 matching messages are grouped.
    ``prefer`` is forwarded, so sessions matching the preferred roles rank
    first.
    """
    msg_result = search_messages(
        idx, q,
        agents=agents, ts_from=ts_from, ts_to=ts_to, cwd=cwd,
        session_id=session_id, subagent=subagent,
        order="bm25" if q else "ts", prefer=prefer, limit=2000,
    )
    groups: dict[tuple[str, str], dict] = {}
    for item in msg_result["items"]:
        sess = item["session"]
        key = (item["agent"], sess["session_id"])
        g = groups.get(key)
        if g is None:
            g = {
                "agent": item["agent"],
                "session_id": sess["session_id"],
                "title": sess.get("title") or "",
                "cwd": sess.get("cwd") or "",
                "subagent": sess.get("subagent", False),
                "matched": 0,
                "best": item.get("score"),
                "latest": item.get("ts_ms") or 0,
            }
            groups[key] = g
        g["matched"] += 1
        if item.get("score") is not None:
            g["best"] = min(g["best"], item["score"])
        g["latest"] = max(g["latest"], item.get("ts_ms") or 0)
    ranked = sorted(
        groups.values(),
        key=lambda g: g["best"] if g["best"] is not None else -g["latest"],
    )
    total = len(ranked)
    page = ranked[int(offset) : int(offset) + int(limit)]
    items = []
    for g in page:
        row = idx.get_session(g["agent"], g["session_id"]) or {}
        items.append(
            {
                "agent": g["agent"],
                "session_id": g["session_id"],
                "title": row.get("title") or g["title"],
                "cwd": row.get("cwd") or g["cwd"],
                "subagent": bool(row.get("subagent", g["subagent"])),
                "first_ts": _iso(row.get("first_ts") or 0),
                "last_ts": _iso(row.get("last_ts") or g["latest"]),
                "msg_count": row.get("msg_count", g["matched"]),
                "matched_messages": g["matched"],
            }
        )
    return {"total": total, "items": items}


def list_agents(idx: Index) -> list[dict]:
    rows = idx.conn.execute(
        """
        SELECT agent, COUNT(*) AS sessions, SUM(msg_count) AS messages,
               MAX(last_ts) AS last_ts
        FROM sessions GROUP BY agent ORDER BY agent
        """
    ).fetchall()
    return [
        {
            "agent": r["agent"],
            "sessions": r["sessions"],
            "messages": r["messages"] or 0,
            "last_activity": _iso(r["last_ts"]),
        }
        for r in rows
    ]


def list_tools(idx: Index, agent: str | None = None) -> list[dict]:
    where, params = ("m.agent = ?", [agent]) if agent else ("1=1", [])
    rows = idx.conn.execute(
        f"""
        SELECT je.value->>'name' AS name, COUNT(*) AS n
        FROM messages m, json_each(m.tools) je
        WHERE {where} AND je.value->>'name' IS NOT NULL
        GROUP BY name ORDER BY n DESC LIMIT 500
        """,
        params,
    ).fetchall()
    return [{"tool": r["name"], "count": r["n"]} for r in rows]
