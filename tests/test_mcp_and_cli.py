"""MCP server and CLI tests."""
from __future__ import annotations

import asyncio
import json

import pytest
from typer.testing import CliRunner

from sessiondex import __version__
from sessiondex.cli import app as cli_app
from sessiondex.config import Config
from sessiondex.index import Index
from sessiondex.mcp_server import build_mcp
from sessiondex.provider import build_providers
from sessiondex.scanner import scan

runner = CliRunner()


@pytest.fixture()
def scanned(cfg, built):
    index = Index(cfg.index_path)
    scan(index, cfg=cfg, providers=build_providers(cfg))
    index.close()
    return cfg


def _env_for(cfg: Config):
    env = {
        "SESSIONDEX_INDEX": str(cfg.index_path),
        "SESSIONDEX_CACHE_DIR": str(cfg.cache_dir),
    }
    env.update({f"SESSIONDEX_{a.upper()}_DIR": str(p) for a, p in cfg.providers.items()
                if a not in ("zcode", "opencode")})
    env["SESSIONDEX_ZCODE_DB"] = str(cfg.providers["zcode"])
    env["SESSIONDEX_OPENCODE_DB"] = str(cfg.providers["opencode"])
    return env


# -- MCP ----------------------------------------------------------------------


def test_mcp_tools_listed(scanned):
    mcp = build_mcp(scanned)
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert names == {
        "search_sessions", "list_sessions", "get_session_messages",
        "list_agents", "list_tools", "refresh_index",
    }


def _call(mcp, name, args) -> str:
    """Invoke a tool and return its text output across mcp SDK majors."""
    res = asyncio.run(mcp.call_tool(name, args))
    if isinstance(res, tuple):  # mcp 1.x: (content_blocks, structured)
        return res[0][0].text
    return res.content[0].text  # mcp 2.x: CallToolResult


def test_mcp_search_and_read(scanned):
    mcp = build_mcp(scanned)
    result = json.loads(_call(mcp, "search_sessions",
                              {"q": "ch011", "agent": "zcode"}))
    assert result["total"] == 1
    item = result["items"][0]
    sid = item["session"]["session_id"]

    detail = json.loads(_call(mcp, "get_session_messages",
                              {"agent": "zcode", "session_id": sid}))
    assert detail["total"] >= 2
    assert any("构建失败" in (m["text"] or "") for m in detail["messages"])

    agents = json.loads(_call(mcp, "list_agents", {}))
    assert {a["agent"] for a in agents} == {
        "claude", "zcode", "kimi", "dsh", "qoder", "opencode"
    }

    tools = json.loads(_call(mcp, "list_tools", {"agent": "qoder"}))
    assert tools[0]["tool"] == "EnterWorktree"

    sessions = json.loads(_call(mcp, "list_sessions", {"q": "优惠券"}))
    assert sessions["total"] == 1

    refreshed = json.loads(_call(mcp, "refresh_index", {}))
    assert "agents" in refreshed


def test_mcp_today_filter(scanned):
    mcp = build_mcp(scanned)
    result = json.loads(_call(mcp, "search_sessions", {"date_from": "today"}))
    assert result["total"] == 0  # fixtures are dated 2026-10-20..22


# -- CLI ----------------------------------------------------------------------


def test_cli_scan_and_search(scanned, built, monkeypatch):
    for k, v in _env_for(scanned).items():
        monkeypatch.setenv(k, v)
    r = runner.invoke(cli_app, ["scan"])
    assert r.exit_code == 0, r.output
    assert "messages indexed" in r.output

    r = runner.invoke(cli_app, ["search", "ch011", "--agent", "zcode", "--json"])
    assert r.exit_code == 0, r.output
    body = json.loads(r.stdout)
    assert body["total"] == 1

    r = runner.invoke(cli_app, ["search", "优惠券", "--role", "user"])
    assert r.exit_code == 0 and "qoder" in r.output

    r = runner.invoke(cli_app, ["sessions", "启动", "--json"])
    assert r.exit_code == 0
    assert json.loads(r.stdout)["total"] == 1

    sid = built["claude"]["session_id"]
    r = runner.invoke(cli_app, ["show", "claude", sid, "--json"])
    assert r.exit_code == 0, r.output
    body = json.loads(r.stdout)
    assert body["total"] == 7

    r = runner.invoke(cli_app, ["status", "--json"])
    assert r.exit_code == 0
    assert json.loads(r.stdout)["sessions"] >= 7

    r = runner.invoke(cli_app, ["agents"])
    assert r.exit_code == 0 and "opencode" in r.output

    r = runner.invoke(cli_app, ["tools"])
    assert r.exit_code == 0 and "Bash" in r.output

    r = runner.invoke(cli_app, ["version"])
    assert __version__ in r.output
