from __future__ import annotations


async def test_sessions_crud(client) -> None:
    create_response = await client.post("/api/sessions", json={})
    assert create_response.status_code == 200
    created = create_response.json()
    session_id = created["session_id"]

    list_response = await client.get("/api/sessions")
    assert list_response.status_code == 200
    ids = {item["session_id"] for item in list_response.json()["sessions"]}
    assert session_id in ids

    get_response = await client.get(f"/api/sessions/{session_id}")
    assert get_response.status_code == 200
    assert get_response.json()["session_id"] == session_id

    delete_response = await client.delete(f"/api/sessions/{session_id}")
    assert delete_response.status_code == 200
    assert delete_response.json()["ok"] is True

    missing_response = await client.get(f"/api/sessions/{session_id}")
    assert missing_response.status_code == 404

