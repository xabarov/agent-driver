"""Server state survives a 'restart' when backed by a SqliteRecordStore."""

from __future__ import annotations

from typing import Any

from starlette.testclient import TestClient

from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.persistence.record_store import SqliteRecordStore
from agent_driver.sdk import ToolSet, create_agent
from agent_driver.server.app import create_app


def _app(store: SqliteRecordStore) -> TestClient:
    agent = create_agent(
        provider=FakeProvider(response_text="durable answer"), tools=ToolSet.only()
    )
    return TestClient(create_app(agent, enable_a2a=True, record_store=store))


def _rpc(method: str, params: dict[str, Any]) -> dict:
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}


def test_stored_response_survives_restart(tmp_path: Any) -> None:
    path = str(tmp_path / "server.db")
    # First "process": create a stored response.
    store1 = SqliteRecordStore(path=path)
    client1 = _app(store1)
    created = client1.post(
        "/v1/responses", json={"model": "m", "input": "remember"}
    ).json()
    rid = created["id"]
    store1.close()

    # Second "process": fresh app + fresh store on the same file.
    store2 = SqliteRecordStore(path=path)
    client2 = _app(store2)
    got = client2.get(f"/v1/responses/{rid}")
    assert got.status_code == 200
    assert got.json()["id"] == rid
    store2.close()


def test_session_history_survives_restart(tmp_path: Any) -> None:
    path = str(tmp_path / "server.db")

    class _Spy(FakeProvider):
        def __init__(self) -> None:
            super().__init__(response_text="noted")
            self.seen: list[str] = []

        async def complete(self, request: Any) -> Any:
            self.seen = [m.content or "" for m in request.messages]
            return await super().complete(request)

    store1 = SqliteRecordStore(path=path)
    spy1 = _Spy()
    c1 = TestClient(
        create_app(
            create_agent(provider=spy1, tools=ToolSet.only()), record_store=store1
        )
    )
    c1.post(
        "/v1/chat/completions",
        json={
            "model": "m",
            "messages": [{"role": "user", "content": "my name is Zed"}],
        },
        headers={"X-Session-Id": "sess-1"},
    )
    store1.close()

    # Restart: new store + agent on the same file; the prior turn is replayed.
    store2 = SqliteRecordStore(path=path)
    spy2 = _Spy()
    c2 = TestClient(
        create_app(
            create_agent(provider=spy2, tools=ToolSet.only()), record_store=store2
        )
    )
    c2.post(
        "/v1/chat/completions",
        json={
            "model": "m",
            "messages": [{"role": "user", "content": "what is my name?"}],
        },
        headers={"X-Session-Id": "sess-1"},
    )
    assert "my name is Zed" in " ".join(spy2.seen)
    store2.close()


def test_a2a_task_survives_restart(tmp_path: Any) -> None:
    path = str(tmp_path / "server.db")
    store1 = SqliteRecordStore(path=path)
    client1 = _app(store1)
    task = client1.post(
        "/a2a",
        json=_rpc(
            "message/send",
            {"message": {"role": "user", "parts": [{"kind": "text", "text": "hi"}]}},
        ),
    ).json()["result"]
    task_id = task["id"]
    store1.close()

    store2 = SqliteRecordStore(path=path)
    client2 = _app(store2)
    got = client2.post("/a2a", json=_rpc("tasks/get", {"id": task_id}))
    assert got.status_code == 200
    assert got.json()["result"]["id"] == task_id
    store2.close()
