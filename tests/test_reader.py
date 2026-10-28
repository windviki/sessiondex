"""Reader tests: full-content reads from original storage via locators."""
from __future__ import annotations

import pytest

from sessiondex import reader
from sessiondex.index import Index
from sessiondex.provider import build_providers
from sessiondex.scanner import scan
from sessiondex import search as search_mod


@pytest.fixture()
def idx(cfg, built):
    index = Index(cfg.index_path)
    scan(index, cfg=cfg, providers=build_providers(cfg))
    return index


def test_read_session_messages_jsonl(idx, built):
    result = reader.session_messages(idx, "claude", built["claude"]["session_id"])
    assert result is not None
    roles = [m["role"] for m in result["messages"]]
    assert roles == ["user", "assistant", "user", "assistant"]  # main + subagent
    # tool_result carrier kept in reader view with previews
    carrier = next(m for m in result["messages"] if m["tool_results"])
    assert carrier["tool_results"][0]["preview"] == "boot ok"
    assert result["session"]["cwd"] == "/home/user/proj"


def test_read_session_messages_role_filter(idx, built):
    result = reader.session_messages(idx, "dsh", built["dsh"]["session_id"], role="user")
    assert [m["role"] for m in result["messages"]] == ["user"]
    assert "deploy" in result["messages"][0]["text"]


def test_around_context_window(idx, built):
    hits = search_mod.search_messages(idx, "ch011", agents=["kimi"])
    # the kimi ch011 hit is in the subagent stream (single message)
    target = hits["items"][0]
    result = reader.session_messages(
        idx, "kimi", target["session"]["session_id"],
        around_message_id=target["id"], context=5,
    )
    assert result is not None
    texts = [m["text"] for m in result["messages"] if m["text"]]
    assert any("vBookmarks" in t for t in texts)  # main flow included
    assert any("ch011" in t for t in texts)


def test_read_raw_sqlite(idx, built):
    hits = search_mod.search_messages(idx, "构建失败", agents=["zcode"])
    assert hits["total"] == 1
    raw = reader.read_raw("zcode", hits["items"][0]["locator"])
    import orjson

    data = orjson.loads(raw["message"]["data"])
    assert data["role"] == "user"


def test_read_raw_zstd(idx, built):
    hits = search_mod.search_messages(idx, "deploy", agents=["dsh"])
    raw = reader.read_raw("dsh", hits["items"][0]["locator"])
    assert raw["type"] == "user/message"


def test_missing_session_returns_none(idx):
    assert reader.session_messages(idx, "claude", "nope") is None


def test_session_messages_full_text_not_truncated_by_index(idx, built):
    """Index snippets are capped, but reader output must carry full text."""
    hits = search_mod.search_messages(idx, "优惠券", agents=["qoder"])
    msg_id = hits["items"][0]["id"]
    result = reader.session_messages(
        idx, "qoder", hits["items"][0]["session"]["session_id"],
        around_message_id=msg_id, context=2,
    )
    target = next(m for m in result["messages"] if "优惠券" in (m["text"] or ""))
    assert target["text"].startswith("给购物车加个优惠券入口")
