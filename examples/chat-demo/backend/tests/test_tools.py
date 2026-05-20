from __future__ import annotations


async def test_tools_default_preset(client) -> None:
    response = await client.get("/api/tools")
    assert response.status_code == 200
    payload = response.json()
    names = {item["name"] for item in payload["tools"]}
    assert "web_search" in names
    assert "read_file" in names
    assert "bash" not in names


async def test_tools_off_preset_query(client) -> None:
    response = await client.get("/api/tools", params={"preset": "off"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["tools"] == []
