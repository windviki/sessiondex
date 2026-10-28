"""MCP server exposing sessiondex as agent tools.

Transport: stdio (``sessiondex mcp``) for on-demand spawning, and
streamable-HTTP mounted at ``/mcp`` when running ``sessiondex serve``.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from . import __version__, reader, search as search_mod
from .config import Config, load_config
from .index import Index
from .scanner import scan


def _server_cls() -> Any:
    """Return the MCP server class across SDK majors (2.x renamed FastMCP)."""
    try:
        from mcp.server.mcpserver import MCPServer  # mcp 2.x

        return MCPServer
    except ModuleNotFoundError:
        from mcp.server.fastmcp import FastMCP  # mcp 1.x

        return FastMCP


def build_mcp(cfg: Config | None = None) -> Any:
    cfg = cfg or load_config()

    mcp: Any = _server_cls()(
        "sessiondex",
        version=__version__,
        instructions=(
            "Search and read local AI coding-agent session histories "
            "(claude/zcode/kimi/dsh/qoder/opencode). Search first, then read."
        ),
    )

    def _idx() -> Index:
        return Index(cfg.index_path)

    def _dates(date_from: Optional[str], date_to: Optional[str]):
        return (
            search_mod.parse_date(date_from) if date_from else None,
            search_mod.parse_date(date_to, end=True) if date_to else None,
        )

    @mcp.tool()
    def search_sessions(
        q: Optional[str] = None,
        agent: Optional[str] = None,
        role: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        tool: Optional[str] = None,
        cwd: Optional[str] = None,
        session_id: Optional[str] = None,
        subagent: Optional[bool] = None,
        limit: int = 20,
    ) -> str:
        """Search indexed messages across local AI-agent session histories.

        q: keywords (CJK supported), multiple terms ANDed; empty = filter only.
        agent: comma-separated names: claude, zcode, kimi, dsh, qoder, opencode.
        role: "user" or "assistant".
        date_from/date_to: "today", "yesterday", "-7d", "YYYY-MM-DD" or ISO datetime.
        tool: comma-separated tool names (e.g. Bash, Read, Edit).
        cwd: session working directory glob/prefix.
        session_id: exact session id from a previous result.
        subagent: include/exclude subagent messages.

        Returns matched messages with snippets plus session metadata; then use
        get_session_messages with session_id (and around_message_id) for context.
        """
        idx = _idx()
        ts_from, ts_to = _dates(date_from, date_to)
        try:
            result = search_mod.search_messages(
                idx, q,
                agents=[a for a in (agent or "").split(",") if a] or None,
                roles=[role] if role else None,
                ts_from=ts_from, ts_to=ts_to,
                tools=[t for t in (tool or "").split(",") if t] or None,
                cwd=cwd, session_id=session_id, subagent=subagent,
                limit=max(1, min(limit, 100)),
            )
        except ValueError as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps(result, ensure_ascii=False)

    @mcp.tool()
    def list_sessions(
        q: Optional[str] = None,
        agent: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        cwd: Optional[str] = None,
        subagent: Optional[bool] = None,
        limit: int = 20,
    ) -> str:
        """List sessions (one row per conversation) matching filters; q matches
        any message full-text."""
        idx = _idx()
        ts_from, ts_to = _dates(date_from, date_to)
        try:
            result = search_mod.search_sessions(
                idx, q,
                agents=[a for a in (agent or "").split(",") if a] or None,
                ts_from=ts_from, ts_to=ts_to, cwd=cwd, subagent=subagent,
                limit=max(1, min(limit, 100)),
            )
        except ValueError as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps(result, ensure_ascii=False)

    @mcp.tool()
    def get_session_messages(
        agent: str,
        session_id: str,
        role: Optional[str] = None,
        around_message_id: Optional[int] = None,
        context: int = 3,
        limit: int = 50,
        include_raw: bool = False,
    ) -> str:
        """Read a session's full messages from the original storage; use
        around_message_id from search results to open a context window."""
        idx = _idx()
        result = reader.session_messages(
            idx, agent, session_id, role=role,
            around_message_id=around_message_id,
            context=max(0, min(context, 50)),
            limit=max(1, min(limit, 500)), include_raw=include_raw,
        )
        if result is None:
            return json.dumps({"error": f"session not found: {agent}/{session_id}"})
        return json.dumps(result, ensure_ascii=False)

    @mcp.tool()
    def list_agents() -> str:
        """List agents present in the index with session/message counts."""
        return json.dumps(search_mod.list_agents(_idx()), ensure_ascii=False)

    @mcp.tool()
    def list_tools(agent: Optional[str] = None) -> str:
        """List tool names (Bash/Read/Edit/...) appearing in the index."""
        return json.dumps(search_mod.list_tools(_idx(), agent), ensure_ascii=False)

    @mcp.tool()
    def refresh_index(full: bool = False) -> str:
        """Incrementally rescan agent storage (or fully rebuild with full=true)."""
        idx = _idx()
        stats = scan(idx, cfg=cfg, full=full)
        return json.dumps(stats.to_dict(), ensure_ascii=False)

    return mcp


def build_mcp_http(cfg: Config | None = None):
    """Return (mcp, starlette_app) for streamable-HTTP mounting, or (None, None)
    when the installed MCP SDK does not support it.

    The sub-app is built with its internal route at "/" so that mounting it
    under "/mcp" exposes exactly that path (mcp 2.x defaults the internal
    route to "/mcp", which would collide into "/mcp/mcp").
    """
    mcp = build_mcp(cfg)
    try:
        http_app = mcp.streamable_http_app(streamable_http_path="/")
    except TypeError:  # older SDK without the path kwarg
        try:
            http_app = mcp.streamable_http_app()
        except Exception:  # noqa: BLE001
            return None, None
    except Exception:  # noqa: BLE001
        return None, None
    return mcp, http_app


def run_stdio() -> None:
    mcp = build_mcp()
    mcp.run()  # stdio transport by default
