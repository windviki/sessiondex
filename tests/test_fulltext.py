"""Full-coverage guarantees of the contentless index.

The FTS feed covers complete message text, reasoning and tool outputs even
though only capped excerpts are *stored*; snippets for hits outside the
stored excerpts are regenerated from the original storage.
"""
from __future__ import annotations

import orjson
import pytest

from sessiondex import search as search_mod
from sessiondex.index import Index, SCHEMA_VERSION
from sessiondex.provider import build_providers
from sessiondex.scanner import scan


@pytest.fixture()
def idx(cfg, built):
    index = Index(cfg.index_path)
    scan(index, cfg=cfg, providers=build_providers(cfg))
    return index


def test_hit_beyond_text_cap(idx):
    """Keyword living past the 2 KB stored excerpt still matches."""
    hits = search_mod.search_messages(idx, "TAILMARKER7f3a")
    assert hits["total"] == 1
    item = hits["items"][0]
    assert item["agent"] == "claude" and item["role"] == "user"
    assert "<mark>" in item["snippet"]  # deep snippet regenerated from source
    assert "TAILMARKER7f3a" in item["snippet"]


def test_hit_in_tool_output_head(idx):
    """Keyword only present in a tool output matches (stored preview path)."""
    hits = search_mod.search_messages(idx, "OUTNEAR3c1d")
    assert hits["total"] == 1
    item = hits["items"][0]
    assert item["role"] == "assistant"
    assert item["tools"][0]["name"] == "Grep"
    assert "<mark>" in item["snippet"]


def test_hit_deep_in_tool_output(idx):
    """Keyword deep inside a tool output, beyond the stored preview, still
    matches and gets a source-regenerated snippet."""
    hits = search_mod.search_messages(idx, "OUTFAR9d31")
    assert hits["total"] == 1
    assert "<mark>" in hits["items"][0]["snippet"]


def test_hit_in_zcode_tool_output(idx):
    """sqlite agents: state.output is indexed too."""
    hits = search_mod.search_messages(idx, "ch011", agents=["zcode"])
    assert hits["total"] >= 1
    # fixture output "ERROR at ch011" lands on the assistant message
    assert any(
        any("ERROR at ch011" in (t.get("output_preview") or "") for t in h["tools"])
        for h in hits["items"]
    )


def test_schema_upgrade_wipes_old_index(cfg, built):
    index = Index(cfg.index_path)
    scan(index, cfg=cfg, providers=build_providers(cfg))
    assert index.stats()["messages"] > 0
    index.conn.execute("UPDATE meta SET value='1' WHERE key='schema_version'")
    index.conn.commit()
    index.close()

    reopened = Index(cfg.index_path)
    assert reopened.rebuilt_on_open is True
    assert reopened.stats()["messages"] == 0
    assert reopened.get_meta("schema_version") == str(SCHEMA_VERSION)
    # rescan repopulates from scratch
    scan(reopened, cfg=cfg, providers=build_providers(cfg))
    assert reopened.stats()["messages"] > 0
    reopened.close()


def test_orphan_postings_do_not_leak(idx, cfg, built):
    """Rewriting a source file must remove its messages from results even
    though contentless FTS postings cannot be deleted."""
    main = built["claude"]["main"]
    records = [
        orjson.loads(line)
        for line in open(main, "rb").read().splitlines()
        if b"TAILMARKER7f3a" not in line
    ]
    with open(main, "wb") as fh:
        for rec in records:
            fh.write(orjson.dumps(rec) + b"\n")
    import os, time

    os.utime(main, ns=(0, time.time_ns() + 2_000_000_000))

    rescan = Index(cfg.index_path)
    scan(rescan, cfg=cfg, providers=build_providers(cfg))
    hits = search_mod.search_messages(rescan, "TAILMARKER7f3a")
    assert hits["total"] == 0
    # other content untouched
    assert search_mod.search_messages(rescan, "ch011 模块")["total"] == 1
    rescan.close()


def test_upsert_replace_leaves_no_duplicates(idx, cfg, built):
    """Re-emitting a message (sqlite watermark replay) replaces, not dupes."""
    scan(idx, cfg=cfg, providers=build_providers(cfg))
    # user text + assistant tool output both legitimately contain the term
    assert search_mod.search_messages(idx, "构建失败")["total"] == 2
