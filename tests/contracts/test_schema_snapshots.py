"""Public schema generation tests for contract models."""

from __future__ import annotations

from agent_driver.contracts import (
    AgentRunInput,
    AgentRunOutput,
    CheckpointRef,
    InterruptRequest,
    ResumeCommand,
    RuntimeEvent,
    SubagentRun,
    ToolTrace,
)


def test_public_contract_schema_generation() -> None:
    """Ensure JSON schemas are generated for key public contracts."""
    schema = {
        "AgentRunInput": AgentRunInput.model_json_schema(),
        "AgentRunOutput": AgentRunOutput.model_json_schema(),
        "RuntimeEvent": RuntimeEvent.model_json_schema(),
        "CheckpointRef": CheckpointRef.model_json_schema(),
        "ResumeCommand": ResumeCommand.model_json_schema(),
        "InterruptRequest": InterruptRequest.model_json_schema(),
        "ToolTrace": ToolTrace.model_json_schema(),
        "SubagentRun": SubagentRun.model_json_schema(),
    }

    assert "properties" in schema["AgentRunInput"]
    assert "properties" in schema["AgentRunOutput"]
    assert "status" in schema["AgentRunOutput"]["properties"]
    assert "type" in schema["RuntimeEvent"]["properties"]
    assert "checkpoint_id" in schema["CheckpointRef"]["properties"]
    assert "action" in schema["ResumeCommand"]["properties"]
    assert "allowed_actions" in schema["InterruptRequest"]["properties"]
    assert "tool_name" in schema["ToolTrace"]["properties"]
    assert "terminal_state" in schema["SubagentRun"]["properties"]
