"""REST API tests via TestClient."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sessiondex.api import create_app
from sessiondex.index import Index
from sessiondex.provider import build_providers
from sessiondex.scanner import scan


@pytest.fixture()
def client(cfg, built):
    index = Index(cfg.index_path)
    scan(index, cfg=cfg, providers=build_providers(cfg))
    index.close()
    app = create_app(cfg)
    return TestClient(app)


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["sessions"] >= 7
    assert len(body["agents"]) == 6


def test_search_endpoint_combinations(client):
    # agent + keyword + role
    r = client.get("/api/search", params={
        "q": "ch011", "agent": "zcode", "role": "assistant",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["agent"] == "zcode" and item["role"] == "assistant"
    assert item["session"]["cwd"] == "/home/user/zcode-work"

    # date filter excludes everything
    r = client.get("/api/search", params={
        "q": "ch011", "date_from": "2020-01-01", "date_to": "2020-01-02",
    })
    assert r.json()["total"] == 0

    # relative dates accepted
    r = client.get("/api/search", params={"date_from": "-30d", "limit": 3})
    assert r.status_code == 200

    # invalid date rejected
    r = client.get("/api/search", params={"date_from": "nope"})
    assert r.status_code == 422

    # tool filter
    r = client.get("/api/search", params={"tool": "EnterWorktree"})
    assert r.json()["items"][0]["agent"] == "qoder"


def test_sessions_endpoints(client, built):
    r = client.get("/api/sessions", params={"q": "ch011"})
    body = r.json()
    assert body["total"] >= 3

    sid = built["claude"]["session_id"]
    r = client.get(f"/api/sessions/claude/{sid}")
    assert r.status_code == 200
    assert r.json()["cwd"] == "/home/user/proj"

    r = client.get(f"/api/sessions/claude/{sid}/messages", params={"limit": 2})
    body = r.json()
    assert body["total"] == 4 and body["returned"] == 2

    r = client.get(f"/api/sessions/claude/{sid}/messages",
                   params={"role": "user"})
    assert all(m["role"] == "user" for m in r.json()["messages"])

    r = client.get("/api/sessions/claude/unknown")
    assert r.status_code == 404

    # around window
    r = client.get("/api/search", params={"q": "ch011", "agent": "claude"})
    msg_id = r.json()["items"][0]["id"]
    r = client.get(f"/api/sessions/claude/{sid}/messages",
                   params={"around": msg_id, "context": 1})
    body = r.json()
    assert 1 <= body["returned"] <= 3


def test_messages_read_endpoint(client):
    r = client.get("/api/search", params={"q": "构建失败"})
    loc = r.json()["items"][0]
    r = client.post("/api/messages/read", json={
        "agent": loc["agent"], "locator": loc["locator"],
    })
    assert r.status_code == 200
    assert "message" in r.json()


def test_agents_and_tools(client):
    assert len(client.get("/api/agents").json()) == 6
    tools = client.get("/api/tools", params={"agent": "kimi"}).json()
    assert {t["tool"] for t in tools} == {"Bash"}


def test_scan_endpoint_incremental(client):
    r = client.post("/api/scan")
    assert r.status_code == 200
    body = r.json()
    assert body["agents"]["claude"]["sources_scanned"] == 0  # nothing changed
    assert body["messages_indexed"] == 0


def test_token_auth(built, cfg):
    import dataclasses

    index = Index(cfg.index_path)
    scan(index, cfg=cfg, providers=build_providers(cfg))
    index.close()
    protected = dataclasses.replace(cfg, token="s3cret")
    app = create_app(protected)
    c = TestClient(app)
    assert c.get("/api/agents").status_code == 401
    assert c.get("/api/health").status_code == 200
    ok = c.get("/api/agents", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200
