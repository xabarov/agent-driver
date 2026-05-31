from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent_driver.contracts import AgentRunInput, ToolCall, ToolPolicyInput
from agent_driver.contracts.enums import (
    ApprovalMode,
    RuntimeEventType,
    SideEffectClass,
    ToolRisk,
    ToolTraceStatus,
)
from agent_driver.contracts.tools import ToolResultEnvelope, ToolTrace
from agent_driver.runtime.artifact_events import artifact_event_from_tool_result
from agent_driver.runtime.single_agent.tool_stage import _emit_tool_completed_if_needed
from agent_driver.runtime.single_agent.types import RunContext
from agent_driver.runtime.tools import ToolExecutionResult


def _context(tmp_path: Path) -> RunContext:
    return RunContext(
        run_input=AgentRunInput(
            input="write report",
            run_id="run_artifact_events",
            agent_id="agent.test",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(),
        ),
        identifiers={
            "run_id": "run_artifact_events",
            "attempt_id": "attempt_test",
        },
        metadata={"workspace_cwd": str(tmp_path)},
    )


def _envelope(
    *,
    tool_name: str,
    path: Path,
    created: bool,
    dry_run: bool = False,
) -> ToolResultEnvelope:
    return ToolResultEnvelope(
        call=ToolCall(
            tool_name=tool_name,
            tool_call_id=f"call_{tool_name}",
            args={"path": str(path)},
        ),
        structured_output={
            "path": str(path),
            "operation": "write" if tool_name == "file_write" else "edit",
            "dry_run": dry_run,
            "created": created,
            "size_bytes": 1234,
            "replacements": 1,
        },
    )


def test_file_write_created_projects_report_artifact_event(tmp_path: Path) -> None:
    context = _context(tmp_path)
    report = tmp_path / "research" / "report.md"

    event = artifact_event_from_tool_result(
        context,
        _envelope(tool_name="file_write", path=report, created=True),
    )

    assert event is not None
    event_type, payload = event
    assert event_type == RuntimeEventType.ARTIFACT_CREATED
    assert payload["path"] == "research/report.md"
    assert payload["kind"] == "report"
    assert payload["size_bytes"] == 1234


def test_file_edit_projects_artifact_updated_event(tmp_path: Path) -> None:
    context = _context(tmp_path)
    report = tmp_path / "research" / "report.md"

    event = artifact_event_from_tool_result(
        context,
        _envelope(tool_name="file_edit", path=report, created=False),
    )

    assert event is not None
    event_type, payload = event
    assert event_type == RuntimeEventType.ARTIFACT_UPDATED
    assert payload["operation"] == "edit"
    assert payload["replacements"] == 1


def test_artifact_event_skips_dry_run_and_workspace_escape(tmp_path: Path) -> None:
    context = _context(tmp_path)

    assert (
        artifact_event_from_tool_result(
            context,
            _envelope(
                tool_name="file_write",
                path=tmp_path / "research" / "report.md",
                created=True,
                dry_run=True,
            ),
        )
        is None
    )


def test_tool_completed_emits_artifact_created_event(tmp_path: Path) -> None:
    context = _context(tmp_path)
    report = tmp_path / "research" / "report.md"
    emitted: list[object] = []
    host = SimpleNamespace(_emit=emitted.append)
    result = ToolExecutionResult(
        traces=[
            ToolTrace(
                step=1,
                tool_name="file_write",
                tool_call_id="call_file_write",
                status=ToolTraceStatus.COMPLETED,
                risk=ToolRisk.MEDIUM,
                side_effect=SideEffectClass.REVERSIBLE_WRITE,
                approval_mode=ApprovalMode.ON_POLICY_MATCH,
            )
        ],
        envelopes=[
            _envelope(tool_name="file_write", path=report, created=True),
        ],
    )

    _emit_tool_completed_if_needed(host, context, result)

    event_types = [item.event_type for item in emitted]
    assert RuntimeEventType.TOOL_CALL_COMPLETED in event_types
    assert RuntimeEventType.ARTIFACT_CREATED in event_types
    artifact_event = next(
        item for item in emitted if item.event_type == RuntimeEventType.ARTIFACT_CREATED
    )
    assert artifact_event.payload["path"] == "research/report.md"
    assert (
        artifact_event_from_tool_result(
            context,
            _envelope(
                tool_name="file_write",
                path=tmp_path.parent / "outside.md",
                created=True,
            ),
        )
        is None
    )
