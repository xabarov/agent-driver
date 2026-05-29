from __future__ import annotations


async def test_tools_default_preset(client) -> None:
    response = await client.get("/api/tools")
    assert response.status_code == 200
    payload = response.json()
    names = {item["name"] for item in payload["tools"]}
    assert names == {"web_fetch", "web_search"}
    assert "read_file" not in names
    assert "bash" not in names
    assert payload["workspace"]["mode"] == "session"


async def test_tools_off_preset_query(client) -> None:
    response = await client.get("/api/tools", params={"preset": "off"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["tools"] == []


async def test_tools_web_search_preset_only_shows_search(client) -> None:
    response = await client.get("/api/tools", params={"preset": "web_search"})
    assert response.status_code == 200
    names = {item["name"] for item in response.json()["tools"]}
    assert names == {"web_search"}


async def test_tools_web_fetch_preset_only_shows_fetch(client) -> None:
    response = await client.get("/api/tools", params={"preset": "web_fetch"})
    assert response.status_code == 200
    names = {item["name"] for item in response.json()["tools"]}
    assert names == {"web_fetch"}


async def test_tools_legacy_dev_preset_still_hides_filesystem_from_public_endpoint(client) -> None:
    response = await client.get("/api/tools", params={"preset": "dev"})
    assert response.status_code == 200
    names = {item["name"] for item in response.json()["tools"]}
    assert names == {"web_fetch", "web_search"}


async def test_workspace_sample_import_populates_session_workspace(client) -> None:
    response = await client.post("/api/workspace/sample", params={"session_id": "session_sample"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert "README.md" in payload["files"]
    assert payload["workspace"]["sessionId"] == "session_sample"
    assert payload["workspace"]["fileCount"] >= 3
