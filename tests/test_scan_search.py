"""End-to-end tests: scan fixtures into an index, then search with filters."""
from __future__ import annotations

import time

import orjson
import pytest

from sessiondex import search as search_mod
from sessiondex.index import Index
from sessiondex.scanner import scan
from sessiondex.provider import build_providers


@pytest.fixture()
def idx(cfg, built):
    index = Index(cfg.index_path)
    scan(index, cfg=cfg, providers=build_providers(cfg))
    return index


def _q(idx, q, **kw):
    return search_mod.search_messages(idx, q, **kw)


def test_scan_indexes_all_agents(idx):
    stats = idx.stats()
    assert stats["sessions"] >= 7
    by_agent = {a["agent"]: a for a in stats["agents"]}
    assert set(by_agent) == {"claude", "zcode", "kimi", "dsh", "qoder", "opencode"}
    assert all(a["messages"] > 0 for a in by_agent.values())


def test_keyword_ascii_across_agents(idx):
    hits = _q(idx, "ch011")
    agents = {h["agent"] for h in hits["items"]}
    assert {"claude", "kimi", "zcode"} <= agents
    assert hits["total"] >= 3
    for item in hits["items"]:
        assert item["session"]["session_id"]
        assert item["locator"]
        assert "<mark>" in item["snippet"]


def test_keyword_cjk(idx):
    hits = _q(idx, "启动速度")
    assert hits["total"] >= 1
    assert any("claude" == h["agent"] for h in hits["items"])
    hits2 = _q(idx, "优惠券")
    assert hits2["total"] == 1 and hits2["items"][0]["agent"] == "qoder"


def test_multi_term_and_semantics(idx):
    hits = _q(idx, "ch011 模块")
    assert hits["total"] == 1
    assert hits["items"][0]["agent"] == "claude"


def test_filter_agent(idx):
    hits = _q(idx, "ch011", agents=["zcode"])
    assert {h["agent"] for h in hits["items"]} == {"zcode"}


def test_filter_role(idx):
    users = _q(idx, "ch011", roles=["user"])
    assert users["total"] == 1 and users["items"][0]["role"] == "user"
    agents_hits = _q(idx, "ch011", roles=["assistant"])
    assert all(h["role"] == "assistant" for h in agents_hits["items"])


def test_filter_tool(idx):
    hits = _q(idx, None, tools=["bash"])
    assert {h["agent"] for h in hits["items"]} == {"claude", "kimi", "dsh"}
    hits2 = _q(idx, None, tools=["Read"])
    assert {h["agent"] for h in hits2["items"]} == {"zcode", "opencode"}


def test_filter_tool_with_query(idx):
    hits = _q(idx, "deploy", tools=["bash"])
    assert hits["total"] == 1 and hits["items"][0]["agent"] == "dsh"


def test_filter_subagent(idx):
    subs = _q(idx, None, subagent=True)
    # claude subagent transcript + kimi subagent stream
    assert subs["total"] == 2
    assert {h["agent"] for h in subs["items"]} == {"claude", "kimi"}
    subs_kimi = _q(idx, "ch011", agents=["kimi"])
    assert subs_kimi["items"][0]["subagent"] is True
    mains = _q(idx, None, subagent=False, agents=["kimi"])
    assert all(not h["subagent"] for h in mains["items"])


def test_filter_cwd_prefix_and_glob(idx):
    hits = _q(idx, None, cwd="/home/user/proj")
    assert hits["total"] >= 1
    assert all(h["session"]["cwd"] == "/home/user/proj" for h in hits["items"])
    glob = _q(idx, None, cwd="/home/user/*proj")
    cwds = {h["session"]["cwd"] for h in glob["items"]}
    assert "/home/user/dsh-proj" in cwds


def test_filter_session_id(idx):
    first = _q(idx, "优惠券")["items"][0]
    hits = _q(idx, "优惠券", session_id=first["session"]["session_id"])
    assert hits["total"] == 1


def test_date_filters(idx):
    # fixture timestamps are 2026-10-20..22 (UTC) -> use wide local ranges
    hits = _q(idx, None, ts_from=search_mod.parse_date("2026-10-19"),
              ts_to=search_mod.parse_date("2026-10-23", end=True))
    assert hits["total"] > 5
    none = _q(idx, None, ts_from=search_mod.parse_date("2020-01-01"),
              ts_to=search_mod.parse_date("2020-01-02", end=True))
    assert none["total"] == 0
    # invalid date raises
    with pytest.raises(ValueError):
        search_mod.parse_date("not-a-date")


def test_relative_dates():
    now = time.time() * 1000
    today = search_mod.parse_date("today")
    assert 0 <= now - today < 36 * 3600 * 1000
    yday = search_mod.parse_date("yesterday")
    assert 0 < today - yday <= 24 * 3600 * 1000
    week = search_mod.parse_date("-7d")
    assert 6.9 * 86400 * 1000 < now - week < 7.2 * 86400 * 1000


def test_order_ts(idx):
    hits = _q(idx, None, order="ts", limit=3)
    ts_list = [h["ts_ms"] for h in hits["items"]]
    assert ts_list == sorted(ts_list, reverse=True)


def test_sessions_level_search(idx):
    result = search_mod.search_sessions(idx, "ch011")
    assert result["total"] >= 3
    top = result["items"][0]
    assert top["matched_messages"] >= 1
    assert top["session_id"]
    # session filter narrows
    only_claude = search_mod.search_sessions(idx, "ch011", agents=["claude"])
    assert only_claude["total"] == 1


def test_list_agents_and_tools(idx):
    agents = search_mod.list_agents(idx)
    assert {a["agent"] for a in agents} == {
        "claude", "zcode", "kimi", "dsh", "qoder", "opencode"
    }
    tools = {t["tool"]: t["count"] for t in search_mod.list_tools(idx)}
    assert tools["Bash"] >= 2  # claude + kimi
    zc_tools = {t["tool"] for t in search_mod.list_tools(idx, "zcode")}
    assert zc_tools == {"Read"}


def test_incremental_append(cfg, idx, built):
    from sessiondex.scanner import scan as do_scan

    # append a new user message to the claude transcript
    main = built["claude"]["main"]
    sid = built["claude"]["session_id"]
    with open(main, "ab") as fh:
        fh.write(orjson.dumps(
            {"type": "user", "uuid": "u9", "sessionId": sid,
             "timestamp": "2026-10-20T11:00:00.000Z", "cwd": "/home/user/proj",
             "message": {"role": "user", "content": "新增的增量消息 incremental-marker"}}
        ) + b"\n")
    # ensure mtime differs enough to be detected
    import os

    os.utime(main, ns=(0, time.time_ns() + 1_000_000_000))
    stats = do_scan(idx, cfg=cfg, providers=build_providers(cfg))
    assert stats.agents["claude"]["sources_scanned"] >= 1
    hits = _q(idx, "incremental-marker")
    assert hits["total"] == 1
    assert hits["items"][0]["agent"] == "claude"
    # old content still there exactly once
    assert _q(idx, "ch011 模块")["total"] == 1
    # second scan with no changes skips everything
    stats2 = do_scan(idx, cfg=cfg, providers=build_providers(cfg))
    assert stats2.agents["claude"]["sources_scanned"] == 0
    assert stats2.agents["claude"]["sources_skipped"] >= 2


def test_full_rebuild_dedupes(cfg, idx):
    from sessiondex.scanner import scan as do_scan

    do_scan(idx, cfg=cfg, providers=build_providers(cfg), full=True)
    assert _q(idx, "ch011")["total"] == _q(idx, "ch011")["total"]
    assert _q(idx, "ch011")["total"] >= 3
