from __future__ import annotations

from app.api.chat import _chat_tool_policy, _effective_chat_preset
from app.config import Settings
from app.schemas.chat import ChatMessageRequest
from app.services.agent_factory import _tool_config_from_preset

from agent_driver.runtime.chat_policy import initial_tool_choice_for_chat


async def test_tools_default_preset(client) -> None:
    response = await client.get("/api/tools")
    assert response.status_code == 200
    payload = response.json()
    names = {item["name"] for item in payload["tools"]}
    assert names == {
        "agent_tool",
        "python",
        "skill_tool",
        "skill_view",
        "web_fetch",
        "web_search",
    }
    assert "read_file" not in names
    assert "bash" not in names
    assert payload["workspace"]["mode"] == "session"


async def test_tools_off_preset_query(client) -> None:
    response = await client.get("/api/tools", params={"preset": "off"})
    assert response.status_code == 200
    payload = response.json()
    names = {item["name"] for item in payload["tools"]}
    assert names == {"agent_tool", "python"}


async def test_tools_web_search_preset_only_shows_search(client) -> None:
    response = await client.get("/api/tools", params={"preset": "web_search"})
    assert response.status_code == 200
    names = {item["name"] for item in response.json()["tools"]}
    assert names == {"agent_tool", "python", "web_search"}


async def test_tools_web_fetch_preset_only_shows_fetch(client) -> None:
    response = await client.get("/api/tools", params={"preset": "web_fetch"})
    assert response.status_code == 200
    names = {item["name"] for item in response.json()["tools"]}
    assert names == {"agent_tool", "python", "web_fetch"}


async def test_tools_agents_preset_shows_agent_tool(client) -> None:
    response = await client.get("/api/tools", params={"preset": "agents"})
    assert response.status_code == 200
    names = {item["name"] for item in response.json()["tools"]}
    assert names == {"agent_tool", "python"}


async def test_tools_deep_research_preset_hides_agent_tool(client) -> None:
    response = await client.get("/api/tools", params={"preset": "deep_research"})
    assert response.status_code == 200
    names = {item["name"] for item in response.json()["tools"]}
    assert "agent_tool" not in names
    assert "python" not in names
    assert {"skill_tool", "skill_view", "web_fetch", "web_search"}.issubset(names)


async def test_tools_legacy_dev_preset_still_hides_filesystem_from_public_endpoint(
    client,
) -> None:
    response = await client.get("/api/tools", params={"preset": "dev"})
    assert response.status_code == 200
    names = {item["name"] for item in response.json()["tools"]}
    assert "read_file" not in names
    assert "write_file" not in names
    assert "bash" not in names


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


def test_chat_tool_policy_treats_report_contract_as_deliverable_request() -> None:
    """Task contract and hard deliverable policy should not disagree."""
    policy = _chat_tool_policy(
        body=ChatMessageRequest(
            message="составь план поиска информации и написания реферата по Fender",
        ),
        settings=Settings(),
    )
    assert policy.metadata["task_contract"]["kind"] == "deliverable"
    assert policy.metadata["deliverable_request"]["enabled"] is True
    assert "ask_user_question" in set(policy.denied_tools or ())


def test_initial_tool_choice_keeps_research_deliverable_auto() -> None:
    policy = _chat_tool_policy(
        body=ChatMessageRequest(
            message="составь план поиска в интернете и написания реферата"
        ),
        settings=Settings(),
    )
    assert initial_tool_choice_for_chat(policy=policy, preset="web") is None
    assert initial_tool_choice_for_chat(policy=policy, preset="off") is None


def test_chat_tool_policy_denies_clarification_for_research_request() -> None:
    policy = _chat_tool_policy(
        body=ChatMessageRequest(
            message="найди в интернете краткую информацию о Fender Jazzmaster",
        ),
        settings=Settings(),
    )

    assert policy.metadata["task_contract"]["kind"] == "research"
    assert policy.metadata["research_request"]["enabled"] is True
    assert policy.denied_tools == ["ask_user_question"]
    assert initial_tool_choice_for_chat(policy=policy, preset="web") is None


def test_chat_tool_policy_accepts_deep_research_mode() -> None:
    policy = _chat_tool_policy(
        body=ChatMessageRequest(
            message="найди источники и подготовь отчет",
            research_depth="deep_parallel_research",
        ),
        settings=Settings(),
    )

    assert policy.metadata["deep_research_mode"]["enabled"] is True
    assert policy.metadata["task_contract"]["research_depth"] == (
        "deep_parallel_research"
    )
    assert policy.metadata["task_contract"]["requires_research"] is True


def test_deep_research_mode_uses_artifact_tool_preset() -> None:
    body = ChatMessageRequest(
        message="найди источники и подготовь отчет",
        tool_preset="web",
        research_depth="deep_parallel_research",
    )

    assert _effective_chat_preset(body) == "deep_research"


def test_deep_research_preset_includes_scoped_artifact_tools() -> None:
    config = _tool_config_from_preset("deep_research")

    assert set(config.tools) == {"skill_tool", "skill_view"}
    assert set(config.tool_packs) == {
        "web",
        "planning_progress",
        "filesystem_read",
        "filesystem_write",
        "artifacts",
    }
    assert "agent_tool" not in config.tools
    assert "shell" not in config.tool_packs
    assert "discovery" not in config.tool_packs
    assert config.allow_dangerous_tools is True
    assert config.enable_python is False


def test_chat_tool_policy_denies_web_tools_for_plan_only() -> None:
    policy = _chat_tool_policy(
        body=ChatMessageRequest(
            message="составь только план поиска информации по истории Fender, без реферата",
        ),
        settings=Settings(),
    )

    assert policy.metadata["task_contract"]["kind"] == "plan"
    assert policy.metadata["plan_only_request"]["enabled"] is True
    assert set(policy.denied_tools or ()) == {"web_search", "web_fetch"}
    assert initial_tool_choice_for_chat(policy=policy, preset="web") is None


def test_public_chat_presets_exclude_modal_plan_approval_tools() -> None:
    """Public chat can show live todo progress without plan approval loops."""
    for preset in (
        "off",
        "web_search",
        "web_fetch",
        "web",
        "agents",
        "safe",
        "workspace",
    ):
        config = _tool_config_from_preset(preset)
        selected = set(config.tools) | set(config.tool_packs)
        assert "planning_progress" in selected
        assert "planning" not in selected

    dev_config = _tool_config_from_preset("dev")
    assert "planning" in set(dev_config.tool_packs)


def test_public_chat_presets_include_agent_tool_without_dangerous_tools() -> None:
    for preset in ("off", "web_search", "web_fetch", "web", "agents"):
        config = _tool_config_from_preset(preset)
        assert "agent_tool" in set(config.tools)
        assert config.enable_python is True
        assert "shell" not in config.tool_packs
        assert "filesystem_write" not in config.tool_packs
        assert config.allow_dangerous_tools is False
