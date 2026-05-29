from __future__ import annotations


async def test_providers(client) -> None:
    response = await client.get("/api/providers")
    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "fake"
    assert payload["status"]["provider_name"] == "fake"
    assert payload["status"]["healthy"] is True

