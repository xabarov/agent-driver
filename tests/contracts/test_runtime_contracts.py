"""Runtime contract validation tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_driver.contracts import (
    AgentProfile,
    AgentRunInput,
    AgentRunOutput,
    ChatMessage,
    CheckpointRef,
    InterruptReason,
    InterruptRequest,
    ResumeAction,
    ResumeCommand,
    RunStatus,
    RuntimeEventType,
    SerializationMode,
    SubagentGroup,
    SubagentJoinPolicy,
    TerminalReason,
    ToolPolicyInput,
    ToolPolicyMode,
    ToolRisk,
    new_runtime_event,
)
from agent_driver.contracts.memory import MemoryProjection, MemoryStep
from agent_driver.contracts.profiles import PromptRenderResult
from agent_driver.contracts.serialization import ExecutorSerializationPolicy


def test_agent_run_input_accepts_input_only() -> None:
    """Accept plain input-only request payload."""
    req = AgentRunInput(
        input="hello",
        agent_id="agent.default",
        graph_preset="single_react",
        model_role="default",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
    )
    assert req.input == "hello"
    assert req.messages == []
    assert req.agent_profile == AgentProfile.REACT_TEXT


def test_agent_run_input_accepts_resume_only() -> None:
    """Accept resume-only request payload."""
    resume = ResumeCommand(interrupt_id="int_1", action=ResumeAction.APPROVE)
    req = AgentRunInput(
        resume=resume,
        agent_id="agent.default",
        graph_preset="single_react",
        model_role="default",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
    )
    assert req.resume is not None


def test_agent_run_input_requires_content_or_resume() -> None:
    """Reject requests that have no input/messages/resume."""
    with pytest.raises(ValidationError):
        AgentRunInput(
            agent_id="agent.default",
            graph_preset="single_react",
            model_role="default",
            tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
        )


def test_agent_run_output_paused_requires_interrupt() -> None:
    """Reject paused output envelopes without interrupt payload."""
    with pytest.raises(ValidationError):
        AgentRunOutput(
            run_id="run_1",
            attempt_id="att_1",
            status=RunStatus.PAUSED,
            terminal_reason=None,
        )


def test_agent_run_output_terminal_requires_reason() -> None:
    """Reject terminal output when terminal reason is missing."""
    with pytest.raises(ValidationError):
        AgentRunOutput(
            run_id="run_1",
            attempt_id="att_1",
            status=RunStatus.COMPLETED,
            events=[
                new_runtime_event(
                    event_type=RuntimeEventType.RUN_COMPLETED,
                    context={"run_id": "run_1", "attempt_id": "att_1", "seq": 1},
                )
            ],
        )


def test_agent_run_output_terminal_requires_terminal_event() -> None:
    """Reject terminal output that has no terminal runtime event."""
    with pytest.raises(ValidationError):
        AgentRunOutput(
            run_id="run_1",
            attempt_id="att_1",
            status=RunStatus.COMPLETED,
            terminal_reason=TerminalReason.FINAL_ANSWER,
            events=[
                new_runtime_event(
                    event_type=RuntimeEventType.NODE_COMPLETED,
                    context={"run_id": "run_1", "attempt_id": "att_1", "seq": 1},
                )
            ],
        )


def test_agent_run_output_round_trip() -> None:
    """Round-trip paused output envelope through JSON payload."""
    interrupt = InterruptRequest(
        interrupt_id="int_1",
        run_id="run_1",
        attempt_id="att_1",
        checkpoint_id="ckpt_1",
        reason=InterruptReason.APPROVAL_REQUIRED,
        title="Need approval",
        description="Approve tool execution",
        risk=ToolRisk.HIGH,
        proposed_action={"tool_name": "shell"},
        allowed_actions=[ResumeAction.APPROVE, ResumeAction.REJECT],
    )
    payload = AgentRunOutput(
        run_id="run_1",
        attempt_id="att_1",
        status=RunStatus.PAUSED,
        interrupt=interrupt,
        messages=[ChatMessage(role="assistant", content="Pending approval")],
        checkpoint=CheckpointRef(
            checkpoint_id="ckpt_1",
            run_id="run_1",
            attempt_id="att_1",
            graph_id="single_react",
            created_at="2026-05-18T10:00:00Z",
            state_version="v1",
            storage_backend="memory",
        ),
    )

    dumped = payload.model_dump(mode="json")
    restored = AgentRunOutput.model_validate(dumped)
    assert restored.run_id == "run_1"
    assert restored.interrupt is not None


def test_agent_run_input_tool_choice_defaults_to_none() -> None:
    """Without an explicit value, ``tool_choice`` is ``None`` — preserves
    the legacy behaviour where the provider sets ``"auto"``."""
    req = AgentRunInput(
        input="hello",
        agent_id="agent.default",
        graph_preset="single_react",
        model_role="default",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
    )
    assert req.tool_choice is None


def test_agent_run_input_tool_choice_accepts_string_forms() -> None:
    """Standard string forms (``"auto"`` / ``"required"`` / ``"none"``) and
    arbitrary string passthrough for experimental backends."""
    for choice in ("auto", "required", "none", "vendor-specific"):
        req = AgentRunInput(
            input="hello",
            agent_id="agent.default",
            graph_preset="single_react",
            model_role="default",
            tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
            tool_choice=choice,
        )
        assert req.tool_choice == choice


def test_agent_run_input_tool_choice_accepts_specific_tool_object() -> None:
    """``{"type": "tool", "name": "X"}`` shape that forces a named tool —
    this is the primary motivation: callers can guarantee a chart is
    rendered / a structured-output schema is filled / etc."""
    choice = {"type": "tool", "name": "chart_vegalite"}
    req = AgentRunInput(
        input="hello",
        agent_id="agent.default",
        graph_preset="single_react",
        model_role="default",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
        tool_choice=choice,
    )
    assert req.tool_choice == choice


def test_agent_run_input_tool_choice_rejects_non_json_payload() -> None:
    """Non-string / non-dict / non-null is rejected — the validator must
    refuse silently coercing types so downstream provider adapters never
    receive garbage."""
    with pytest.raises(ValidationError):
        AgentRunInput(
            input="hello",
            agent_id="agent.default",
            graph_preset="single_react",
            model_role="default",
            tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
            tool_choice=123,  # type: ignore[arg-type]
        )


def test_agent_run_input_tool_choice_roundtrips_via_json() -> None:
    """Field roundtrips through ``model_dump`` / ``model_validate`` so
    transport (queue / HTTP / checkpoint) preserves the value."""
    choice = {"type": "tool", "name": "chart_vegalite"}
    req = AgentRunInput(
        input="hello",
        agent_id="agent.default",
        graph_preset="single_react",
        model_role="default",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
        tool_choice=choice,
    )
    restored = AgentRunInput.model_validate(req.model_dump(mode="json"))
    assert restored.tool_choice == choice


def test_agent_run_input_accepts_profile_and_serialization_policy() -> None:
    """Allow profile and serialization policy in run input."""
    req = AgentRunInput(
        input="hello",
        agent_id="agent.default",
        graph_preset="single_react",
        model_role="default",
        agent_profile=AgentProfile.CODE_AGENT,
        serialization_policy=ExecutorSerializationPolicy(
            mode=SerializationMode.JSON_SAFE,
            schema_version="v2",
        ),
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
    )
    assert req.agent_profile == AgentProfile.CODE_AGENT
    assert req.serialization_policy is not None
    assert req.serialization_policy.schema_version == "v2"


def test_agent_run_input_accepts_coordinator_profile() -> None:
    """Coordinator should be a first-class run profile."""
    req = AgentRunInput(
        input="coordinate this",
        agent_id="agent.coordinator",
        graph_preset="single_react",
        agent_profile=AgentProfile.COORDINATOR,
    )
    assert req.agent_profile == AgentProfile.COORDINATOR


def test_agent_run_output_accepts_new_projection_and_group_fields() -> None:
    """Allow optional memory projection, prompt render, and subagent groups."""
    output = AgentRunOutput(
        run_id="run_2",
        attempt_id="att_2",
        status=RunStatus.RUNNING,
        subagent_groups=[
            SubagentGroup(
                group_id="grp_1",
                parent_run_id="run_2",
                parent_attempt_id="att_2",
                join_policy=SubagentJoinPolicy.WAIT_ALL,
            )
        ],
        memory_projection=MemoryProjection(
            run_id="run_2",
            attempt_id="att_2",
            view="succinct",
            steps=[
                MemoryStep(
                    step_index=0,
                    kind="task",
                    content="Investigate query",
                )
            ],
        ),
        prompt_render=PromptRenderResult(
            template_id="react.default",
            template_version=1,
            profile=AgentProfile.REACT_TEXT,
            rendered_text="Prompt",
            rendered_hash="hash_123",
        ),
    )
    assert output.memory_projection is not None
    assert output.subagent_groups[0].join_policy == SubagentJoinPolicy.WAIT_ALL
