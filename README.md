# sessiondex

[中文文档](README.zh-CN.md)

**A local full-text index & search service over your AI coding-agent session
histories** — Claude Code, ZCode, Kimi Code, dsh (DeepSeek), Qoder and
OpenCode, all in one queryable index.

```bash
# "find my zcode sessions from today that mention ch011"
sessiondex search "ch011" --agent zcode --from today
sessiondex sessions "ch011" --agent zcode --from today   # session-level
sessiondex show zcode <session_id> --around <msg_id>     # full original messages
```

## Why

Every CLI agent keeps its own session transcripts on disk, in six different
formats (JSONL, event streams, zstd-compressed JSONL, SQLite…). When you want
to find *that conversation from last Tuesday*, the only options are grepping
hundreds of megabytes by hand or scrolling a picker UI. sessiondex builds a
**compact, incremental, CJK-aware full-text index** over all of them and
exposes it through a CLI, a REST API and MCP tools that any agent can call.

## Design principle: full-text search without storing your data

sessiondex is an **index layer, not a data lake**:

- The FTS5 index is **contentless** (`content=''`): it keeps only the
  inverted index. Search covers **100 % of message text, reasoning and tool
  outputs**, yet nothing reconstructable is written to disk.
- Stored rows hold only capped excerpts for display: text 2 KB, reasoning
  1 KB, tool name + 200-char input summary + 1 KB output preview. When a
  hit falls outside the stored excerpt, its snippet is regenerated from the
  agent's original storage via per-message locators.
- Tool outputs are indexed by default (`SESSIONDEX_TOOL_OUTPUT_CAP` per
  call, default 64 KB, `0` = unlimited).
- Real-world footprint: a ~4.3 GB history (157k messages) indexes to
  ~890 MB — ~20 % of the source, zero full-text copies. Caps tunable via
  `SESSIONDEX_TEXT_CAP` / `SESSIONDEX_REASONING_CAP`.
- Agent storage is opened **read-only** (SQLite in `mode=ro`; a snapshot-copy
  fallback recovers stale WALs without touching the original).

## Supported agents & formats

| agent | storage | format notes |
|---|---|---|
| claude | `~/.claude/projects/**.jsonl` | per-session JSONL + subagent transcripts |
| zcode | `~/.zcode/cli/db/db.sqlite` | `session`/`message`/`part` SQLite |
| kimi | `~/.kimi-code/sessions/**/wire.jsonl` | event stream, per-agent wires |
| dsh | `~/.dsh/sessions/**/session.v4.jsonl.zstd` | zstd-compressed envelopes |
| qoder | `~/.qoder-cn/projects/**.jsonl` | Claude-style JSONL |
| opencode | `~/.local/share/opencode/opencode.db` | `session`/`message`/`part` SQLite |

All locations are overridable (`SESSIONDEX_<AGENT>_DIR`, …) — see
[docs/data-formats.md](docs/data-formats.md) for the reverse-engineered
schemas.

## Quick start

```bash
git clone https://github.com/windviki/sessiondex && cd sessiondex
uv venv && uv pip install -e '.[dev]'   # or: python -m venv && .venv/bin/pip install -e '.[dev]'

sessiondex scan       # incremental index of all discovered agents
sessiondex search "ch011" --agent zcode --from today
sessiondex serve      # optional: REST API + MCP at http://127.0.0.1:8765
```

Python ≥ 3.10 with a SQLite build that includes FTS5 (standard on 3.11+).

## Search filters (combinable)

| filter | CLI | REST/MCP |
|---|---|---|
| keywords (CJK ok, AND) | positional `q` | `q` |
| agent kind | `--agent zcode` | `agent=zcode,claude` |
| role | `--role user\|assistant` | `role=user` |
| date | `--from today --to yesterday` | `date_from=today&date_to=-7d` |
| tool type | `--tool Bash` | `tool=Bash,Read` |
| working dir | `--cwd "/src/project*"` | `cwd=/src/project*` |
| exact session | `--session <id>` | `session=<id>` |
| subagent only / main only | `--subagent / --main-only` | `subagent=true/false` |

Date values accept `today`, `yesterday`, `-7d`, `-2w`, `YYYY-MM-DD` and ISO
datetimes, interpreted in your local timezone. Search hits return snippets
with `<mark>` highlights, the session id, and a locator for reading the full
original record.

## Interfaces

- **CLI** — `scan` / `search` / `sessions` / `show` / `status` / `agents` /
  `tools` / `serve` / `mcp` (all list/search commands take `--json`).
- **REST API** — FastAPI on `127.0.0.1:8765` (bind/token configurable), live
  OpenAPI docs at `/docs`. Endpoints: `/api/search`, `/api/sessions`,
  `/api/sessions/{agent}/{id}/messages`, `/api/agents`, `/api/tools`,
  `/api/scan`, `/api/health`, `/api/messages/read`. Details in
  [docs/api.md](docs/api.md).
- **MCP server** — stdio (`sessiondex mcp`) or streamable-HTTP (`/mcp` while
  serving). Tools: `search_sessions`, `list_sessions`,
  `get_session_messages`, `list_agents`, `list_tools`, `refresh_index`.
  Registration snippets for all six agents live in
  [integrations/mcp.md](integrations/mcp.md); a drop-in agent skill at
  [integrations/skills/search-sessions/SKILL.md](integrations/skills/search-sessions/SKILL.md).

## How the index works

- **Tokenizer** — ASCII words are lowercased and indexed as tokens; CJK runs
  are indexed as character bigrams, so queries like `启动` or `ch011 模块`
  behave like substring search. Index-time and query-time tokenization are
  identical (FTS5 `unicode61` over pre-tokenized streams).
- **Contentless FTS** — the inverted index covers complete text, reasoning
  and tool outputs; no document text is stored. Ranking uses bm25 with
  column weights (text > tool calls > reasoning > tool outputs).
- **Incremental scanning** — JSONL sources resume from the last byte offset
  (size + mtime watermarks); zstd sources reparse whole (small) files on
  change; SQLite sources use rowid watermarks on `message`/`part` so new or
  edited messages are re-upserted by stable message id. `--full` rebuilds
  (and reclaims space left by contentless orphan postings).
- **No daemon required** — every command opens the SQLite index directly; a
  background rescan thread is optional in `serve` mode.

More depth in [docs/architecture.md](docs/architecture.md).

## Development

```bash
uv pip install -e '.[dev]'
pytest               # 60+ tests, fully synthetic fixtures for all 6 formats
```

The test suite builds miniature fake session stores for every agent format —
no real session data is ever needed or committed.

## License

MIT — see [LICENSE](LICENSE).
