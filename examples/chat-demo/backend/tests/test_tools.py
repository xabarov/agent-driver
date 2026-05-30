from __future__ import annotations

from app.api.chat import _chat_tool_policy
from app.config import Settings
from app.schemas.chat import ChatMessageRequest
from app.services.agent_factory import _tool_config_from_preset


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


async def test_tools_legacy_dev_preset_still_hides_filesystem_from_public_endpoint(
    client,
) -> None:
    response = await client.get("/api/tools", params={"preset": "dev"})
    assert response.status_code == 200
    names = {item["name"] for item in response.json()["tools"]}
    assert names == {"web_fetch", "web_search"}


async def test_workspace_sample_import_populates_session_workspace(client) -> None:
    response = await client.post(
        "/api/workspace/sample", params={"session_id": "session_sample"}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert "README.md" in payload["files"]
    assert payload["workspace"]["sessionId"] == "session_sample"
    assert payload["workspace"]["fileCount"] >= 3


def test_chat_tool_policy_adds_adaptive_planning_hint() -> None:
    """Chat requests should carry adaptive planning metadata into the runtime."""
    policy = _chat_tool_policy(
        body=ChatMessageRequest(
            message="Add adaptive planning support and update runtime tests",
        ),
        settings=Settings(),
    )
    hint = policy.metadata["planning_hint"]
    assert isinstance(hint, dict)
    assert hint["level"] == "suggested"


def test_chat_tool_policy_adds_force_planning_mode() -> None:
    """Chat-demo env can choose the force-planning runtime mode."""
    policy = _chat_tool_policy(
        body=ChatMessageRequest(
            message="write a file",
            force_planning=True,
        ),
        settings=Settings(CHAT_DEMO_FORCE_PLANNING_MODE="prompt_only"),
    )
    force_planning = policy.metadata["force_planning"]
    assert isinstance(force_planning, dict)
    assert force_planning["enabled"] is True
    assert force_planning["mode"] == "prompt_only"


def test_chat_tool_policy_denies_clarification_tools_for_deliverable_request() -> None:
    """A direct final/draft request should not pause on another clarification."""
    policy = _chat_tool_policy(
        body=ChatMessageRequest(
            message="напиши реферат, не план, начни с введения",
        ),
        settings=Settings(),
    )
    assert policy.metadata["deliverable_request"]["enabled"] is True
    assert set(policy.denied_tools or ()) == {
        "ask_user_question",
        "enter_plan_mode",
        "exit_plan_mode_v2",
    }


def test_public_chat_presets_exclude_modal_plan_approval_tools() -> None:
    """Public chat can show live todo progress without plan approval loops."""
    for preset in ("off", "web_search", "web_fetch", "web", "safe", "workspace"):
        config = _tool_config_from_preset(preset)
        selected = set(config.tools) | set(config.tool_packs)
        assert "planning_progress" in selected
        assert "planning" not in selected

    dev_config = _tool_config_from_preset("dev")
    assert "planning" in set(dev_config.tool_packs)
