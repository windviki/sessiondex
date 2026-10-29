"""Local REST API over the sessiondex index.

All endpoints are read-only against agent storage; POST /api/scan only
updates the sessiondex index. Bind to 127.0.0.1 by default. Set
SESSIONDEX_TOKEN to require ``Authorization: Bearer <token>``.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request, Response

from . import __version__, reader, search as search_mod
from .config import Config, load_config
from .index import Index
from .scanner import scan


def _parse_date_or_422(value: Optional[str], *, end: bool = False) -> Optional[int]:
    if value is None:
        return None
    try:
        return search_mod.parse_date(value, end=end)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


def create_app(cfg: Config | None = None, start_background_scan: bool = False) -> FastAPI:
    cfg = cfg or load_config()

    # MCP over streamable-HTTP: the sub-app's session manager must be run in
    # the parent lifespan (mounted Starlette apps get no lifespan of their own).
    mcp_instance = None
    mcp_http = None
    try:
        from .mcp_server import build_mcp_http

        mcp_instance, mcp_http = build_mcp_http(cfg)
    except Exception:  # noqa: BLE001 - REST keeps working without MCP
        mcp_instance = None
        mcp_http = None

    lifespan = None
    if mcp_instance is not None and hasattr(mcp_instance, "session_manager"):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def lifespan(_app):  # noqa: F811
            async with mcp_instance.session_manager.run():
                yield

    app = FastAPI(
        title="sessiondex",
        version=__version__,
        description="Local full-text search over AI coding-agent session histories.",
        lifespan=lifespan,
    )
    idx = Index(cfg.index_path)
    idx_lock = threading.Lock()
    state = {"last_scan": None, "scanning": False}

    def _do_scan(full: bool) -> dict:
        with idx_lock:
            if state["scanning"]:
                raise HTTPException(409, "a scan is already running")
            state["scanning"] = True
        try:
            stats = scan(idx, cfg=cfg, full=full)
            state["last_scan"] = time.time()
            return stats.to_dict()
        finally:
            state["scanning"] = False

    if start_background_scan and cfg.scan_interval >= 0:

        def _bg_loop() -> None:
            while True:
                try:
                    with idx_lock:
                        busy = state["scanning"]
                    if not busy:
                        _do_scan(full=False)
                except Exception:  # noqa: BLE001 - keep the loop alive
                    pass
                time.sleep(max(cfg.scan_interval, 1) if cfg.scan_interval else 3600)

        threading.Thread(target=_bg_loop, daemon=True, name="sessiondex-scan").start()

    @app.middleware("http")
    async def auth(request: Request, call_next):
        if cfg.token and not request.url.path.startswith("/api/health"):
            header = request.headers.get("authorization", "")
            if header != f"Bearer {cfg.token}":
                return Response(status_code=401, content="unauthorized")
        return await call_next(request)

    @app.get("/api/health")
    def health() -> dict:
        with idx_lock:
            st = idx.stats()
        st["version"] = __version__
        st["scanning"] = state["scanning"]
        return st

    @app.post("/api/scan")
    def trigger_scan(full: bool = False) -> dict:
        return _do_scan(full)

    @app.get("/api/agents")
    def agents() -> list[dict]:
        with idx_lock:
            return search_mod.list_agents(idx)

    @app.get("/api/tools")
    def tools(agent: Optional[str] = None) -> list[dict]:
        with idx_lock:
            return search_mod.list_tools(idx, agent)

    @app.get("/api/search")
    def search(
        q: Optional[str] = None,
        agent: Optional[str] = Query(None, description="comma-separated agent names"),
        role: Optional[str] = Query(None, pattern="^(user|assistant)$"),
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        tool: Optional[str] = Query(None, description="comma-separated tool names"),
        cwd: Optional[str] = None,
        session: Optional[str] = None,
        subagent: Optional[bool] = None,
        order: str = Query("bm25", pattern="^(bm25|ts)$"),
        prefer: Optional[str] = Query(
            None,
            description="rank these roles first without filtering: user, assistant",
        ),
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> dict:
        with idx_lock:
            return search_mod.search_messages(
                idx, q,
                agents=[a for a in (agent or "").split(",") if a] or None,
                roles=[role] if role else None,
                ts_from=_parse_date_or_422(date_from),
                ts_to=_parse_date_or_422(date_to, end=True),
                tools=[t for t in (tool or "").split(",") if t] or None,
                cwd=cwd, session_id=session, subagent=subagent,
                order=order,
                prefer=[p for p in (prefer or "").split(",") if p] or None,
                limit=limit, offset=offset,
            )

    @app.get("/api/sessions")
    def sessions(
        q: Optional[str] = None,
        agent: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        cwd: Optional[str] = None,
        session: Optional[str] = None,
        subagent: Optional[bool] = None,
        prefer: Optional[str] = Query(None, description="user or assistant"),
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> dict:
        with idx_lock:
            return search_mod.search_sessions(
                idx, q,
                agents=[a for a in (agent or "").split(",") if a] or None,
                ts_from=_parse_date_or_422(date_from),
                ts_to=_parse_date_or_422(date_to, end=True),
                cwd=cwd, session_id=session, subagent=subagent,
                prefer=[p for p in (prefer or "").split(",") if p] or None,
                limit=limit, offset=offset,
            )

    @app.get("/api/sessions/{agent}/{session_id}")
    def session_detail(agent: str, session_id: str) -> dict:
        row = idx.get_session(agent, session_id)
        if row is None:
            raise HTTPException(404, "session not found")
        with idx_lock:
            matches = search_mod.search_sessions(
                idx, None, agents=[agent], session_id=session_id, limit=1
            )
        detail = dict(row)
        detail.pop("id", None)
        detail["matched"] = matches["items"][0] if matches["items"] else None
        return detail

    @app.get("/api/sessions/{agent}/{session_id}/messages")
    def session_msgs(
        agent: str,
        session_id: str,
        role: Optional[str] = Query(None, pattern="^(user|assistant)$"),
        around: Optional[int] = Query(None, description="message id to center on"),
        context: int = Query(3, ge=0, le=100),
        limit: int = Query(50, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        raw: bool = False,
    ) -> dict:
        try:
            result = reader.session_messages(
                idx, agent, session_id, role=role, around_message_id=around,
                context=context, limit=limit, offset=offset, include_raw=raw,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        if result is None:
            raise HTTPException(404, "session not found")
        return result

    @app.post("/api/messages/read")
    def read_message(body: dict) -> dict:
        """Read the full original record for a locator from /api/search results."""
        agent = body.get("agent")
        locator = body.get("locator")
        if not agent or not isinstance(locator, dict):
            raise HTTPException(422, "body must include agent and locator")
        try:
            return reader.read_raw(agent, locator)
        except (KeyError, OSError) as exc:
            raise HTTPException(404, f"cannot read original record: {exc}") from None

    try:  # mount the MCP streamable-http endpoint when available
        if mcp_http is not None:
            app.mount("/mcp", mcp_http)
    except Exception:  # noqa: BLE001 - REST works without MCP transport
        pass

    return app


def app_factory() -> FastAPI:
    """Uvicorn factory target (``uvicorn sessiondex.api:app_factory``)."""
    return create_app(load_config(), start_background_scan=True)
