from __future__ import annotations


async def test_health(client) -> None:
    response = await client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["provider"]["healthy"] is True
    assert payload["store_kind"] == "memory"

