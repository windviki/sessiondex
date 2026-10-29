---
name: search-sessions
description: >-
  Search past AI agent session histories (claude / zcode / kimi / dsh / qoder /
  opencode) by keyword, agent kind, date range, role, tool type, or working
  directory via the local sessiondex index. Use whenever the user asks to find,
  locate or recall a previous conversation or session — e.g. "搜索 zcode 今天
  含有 ch011 的会话", "find the session where we fixed the login bug", "上次
  关于部署的对话是哪次", "我哪次让 claude 改过测试". Do NOT grep agent session
  directories manually — always query sessiondex first.
---

# Search Agent Sessions (sessiondex)

`sessiondex` is a local full-text index over this machine's agent session
histories (user messages, agent replies, thinking, tool calls AND tool
outputs — all fully searchable). Query it instead of scanning directories.

## Commands (CLI)

```bash
sessiondex search "keywords" [filters] --json        # message-level hits
sessiondex sessions "keywords" [filters] --json      # one row per conversation
sessiondex show <agent> <session_id> [--around <msg_id>] --json  # full original messages
sessiondex scan                                       # incremental refresh if stale
sessiondex agents / sessiondex tools [--agent NAME]   # discover values
```

MCP tools (`search_sessions`, `list_sessions`, `get_session_messages`,
`list_agents`, `list_tools`) take the same parameters if registered.

## Natural language → parameters

Translate the user's phrasing before calling. Multiple filters combine
freely (AND). Only include filters the user actually asked for.

### Which agent (`--agent` / `agent=`)

| user says | value |
|---|---|
| "zcode 的会话", "z code" | `zcode` |
| "claude / claude code / CC 的对话" | `claude` |
| "kimi / 月之暗面 的" | `kimi` |
| "dsh / deepseek 的" | `dsh` |
| "qoder 的" | `qoder` |
| "opencode / open code 的" | `opencode` |
| "所有 agent 都搜" / not mentioned | omit the filter (searches all six) |
| "claude 和 zcode 一起搜" | `--agent claude --agent zcode` (or `agent=claude,zcode`) |

### Date / time range (`--from` / `--to`, inclusive, local timezone)

| user says | filters |
|---|---|
| "今天" / today | `--from today` |
| "昨天" / yesterday | `--from yesterday --to yesterday` |
| "前天" | `--from 2026-10-27 --to 2026-10-27` (compute the date) |
| "最近三天 / 最近一周 / -7d" | `--from -3d` / `--from -7d` |
| "上周" | `--from <last Monday> --to <last Sunday>` (compute dates) |
| "10月20日到22日 / 从10月20日以来" | `--from 2026-10-20 --to 2026-10-22` / `--from 2026-10-20` |
| "10月20日下午之后" | `--from 2026-10-20T12:00:00` |
| "最近一小时" | `--from 2026-10-29T14:30:00` (compute ISO datetime) |

Values accept `today`, `yesterday`, `-Nd`, `-Nw`, `YYYY-MM-DD`, ISO
datetimes. `--from` starts at 00:00 of that day; `--to` ends at 23:59:59.

### Role: who said it (`--role` filters, `--prefer` boosts — different!)

| user says | parameter |
|---|---|
| "我说的 / 我提问的 / 用户消息 / 我当时让…" | `--role user` (assistant messages excluded) |
| "agent 回复的 / 助手说的 / 模型的回答" | `--role assistant` |
| "全部都搜，但我说的优先 / 排前面" | `--prefer user` (NOT a filter — everything is searched, user hits ranked first) |
| "都搜，agent 的回复靠前" | `--prefer assistant` |

**Rule of thumb: 「只要 / 仅」 → `--role`; 「优先 / 靠前 / 但也搜其他的」 → `--prefer`.**

### Tool type (`--tool`, case-insensitive, matches calls made in the session)

| user says | likely value (run `sessiondex tools` to confirm) |
|---|---|
| "跑过命令的 / 执行了 shell / 跑了测试" | `--tool Bash` (dsh uses lowercase `bash`) |
| "改过代码 / 编辑过文件的会话" | `--tool Edit,Write` |
| "读过 xx 文件的" | `--tool Read` + keyword = filename |
| "不确定工具名" | run `sessiondex tools [--agent NAME]` first, pick from the list |

### Scope: session vs message vs subagent

| user says | approach |
|---|---|
| "哪个会话 / 哪次对话 / 定位会话" | `sessiondex sessions "kw" …` (session-level, shows matched counts) |
| "哪条消息 / 具体哪句" | `sessiondex search "kw" …` (message-level with snippets) |
| "包括子 agent / 子代理的也要" | add `--subagent` (default: included) |
| "只要主会话 / 不算子 agent" | add `--main-only` |
| "在 xx 项目 / xx 仓库里的会话" | `--cwd "/path/to/project*"` (prefix or glob) |

### Keywords (`q`, positional)

- Multiple terms are ANDed: `"登录 bug"` matches messages containing both.
- Chinese works as substring (bigram index): `"启动速度"` matches inside
  longer sentences. Mixed `"ch011 模块"` fine.
- Keywords also match tool calls, tool **outputs** and thinking — if the
  user wants to exclude noise, combine with `--role user`/`--role assistant`.

### Ranking ("search everything, but … first")

| user says | parameter |
|---|---|
| (default) most relevant first | nothing (bm25; built-in field weights: message text > tool calls > thinking > tool outputs) |
| "最新的优先 / 按时间排" | `--order ts` |
| "全部搜，我说的排前面" | `--prefer user` |
| "全部搜，回复的排前面" | `--prefer assistant` |

## Workflow

1. If results look stale or the index is missing, run `sessiondex scan`
   (incremental, sub-second to seconds).
2. Map the request to filters per the tables above; start message-level
   (`search`) with `--limit 20 --json`.
3. Each item has `id` (message id), `session.session_id`, `locator`,
   `snippet` (with `<mark>` highlights; prefixes `[thinking]`/`[tools]`/
   `[source]` show which field matched — `[source]` means the hit was
   regenerated from the original storage).
4. To read the full conversation around a hit:
   `sessiondex show <agent> <session_id> --around <item.id> --context 5 --json`
   — this reads the COMPLETE original messages from the agent's storage.
5. Too many hits? Narrow by agent/date/cwd, or switch to session-level
   (`sessions`) to see which conversations matched at all, then read one.

## Worked examples

```bash
# "搜索 zcode 今天含有 ch011 的会话"
sessiondex sessions "ch011" --agent zcode --from today --json

# "我上周是不是让 claude 改过书签导出的 bug？"
sessiondex search "书签 导出 bug" --agent claude --role user --from -7d --json

# "找一下 dsh 跑过部署脚本的那次对话（记不清哪天，大概上个月）"
sessiondex sessions "deploy" --agent dsh --from -30d --json

# "所有 agent 都搜 error ECONNREFUSED，但我自己说过的优先显示"
sessiondex search "ECONNREFUSED" --prefer user --limit 30 --json

# "昨天在 vBookmarks 项目里编辑过文件的会话"
sessiondex sessions "" --cwd "/home/*/vBookmarks*" --from yesterday --to yesterday --tool Edit --json

# "那次 kimi 子 agent 检索出 42 个书签的会话，给我看上下文"
sessiondex search "42 bookmarks" --agent kimi --subagent --json
sessiondex show kimi <session_id> --around <item.id> --context 5 --json
```

If `sessiondex` reports the index is missing, run `sessiondex scan` once.
