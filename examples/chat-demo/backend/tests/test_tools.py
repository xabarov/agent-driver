from __future__ import annotations


async def test_tools_default_preset(client) -> None:
    response = await client.get("/api/tools")
    assert response.status_code == 200
    payload = response.json()
    names = {item["name"] for item in payload["tools"]}
    assert "web_search" in names
    assert "todo_write" in names
    assert "read_file" not in names
    assert "bash" not in names
    assert payload["workspace"]["mode"] == "session"


async def test_tools_off_preset_query(client) -> None:
    response = await client.get("/api/tools", params={"preset": "off"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["tools"] == []


async def test_tools_workspace_preset_includes_readonly_filesystem(client) -> None:
    response = await client.get("/api/tools", params={"preset": "workspace", "session_id": "session_a"})
    assert response.status_code == 200
    payload = response.json()
    names = {item["name"] for item in payload["tools"]}
    assert "web_search" in names
    assert "read_file" in names
    assert "grep_search" in names
    assert "file_write" not in names
    assert payload["workspace"]["sessionId"] == "session_a"


async def test_tools_dev_preset_includes_workspace_write_and_shell(client) -> None:
    response = await client.get("/api/tools", params={"preset": "dev"})
    assert response.status_code == 200
    names = {item["name"] for item in response.json()["tools"]}
    assert {"web_search", "read_file", "file_write", "bash"}.issubset(names)


async def test_workspace_sample_import_populates_session_workspace(client) -> None:
    response = await client.post("/api/workspace/sample", params={"session_id": "session_sample"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert "README.md" in payload["files"]
    assert payload["workspace"]["sessionId"] == "session_sample"
    assert payload["workspace"]["fileCount"] >= 3
