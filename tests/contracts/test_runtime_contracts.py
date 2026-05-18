"""Runtime contract validation tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_driver.contracts import (
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
    TerminalReason,
    ToolPolicyInput,
    ToolPolicyMode,
    ToolRisk,
    new_runtime_event,
)


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
