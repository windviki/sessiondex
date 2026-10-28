"""Provider parsing tests against synthetic fixtures."""
from __future__ import annotations

from pathlib import Path

import pytest

from sessiondex.model import SessionMeta
from sessiondex.provider import PROVIDER_CLASSES, build_providers


@pytest.fixture()
def providers(cfg, built):
    return build_providers(cfg)


def _collect(provider, session):
    out = []
    provider.scan_source(
        session.sources[0] if session.sources else session.file_path,
        session,
        None,
        out.append,
    )
    return out


def test_all_providers_discovered(providers):
    assert set(providers) == {"claude", "zcode", "kimi", "dsh", "qoder", "opencode"}
    for name, provider in providers.items():
        sessions = provider.discover()
        assert sessions, f"{name} found no sessions"


def test_claude_parse(providers, built):
    provider = providers["claude"]
    metas = provider.discover()
    assert len(metas) == 1  # subagent transcripts group under the parent session
    meta = metas[0]
    assert meta.session_id == built["claude"]["session_id"]
    assert len(meta.sources) == 2  # main + subagents/agent-*.jsonl

    out = []
    provider.scan_source(meta.sources[0], meta, None, out.append)
    # tool_result carrier skipped for indexing
    assert [m.role for m in out] == ["user", "assistant"]
    assert "ch011" in out[0].text
    assert out[0].cwd == "/home/user/proj"
    assert out[1].reasoning.startswith("先查看")
    assert out[1].tools[0].name == "Bash"
    assert "ch011.log" in out[1].tools[0].input_summary
    assert out[1].locator["line"] == 2

    sub_out = []
    provider.scan_source(meta.sources[1], meta, None, sub_out.append)
    assert sub_out and sub_out[0].subagent is True

    raw = provider.read_raw(out[0].locator)
    assert raw["message"]["content"].startswith("帮我优化")


def test_qoder_parse(providers, built):
    provider = providers["qoder"]
    meta = provider.discover()[0]
    msgs = _collect(provider, meta)
    assert msgs[0].role == "user" and "优惠券" in msgs[0].text
    assert msgs[1].tools[0].name == "EnterWorktree"


def test_kimi_parse(providers, built):
    provider = providers["kimi"]
    meta = provider.discover()[0]
    assert meta.cwd == "/home/user/kimi-demo"
    main = [s for s in meta.sources if s.endswith("main/wire.jsonl")][0]
    out = []
    provider.scan_source(main, meta, None, out.append)
    assert [m.role for m in out] == ["user", "assistant"]
    assert "vBookmarks" in out[0].text
    assert out[1].reasoning == "需要读取数据库"
    # tool.call attached to the last assistant message
    assert out[1].tools[0].name == "Bash"
    assert "sqlite3" in out[1].tools[0].input_summary

    sub = [s for s in meta.sources if "agent-35" in s][0]
    sub_out = []
    provider.scan_source(sub, meta, None, sub_out.append)
    assert sub_out[0].subagent is True
    assert "ch011" in sub_out[0].text

    # reader mode: tool result preview attached
    recs = list(provider.iter_messages(meta))
    assistant = next(r for r in recs if r.role == "assistant" and r.tools)
    assert assistant.tools[0].result_preview == "42 bookmarks"


def test_dsh_parse(providers, built):
    provider = providers["dsh"]
    meta = provider.discover()[0]
    assert meta.session_id == built["dsh"]["session_id"]
    out = []
    provider.scan_source(meta.file_path, meta, None, out.append)
    assert [m.role for m in out] == ["user", "assistant"]
    assert out[0].cwd == "/home/user/dsh-proj"
    assert "deploy" in out[0].text
    assert out[1].reasoning == "先读脚本"
    assert out[1].tools[0].name == "bash"
    assert "deploy.sh" in out[1].tools[0].input_summary
    assert out[1].locator["line"] == 3

    recs = list(provider.iter_messages(meta))
    assistant = next(r for r in recs if r.tools)
    assert "deploy" in assistant.tools[0].result_preview

    raw = provider.read_raw(out[0].locator)
    assert raw["type"] == "user/message"


def test_zcode_parse(providers, built):
    provider = providers["zcode"]
    sessions = provider.discover()
    main = next(s for s in sessions if not s.subagent)
    assert main.cwd == "/home/user/zcode-work"
    assert main.title == "数据库会话标题"
    out = []
    provider.scan_source(str(Path(built["zcode"]["db"])), main, None, out.append)
    assert [m.role for m in out] == ["user", "assistant"]
    assert "构建失败" in out[0].text
    assert out[1].reasoning == "查看 CI 日志"
    assert out[1].tools[0].name == "Read"
    assert "ci.log" in out[1].tools[0].input_summary
    assert out[1].locator["kind"] == "sqlite"
    assert out[1].locator["db"] == built["zcode"]["db"]
    # subagent session detected via sess_subagent prefix
    assert any(s.subagent for s in sessions)

    raw = provider.read_raw(out[0].locator)
    assert raw["message"]["session_id"] == main.session_id
    assert raw["parts"]


def test_opencode_parse(providers, built):
    provider = providers["opencode"]
    meta = provider.discover()[0]
    out = []
    provider.scan_source(built["opencode"]["db"], meta, None, out.append)
    assert len(out) == 2
    assert out[1].tools[0].name == "Read"


def test_unknown_agent_raises():
    with pytest.raises(KeyError):
        PROVIDER_CLASSES["nope"]
