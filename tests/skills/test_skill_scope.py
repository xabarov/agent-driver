"""Skills S6 — runtime tool-scoping by a pinned skill's allowed_tools."""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput
from agent_driver.llm.contracts import LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime.single_agent.llm_step.request import (
    _combine_request_allowlists,
)
from agent_driver.runtime.single_agent.types import RunnerConfig
from agent_driver.sdk import create_agent
from agent_driver.skills import resolve_skill_allowed_tools
from agent_driver.skills.parser import clear_skill_manifest_cache
from agent_driver.tools import ToolSet


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_skill_manifest_cache()
    yield
    clear_skill_manifest_cache()


def _write_skill(root, name: str, allowed_tools: str | None) -> None:
    sd = root / name
    sd.mkdir(parents=True)
    at = f"allowed_tools: [{allowed_tools}]\n" if allowed_tools else ""
    (sd / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\n{at}---\n# {name}\nbody\n", encoding="utf-8"
    )


# --- resolver unit -------------------------------------------------------------


def test_resolve_returns_declared_tools(tmp_path) -> None:
    _write_skill(tmp_path, "reader", "read_file, grep_search")
    assert resolve_skill_allowed_tools([str(tmp_path)], "reader") == [
        "read_file",
        "grep_search",
    ]


def test_resolve_missing_skill_returns_none(tmp_path) -> None:
    _write_skill(tmp_path, "reader", "read_file")
    assert resolve_skill_allowed_tools([str(tmp_path)], "nope") is None


def test_resolve_skill_without_allowed_tools_returns_none(tmp_path) -> None:
    _write_skill(tmp_path, "reader", None)
    assert resolve_skill_allowed_tools([str(tmp_path)], "reader") is None


def test_resolve_blank_name_returns_none(tmp_path) -> None:
    _write_skill(tmp_path, "reader", "read_file")
    assert resolve_skill_allowed_tools([str(tmp_path)], "  ") is None


# --- allowlist combination unit ------------------------------------------------


def test_combine_allowlists() -> None:
    assert _combine_request_allowlists(None, None) is None
    assert _combine_request_allowlists(("a", "b"), None) == ("a", "b")
    assert _combine_request_allowlists(None, ("a", "b")) == ("a", "b")
    assert _combine_request_allowlists(("a", "b", "c"), ("b", "c", "d")) == ("b", "c")
    assert _combine_request_allowlists(("a",), ("b",)) == ()  # disjoint → locked down


# --- integration: skill_scope narrows the request tool surface -----------------


class _RecordingProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__(response_text="ok")
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        return await super().complete(request)


def _tool_names(request: LlmRequest) -> list[str]:
    names: list[str] = []
    for tool in request.tools or []:
        if isinstance(tool, dict):
            name = (tool.get("function") or {}).get("name") or tool.get("name")
        else:
            name = getattr(tool, "name", None)
        if name:
            names.append(name)
    return sorted(names)


async def _run(tmp_path, scope: str | None) -> list[str]:
    provider = _RecordingProvider()
    agent = create_agent(
        provider=provider,
        tools=ToolSet.only("read_file", "grep_search", "skill_view"),
        config=RunnerConfig(skills_catalog_sources=(str(tmp_path),)),
    )
    tool_policy = {"metadata": {"skill_scope": scope}} if scope else {}
    await agent.run(
        AgentRunInput(
            input="hi",
            run_id=f"scope_{scope}",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=tool_policy,
        )
    )
    return _tool_names(provider.requests[0])


@pytest.mark.asyncio
async def test_skill_scope_narrows_tool_surface(tmp_path) -> None:
    _write_skill(tmp_path, "reader", "read_file")
    # No scope → the full configured surface is visible.
    assert await _run(tmp_path, None) == ["grep_search", "read_file", "skill_view"]
    # Scoped → only the skill's tools + the skill-load tool remain; grep_search hidden.
    scoped = await _run(tmp_path, "reader")
    assert "read_file" in scoped
    assert "skill_view" in scoped  # kept so the model can open the scoped skill
    assert "grep_search" not in scoped


@pytest.mark.asyncio
async def test_missing_scope_skill_does_not_narrow(tmp_path) -> None:
    _write_skill(tmp_path, "reader", "read_file")
    assert await _run(tmp_path, "does-not-exist") == [
        "grep_search",
        "read_file",
        "skill_view",
    ]
