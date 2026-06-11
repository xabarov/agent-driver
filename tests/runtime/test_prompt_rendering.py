"""Prompt template registry and deterministic tool-doc rendering tests."""

from __future__ import annotations

import pytest

from agent_driver.contracts import (
    AgentProfile,
    AgentRunInput,
    PromptTemplate,
    ToolManifest,
)
from agent_driver.prompts.agent import react_chat_tool_policy
from agent_driver.runtime.single_agent.llm import (
    LlmRequestBuildContext,
    build_single_agent_llm_request,
)
from agent_driver.tools import (
    PromptTemplateRegistry,
    ToolRegistry,
    render_tool_doc,
    render_tool_docs,
    rendered_tool_docs_hash,
)
from agent_driver.tools.planning import register_planning_tool


def _sample_manifest() -> ToolManifest:
    return ToolManifest(
        name="lookup_tool",
        description="Lookup facts",
        args_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        output_type="object",
        output_schema={"type": "object", "properties": {"summary": {"type": "string"}}},
        remediation_hints=["retry with shorter query", "ask clarification"],
    )


def test_render_tool_doc_is_deterministic() -> None:
    """One manifest should render deterministically for profile."""
    manifest = _sample_manifest()
    first = render_tool_doc(manifest, AgentProfile.REACT_TEXT)
    second = render_tool_doc(manifest, AgentProfile.REACT_TEXT)
    assert first == second
    assert "name: lookup_tool" in first


def test_render_tool_docs_hash_is_stable() -> None:
    """Rendered docs hash stays stable for same sorted inputs."""
    one = _sample_manifest()
    two = ToolManifest(name="browse_tool", description="Browse pages")
    first = rendered_tool_docs_hash([one, two], AgentProfile.REACT_TEXT)
    second = rendered_tool_docs_hash([two, one], AgentProfile.REACT_TEXT)
    assert first == second
    assert len(first) == 64


def test_render_tool_doc_rejects_unsupported_profile() -> None:
    """Renderer should reject manifests unsupported for requested profile."""
    manifest = ToolManifest(
        name="json_tool",
        description="tool-calling only",
        supported_profiles=[AgentProfile.TOOL_CALLING],
    )
    with pytest.raises(ValueError):
        render_tool_doc(manifest, AgentProfile.CODE_AGENT)


def test_prompt_template_registry_render_and_hash() -> None:
    """Template registry should render and hash deterministic output."""
    registry = PromptTemplateRegistry()
    registry.register(
        PromptTemplate(
            template_id="react.default",
            version=1,
            profile=AgentProfile.REACT_TEXT,
            required_placeholders=["tools", "task"],
            body="TOOLS:\\n{{tools}}\\nTASK:\\n{{task}}",
        )
    )
    rendered = registry.render(
        template_id="react.default",
        profile=AgentProfile.REACT_TEXT,
        version=1,
        values={"tools": "- lookup_tool", "task": "answer user"},
    )
    assert rendered.template_id == "react.default"
    assert "TOOLS:" in rendered.rendered_text
    assert len(rendered.rendered_hash) == 64


def test_prompt_template_registry_missing_placeholder() -> None:
    """Registry should reject render calls missing required placeholders."""
    registry = PromptTemplateRegistry()
    registry.register(
        PromptTemplate(
            template_id="react.default",
            version=1,
            profile=AgentProfile.REACT_TEXT,
            required_placeholders=["tools", "task"],
            body="x {{tools}} y {{task}}",
        )
    )
    with pytest.raises(ValueError):
        registry.render(
            template_id="react.default",
            profile=AgentProfile.REACT_TEXT,
            version=1,
            values={"tools": "- only"},
        )


def test_render_tool_docs_profile_filtering() -> None:
    """Profile rendering includes only profile-compatible manifests."""
    manifests = [
        ToolManifest(name="common_tool", description="all"),
        ToolManifest(
            name="call_only_tool",
            description="tool-calling only",
            supported_profiles=[AgentProfile.TOOL_CALLING],
        ),
    ]
    text = render_tool_docs(manifests, AgentProfile.REACT_TEXT)
    assert "common_tool" in text
    assert "call_only_tool" not in text


def test_build_single_agent_llm_request_renders_code_agent_prompt() -> None:
    """Code-agent request builder should emit prompt_render payload."""
    run_input = AgentRunInput(
        input="Solve task",
        agent_id="agent",
        graph_preset="single_react",
        agent_profile=AgentProfile.CODE_AGENT,
    )
    request, payload = build_single_agent_llm_request(
        LlmRequestBuildContext(
            run_input=run_input,
            tool_docs="def calc(x: object) -> dict[str, object]",
            authorized_imports=("math",),
        )
    )
    assert payload["prompt_render"] is not None
    assert "final_answer(...)" in request.messages[-1].content


def test_react_chat_policy_guides_adaptive_plan_mode() -> None:
    """Chat policy should mirror Claude Code-like voluntary plan mode behavior."""
    policy = react_chat_tool_policy()
    assert (
        "Use `enter_plan_mode` proactively before non-trivial implementation" in policy
    )
    assert "Do not use approval plan mode for simple factual answers" in policy
    assert "writing deliverables such as essays, reports, drafts" in policy
    assert "напиши" in policy
    assert "Do not use `ask_user_question` as a way to avoid producing" in policy
    assert "force_planning_required" in policy
    assert "call `exit_plan_mode_v2`" in policy


def test_exit_plan_mode_schema_exposes_plan_content_fields() -> None:
    """Model-visible schema must include the fields the handler already accepts."""
    registry = ToolRegistry()
    register_planning_tool(registry)
    registered = registry.get("exit_plan_mode_v2")
    assert registered is not None
    properties = registered.manifest.args_schema["properties"]
    assert "content" in properties
    assert "plan" in properties
    assert "plan_id" in properties
    assert "path" in properties
