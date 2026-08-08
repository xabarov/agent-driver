"""Skills S1 — tier-1 available-skills catalog block + system-prompt injection."""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput
from agent_driver.llm.contracts import LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime.single_agent.types import RunnerConfig
from agent_driver.sdk import create_agent
from agent_driver.skills import build_skills_catalog_block
from agent_driver.tools import ToolSet


def _write_skill(root, name: str, description: str, when_to_use: str = "") -> None:
    sd = root / name
    sd.mkdir(parents=True)
    wtu = f"when_to_use: {when_to_use}\n" if when_to_use else ""
    (sd / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{wtu}---\n# {name}\nBody.\n",
        encoding="utf-8",
    )


# --- builder unit tests --------------------------------------------------------


def test_catalog_renders_full_entries(tmp_path) -> None:
    _write_skill(tmp_path, "chart-builder", "Build charts", "Use for a chart or graph.")
    _write_skill(tmp_path, "pivot-maker", "Make pivots", "Use to summarize by group.")

    block = build_skills_catalog_block([str(tmp_path)], trusted_roots=[str(tmp_path)])

    assert block.startswith("## Available skills")
    assert "skill_view(name=<name>, base_dir=<base_dir>)" in block
    assert "chart-builder" in block and "pivot-maker" in block
    # Full tier keeps the summaries.
    assert "Use for a chart or graph." in block


def test_catalog_empty_sources_returns_empty() -> None:
    assert build_skills_catalog_block([]) == ""


def test_catalog_header_override(tmp_path) -> None:
    """A consumer (e.g. a localized product) can supply its own intro header."""
    _write_skill(tmp_path, "chart-builder", "Build charts", "Use for a chart.")
    block = build_skills_catalog_block(
        [str(tmp_path)], header="### Доступные skills\nЗагрузи нужный через skill_view."
    )
    assert block.startswith("### Доступные skills")
    assert "## Available skills" not in block
    assert "chart-builder" in block


def test_catalog_missing_dir_is_skipped(tmp_path) -> None:
    # A non-existent source must not raise — just yields no entries.
    assert build_skills_catalog_block([str(tmp_path / "nope")]) == ""


def test_catalog_dedupes_by_name_first_source_wins(tmp_path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write_skill(a, "dupe", "From A")
    _write_skill(b, "dupe", "From B")
    block = build_skills_catalog_block([str(a), str(b)])
    assert block.count("**dupe**") == 1
    assert "From A" in block and "From B" not in block


def test_catalog_degrades_to_names_only_under_budget(tmp_path) -> None:
    # Long summaries widen the gap between the full tier and the names-only tier so
    # the budget window lands cleanly on names-only regardless of the tmp path length.
    long_a = "ALPHA " * 30  # ~180 chars, capped by render at max_when_to_use=220
    long_b = "BETA " * 30
    _write_skill(tmp_path, "chart-builder", "d", long_a.strip())
    _write_skill(tmp_path, "pivot-maker", "d", long_b.strip())
    full = build_skills_catalog_block([str(tmp_path)], max_chars=100_000)
    names_only = build_skills_catalog_block([str(tmp_path)], max_chars=len(full) - 100)
    assert "chart-builder" in names_only and "pivot-maker" in names_only
    assert "Summaries omitted" in names_only
    assert "ALPHA" not in names_only and "BETA" not in names_only


def test_catalog_truncates_list_when_tiny_budget(tmp_path) -> None:
    for i in range(6):
        _write_skill(tmp_path, f"skill-{i}", "d", "s")
    block = build_skills_catalog_block([str(tmp_path)], max_chars=200)
    assert "more — use skill_tool to list all skills." in block


# --- system-prompt injection integration --------------------------------------


class _RecordingProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__(response_text="ok")
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        return await super().complete(request)


def _system_text(request: LlmRequest) -> str:
    return "\n".join(m.content or "" for m in request.messages)


@pytest.mark.asyncio
async def test_catalog_injected_into_system_prompt_when_configured(tmp_path) -> None:
    """S1: with sources configured AND skill_view available, the catalog appears in
    the system prompt so the model knows the skills exist."""
    _write_skill(tmp_path, "chart-builder", "Build charts", "Use for a chart.")
    provider = _RecordingProvider()
    agent = create_agent(
        provider=provider,
        tools=ToolSet.only("skill_view"),
        config=RunnerConfig(skills_catalog_sources=(str(tmp_path),)),
    )
    await agent.run(
        AgentRunInput(
            input="hi",
            run_id="run_catalog_on",
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    assert provider.requests
    text = _system_text(provider.requests[0])
    assert "## Available skills" in text
    assert "chart-builder" in text


@pytest.mark.asyncio
async def test_catalog_absent_without_skill_tool(tmp_path) -> None:
    """No skill_view/skill_tool in the surface → listing skills the model can't open
    would be noise, so the catalog is suppressed."""
    _write_skill(tmp_path, "chart-builder", "Build charts")
    provider = _RecordingProvider()
    agent = create_agent(
        provider=provider,
        tools=ToolSet.only(),
        config=RunnerConfig(skills_catalog_sources=(str(tmp_path),)),
    )
    await agent.run(
        AgentRunInput(
            input="hi",
            run_id="run_catalog_notool",
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    assert provider.requests
    assert "## Available skills" not in _system_text(provider.requests[0])


@pytest.mark.asyncio
async def test_catalog_absent_when_unconfigured(tmp_path) -> None:
    """Default (no sources) → no catalog (historical behaviour unchanged)."""
    provider = _RecordingProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only("skill_view"))
    await agent.run(
        AgentRunInput(
            input="hi",
            run_id="run_catalog_off",
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    assert provider.requests
    assert "## Available skills" not in _system_text(provider.requests[0])
