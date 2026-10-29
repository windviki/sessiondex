# Installing the search-sessions skill per agent

The same skill file (`integrations/skills/search-sessions/SKILL.md`) works
everywhere — it is a directory bundle with `name`/`description` frontmatter,
the convention all six agents understand. Install it into each agent's
user-scope skill directory:

| agent | skill directory | note |
|---|---|---|
| zcode | `~/.zcode/skills/` | |
| claude (Claude Code) | `~/.claude/skills/` | |
| kimi (Kimi Code) | `~/.kimi-code/skills/` | auto-discovered user dir |
| opencode | `~/.config/opencode/skill/` | note the singular `skill` |
| dsh (DeepSeek) | `~/.dsh/skills/` and `~/.agents/skills/` | both are scanned (ranks 400/500) |
| qoder | `~/.agents/skills/` | loads the home `.agents/skills` compatibility dir |

`~/.agents/skills/` is the cross-agent convention — zcode, dsh and qoder all
read it, so one copy covers three agents.

```bash
SRC=integrations/skills/search-sessions/SKILL.md
for d in ~/.zcode/skills ~/.claude/skills ~/.kimi-code/skills \
         ~/.config/opencode/skill ~/.dsh/skills ~/.agents/skills; do
  mkdir -p "$d/search-sessions" && cp "$SRC" "$d/search-sessions/SKILL.md"
done
```

**Make the CLI callable** (the skill shells out to `sessiondex …`):

```bash
ln -sf /path/to/sessiondex/.venv/bin/sessiondex ~/.local/bin/sessiondex
```

(~/.local/bin is on PATH on most systems; otherwise point the symlink at
any directory that is.)

## What the skill gives each agent

- Trigger phrases in Chinese and English ("搜索 zcode 今天含有 ch011 的会话",
  "find the session where we fixed the login bug").
- A natural-language → parameter mapping table: agent aliases, date-range
  phrasing, role filtering vs `--prefer` boosting, tool-type filters,
  session/message scope, subagent include/exclude, cwd, ranking options.
- A two-step workflow: `sessiondex search … --json` →
  `sessiondex show <agent> <session_id> --around <msg_id> --json` to read
  the full original conversation.
- MCP-registered agents (see [mcp.md](mcp.md)) get the same capabilities as
  structured tools; the skill works with or without MCP registration.
