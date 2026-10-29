# Registering sessiondex as a tool in your agents

sessiondex ships an MCP server (`search_sessions`, `list_sessions`,
`get_session_messages`, `list_agents`, `list_tools`, `refresh_index`) with two
transports:

- **stdio** (zero-daemon, recommended): `<SESSIONDEX>/sessiondex mcp`
- **HTTP** (needs `sessiondex serve`): `http://127.0.0.1:8765/mcp`

Replace `<SESSIONDEX>` below with the absolute path to your sessiondex CLI,
e.g. `/home/you/sessiondex/.venv/bin/sessiondex`.

The generic skill at `integrations/skills/search-sessions/SKILL.md` works for
any agent that can run shell commands — install it if MCP is unavailable.

---

## Claude Code

```bash
claude mcp add sessiondex -- <SESSIONDEX> mcp
```

or in a project `.mcp.json` / `~/.claude.json`:

```json
{
  "mcpServers": {
    "sessiondex": {
      "type": "stdio",
      "command": "<SESSIONDEX>",
      "args": ["mcp"]
    }
  }
}
```

## ZCode

`~/.zcode/cli/config.json`:

```json
{
  "mcp": {
    "servers": {
      "sessiondex": {
        "type": "stdio",
        "command": "<SESSIONDEX>",
        "args": ["mcp"]
      }
    }
  }
}
```

## OpenCode

`~/.config/opencode/opencode.jsonc`:

```jsonc
{
  "mcp": {
    "sessiondex": {
      "type": "local",
      "command": ["<SESSIONDEX>", "mcp"],
      "enabled": true
    }
  }
}
```

## Qoder

```bash
qoder mcp add sessiondex -- <SESSIONDEX> mcp
```

(or the same `.mcp.json` snippet as Claude Code in the project root)

## Kimi Code

`~/.kimi-code/mcp.json` (v2 engine; the legacy `[mcp_servers]` table in
`config.toml` is ignored):

```json
{
  "mcpServers": {
    "sessiondex": {
      "command": "<SESSIONDEX>",
      "args": ["mcp"]
    }
  }
}
```

Validate afterwards with `kimi doctor`.

## dsh (DeepSeek CLI)

dsh has no MCP-server config today — use the skill integration instead:
install `integrations/skills/search-sessions/SKILL.md` into
`~/.dsh/skills/` and/or `~/.agents/skills/` (both are scanned). See
[skills.md](skills.md) for the full per-agent install matrix.

## HTTP mode (optional, all agents)

```bash
sessiondex serve            # 127.0.0.1:8765, REST + /mcp endpoint
curl "http://127.0.0.1:8765/api/search?q=ch011&agent=zcode&date_from=today"
```

Set `SESSIONDEX_TOKEN` to require `Authorization: Bearer <token>`.
