# sessiondex

[English](README.md)

**本地 AI 编程 Agent 会话历史的全文索引与检索服务** —— Claude Code、ZCode、
Kimi Code、dsh（DeepSeek）、Qoder、OpenCode 六家会话，一个索引统一检索。

```bash
# 「搜索 zcode 今天含有 ch011 的会话」
sessiondex search "ch011" --agent zcode --from today
sessiondex sessions "ch011" --agent zcode --from today   # 会话级结果
sessiondex show zcode <session_id> --around <msg_id>     # 回读完整原文
```

## 解决什么问题

每个 CLI agent 都用自己的格式在本地落盘会话记录（JSONL、事件流、zstd 压缩
JSONL、SQLite……共六种）。想找「上周二那次对话」，只能手工 grep 几百 MB 的
文件，或在选择器里翻半天。sessiondex 在这些存储之上建一层**紧凑、增量、中文
友好的全文索引**，并通过 CLI、REST API 和 MCP 工具暴露出来 —— 任何 agent 都
能直接调用，而不必自己去扫目录。

## 设计原则：只做索引层，不复制数据

- 索引只存**截断后的消息正文**（默认 2 KB）、思考块摘录（1 KB）、工具名与
  200 字符输入摘要 —— **绝不存工具输出**。
- 完整内容永远通过每条消息的 locator **按需回读 agent 自己的原始文件**。
- 索引体积通常为源数据的 5–15%（取决于 agent 产生的思考文本量；实测约 4 GB
  历史索引后约 590 MB。可通过 `SESSIONDEX_TEXT_CAP` /
  `SESSIONDEX_REASONING_CAP` 调整）。
- Agent 存储一律**只读打开**（SQLite 用 `mode=ro`；WAL 异常时复制快照恢复，
  绝不写原库）。

## 支持的 agent 与格式

| agent | 存储位置 | 格式要点 |
|---|---|---|
| claude | `~/.claude/projects/**.jsonl` | 每会话一个 JSONL + 子 agent 转录 |
| zcode | `~/.zcode/cli/db/db.sqlite` | session/message/part 三表 SQLite |
| kimi | `~/.kimi-code/sessions/**/wire.jsonl` | 事件流，每 agent 一条 wire |
| dsh | `~/.dsh/sessions/**/session.v4.jsonl.zstd` | zstd 压缩的信封记录 |
| qoder | `~/.qoder-cn/projects/**.jsonl` | Claude 风格 JSONL |
| opencode | `~/.local/share/opencode/opencode.db` | session/message/part 三表 SQLite |

所有路径可用环境变量覆盖（`SESSIONDEX_<AGENT>_DIR` 等）。六种格式的字段级
逆向结论见 [docs/data-formats.md](docs/data-formats.md)。

## 快速开始

```bash
git clone https://github.com/windviki/sessiondex && cd sessiondex
uv venv && uv pip install -e '.[dev]'   # 或 python -m venv .venv && .venv/bin/pip install -e '.[dev]'

sessiondex scan       # 增量索引所有已发现的 agent
sessiondex search "ch011" --agent zcode --from today
sessiondex serve      # 可选：REST + MCP 服务 http://127.0.0.1:8765
```

要求 Python ≥ 3.10，SQLite 需带 FTS5（3.11+ 标配）。

## 检索条件（可任意组合）

| 条件 | CLI | REST/MCP |
|---|---|---|
| 关键词（支持中文，多词 AND） | 位置参数 | `q` |
| agent 种类 | `--agent zcode` | `agent=zcode,claude` |
| 用户消息 / agent 消息 | `--role user\|assistant` | `role=user` |
| 日期 | `--from today --to yesterday` | `date_from=today&date_to=-7d` |
| 工具调用类型 | `--tool Bash` | `tool=Bash,Read` |
| 会话工作目录 | `--cwd "/src/project*"` | `cwd=/src/project*` |
| 精确会话 | `--session <id>` | `session=<id>` |
| 仅子会话 / 仅主会话 | `--subagent / --main-only` | `subagent=true/false` |

日期支持 `today`、`yesterday`、`-7d`、`-2w`、`YYYY-MM-DD` 和 ISO 时间，
按本地时区解释。命中结果带 `<mark>` 高亮 snippet、session id 和回读原文用
的 locator。

## 三种调用方式

- **CLI** —— `scan` / `search` / `sessions` / `show` / `status` / `agents` /
  `tools` / `serve` / `mcp`（列表/检索命令都支持 `--json`）。
- **REST API** —— FastAPI，默认 `127.0.0.1:8765`（绑定地址与 token 可配），
  `/docs` 有交互式文档。端点见 [docs/api.md](docs/api.md)。
- **MCP server** —— stdio（`sessiondex mcp`，零常驻）或 streamable-HTTP
  （`serve` 时挂载在 `/mcp`）。六个 agent 的注册片段见
  [integrations/mcp.md](integrations/mcp.md)；通用 skill 见
  [integrations/skills/search-sessions/SKILL.md](integrations/skills/search-sessions/SKILL.md)，
  装进任何支持 skill 的 agent 即可让它主动调用。

## 索引原理

- **分词** —— ASCII 词小写化为 token；CJK 连续段切成字符二元组（bigram），
  因此 `启动`、`ch011 模块` 这类查询等效于子串匹配。索引与查询用同一分词器
  （FTS5 `unicode61` 作用于预分词流）。
- **增量扫描** —— JSONL 源按字节偏移水位续扫（size + mtime 判断变化）；zstd
  源变化时整文件重析（单文件很小）；SQLite 源按 `message`/`part` 的 rowid
  水位增量，按稳定 message id 去重 upsert。`--full` 全量重建。
- **无常驻要求** —— 每个命令直接打开 SQLite 索引；`serve` 模式可选开启后台
  定时重扫线程。

更多细节见 [docs/architecture.md](docs/architecture.md)。

## 开发

```bash
uv pip install -e '.[dev]'
pytest               # 50+ 测试，六种格式全部用合成 fixture
```

测试为每种 agent 格式构造微型假会话库 —— 不需要也不会提交任何真实会话数据。

## 许可

MIT —— 见 [LICENSE](LICENSE)。
