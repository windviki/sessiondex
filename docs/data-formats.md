# Agent session data formats

Field-level notes from reverse-engineering each agent's local storage (as of
2026-10). sessiondex normalizes all of them into one `MessageRecord` shape.

Normalization target:

```
MessageRecord(agent, session_id, ts_ms(epoch ms), role(user|assistant),
              text, reasoning, tools[{name, input_summary}],
              locator{kind,path|db,line|message_id}, subagent, cwd)
```

## Claude Code — `~/.claude/projects/<sanitized-cwd>/<uuid>.jsonl`

- One JSONL file per session; file name = session UUID; subagent
  transcripts at `<proj>/<uuid>/subagents/agent-*.jsonl` (`isSidechain: true`).
- Record: `type` ∈ `user|assistant|system|summary|…`; timestamps are ISO
  8601 strings; `cwd` on every record; `message.content` is a string or a
  block array (`text`, `thinking`, `tool_use`, `tool_result`).
- `tool_use` blocks carry the tool name; `tool_result` blocks arrive inside
  the *next* `user` record together with a top-level `toolUseResult`.

## Qoder — `~/.qoder-cn/projects/<sanitized-cwd>/<uuid>.jsonl`

Claude-Code-compatible JSONL. Same block model; `sessionId` on every
record; first lines carry `workspace-directories` / `runtime-config`. Human
input is usually a plain string content.

## Kimi Code — `~/.kimi-code/sessions/wd_<name>_<hash>/session_<uuid>/`

- `state.json`: `{id, cwd, createdAt, updatedAt, agents{main, agent-N}}` —
  timestamps are epoch ms **or** ISO strings depending on version.
- Per agent stream `agents/<agentId>/wire.jsonl` (event protocol ~1.5):
  - `agent.message.appended` → `message.message.{role, content[]}` (text
    blocks `{"type":"text"}`, thinking blocks use the key **`think`**);
    outer `time` is epoch ms, inner `createdAt` ISO.
  - `context.append_loop_event` wrapping `event.type` `tool.call`
    (name in `event.name`, args in `event.args`) / `tool.result`
    (`event.result.output`).
- Tool calls are attached to the most recent assistant message of the same
  agent stream.

## dsh (DeepSeek) — `~/.dsh/sessions/<sanitized-cwd>/<session-dir>/`

- Session dir `session-<uuid>` (main) or bare `<uuid>` (subagent, header
  `origin: "subagent"`); payload `session.v4.jsonl.zstd` — **zstd-compressed
  JSONL, possibly multiple concatenated frames**.
- First record: `{"type":"session","version":4,"id","createdAt","cwd","origin"}`.
- Envelope `{"type","seq","time"(epoch ms),"data"}` with types:
  - `user/message` → `data.content[]` text blocks
  - `assistant/message` → `data.message.content[]` (`reasoning`, `text`,
    `tool-call` blocks whose `arguments` is a **JSON-encoded string**)
  - `tool/call`, `tool/result` → `data.message.content[]` with
    `toolCallId` correlation
- Legacy `session.jsonl.zstd` (version 0) is parsed with the same envelope
  rules where possible.

## ZCode — `~/.zcode/cli/db/db.sqlite` (WAL)

- Tables `session` / `message` / `part` with JSON `data` columns.
- `session`: `id` (`sess_<uuid>`, subagents `sess_subagent_agent_<uuid>`),
  `directory` (= cwd), `title`, `task_type` ∈ `interactive|subagent_child|fork`,
  `time_created`/`time_updated` epoch ms.
- `message.data`: `role`, `time.created` (ms), optional `path.cwd` /
  `contextSnapshot.envInfo.cwd`.
- `part.data` types: `text`, `reasoning`, `tool`
  (`{callID, tool, state{status, input, output}}`), `step-start`,
  `step-finish`, `timeline`.

## OpenCode — `~/.local/share/opencode/opencode.db` (WAL)

Same three-table shape as ZCode: `session.id` `ses_…`,
`directory` = cwd, `title`, `parent_id` (non-null ⇒ subagent),
epoch-ms `time_created`/`time_updated`; `message.data.role`; `part.data`
with `text` / `reasoning` / `tool` (`data.tool` is the tool name,
`state.input/output` the payload).

## Read-only access notes for SQLite agents

Both databases are opened with `file:…?mode=ro`. A read-only open can fail
when the WAL needs recovery and no writer is alive; sessiondex then copies
db + `-wal` into its cache dir and recovers the snapshot there. The agent's
own database is never written to.
