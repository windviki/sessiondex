# Architecture

```
                     ┌────────────────────────────────────────────┐
   agent storage     │                sessiondex                  │
 (read-only)         │                                            │
                     │  providers ──► scanner ──► index (SQLite)  │
 ~/.claude/...  ───► │    claude       │         sessions          │
 ~/.zcode/...   ───► │    zcode        │         messages          │
 ~/.kimi-code/… ───► │    kimi         │         messages_fts      │
 ~/.dsh/...     ───► │    dsh          │         sources           │
 ~/.qoder-cn/…  ───► │    qoder        │                            │
 ~/.local/...   ───► │    opencode     ▼                            │
                     │              search ◄─── CLI / REST / MCP   │
                     │                 │                           │
                     │               reader ───► original records  │
                     └────────────────────────────────────────────┘
```

## Layers

### 1. Providers (`src/sessiondex/provider/`)

One adapter per agent. Contract:

- `discover()` — enumerate `SessionMeta` (session id, cwd, timestamps, and
  the list of *source files* feeding the session).
- `scan_source(path, session, state, emit)` — (re)index one source file.
  `state` is the persisted watermark; JSONL providers resume from a byte
  offset, SQLite providers from rowid watermarks on `message` + `part`.
- `iter_messages(session, include_raw)` — replay the whole session in
  normalized form (used by the reader; also returns the raw record).
- `read_raw(locator)` — read one full original record.

Every provider normalizes to `MessageRecord(agent, session_id, ts_ms, role,
text, reasoning, tools[name, input_summary, output], locator, subagent,
cwd)`. Tool outputs are captured in full (per-call cap) and attached back to
the assistant message that issued the call — Claude/Qoder buffer the file
to match `tool_result` blocks to their `tool_use`; Kimi/dsh keep a
`toolCallId → call` map over their buffered event streams; SQLite parts
already carry `state.output` inline.

Two shared bases avoid duplication: `claude_style.py` (claude + qoder) and
`sqlite_provider.py` (zcode + opencode, which share the
session/message/part schema). Kimi needs its own buffered parser so that
`tool.call` events attach to the preceding assistant message of the same
agent stream; dsh needs zstd stream decompression (multi-frame aware, with a
bounded LRU cache of decompressed lines).

SQLite databases are opened `mode=ro` with a `sqlite_master` + `message`
sanity probe; on `DatabaseError` (stale WAL needing recovery) a snapshot of
db + wal is copied into the cache dir and recovered there. The original
database is never written.

### 2. Scanner (`scanner.py`)

Per session: upsert session row, then per source file compare
`(size, mtime_ns)` (SQLite providers fold `-wal`/`-shm` into the
fingerprint):

- unchanged → skip
- grown & appendable → resume from stored byte offset (JSONL)
- else → delete that source's messages and reparse

Messages are upserted by stable `msg_key` (`<file>:<line>` for JSONL,
`<agent>:<message_id>` for SQLite), so re-emits replace rather than
duplicate. Session stats (first/last ts, count, fallback title from first
user message) refresh after each touched session.

### 3. Index (`index.py`)

SQLite schema:

- `sessions(agent, session_id UNIQUE, title, cwd, file_path, sources, subagent, first_ts, last_ts, msg_count)`
- `messages(id AUTOINCREMENT, agent, session_rowid, src, msg_key UNIQUE, role, ts_ms, subagent, tools, locator, snippet, text, reasoning)`
- `messages_fts` — **contentless** FTS5 table (`content=''`,
  `contentless_delete=1`, `unicode61`) with four columns (text, reasoning,
  tools, outputs) fed *full* pre-tokenized streams. Contentless means only
  the inverted index is stored: search covers 100 % of message text,
  reasoning and tool outputs, yet no reconstructable document text is kept
  on disk.
- `sources(path PK, size, mtime_ns, state)` — scan watermarks.

What is stored vs. indexed:

- Stored (capped excerpts, for display/snippets): `text` 2 KB, `reasoning`
  1 KB, tool name + 200-char input summary + 1 KB output preview.
- Indexed (complete): message text, reasoning, tool inputs *and* tool
  outputs (`SESSIONDEX_TOOL_OUTPUT_CAP` per call, default 64 KB, 0 =
  unlimited).
- Snippets for hits outside the stored excerpts are regenerated from the
  original storage via locators (see reader).

Deletion semantics: contentless FTS rows cannot be deleted without their
original text, so re-parsed or removed messages leave orphan postings.
Every query INNER JOINs `messages` on the FTS rowid, so orphans never
surface; their space is reclaimed by `--full` rebuilds. `messages.id` is
AUTOINCREMENT so rowids are never reused (a reused rowid would attach stale
postings to a new message). Schema changes bump `meta.schema_version`;
opening an older index wipes it (it is fully rebuildable) and the next scan
repopulates.

### 4. Tokenizer (`tokenize.py`)

ASCII runs → lowercase word tokens. CJK runs (Han, kana, hangul, fullwidth)
→ all consecutive bigrams (1-char runs stay single tokens). The same
function runs at index time (space-joined stream into FTS) and query time
(each run becomes an FTS phrase, runs ANDed). Result: CJK substring
semantics, `ch011`-style ASCII tokens, and mixed queries all work with one
tokenize='unicode61' table.

### 5. Search (`search.py`)

Message-level: `messages_fts MATCH ?` joined to `messages` + `sessions`
with SQL filters (agent/role/ts range/tool/cwd GLOB/session/subagent),
ordered by `bm25(messages_fts, 1.0, 0.5, 0.8, 0.2)` (text > tool calls >
reasoning > tool outputs) or `ts_ms`. Session-level aggregates the top 2000
message hits in Python (FTS5 cannot aggregate bm25 in one statement).

Snippets are built in Python with `<mark>` highlights through a fallback
chain: stored text → stored reasoning (`[thinking] …`) → stored tool
previews (`[tools] …`) → **regenerated from the original storage**
(`[source] …`) via `reader.context_text`, which walks the raw record — and
for JSONL agents the records right after it, where tool outputs live — so
hits deep inside long messages or tool outputs still get a highlighted
snippet.

### 6. Reader (`reader.py`)

Takes a locator (`{kind: jsonl|jsonl_zst|sqlite, path|line|db|message_id}`)
or a session id and reads the **full original** record via the provider —
including an `around <message_id>` context window aligned on `msg_key`.
`context_text()` additionally reconstructs the searchable text of the hit
record (plus following records for JSONL agents) for deep-snippet
regeneration.

### 7. Interfaces

- `cli.py` (typer) — every command works directly against the index file.
- `api.py` (FastAPI) — REST + optional background scan thread + MCP
  streamable-HTTP mounted at `/mcp`; optional bearer-token auth.
- `mcp_server.py` — one `MCPServer` (mcp 2.x; FastMCP for 1.x) exposing six
  tools; stdio via `sessiondex mcp`.

## Failure modes & guarantees

- Partially written trailing JSONL lines (no `\n` yet) are skipped and
  picked up on the next scan — offsets only advance past complete lines.
- A provider error (corrupt file, unexpected schema) is contained: that
  source is skipped, the scan continues, errors are counted per agent.
- Deleted sessions leave stale rows until a `--full` rebuild; watermark
  rows for deleted files are pruned by `prune_missing_sources`.
- Concurrent access: the CLI takes no locks beyond SQLite's own; `serve`
  serializes index writes behind `idx_lock`.
