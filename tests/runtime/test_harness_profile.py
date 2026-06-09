"""D4: declarative harness profiles (prompt slots, tool exclusion, overrides)."""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput, HarnessProfile
from agent_driver.harness import (
    apply_system_slots,
    apply_tool_overrides,
    profile_excluded_tools,
    select_harness_profile,
)
from agent_driver.llm.contracts import LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import RunnerConfig
from agent_driver.tools import ToolSet
from agent_driver.sdk import create_agent

# --- pure helpers -----------------------------------------------------------


def _profile(**kw) -> HarnessProfile:
    return HarnessProfile(name=kw.pop("name", "p"), **kw)


def test_select_first_match_wins() -> None:
    a = _profile(name="a", match_models=("gpt-*",))
    b = _profile(name="b", match_models=("claude-*",))
    assert select_harness_profile((a, b), "claude-opus-4").name == "b"
    assert select_harness_profile((a, b), "gpt-4o").name == "a"


def test_empty_match_models_is_wildcard_default() -> None:
    default = _profile(name="default")
    specific = _profile(name="claude", match_models=("claude-*",))
    # Default (no patterns) matches anything and, placed first, wins.
    assert select_harness_profile((default, specific), "claude-x").name == "default"
    # Placed last, it still catches non-matching models.
    assert select_harness_profile((specific, default), "gpt-4").name == "default"


def test_select_returns_none_when_no_match() -> None:
    assert (
        select_harness_profile((_profile(match_models=("claude-*",)),), "gpt-4") is None
    )


def test_match_is_case_insensitive() -> None:
    p = _profile(match_models=("Claude-*",))
    assert select_harness_profile((p,), "claude-opus") is not None


def test_apply_system_slots_wraps_prefix_and_suffix() -> None:
    p = _profile(system_prefix="PRE", system_suffix="POST")
    assert apply_system_slots("BASE", p) == "PRE\n\nBASE\n\nPOST"
    # Empty slots collapse cleanly.
    assert apply_system_slots("BASE", _profile()) == "BASE"


def test_profile_excluded_tools_merges_into_denied() -> None:
    p = _profile(excluded_tools=("web_fetch", "bash"))
    assert profile_excluded_tools(p, ("python",)) == ("python", "web_fetch", "bash")
    # No profile / no exclusions returns the deny tuple unchanged.
    assert profile_excluded_tools(None, ("python",)) == ("python",)
    assert profile_excluded_tools(_profile(), ("python",)) == ("python",)


def test_apply_tool_overrides_rewrites_only_named_descriptions() -> None:
    tools = [
        {"type": "function", "function": {"name": "a", "description": "old-a"}},
        {"type": "function", "function": {"name": "b", "description": "old-b"}},
    ]
    p = _profile(tool_description_overrides={"a": "new-a", "missing": "x"})
    out = apply_tool_overrides(tools, p)
    assert out[0]["function"]["description"] == "new-a"
    assert out[1]["function"]["description"] == "old-b"
    # Inputs are not mutated.
    assert tools[0]["function"]["description"] == "old-a"


# --- end-to-end through the runner ------------------------------------------


class _CapturingProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__(response_text="done")
        self.system_text: str = ""
        self.tools: list[dict] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.system_text = " ".join(
            m.content for m in request.messages if m.role == "system"
        )
        self.tools = list(request.tools)
        return await super().complete(request)


@pytest.mark.asyncio
async def test_profile_shapes_request_end_to_end() -> None:
    provider = _CapturingProvider()
    profile = HarnessProfile(
        name="default",
        system_suffix="Always answer in one sentence.",
        excluded_tools=("web_fetch",),
        tool_description_overrides={"web_search": "Search the web (profiled)."},
    )
    agent = create_agent(
        provider=provider,
        tools=ToolSet.only("web_search", "web_fetch"),
        config=RunnerConfig(harness_profiles=(profile,)),
    )
    await agent.run(
        AgentRunInput(
            input="hi",
            run_id="r1",
            agent_id="a",
            thread_id="t",
            graph_preset="single_react",
        )
    )
    # System slot applied.
    assert "Always answer in one sentence." in provider.system_text
    # Excluded tool never surfaced; surviving tool's description overridden.
    names = {t["function"]["name"] for t in provider.tools}
    assert "web_fetch" not in names
    assert "web_search" in names
    search = next(t for t in provider.tools if t["function"]["name"] == "web_search")
    assert search["function"]["description"] == "Search the web (profiled)."
