"""Offline tests for the MCP Streamable-HTTP transport (Phase 3)."""

from __future__ import annotations

from typing import Any

from starlette.testclient import TestClient

from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.mcp_server.http import SESSION_HEADER, create_mcp_app
from agent_driver.sdk import ToolSet, create_agent
from agent_driver.server.app import create_app


def _client(*, api_key: str | None = None) -> TestClient:
    agent = create_agent(
        provider=FakeProvider(response_text="mcp answer"), tools=ToolSet.only()
    )
    return TestClient(create_mcp_app(agent, api_key=api_key))


def _rpc(method: str, params: dict[str, Any] | None = None, *, id: Any = 1) -> dict:
    body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if id is not None:
        body["id"] = id
    if params is not None:
        body["params"] = params
    return body


def test_initialize_mints_session_and_returns_capabilities() -> None:
    client = _client()
    resp = client.post(
        "/mcp", json=_rpc("initialize", {"protocolVersion": "2025-03-26"})
    )
    assert resp.status_code == 200
    assert resp.headers.get(SESSION_HEADER)
    data = resp.json()
    assert data["id"] == 1
    assert "protocolVersion" in data["result"]
    assert data["result"]["serverInfo"]["name"]
    assert "tools" in data["result"]["capabilities"]


def test_tools_list() -> None:
    client = _client()
    resp = client.post("/mcp", json=_rpc("tools/list"))
    assert resp.status_code == 200
    names = {t["name"] for t in resp.json()["result"]["tools"]}
    assert {"agent_query", "session_send", "session_history"} <= names


def test_tools_call_agent_query() -> None:
    client = _client()
    resp = client.post(
        "/mcp",
        json=_rpc("tools/call", {"name": "agent_query", "arguments": {"input": "hi"}}),
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["isError"] is False
    assert result["content"][0]["text"] == "mcp answer"


def test_notification_returns_202() -> None:
    client = _client()
    # No "id" -> notification -> no response body, 202 Accepted.
    resp = client.post("/mcp", json=_rpc("notifications/initialized", id=None))
    assert resp.status_code == 202
    assert resp.content == b""


def test_batch_request() -> None:
    client = _client()
    batch = [_rpc("ping", id=1), _rpc("tools/list", id=2)]
    resp = client.post("/mcp", json=batch)
    assert resp.status_code == 200
    payload = resp.json()
    assert isinstance(payload, list)
    assert {item["id"] for item in payload} == {1, 2}


def test_unknown_method_is_jsonrpc_error() -> None:
    client = _client()
    resp = client.post("/mcp", json=_rpc("does/not/exist"))
    assert resp.status_code == 200
    assert resp.json()["error"]["code"] == -32601


def test_bad_json_is_parse_error() -> None:
    client = _client()
    resp = client.post(
        "/mcp", content="{nope", headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == -32700


def test_get_not_allowed() -> None:
    client = _client()
    resp = client.get("/mcp")
    assert resp.status_code == 405
    assert "POST" in resp.headers.get("allow", "")


def test_delete_terminates_session() -> None:
    client = _client()
    init = client.post("/mcp", json=_rpc("initialize"))
    session_id = init.headers[SESSION_HEADER]
    resp = client.request("DELETE", "/mcp", headers={SESSION_HEADER: session_id})
    assert resp.status_code == 204


def test_auth_required() -> None:
    client = _client(api_key="sekret")
    body = _rpc("tools/list")
    assert client.post("/mcp", json=body).status_code == 401
    ok = client.post("/mcp", json=body, headers={"Authorization": "Bearer sekret"})
    assert ok.status_code == 200


def test_mounted_on_openai_app() -> None:
    # enable_mcp mounts /mcp on the same ASGI app as /v1/...
    agent = create_agent(provider=FakeProvider(response_text="x"), tools=ToolSet.only())
    client = TestClient(create_app(agent, enable_mcp=True))
    assert client.get("/healthz").status_code == 200
    assert client.post("/mcp", json=_rpc("tools/list")).status_code == 200
