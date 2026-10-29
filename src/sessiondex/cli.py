"""Command-line interface: scan / search / serve / status / show / mcp."""
from __future__ import annotations

import json
import re
import time
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from . import __version__, reader, search as search_mod
from .config import load_config
from .index import Index
from .scanner import scan_config

app = typer.Typer(
    name="sessiondex",
    help="Local full-text search over AI coding-agent session histories.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
err = Console(stderr=True)

_MARK = re.compile(r"</?mark>")


def _open_index() -> Index:
    cfg = load_config()
    if not cfg.index_path.exists():
        err.print(
            f"[yellow]index not found at {cfg.index_path}; run `sessiondex scan` first[/yellow]"
        )
        raise typer.Exit(1)
    return Index(cfg.index_path)


def _parse_dates(date_from: Optional[str], date_to: Optional[str]):
    try:
        return (
            search_mod.parse_date(date_from) if date_from else None,
            search_mod.parse_date(date_to, end=True) if date_to else None,
        )
    except ValueError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from None


@app.command()
def scan(
    full: bool = typer.Option(False, "--full", help="rebuild the index from scratch"),
    agent: Optional[list[str]] = typer.Option(
        None, "--agent", "-a", help="limit scan to these agents (repeatable)"
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
) -> None:
    """Scan agent session directories and update the index incrementally."""
    cfg = load_config()
    progress = None if quiet else (lambda msg: err.print(f"[dim]{msg}[/dim]"))
    stats = scan_config(cfg, full=full, only=list(agent) if agent else None,
                        progress=progress)
    table = Table(title="scan complete")
    table.add_column("agent")
    table.add_column("sessions", justify="right")
    table.add_column("sources", justify="right")
    table.add_column("skipped", justify="right")
    table.add_column("messages", justify="right")
    table.add_column("errors", justify="right")
    for name, s in sorted(stats.agents.items()):
        table.add_row(
            name, str(s["sessions"]), str(s["sources_scanned"]),
            str(s["sources_skipped"]), str(s["messages"]), str(s["errors"]),
        )
    console.print(table)
    console.print(
        f"[green]{stats.total_messages}[/green] messages indexed in"
        f" {stats.duration_s:.1f}s -> {cfg.index_path}"
    )


@app.command()
def search(
    q: Optional[str] = typer.Argument(None, help="keyword(s), CJK supported"),
    agent: Optional[list[str]] = typer.Option(None, "--agent", "-a"),
    role: Optional[str] = typer.Option(None, "--role", "-r", help="user | assistant"),
    date_from: Optional[str] = typer.Option(
        None, "--from", help="today | yesterday | -7d | YYYY-MM-DD | ISO datetime"
    ),
    date_to: Optional[str] = typer.Option(None, "--to"),
    tool: Optional[list[str]] = typer.Option(None, "--tool", "-t"),
    cwd: Optional[str] = typer.Option(None, "--cwd", help="working dir glob/prefix"),
    session: Optional[str] = typer.Option(None, "--session", help="session id"),
    subagent: Optional[bool] = typer.Option(None, "--subagent/--main-only"),
    order: str = typer.Option("bm25", "--order", help="bm25 | ts"),
    prefer: Optional[str] = typer.Option(
        None, "--prefer", "-p", help="rank these roles first (user|assistant), no filtering"
    ),
    limit: int = typer.Option(50, "--limit", "-l"),
    offset: int = typer.Option(0, "--offset"),
    as_json: bool = typer.Option(False, "--json", help="machine-readable output"),
) -> None:
    """Full-text search across indexed agent messages."""
    idx = _open_index()
    ts_from, ts_to = _parse_dates(date_from, date_to)
    result = search_mod.search_messages(
        idx, q,
        agents=list(agent) if agent else None,
        roles=[role] if role else None,
        ts_from=ts_from, ts_to=ts_to,
        tools=list(tool) if tool else None,
        cwd=cwd, session_id=session, subagent=subagent,
        order=order,
        prefer=[p for p in (prefer or "").split(",") if p] or None,
        limit=limit, offset=offset,
    )
    if as_json:
        console.print_json(json.dumps(result, ensure_ascii=False))
        return
    table = Table(title=f"{result['total']} match(es) for {q!r}")
    table.add_column("ts", style="dim")
    table.add_column("agent")
    table.add_column("role")
    table.add_column("session", style="cyan", max_width=30)
    table.add_column("snippet", max_width=80, overflow="ellipsis")
    for item in result["items"]:
        table.add_row(
            (item["ts"] or "")[:19],
            item["agent"],
            "[blue]user[/blue]" if item["role"] == "user" else "[green]agent[/green]",
            (item["session"]["session_id"] or "")[-30:],
            _MARK.sub("", item["snippet"]).replace("\n", " "),
        )
    console.print(table)


@app.command("sessions")
def sessions_cmd(
    q: Optional[str] = typer.Argument(None),
    agent: Optional[list[str]] = typer.Option(None, "--agent", "-a"),
    date_from: Optional[str] = typer.Option(None, "--from"),
    date_to: Optional[str] = typer.Option(None, "--to"),
    cwd: Optional[str] = typer.Option(None, "--cwd"),
    subagent: Optional[bool] = typer.Option(None, "--subagent/--main-only"),
    prefer: Optional[str] = typer.Option(
        None, "--prefer", "-p", help="rank these roles first (user|assistant)"
    ),
    limit: int = typer.Option(30, "--limit", "-l"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List sessions matching filters (q = any-message full-text)."""
    idx = _open_index()
    ts_from, ts_to = _parse_dates(date_from, date_to)
    result = search_mod.search_sessions(
        idx, q, agents=list(agent) if agent else None,
        ts_from=ts_from, ts_to=ts_to, cwd=cwd, subagent=subagent,
        prefer=[p for p in (prefer or "").split(",") if p] or None,
        limit=limit,
    )
    if as_json:
        console.print_json(json.dumps(result, ensure_ascii=False))
        return
    table = Table(title=f"{result['total']} session(s)")
    table.add_column("agent")
    table.add_column("session", style="cyan", max_width=36)
    table.add_column("last activity", style="dim")
    table.add_column("msgs", justify="right")
    table.add_column("cwd", max_width=32, overflow="ellipsis")
    table.add_column("title", max_width=40, overflow="ellipsis")
    for s in result["items"]:
        table.add_row(
            s["agent"], s["session_id"][-36:], (s["last_ts"] or "")[:19],
            str(s["matched_messages"]), s["cwd"], _MARK.sub("", s["title"] or ""),
        )
    console.print(table)


@app.command()
def show(
    agent: str = typer.Argument(...),
    session_id: str = typer.Argument(...),
    role: Optional[str] = typer.Option(None, "--role"),
    around: Optional[int] = typer.Option(None, "--around", help="message id to center on"),
    context: int = typer.Option(3, "--context", "-c", help="messages around --around"),
    limit: int = typer.Option(20, "--limit", "-l"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Print a session's messages read from the original storage."""
    idx = _open_index()
    result = reader.session_messages(
        idx, agent, session_id, role=role, around_message_id=around,
        context=context, limit=limit,
    )
    if result is None:
        err.print(f"[red]session not found: {agent}/{session_id}[/red]")
        raise typer.Exit(1)
    if as_json:
        console.print_json(json.dumps(result, ensure_ascii=False))
        return
    sess = result["session"]
    console.print(
        f"[bold]{agent}[/bold]/{session_id}\n"
        f"  title: {sess['title'] or '-'}\n  cwd: {sess['cwd'] or '-'}"
        f"  messages: {result['total']}"
    )
    for m in result["messages"]:
        who = "[blue]user[/blue]" if m["role"] == "user" else "[green]agent[/green]"
        head = f"{who} {(m['ts'] or '')[:19]} #{m['locator'].get('line', m['locator'].get('message_id', ''))}"
        console.rule(head)
        text = m["text"] or (m["reasoning"][:400] + ("…" if len(m["reasoning"]) > 400 else ""))
        console.print(text or "[dim](tool call)[/dim]")
        for t in m["tools"]:
            console.print(f"  [magenta]{t['name']}[/magenta] {t.get('input_summary', '')[:120]}")


@app.command()
def status(as_json: bool = typer.Option(False, "--json")) -> None:
    """Show index statistics and last scan time."""
    idx = _open_index()
    st = idx.stats()
    st["last_scan"] = time.strftime(
        "%Y-%m-%d %H:%M:%S", time.localtime(st["last_scan"] / 1000)
    ) if st["last_scan"] else None
    agents = search_mod.list_agents(idx)
    if as_json:
        st["agents"] = agents
        console.print_json(json.dumps(st, ensure_ascii=False, default=str))
        return
    table = Table(title="sessiondex index")
    table.add_column("agent")
    table.add_column("sessions", justify="right")
    table.add_column("messages", justify="right")
    table.add_column("last activity", style="dim")
    for a in agents:
        table.add_row(a["agent"], str(a["sessions"]), str(a["messages"]),
                      (a["last_activity"] or "")[:19])
    console.print(table)
    console.print(f"path: {st['index_path']}  size: {st['index_size_bytes']/1e6:.1f}MB"
                  f"  last scan: {st['last_scan'] or '-'}")


@app.command()
def agents() -> None:
    """List agents present in the index."""
    idx = _open_index()
    for a in search_mod.list_agents(idx):
        console.print(
            f"{a['agent']:<10} sessions={a['sessions']:<6} messages={a['messages']:<8}"
            f" last={a['last_activity']}"
        )


@app.command()
def tools(agent: Optional[str] = typer.Option(None, "--agent", "-a")) -> None:
    """List tool names appearing in the index (for tool-type filters)."""
    idx = _open_index()
    for t in search_mod.list_tools(idx, agent)[:50]:
        console.print(f"{t['tool']:<30} {t['count']}")


@app.command()
def serve(
    host: Optional[str] = typer.Option(None, "--host"),
    port: Optional[int] = typer.Option(None, "--port"),
    no_scan: bool = typer.Option(False, "--no-scan", help="disable background scanning"),
) -> None:
    """Run the local HTTP (+MCP) search service."""
    import uvicorn

    from .api import app_factory

    cfg = load_config()
    if no_scan:
        # factory mode always starts the background scanner; honor --no-scan
        import os

        os.environ["SESSIONDEX_SCAN_INTERVAL"] = "0"
    del cfg
    uvicorn.run(
        app_factory,
        host=host or load_config().host,
        port=port or load_config().port,
        log_level="info",
    )


@app.command()
def mcp() -> None:
    """Run the MCP server over stdio (for agent tool registration)."""
    from .mcp_server import run_stdio

    run_stdio()


@app.command()
def version() -> None:
    console.print(f"sessiondex {__version__}")


def main() -> None:  # console_scripts entry point
    app()


if __name__ == "__main__":
    main()
