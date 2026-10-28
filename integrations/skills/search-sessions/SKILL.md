---
name: search-sessions
description: >-
  Search past AI agent session histories (claude / zcode / kimi / dsh / qoder /
  opencode) by keyword, agent kind, date, role, tool name, or working directory
  via the local sessiondex index. Use whenever the user asks to find, locate or
  recall a previous conversation or session — e.g. "搜索 zcode 今天含有 ch011 的
  会话", "find the session where we fixed the login bug", "上次关于部署的对话是哪次".
  Do NOT grep agent session directories manually — always query sessiondex first.
---

# Search Agent Sessions (sessiondex)

`sessiondex` is a local full-text index over this machine's agent session
histories. Query it instead of scanning directories.

## Commands

```bash
# message-level search (returns snippets + session ids + message ids)
sessiondex search "keywords" --agent zcode --from today [--to ...] \
    [--role user|assistant] [--tool Bash] [--cwd "/path/prefix*"] \
    [--session <id>] [--subagent] [--limit 20] --json

# session-level search (one row per conversation that matched)
sessiondex sessions "keywords" --agent claude --from -7d --json

# read a session's full messages from the original storage
sessiondex show <agent> <session_id> [--around <message_id>] [--context 3] --json

# refresh the index (incremental, fast) if results look stale
sessiondex scan
```

## How to map a user request

| user says | filter |
|---|---|
| "zcode 的会话" / "claude 的对话" | `--agent zcode` (claude/zcode/kimi/dsh/qoder/opencode) |
| "今天" / "昨天" / "最近一周" | `--from today` / `--from yesterday` / `--from -7d` (also `--to`) |
| "我说过的" / "agent 回复的" | `--role user` / `--role assistant` |
| "调用了 Bash 的" / "用了 Read 工具的" | `--tool Bash` (repeatable) |
| "在 xx 项目里的会话" | `--cwd "/path/to/project*"` |
| 关键词 (CJK ok, multiple terms are ANDed) | positional argument |

## Reading full content

1. Run `sessiondex search ... --json`; each item has `id`, `locator`,
   `session.session_id`, `snippet` (with `<mark>` highlights).
2. `sessiondex show <agent> <session_id> --around <item.id> --context 5 --json`
   reads the **full original messages** around that hit from the agent's own
   storage — not truncated index copies.
3. `--json` output is machine-readable; omit it for human tables.

If `sessiondex` reports the index is missing, run `sessiondex scan` once.
