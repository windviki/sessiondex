# REST API reference

Base URL: `http://127.0.0.1:8765` (override with `SESSIONDEX_HOST` /
`SESSIONDEX_PORT`; `sessiondex serve --host/--port` for one-offs). Live
OpenAPI docs at `/docs` and `/redoc`.

If `SESSIONDEX_TOKEN` is set, every endpoint except `/api/health` requires
`Authorization: Bearer <token>`.

Common parameter conventions:

- `agent` / `tool` accept comma-separated lists (`agent=zcode,claude`).
- Date parameters accept `today`, `yesterday`, `-7d`, `-2w`, `YYYY-MM-DD`,
  or ISO datetimes, in local time. `date_from` is inclusive from 00:00,
  `date_to` inclusive to 23:59:59.999 of that day.
- `cwd` is a glob against the session working directory (`/src/proj*`);
  without wildcards it is treated as a prefix.
- Invalid dates return `422` with a message.

---

## GET /api/health

Index stats: per-agent session/message counts, index path & size, last scan
time, whether a scan is running. Never auth-gated.

## POST /api/scan?full=false

Run an incremental scan (or full rebuild with `full=true`) synchronously.
Returns per-agent `{sessions, sources_scanned, sources_skipped, messages,
errors}` and the total duration. `409` if a scan is already running.

## GET /api/agents

`[{agent, sessions, messages, last_activity}]`

## GET /api/tools?agent=

Aggregated tool names with counts: `[{tool, count}]` — use this to discover
valid values for the `tool` filter.

## GET /api/search

Message-level search.

| param | type | notes |
|---|---|---|
| `q` | string | keywords, CJK ok, ANDed; omit for pure filtering |
| `agent` | csv | claude, zcode, kimi, dsh, qoder, opencode |
| `role` | enum | `user` \| `assistant` |
| `date_from` / `date_to` | date | see conventions |
| `tool` | csv | tool names, e.g. `Bash,Read` |
| `cwd` | glob | session working directory |
| `session` | string | exact session id |
| `subagent` | bool | include/exclude subagent messages |
| `order` | enum | `bm25` (default with `q`) \| `ts` |
| `limit` / `offset` | int | limit ≤ 500 |

Response:

```json
{
  "total": 285,
  "items": [
    {
      "id": 77366,
      "agent": "zcode",
      "role": "assistant",
      "ts": "2026-10-28T14:55:01+08:00",
      "ts_ms": 1793199301000,
      "subagent": false,
      "tools": [{"name": "Bash", "input_summary": "grep -r ch011 src"}],
      "text": "...truncated stored text...",
      "reasoning": "...",
      "snippet": "...<mark>ch011</mark>...",
      "locator": {"kind": "sqlite", "agent": "zcode", "db": "…", "message_id": "msg_…"},
      "session": {"session_id": "sess_…", "title": "…", "cwd": "/src/proj", "subagent": false},
      "score": -3.412
    }
  ]
}
```

## GET /api/sessions

Session-level search (one row per conversation with ≥1 matching message).
Same filters minus `role`/`tool`. Response items add `matched_messages`,
`msg_count`, `first_ts`, `last_ts`, `title`, `cwd`.

## GET /api/sessions/{agent}/{session_id}

Session metadata row (or `404`).

## GET /api/sessions/{agent}/{session_id}/messages

Read the **full original messages** from the agent storage (not the
truncated index copies).

| param | notes |
|---|---|
| `role` | filter user/assistant |
| `around` | message id from a search hit; centers a context window |
| `context` | messages around `around` (default 3, ≤ 100) |
| `limit` / `offset` | pagination without `around` |
| `raw` | include the raw underlying record per message |

Response: `{agent, session_id, session{…}, total, returned, messages[]}`
where each message has `role, ts, text, reasoning, tools[{name,
input_summary, result_preview?}], tool_results[], locator, subagent, cwd`.

## POST /api/messages/read

Body: `{"agent": "zcode", "locator": {…}}` (from a search hit). Returns the
complete original record — the JSONL line, or `{"message": …, "parts": […]}`
for SQLite agents. `404` if the record can no longer be found.

## MCP endpoint

While `sessiondex serve` is running, `POST /mcp` speaks MCP
streamable-HTTP (any MCP client can point at it). Tools mirror the REST
surface: `search_sessions`, `list_sessions`, `get_session_messages`,
`list_agents`, `list_tools`, `refresh_index`. Tool arguments use the same
names as the REST query parameters.

## Examples

```bash
# the canonical example: zcode sessions from today mentioning ch011
curl 'http://127.0.0.1:8765/api/search?q=ch011&agent=zcode&date_from=today'
# → then pull the full conversation around the first hit
curl 'http://127.0.0.1:8765/api/sessions/zcode/sess_…/messages?around=<id>&context=5'

# all Bash-tool messages under a project in the last week
curl 'http://127.0.0.1:8765/api/search?tool=Bash&cwd=/src/proj*&date_from=-7d&limit=100'

# user messages only, Chinese keywords
curl 'http://127.0.0.1:8765/api/search?q=%E5%90%AF%E5%8A%A8%E9%80%9F%E5%BA%A6&role=user'
```
