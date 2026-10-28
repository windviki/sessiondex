"""Synthetic fixture data for every supported agent format.

All paths, uuids and texts are fabricated (``/home/user/proj`` etc.) so no
real session data ever enters the repository.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import orjson
import pytest
import zstandard

from sessiondex.config import Config

T0 = 1730000000000  # fixed epoch ms base (2024-10-27)


def _config(tmp_path: Path) -> Config:
    root = tmp_path / "agents"
    return Config(
        index_path=tmp_path / "index" / "index.sqlite",
        cache_dir=tmp_path / "cache",
        host="127.0.0.1",
        port=0,
        token=None,
        scan_interval=0,
        providers={
            "claude": root / "claude",
            "zcode": root / "zcode" / "db.sqlite",
            "kimi": root / "kimi",
            "dsh": root / "dsh",
            "qoder": root / "qoder",
            "opencode": root / "opencode" / "opencode.db",
        },
    )


def _write_lines(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as fh:
        for rec in records:
            fh.write(orjson.dumps(rec) + b"\n")


# -- Claude Code -------------------------------------------------------------


def build_claude(root: Path) -> dict:
    sid = "11111111-aaaa-4bbb-8ccc-000000000001"
    sub_sid = "11111111-aaaa-4bbb-8ccc-000000000002"
    sub_agent_file = "agent-deadbeefdeadbeef.jsonl"
    main = root / "claude" / "projects" / "-home-user-proj" / f"{sid}.jsonl"
    _write_lines(
        main,
        [
            {
                "type": "user",
                "uuid": "u1",
                "sessionId": sid,
                "timestamp": "2026-10-20T10:00:00.000Z",
                "cwd": "/home/user/proj",
                "message": {"role": "user", "content": "帮我优化 ch011 模块的启动速度"},
            },
            {
                "type": "assistant",
                "uuid": "a1",
                "sessionId": sid,
                "timestamp": "2026-10-20T10:00:05.000Z",
                "cwd": "/home/user/proj",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "先查看 ch011 的配置"},
                        {"type": "text", "text": "我先看看启动日志"},
                        {
                            "type": "tool_use",
                            "id": "call_01",
                            "name": "Bash",
                            "input": {"command": "cat ch011.log | head -50"},
                        },
                    ],
                },
            },
            {
                "type": "user",
                "uuid": "u2",
                "sessionId": sid,
                "timestamp": "2026-10-20T10:00:06.000Z",
                "cwd": "/home/user/proj",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "call_01", "content": "boot ok"}
                    ],
                },
            },
        ],
    )
    sub = (
        root / "claude" / "projects" / "-home-user-proj" / sid / "subagents"
        / sub_agent_file
    )
    _write_lines(
        sub,
        [
            {
                "type": "assistant",
                "uuid": "s1",
                "sessionId": sid,
                "isSidechain": True,
                "timestamp": "2026-10-20T10:01:00.000Z",
                "cwd": "/home/user/proj",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "subagent 搜索结果汇总"}],
                },
            }
        ],
    )
    return {"session_id": sid, "main": str(main), "sub": str(sub)}


# -- Qoder ---------------------------------------------------------------------


def build_qoder(root: Path) -> dict:
    sid = "22222222-aaaa-4bbb-8ccc-000000000002"
    main = root / "qoder" / "projects" / "-home-user-shop" / f"{sid}.jsonl"
    _write_lines(
        main,
        [
            {
                "type": "workspace-directories",
                "sessionId": sid,
                "directories": ["/home/user/shop"],
            },
            {
                "type": "user",
                "uuid": "q1",
                "sessionId": sid,
                "timestamp": "2026-10-21T08:30:00.000Z",
                "cwd": "/home/user/shop",
                "message": {"role": "user", "content": "给购物车加个优惠券入口"},
            },
            {
                "type": "assistant",
                "uuid": "q2",
                "sessionId": sid,
                "timestamp": "2026-10-21T08:30:10.000Z",
                "cwd": "/home/user/shop",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "好的，先看下组件结构"},
                        {
                            "type": "tool_use",
                            "id": "call_q1",
                            "name": "EnterWorktree",
                            "input": {"name": "coupon-feature"},
                        },
                    ],
                },
            },
        ],
    )
    return {"session_id": sid, "main": str(main)}


# -- Kimi ------------------------------------------------------------------------


def build_kimi(root: Path) -> dict:
    sid = "session_33333333-aaaa-4bbb-8ccc-000000000003"
    sess_dir = root / "kimi" / "sessions" / "wd_user_90ab" / sid
    state = {
        "id": sid,
        "version": 2,
        "cwd": "/home/user/kimi-demo",
        "createdAt": T0,
        "updatedAt": T0 + 60000,
        "agents": {
            "main": {"type": "main"},
            "agent-35": {"type": "sub", "parentAgentId": "main"},
        },
    }
    (sess_dir / "state.json").parent.mkdir(parents=True, exist_ok=True)
    (sess_dir / "state.json").write_text(json.dumps(state))
    wire = [
        {"type": "metadata", "protocol_version": "1.5", "created_at": T0},
        {
            "type": "agent.message.appended",
            "agentId": "main",
            "time": T0 + 1000,
            "message": {
                "tracked": True,
                "createdAt": "2026-10-22T03:00:01.000Z",
                "userMessageId": "msg_u1",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "统计 vBookmarks 的书签数量"}],
                },
            },
        },
        {
            "type": "agent.message.appended",
            "agentId": "main",
            "time": T0 + 2000,
            "message": {
                "tracked": True,
                "createdAt": "2026-10-22T03:00:02.000Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "think", "think": "需要读取数据库"},
                        {"type": "text", "text": "我来查询书签表"},
                    ],
                },
            },
        },
        {
            "type": "context.append_loop_event",
            "agentId": "main",
            "event": {
                "type": "tool.call",
                "toolCallId": "tool_k1",
                "name": "Bash",
                "args": {"command": "sqlite3 bookmarks.db 'count(*)'"},
            },
        },
        {
            "type": "context.append_loop_event",
            "agentId": "main",
            "event": {
                "type": "tool.result",
                "toolCallId": "tool_k1",
                "result": {"output": "42 bookmarks", "durationMs": 12},
            },
        },
    ]
    _write_lines(sess_dir / "agents" / "main" / "wire.jsonl", wire)
    sub_wire = [
        {
            "type": "agent.message.appended",
            "agentId": "agent-35",
            "time": T0 + 9000,
            "message": {
                "tracked": True,
                "createdAt": "2026-10-22T03:00:09.000Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "子代理完成检索 ch011"}],
                },
            },
        }
    ]
    _write_lines(sess_dir / "agents" / "agent-35" / "wire.jsonl", sub_wire)
    return {"session_id": sid, "dir": str(sess_dir)}


# -- dsh -------------------------------------------------------------------------


def build_dsh(root: Path) -> dict:
    sid = "44444444-aaaa-4bbb-8ccc-000000000004"
    f = root / "dsh" / "sessions" / "--home-user-dsh-proj" / f"session-{sid}" / "session.v4.jsonl.zstd"
    records = [
        {
            "type": "session",
            "version": 4,
            "id": sid,
            "createdAt": T0,
            "cwd": "/home/user/dsh-proj",
            "origin": "main",
        },
        {
            "type": "user/message",
            "seq": 1,
            "time": T0 + 500,
            "data": {"content": [{"type": "text", "text": "重构 deploy 脚本"}]},
        },
        {
            "type": "assistant/message",
            "seq": 2,
            "time": T0 + 1500,
            "data": {
                "turn": 1,
                "step": 1,
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "reasoning", "text": "先读脚本"},
                        {"type": "text", "text": "读取 deploy.sh 中"},
                        {
                            "type": "tool-call",
                            "id": "call_d1",
                            "name": "bash",
                            "arguments": json.dumps({"command": "cat deploy.sh"}),
                        },
                    ],
                },
            },
        },
        {
            "type": "tool/result",
            "seq": 3,
            "time": T0 + 2000,
            "data": {
                "message": {
                    "role": "tool",
                    "toolCallId": "call_d1",
                    "content": [{"type": "text", "text": "#!/bin/bash\necho deploy"}],
                }
            },
        },
    ]
    f.parent.mkdir(parents=True, exist_ok=True)
    cctx = zstandard.ZstdCompressor()
    # two concatenated frames to exercise multi-frame decompression
    frame1 = b"".join(orjson.dumps(r) + b"\n" for r in records[:2])
    frame2 = b"".join(orjson.dumps(r) + b"\n" for r in records[2:])
    f.write_bytes(cctx.compress(frame1) + cctx.compress(frame2))
    return {"session_id": f"session-{sid}", "file": str(f)}


# -- SQLite (zcode / opencode) ------------------------------------------------------


def _build_sqlite_agent(
    db_path: Path,
    *,
    sess_id: str,
    cwd: str,
    extra_col_def: str,   # e.g. ", task_type TEXT"
    extra_col_name: str,  # e.g. "task_type"
    extra_col_val: str,   # e.g. "'interactive'" or "NULL"
    msg_id: str,
    sub_sess_id: str | None,
) -> dict:
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE session(
          id TEXT PRIMARY KEY, directory TEXT, title TEXT,
          time_created INTEGER, time_updated INTEGER %s
        );
        CREATE TABLE message(
          id TEXT PRIMARY KEY, session_id TEXT,
          data TEXT, time_created INTEGER
        );
        CREATE TABLE part(
          id INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT, data TEXT
        );
        """
        % (extra_col_def or "")
    )
    conn.execute(
        "INSERT INTO session(id, directory, title, time_created, time_updated"
        f"{', ' + extra_col_name if extra_col_name else ''})"
        f" VALUES(?,?,?,?,?{', ' + extra_col_val if extra_col_name else ''})",
        (sess_id, cwd, "数据库会话标题", T0, T0 + 9000),
    )
    msgs = [
        (
            msg_id,
            sess_id,
            orjson.dumps(
                {"role": "user", "time": {"created": T0 + 100},
                 "contextSnapshot": {"envInfo": {"cwd": cwd}}}
            ).decode(),
            T0 + 100,
        ),
        (
            msg_id + "b",
            sess_id,
            orjson.dumps(
                {"role": "assistant", "time": {"created": T0 + 900},
                 "path": {"cwd": cwd}}
            ).decode(),
            T0 + 900,
        ),
    ]
    for mid, sid_, data, ts in msgs:
        conn.execute(
            "INSERT INTO message(id, session_id, data, time_created) VALUES(?,?,?,?)",
            (mid, sid_, data, ts),
        )
    parts = [
        (msg_id, {"type": "text", "text": "查询今天的构建失败原因"}),
        (
            msg_id + "b",
            {"type": "reasoning", "text": "查看 CI 日志"},
        ),
        (
            msg_id + "b",
            {
                "type": "tool",
                "callID": "call_z1",
                "tool": "Read",
                "state": {
                    "status": "completed",
                    "input": {"filePath": "/home/user/ci.log"},
                    "output": "ERROR at ch011",
                },
            },
        ),
        (msg_id + "b", {"type": "text", "text": "找到 ch011 的报错了"}),
    ]
    for message_id, data in parts:
        conn.execute(
            "INSERT INTO part(message_id, data) VALUES(?,?)",
            (message_id, orjson.dumps(data).decode()),
        )
    if sub_sess_id:
        conn.execute(
            "INSERT INTO session(id, directory, title, time_created, time_updated"
            f"{', ' + extra_col_name if extra_col_name else ''})"
            f" VALUES(?,?,?,?,?{', ' + extra_col_val if extra_col_name else ''})",
            (sub_sess_id, cwd, "", T0, T0 + 9500),
        )
    conn.commit()
    conn.close()
    return {"session_id": sess_id, "db": str(db_path)}


def build_zcode(root: Path) -> dict:
    db = root / "zcode" / "db.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    sess = "sess_55555555-aaaa-4bbb-8ccc-000000000005"
    sub = "sess_subagent_agent_66666666-aaaa-4bbb-8ccc-000000000006"
    return _build_sqlite_agent(
        db,
        sess_id=sess,
        cwd="/home/user/zcode-work",
        extra_col_def=", task_type TEXT",
        extra_col_name="task_type",
        extra_col_val="'interactive'",
        msg_id="msg_abcdef12_" + str(uuid.uuid4()),
        sub_sess_id=sub,
    )


def build_opencode(root: Path) -> dict:
    db = root / "opencode" / "opencode.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    sess = "ses_77777777aaaaHHZY"
    return _build_sqlite_agent(
        db,
        sess_id=sess,
        cwd="/home/user/oc-project",
        extra_col_def=", parent_id TEXT",
        extra_col_name="parent_id",
        extra_col_val="NULL",
        msg_id="msg_88888888" + str(uuid.uuid4()),
        sub_sess_id=None,
    )


# -- pytest fixtures ------------------------------------------------------------------


@pytest.fixture()
def agents_root(tmp_path: Path) -> Path:
    root = tmp_path / "agents"
    build_claude(root)
    build_qoder(root)
    build_kimi(root)
    build_dsh(root)
    build_zcode(root)
    build_opencode(root)
    return root


@pytest.fixture()
def cfg(tmp_path: Path) -> Config:
    return _config(tmp_path)


@pytest.fixture()
def built(tmp_path: Path) -> dict:
    root = tmp_path / "agents"
    return {
        "claude": build_claude(root),
        "qoder": build_qoder(root),
        "kimi": build_kimi(root),
        "dsh": build_dsh(root),
        "zcode": build_zcode(root),
        "opencode": build_opencode(root),
    }
