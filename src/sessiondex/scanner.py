"""Incremental scan orchestration.

For every discovered session the scanner checks each source file against the
persisted watermark (size + mtime): unchanged sources are skipped, appended
files resume from the stored byte offset, and rewritten/compressed files are
fully reparsed. SQLite sources use rowid watermarks maintained by the
providers themselves.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import Config
from .index import Index
from .model import MessageRecord
from .provider import Provider, build_providers


@dataclass
class ScanStats:
    agents: dict[str, dict] = field(default_factory=dict)
    started_at: float = 0.0
    duration_s: float = 0.0

    @property
    def total_messages(self) -> int:
        return sum(a.get("messages", 0) for a in self.agents.values())

    def to_dict(self) -> dict:
        return {
            "duration_s": round(self.duration_s, 2),
            "agents": self.agents,
            "messages_indexed": self.total_messages,
        }


ProgressFn = Callable[[str], None]


def _scan_provider(
    provider: Provider, idx: Index, full: bool, progress: ProgressFn
) -> dict:
    stat = {"sessions": 0, "sources_scanned": 0, "sources_skipped": 0,
            "messages": 0, "errors": 0}
    try:
        sessions = provider.discover()
    except Exception as exc:  # noqa: BLE001 - keep scanning other providers
        progress(f"[{provider.name}] discover failed: {exc}")
        stat["errors"] += 1
        return stat
    for meta in sessions:
        session_key = f"{provider.name}:{meta.session_id}"
        srow = idx.upsert_session(meta)
        stat["sessions"] += 1
        touched = False
        for src in meta.sources or ([meta.file_path] if meta.file_path else []):
            try:
                size, mtime_ns = provider.source_fingerprint(src)
            except OSError:
                stat["errors"] += 1
                continue
            prev = None if full else idx.get_source(src)
            if (
                prev is not None
                and int(prev["size"]) == size
                and int(prev["mtime_ns"]) == mtime_ns
            ):
                stat["sources_skipped"] += 1
                continue
            resume = (
                prev is not None
                and provider.supports_append
                and (provider.kind != "jsonl" or size > int(prev["size"]))
            )
            if not resume:
                idx.delete_messages_for_src(src)
            state = prev["state"] if (resume and prev) else None
            try:
                new_state = provider.scan_source(
                    src,
                    meta,
                    state,
                    lambda rec, _srow=srow: _emit(idx, rec, _srow, stat),
                )
                idx.set_source(src, provider.name, session_key, size, mtime_ns, new_state)
                stat["sources_scanned"] += 1
                touched = True
            except Exception as exc:  # noqa: BLE001
                progress(f"[{provider.name}] scan {src} failed: {exc}")
                stat["errors"] += 1
        if touched:
            # sessions meta may have been enriched during parsing (e.g. dsh cwd)
            idx.upsert_session(meta)
        else:
            row = idx.get_session_row(srow)
            if row and row["msg_count"] == 0 and row["last_ts"] == 0 and row["first_ts"] == 0:
                idx.upsert_session(meta)  # repair rows left empty by past errors
        idx.refresh_session_stats(srow)
        idx.fill_session_title(srow)
    return stat


def _emit(idx: Index, rec: MessageRecord, srow: int, stat: dict) -> None:
    idx.upsert_message(rec, srow)
    stat["messages"] += 1


def scan(
    idx: Index,
    cfg: Config | None = None,
    providers: dict[str, Provider] | None = None,
    full: bool = False,
    only: list[str] | None = None,
    progress: ProgressFn | None = None,
) -> ScanStats:
    """Run an incremental (or full) scan and return statistics."""
    progress = progress or (lambda _msg: None)
    if full:
        idx.rebuild()
    if providers is None:
        providers = build_providers(cfg, only=only)
    stats = ScanStats(started_at=time.time())
    for name, provider in providers.items():
        if only and name not in only:
            continue
        progress(f"[{name}] scanning")
        stats.agents[name] = _scan_provider(provider, idx, full, progress)
        idx.conn.commit()
    stats.duration_s = time.time() - stats.started_at
    idx.mark_scan()
    return stats


def scan_config(cfg: Config, full: bool = False, only: list[str] | None = None,
                progress: ProgressFn | None = None) -> ScanStats:
    idx = Index(cfg.index_path)
    try:
        return scan(idx, cfg=cfg, full=full, only=only, progress=progress)
    finally:
        idx.close()


def prune_missing_sources(idx: Index, cfg: Config | None = None) -> int:
    """Remove watermark rows for source files that no longer exist."""
    removed = 0
    rows = idx.conn.execute("SELECT path FROM sources").fetchall()
    for row in rows:
        if not Path(row["path"]).exists():
            idx.conn.execute("DELETE FROM sources WHERE path=?", (row["path"],))
            removed += 1
    idx.conn.commit()
    return removed
