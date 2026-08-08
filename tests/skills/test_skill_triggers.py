"""Skills S5 — keyword-triggered skill hints + system-prompt injection."""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput
from agent_driver.llm.contracts import LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime.single_agent.types import RunnerConfig
from agent_driver.sdk import create_agent
from agent_driver.skills import build_skill_keyword_hints
from agent_driver.skills.parser import clear_skill_manifest_cache
from agent_driver.tools import ToolSet


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_skill_manifest_cache()
    yield
    clear_skill_manifest_cache()


def _write_skill(root, name: str, keywords: str | None) -> None:
    sd = root / name
    sd.mkdir(parents=True)
    kw = f"keywords: [{keywords}]\n" if keywords else ""
    (sd / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\n{kw}---\n# {name}\nbody\n", encoding="utf-8"
    )


# --- builder unit tests --------------------------------------------------------


def test_keyword_match_emits_hint(tmp_path) -> None:
    _write_skill(tmp_path, "pivot-builder", "pivot, groupby")
    block = build_skill_keyword_hints([str(tmp_path)], "Please build a pivot by region")
    assert block.startswith("## Skills matching this request")
    assert "**pivot-builder**" in block
    assert "matched: pivot" in block
    assert "skill_view(name='pivot-builder'" in block


def test_no_match_returns_empty(tmp_path) -> None:
    _write_skill(tmp_path, "pivot-builder", "pivot")
    assert build_skill_keyword_hints([str(tmp_path)], "hello world") == ""


def test_blank_input_returns_empty(tmp_path) -> None:
    _write_skill(tmp_path, "pivot-builder", "pivot")
    assert build_skill_keyword_hints([str(tmp_path)], "   ") == ""


def test_whole_word_only(tmp_path) -> None:
    _write_skill(tmp_path, "pivot-builder", "pivot")
    # substring inside a larger word must not trigger
    assert build_skill_keyword_hints([str(tmp_path)], "this is pivotal work") == ""


def test_case_insensitive(tmp_path) -> None:
    _write_skill(tmp_path, "pivot-builder", "Pivot")
    assert "pivot-builder" in build_skill_keyword_hints([str(tmp_path)], "make a PIVOT")


def test_skill_without_keywords_never_triggers(tmp_path) -> None:
    _write_skill(tmp_path, "chart", None)
    # even mentioning the skill name doesn't trigger without declared keywords
    assert build_skill_keyword_hints([str(tmp_path)], "make a chart") == ""


def test_caps_at_max_hints(tmp_path) -> None:
    for i in range(5):
        _write_skill(tmp_path, f"skill-{i}", "common")
    block = build_skill_keyword_hints([str(tmp_path)], "common request", max_hints=2)
    assert block.count("- **skill-") == 2


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
async def test_keyword_hint_injected_when_input_matches(tmp_path) -> None:
    _write_skill(tmp_path, "chart", "chart, graph")
    provider = _RecordingProvider()
    agent = create_agent(
        provider=provider,
        tools=ToolSet.only("skill_view"),
        config=RunnerConfig(skills_catalog_sources=(str(tmp_path),)),
    )
    await agent.run(
        AgentRunInput(
            input="make a chart of sales",
            run_id="run_kw_on",
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    text = _system_text(provider.requests[0])
    assert "## Skills matching this request" in text
    assert "**chart**" in text


@pytest.mark.asyncio
async def test_no_keyword_hint_when_input_does_not_match(tmp_path) -> None:
    _write_skill(tmp_path, "chart", "chart, graph")
    provider = _RecordingProvider()
    agent = create_agent(
        provider=provider,
        tools=ToolSet.only("skill_view"),
        config=RunnerConfig(skills_catalog_sources=(str(tmp_path),)),
    )
    await agent.run(
        AgentRunInput(
            input="summarize the rows",
            run_id="run_kw_off",
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    assert "## Skills matching this request" not in _system_text(provider.requests[0])
