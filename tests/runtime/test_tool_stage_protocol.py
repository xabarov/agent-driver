"""Tests for tool protocol message payload shaping."""

from __future__ import annotations

import json
from types import SimpleNamespace

from agent_driver.contracts.enums import ChatRole, ToolPolicyDecision
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.tools import ToolCall, ToolError, ToolResultEnvelope
from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.contracts import LlmFinishReason, LlmResponse
from agent_driver.runtime.single_agent.tool_stage import (
    _clamp_deep_research_parent_artifact_batch,
    _clamp_deep_research_initial_subagent_batch,
    _coerce_deep_research_artifact_repair_batch,
    _coerce_deep_research_parent_synthesis_write,
    _force_web_fetch_for_source_verified_research,
    _repair_deep_research_parent_file_write_args,
    _should_force_final_answer,
    _suppress_deep_research_terminal_tool_calls,
    _update_tool_protocol_messages,
)
from agent_driver.runtime.tools import ToolExecutionResult


def test_deep_research_repairs_empty_parent_file_write_args() -> None:
    llm_response = LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content=""),
        finish_reason=LlmFinishReason.TOOL_CALLS,
        usage=UsageSummary(model_provider="fake", model_name="fake"),
        provider="fake",
        model="fake",
        metadata={
            "planned_tool_calls": [
                ToolCall(
                    tool_name="file_write",
                    tool_call_id="call_1",
                    args={},
                ).model_dump(mode="json")
            ]
        },
    )
    context = SimpleNamespace(
        llm_response=llm_response,
        metadata={
            "tool_results": [],
            "deep_research_child_synthesis": {
                "pending": True,
                "summary": "Candidate source: https://example.com/paper",
            },
        },
    )

    _repair_deep_research_parent_file_write_args(context)

    planned = llm_response.metadata["planned_tool_calls"][0]
    assert planned["args"]["path"] == "research/report.md"
    assert planned["args"]["create_parent"] is True
    assert "https://example.com/paper" in planned["args"]["content"]
    assert planned["metadata"]["deep_research_args_repaired"] is True
    assert context.metadata["deep_research_file_write_args_repaired"]["count"] == 1


def test_deep_research_initial_subagent_batch_drops_sibling_tools() -> None:
    llm_response = LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content=""),
        finish_reason=LlmFinishReason.TOOL_CALLS,
        usage=UsageSummary(model_provider="fake", model_name="fake"),
        provider="fake",
        model="fake",
        metadata={
            "planned_tool_calls": [
                ToolCall(
                    tool_name="web_search",
                    tool_call_id="search_1",
                    args={"query": "fork join"},
                ).model_dump(mode="json"),
                ToolCall(
                    tool_name="agent_tool",
                    tool_call_id="agent_1",
                    args={"task": "Find sources", "description": "Find sources"},
                ).model_dump(mode="json"),
                ToolCall(
                    tool_name="file_write",
                    tool_call_id="write_1",
                    args={"path": "research/report.md", "content": "# Report"},
                ).model_dump(mode="json"),
            ]
        },
    )
    context = SimpleNamespace(
        llm_response=llm_response,
        metadata={"tool_results": []},
        run_input=SimpleNamespace(
            app_metadata={},
            tool_policy=SimpleNamespace(
                allowed_tools=None,
                denied_tools=[],
                metadata={
                    "task_contract": {
                        "requires_research": True,
                        "research_mode": "deep",
                        "research_depth": "deep_parallel_research",
                        "research_profile": "medium",
                    }
                },
            ),
        ),
    )

    _clamp_deep_research_initial_subagent_batch(context)

    planned = llm_response.metadata["planned_tool_calls"]
    assert [item["tool_name"] for item in planned] == ["agent_tool"]
    assert context.metadata["deep_research_initial_subagent_batch_clamped"] == {
        "kept": 1,
        "dropped": 2,
        "reason": "medium_hard_first_child_only",
    }


def test_deep_research_initial_direct_discovery_coerces_to_subagent() -> None:
    llm_response = LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content=""),
        finish_reason=LlmFinishReason.TOOL_CALLS,
        usage=UsageSummary(model_provider="fake", model_name="fake"),
        provider="fake",
        model="fake",
        metadata={
            "planned_tool_calls": [
                ToolCall(
                    tool_name="web_search",
                    tool_call_id="search_1",
                    args={"query": "fork join"},
                ).model_dump(mode="json"),
                ToolCall(
                    tool_name="todo_write",
                    tool_call_id="todo_1",
                    args={"todos": []},
                ).model_dump(mode="json"),
            ]
        },
    )
    context = SimpleNamespace(
        llm_response=llm_response,
        metadata={"tool_results": [{"call": {"tool_name": "todo_write", "args": {}}}]},
        run_input=SimpleNamespace(
            input="Research fork-join queueing models.",
            app_metadata={},
            tool_policy=SimpleNamespace(
                allowed_tools=None,
                denied_tools=[],
                metadata={
                    "task_contract": {
                        "requires_research": True,
                        "research_mode": "deep",
                        "research_depth": "deep_parallel_research",
                        "research_profile": "medium",
                    }
                },
            ),
        ),
    )

    _clamp_deep_research_initial_subagent_batch(context)

    planned = llm_response.metadata["planned_tool_calls"]
    assert [item["tool_name"] for item in planned] == ["agent_tool"]
    assert planned[0]["args"]["execution_mode"] == "sync"
    assert "Research fork-join queueing models." in planned[0]["args"]["task"]
    assert planned[0]["metadata"]["original_tool_name"] == "web_search"
    assert context.metadata["deep_research_initial_direct_discovery_coerced"] == {
        "original_tool": "web_search",
        "dropped": 1,
        "target": "agent_tool",
    }


def test_deep_research_terminal_state_suppresses_text_form_tool_calls() -> None:
    llm_response = LlmResponse(
        message=ChatMessage(
            role=ChatRole.ASSISTANT,
            content=(
                "Done. <tool_call>{\"name\":\"bash\",\"arguments\":{\"cmd\":\"ls\"}}"
                "</tool_call>"
            ),
        ),
        finish_reason=LlmFinishReason.TOOL_CALLS,
        usage=UsageSummary(model_provider="fake", model_name="fake"),
        provider="fake",
        model="fake",
        metadata={
            "planned_tool_calls": [
                ToolCall(
                    tool_name="bash",
                    tool_call_id="bash_1",
                    args={"cmd": "ls"},
                    metadata={"text_form_source": True},
                ).model_dump(mode="json")
            ]
        },
    )
    context = SimpleNamespace(
        llm_response=llm_response,
        metadata={
            "deep_research_artifacts": {
                "report_exists": True,
                "report_path": "research/report.md",
                "source_ledger_exists": True,
                "source_ledger_path": "research/sources.jsonl",
            },
        },
    )

    _suppress_deep_research_terminal_tool_calls(context)

    assert llm_response.metadata["planned_tool_calls"] == []
    assert llm_response.finish_reason == LlmFinishReason.STOP
    assert "<tool_call>" not in llm_response.message.content
    assert context.metadata["deep_research_terminal_tool_calls_suppressed"] == {
        "count": 1,
        "tools": ["bash"],
        "reason": "artifacts_ready",
    }


def test_deep_research_terminal_state_suppresses_file_read_alias_drift() -> None:
    llm_response = LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content="Report is done."),
        finish_reason=LlmFinishReason.TOOL_CALLS,
        usage=UsageSummary(model_provider="fake", model_name="fake"),
        provider="fake",
        model="fake",
        metadata={
            "planned_tool_calls": [
                ToolCall(
                    tool_name="file_read",
                    tool_call_id="read_1",
                    args={"path": "research/report.md"},
                ).model_dump(mode="json")
            ]
        },
    )
    context = SimpleNamespace(
        llm_response=llm_response,
        metadata={
            "deep_research_artifacts": {
                "report_exists": True,
                "report_path": "research/report.md",
                "source_ledger_exists": True,
                "source_ledger_path": "research/sources.jsonl",
            },
        },
    )

    _suppress_deep_research_terminal_tool_calls(context)

    assert llm_response.metadata["planned_tool_calls"] == []
    assert llm_response.finish_reason == LlmFinishReason.STOP
    assert context.metadata["deep_research_terminal_tool_calls_suppressed"] == {
        "count": 1,
        "tools": ["file_read"],
        "reason": "artifacts_ready",
    }


def test_deep_research_terminal_state_suppresses_from_tool_result_artifacts() -> None:
    llm_response = LlmResponse(
        message=ChatMessage(
            role=ChatRole.ASSISTANT,
            content="<tool_call>{\"name\":\"retrieve_subagent_results\",\"arguments\":{}}</tool_call>",
        ),
        finish_reason=LlmFinishReason.TOOL_CALLS,
        usage=UsageSummary(model_provider="fake", model_name="fake"),
        provider="fake",
        model="fake",
        metadata={
            "planned_tool_calls": [
                ToolCall(
                    tool_name="retrieve_subagent_results",
                    tool_call_id="subagent_1",
                    args={},
                    metadata={"text_form_source": True},
                ).model_dump(mode="json")
            ]
        },
    )
    context = SimpleNamespace(
        llm_response=llm_response,
        metadata={
            "tool_results": [
                {
                    "call": {
                        "tool_name": "file_write",
                        "args": {"path": "research/report.md"},
                    }
                },
                {
                    "call": {
                        "tool_name": "file_write",
                        "args": {"path": "research/sources.jsonl"},
                    }
                },
            ]
        },
    )

    _suppress_deep_research_terminal_tool_calls(context)

    assert llm_response.metadata["planned_tool_calls"] == []
    assert llm_response.finish_reason == LlmFinishReason.STOP
    assert "retrieve_subagent_results" not in llm_response.message.content


def test_deep_research_parent_artifact_batch_drops_sibling_tool_drift() -> None:
    llm_response = LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content=""),
        finish_reason=LlmFinishReason.TOOL_CALLS,
        usage=UsageSummary(model_provider="fake", model_name="fake"),
        provider="fake",
        model="fake",
        metadata={
            "planned_tool_calls": [
                ToolCall(
                    tool_name="file_write",
                    tool_call_id="write_1",
                    args={"path": "research/report.md", "content": "# Report"},
                ).model_dump(mode="json"),
                ToolCall(
                    tool_name="shell",
                    tool_call_id="shell_1",
                    args={"cmd": "ls"},
                    metadata={"text_form_source": True},
                ).model_dump(mode="json"),
                ToolCall(
                    tool_name="TodoWrite",
                    tool_call_id="todo_1",
                    args={"todos": []},
                    metadata={"text_form_source": True},
                ).model_dump(mode="json"),
            ]
        },
    )
    context = SimpleNamespace(
        llm_response=llm_response,
        metadata={
            "deep_research_child_synthesis": {
                "pending": True,
                "summary": "child notes",
            }
        },
    )

    _clamp_deep_research_parent_artifact_batch(context)

    planned = llm_response.metadata["planned_tool_calls"]
    assert [item["tool_name"] for item in planned] == ["file_write"]
    assert context.metadata["deep_research_parent_artifact_batch_clamped"] == {
        "kept": 1,
        "dropped": 2,
        "reason": "parent_artifact_writes_only",
        "dropped_tools": ["shell", "TodoWrite"],
    }


def test_deep_research_artifact_repair_coerces_drift_to_missing_ledger() -> None:
    llm_response = LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content=""),
        finish_reason=LlmFinishReason.TOOL_CALLS,
        usage=UsageSummary(model_provider="fake", model_name="fake"),
        provider="fake",
        model="fake",
        metadata={
            "planned_tool_calls": [
                ToolCall(
                    tool_name="bash",
                    tool_call_id="bash_1",
                    args={"cmd": "ls"},
                    metadata={"text_form_source": True},
                ).model_dump(mode="json"),
                ToolCall(
                    tool_name="shell_execute",
                    tool_call_id="shell_1",
                    args={"cmd": "pwd"},
                    metadata={"text_form_source": True},
                ).model_dump(mode="json"),
            ]
        },
    )
    context = SimpleNamespace(
        llm_response=llm_response,
        metadata={
            "deep_research_artifacts": {
                "report_exists": True,
                "report_path": "research/report.md",
            },
            "deep_research_child_synthesis": {
                "pending": True,
                "summary": "Candidate source: https://example.com/source",
            },
        },
    )

    _coerce_deep_research_artifact_repair_batch(context)

    planned = llm_response.metadata["planned_tool_calls"]
    assert [item["tool_name"] for item in planned] == ["file_write"]
    assert planned[0]["args"]["path"] == "research/sources.jsonl"
    assert "https://example.com/source" in planned[0]["args"]["content"]
    assert planned[0]["metadata"]["original_tool_name"] == "bash"
    assert context.metadata["deep_research_artifact_repair_batch_coerced"] == {
        "target": "research/sources.jsonl",
        "dropped": 1,
        "original_tool": "bash",
    }


def test_deep_research_retargets_parent_ledger_write_to_missing_report() -> None:
    llm_response = LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content=""),
        finish_reason=LlmFinishReason.TOOL_CALLS,
        usage=UsageSummary(model_provider="fake", model_name="fake"),
        provider="fake",
        model="fake",
        metadata={
            "planned_tool_calls": [
                ToolCall(
                    tool_name="file_write",
                    tool_call_id="call_1",
                    args={
                        "path": "research/sources.jsonl",
                        "content": '{"url": "https://example.com/paper"}\n',
                    },
                ).model_dump(mode="json")
            ]
        },
    )
    context = SimpleNamespace(
        llm_response=llm_response,
        metadata={
            "tool_results": [],
            "deep_research_artifacts": {
                "source_ledger_exists": True,
                "source_ledger_path": "research/sources.jsonl",
                "source_ledger_size_bytes": 42,
            },
            "deep_research_child_synthesis": {
                "pending": True,
                "summary": "Candidate source: https://example.com/paper",
            },
        },
    )

    _repair_deep_research_parent_file_write_args(context)

    planned = llm_response.metadata["planned_tool_calls"][0]
    assert planned["args"]["path"] == "research/report.md"
    assert "Child Research Notes" in planned["args"]["content"]
    assert planned["metadata"]["deep_research_repair_reason"] == (
        "parent_synthesis_report_required"
    )
    assert context.metadata["deep_research_file_write_args_repaired"]["count"] == 1


def test_deep_research_coerces_parent_synthesis_agent_tool_drift_to_report() -> None:
    llm_response = LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content=""),
        finish_reason=LlmFinishReason.TOOL_CALLS,
        usage=UsageSummary(model_provider="fake", model_name="fake"),
        provider="fake",
        model="fake",
        metadata={
            "planned_tool_calls": [
                ToolCall(
                    tool_name="agent_tool",
                    tool_call_id="agent_2",
                    args={"task": "do more research"},
                ).model_dump(mode="json")
            ]
        },
    )
    context = SimpleNamespace(
        llm_response=llm_response,
        metadata={
            "tool_results": [],
            "deep_research_child_synthesis": {
                "pending": True,
                "summary": "Candidate source: https://example.com/paper",
            },
        },
    )

    _coerce_deep_research_parent_synthesis_write(context)

    planned = llm_response.metadata["planned_tool_calls"][0]
    assert planned["tool_name"] == "file_write"
    assert planned["args"]["path"] == "research/report.md"
    assert planned["metadata"]["original_tool_name"] == "agent_tool"
    assert planned["metadata"]["deep_research_repair_reason"] == (
        "parent_synthesis_tool_coerced"
    )


def test_deep_research_repairs_empty_report_content_during_parent_synthesis() -> None:
    llm_response = LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content=""),
        finish_reason=LlmFinishReason.TOOL_CALLS,
        usage=UsageSummary(model_provider="fake", model_name="fake"),
        provider="fake",
        model="fake",
        metadata={
            "planned_tool_calls": [
                ToolCall(
                    tool_name="file_write",
                    tool_call_id="call_1",
                    args={"path": "research/report.md", "content": ""},
                ).model_dump(mode="json")
            ]
        },
    )
    context = SimpleNamespace(
        llm_response=llm_response,
        metadata={
            "tool_results": [],
            "deep_research_child_synthesis": {
                "pending": True,
                "summary": "Candidate source: https://example.com/paper",
            },
        },
    )

    _repair_deep_research_parent_file_write_args(context)

    planned = llm_response.metadata["planned_tool_calls"][0]
    assert planned["args"]["path"] == "research/report.md"
    assert "Candidate source: https://example.com/paper" in planned["args"]["content"]
    assert planned["metadata"]["deep_research_repair_reason"] == (
        "parent_synthesis_empty_file_write"
    )


def test_deep_research_retargets_wrong_path_write_during_parent_synthesis() -> None:
    llm_response = LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content=""),
        finish_reason=LlmFinishReason.TOOL_CALLS,
        usage=UsageSummary(model_provider="fake", model_name="fake"),
        provider="fake",
        model="fake",
        metadata={
            "planned_tool_calls": [
                ToolCall(
                    tool_name="file_write",
                    tool_call_id="call_1",
                    args={"path": "notes.md", "content": "Wrong target but non-empty."},
                ).model_dump(mode="json")
            ]
        },
    )
    context = SimpleNamespace(
        llm_response=llm_response,
        metadata={
            "tool_results": [],
            "deep_research_child_synthesis": {
                "pending": True,
                "summary": "Candidate source: https://example.com/paper",
            },
        },
    )

    _repair_deep_research_parent_file_write_args(context)

    planned = llm_response.metadata["planned_tool_calls"][0]
    assert planned["args"]["path"] == "research/report.md"
    assert "Candidate source: https://example.com/paper" in planned["args"]["content"]
    assert planned["metadata"]["deep_research_repair_reason"] == (
        "parent_synthesis_report_required"
    )


def test_deep_research_retargets_second_report_write_to_source_ledger() -> None:
    llm_response = LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content=""),
        finish_reason=LlmFinishReason.TOOL_CALLS,
        usage=UsageSummary(model_provider="fake", model_name="fake"),
        provider="fake",
        model="fake",
        metadata={
            "planned_tool_calls": [
                ToolCall(
                    tool_name="file_write",
                    tool_call_id="call_1",
                    args={
                        "path": "research/report.md",
                        "content": "# Report\n\nDraft.",
                    },
                ).model_dump(mode="json"),
                ToolCall(
                    tool_name="file_write",
                    tool_call_id="call_2",
                    args={
                        "path": "research/report.md",
                        "content": "# Report\n\nRewrite.",
                    },
                ).model_dump(mode="json"),
            ]
        },
    )
    context = SimpleNamespace(
        llm_response=llm_response,
        metadata={
            "tool_results": [],
            "deep_research_child_synthesis": {
                "pending": True,
                "summary": "Child notes without URL",
            },
        },
    )

    _repair_deep_research_parent_file_write_args(context)

    planned = llm_response.metadata["planned_tool_calls"]
    assert planned[0]["args"]["path"] == "research/report.md"
    assert planned[1]["args"]["path"] == "research/sources.jsonl"
    assert "verification is still pending" in planned[1]["args"]["content"]
    assert planned[1]["metadata"]["deep_research_repair_reason"] == (
        "parent_synthesis_source_ledger_required"
    )


def test_deep_research_retargets_sources_markdown_alias_to_source_ledger() -> None:
    llm_response = LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content=""),
        finish_reason=LlmFinishReason.TOOL_CALLS,
        usage=UsageSummary(model_provider="fake", model_name="fake"),
        provider="fake",
        model="fake",
        metadata={
            "planned_tool_calls": [
                ToolCall(
                    tool_name="file_write",
                    tool_call_id="call_1",
                    args={
                        "path": "research/report.md",
                        "content": "# Report\n\nDraft.",
                    },
                ).model_dump(mode="json"),
                ToolCall(
                    tool_name="file_write",
                    tool_call_id="call_2",
                    args={
                        "path": "research/sources.md",
                        "content": "- https://example.com/source",
                    },
                ).model_dump(mode="json"),
            ]
        },
    )
    context = SimpleNamespace(
        llm_response=llm_response,
        metadata={
            "tool_results": [],
            "deep_research_child_synthesis": {
                "pending": True,
                "summary": "Candidate source: https://example.com/source",
            },
        },
    )

    _repair_deep_research_parent_file_write_args(context)

    planned = llm_response.metadata["planned_tool_calls"]
    assert planned[1]["args"]["path"] == "research/sources.jsonl"
    assert "https://example.com/source" in planned[1]["args"]["content"]
    assert planned[1]["metadata"]["deep_research_repair_reason"] == (
        "parent_synthesis_source_ledger_alias"
    )


def test_deep_research_retargets_alias_write_ledger_path_to_missing_report() -> None:
    llm_response = LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content=""),
        finish_reason=LlmFinishReason.TOOL_CALLS,
        usage=UsageSummary(model_provider="fake", model_name="fake"),
        provider="fake",
        model="fake",
        metadata={
            "planned_tool_calls": [
                ToolCall(
                    tool_name="write",
                    tool_call_id="call_1",
                    args={
                        "file_path": "research/sources.jsonl",
                        "content": '{"url": "https://example.com/paper"}\n',
                    },
                ).model_dump(mode="json")
            ]
        },
    )
    context = SimpleNamespace(
        llm_response=llm_response,
        metadata={
            "tool_results": [],
            "deep_research_artifacts": {
                "source_ledger_exists": True,
                "source_ledger_path": "research/sources.jsonl",
                "source_ledger_size_bytes": 42,
            },
            "deep_research_child_synthesis": {
                "pending": True,
                "summary": "Candidate source: https://example.com/paper",
            },
        },
    )

    _repair_deep_research_parent_file_write_args(context)

    planned = llm_response.metadata["planned_tool_calls"][0]
    assert planned["tool_name"] == "file_write"
    assert planned["args"]["path"] == "research/report.md"
    assert planned["metadata"]["original_tool_name"] == "write"
    assert planned["metadata"]["tool_alias_normalized"] is True
    assert planned["metadata"]["deep_research_repair_reason"] == (
        "parent_synthesis_report_required"
    )


def test_deep_research_retargets_repeated_report_write_to_missing_source_ledger() -> (
    None
):
    llm_response = LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content=""),
        finish_reason=LlmFinishReason.TOOL_CALLS,
        usage=UsageSummary(model_provider="fake", model_name="fake"),
        provider="fake",
        model="fake",
        metadata={
            "planned_tool_calls": [
                ToolCall(
                    tool_name="file_write",
                    tool_call_id="call_1",
                    args={
                        "path": "research/report.md",
                        "content": "# Report\n\nDraft without sources.",
                    },
                ).model_dump(mode="json")
            ]
        },
    )
    context = SimpleNamespace(
        llm_response=llm_response,
        metadata={
            "deep_research_artifacts": {
                "report_exists": True,
                "report_path": "research/report.md",
                "report_size_bytes": 128,
            },
            "tool_results": [
                {
                    "call": {
                        "tool_name": "web_search",
                        "tool_call_id": "search_1",
                        "args": {"query": "fork join queues"},
                    },
                    "structured_output": {
                        "results": [
                            {
                                "title": "Fork-join source",
                                "url": "https://example.com/fork-join",
                                "snippet": "Queueing model overview",
                            }
                        ]
                    },
                }
            ],
        },
    )

    _repair_deep_research_parent_file_write_args(context)

    planned = llm_response.metadata["planned_tool_calls"][0]
    assert planned["args"]["path"] == "research/sources.jsonl"
    assert "https://example.com/fork-join" in planned["args"]["content"]
    assert planned["metadata"]["deep_research_repair_reason"] == (
        "source_ledger_required"
    )


def test_deep_research_keeps_source_ledger_file_path_alias_with_suffix() -> None:
    llm_response = LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content=""),
        finish_reason=LlmFinishReason.TOOL_CALLS,
        usage=UsageSummary(model_provider="fake", model_name="fake"),
        provider="fake",
        model="fake",
        metadata={
            "planned_tool_calls": [
                ToolCall(
                    tool_name="file_write",
                    tool_call_id="call_1",
                    args={
                        "file_path": "workspace/research/sources.jsonl",
                        "content": '{"url": "https://example.com/fork-join"}\n',
                    },
                ).model_dump(mode="json")
            ]
        },
    )
    context = SimpleNamespace(
        llm_response=llm_response,
        metadata={
            "deep_research_artifacts": {
                "report_exists": True,
                "report_path": "research/report.md",
                "report_size_bytes": 128,
            },
            "tool_results": [],
        },
    )

    _repair_deep_research_parent_file_write_args(context)

    planned = llm_response.metadata["planned_tool_calls"][0]
    assert planned["args"]["path"] == "workspace/research/sources.jsonl"
    assert planned["args"]["content"] == '{"url": "https://example.com/fork-join"}\n'
    assert planned["metadata"]["tool_args_normalized"] is True
    assert context.metadata["deep_research_file_write_args_repaired"] == {
        "count": 1,
        "reason": "source_ledger_file_write_repair",
    }


def test_update_tool_protocol_messages_includes_truncated_and_error_code() -> None:
    llm_response = LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content=""),
        finish_reason=LlmFinishReason.TOOL_CALLS,
        usage=UsageSummary(model_provider="fake", model_name="fake"),
        provider="fake",
        model="fake",
        metadata={
            "planned_tool_calls": [
                ToolCall(
                    tool_name="glob_search",
                    tool_call_id="call_1",
                    args={"pattern": "**/*"},
                ).model_dump(mode="json")
            ]
        },
    )
    envelope = ToolResultEnvelope(
        call=ToolCall(
            tool_name="glob_search", tool_call_id="call_1", args={"pattern": "**/*"}
        ),
        decision=ToolPolicyDecision.DENY,
        structured_output={"summary": "cap hit", "results": ["a.py"]},
        truncated=True,
        error=ToolError(code="tool_policy_denied", message="denied"),
    )
    context = SimpleNamespace(
        llm_response=llm_response,
        run_input=SimpleNamespace(messages=(), input="hello"),
        metadata={},
    )
    _update_tool_protocol_messages(
        context=context, result=ToolExecutionResult(envelopes=[envelope], traces=[])
    )
    rows = context.metadata["protocol_messages"]
    tool_rows = [row for row in rows if row.get("role") == ChatRole.TOOL.value]
    assert tool_rows
    payload = json.loads(tool_rows[-1]["content"])
    assert payload["truncated"] is True
    assert payload["error_code"] == "tool_policy_denied"


def test_update_tool_protocol_messages_prefers_summary_over_bulky_output_preview() -> None:
    llm_response = LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content=""),
        finish_reason=LlmFinishReason.TOOL_CALLS,
        usage=UsageSummary(model_provider="fake", model_name="fake"),
        provider="fake",
        model="fake",
        metadata={
            "planned_tool_calls": [
                ToolCall(
                    tool_name="enum_probe",
                    tool_call_id="call_1",
                    args={"target": "lab"},
                ).model_dump(mode="json")
            ]
        },
    )
    summary = "enum_probe: Domain: WORKGROUP; Users (1): testuser; Shares (2): public"
    envelope = ToolResultEnvelope(
        call=ToolCall(tool_name="enum_probe", tool_call_id="call_1", args={"target": "lab"}),
        decision=ToolPolicyDecision.ALLOW,
        summary=summary,
        structured_output={
            "tool_id": "enum_probe",
            "status": "succeeded",
            "result_summary": summary,
            "output_preview": "ENUM TOOL BANNER\n" + ("raw line\n" * 300),
        },
    )
    context = SimpleNamespace(
        llm_response=llm_response,
        run_input=SimpleNamespace(messages=(), input="hello"),
        metadata={},
    )

    _update_tool_protocol_messages(
        context=context, result=ToolExecutionResult(envelopes=[envelope], traces=[])
    )

    rows = context.metadata["protocol_messages"]
    tool_rows = [row for row in rows if row.get("role") == ChatRole.TOOL.value]
    payload = json.loads(tool_rows[-1]["content"])
    assert payload["summary"] == summary
    assert payload["result_summary"] == summary
    assert "testuser" in payload["summary"]
    assert "omitted from protocol payload" in payload["output_preview"]
    assert payload["output_preview_omitted_chars"] > 0


def test_update_tool_protocol_messages_preserves_reasoning_details() -> None:
    reasoning_details = [
        {
            "type": "reasoning.encrypted",
            "data": "opaque",
            "id": "r1",
            "format": "openai-responses-v1",
            "index": 0,
        }
    ]
    llm_response = LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content=""),
        finish_reason=LlmFinishReason.TOOL_CALLS,
        usage=UsageSummary(model_provider="openrouter", model_name="openai/gpt-5.5"),
        provider="openrouter",
        model="openai/gpt-5.5",
        metadata={
            "provider_reasoning_details": reasoning_details,
            "planned_tool_calls": [
                ToolCall(
                    tool_name="web_search",
                    tool_call_id="call_search",
                    args={"query": "fork join"},
                ).model_dump(mode="json")
            ],
        },
    )
    envelope = ToolResultEnvelope(
        call=ToolCall(
            tool_name="web_search",
            tool_call_id="call_search",
            args={"query": "fork join"},
        ),
        decision=ToolPolicyDecision.ALLOW,
        structured_output={"summary": "ok", "results": []},
    )
    context = SimpleNamespace(
        llm_response=llm_response,
        run_input=SimpleNamespace(messages=(), input="hello"),
        metadata={},
    )

    _update_tool_protocol_messages(
        context=context, result=ToolExecutionResult(envelopes=[envelope], traces=[])
    )

    assistant_rows = [
        row
        for row in context.metadata["protocol_messages"]
        if row.get("role") == "assistant"
    ]
    assert assistant_rows[-1]["metadata"]["reasoning_details"] == reasoning_details


def test_update_tool_protocol_messages_adds_python_policy_hint() -> None:
    llm_response = LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content=""),
        finish_reason=LlmFinishReason.TOOL_CALLS,
        usage=UsageSummary(model_provider="fake", model_name="fake"),
        provider="fake",
        model="fake",
        metadata={
            "planned_tool_calls": [
                ToolCall(
                    tool_name="python",
                    tool_call_id="call_py",
                    args={"code": "import scipy"},
                ).model_dump(mode="json")
            ]
        },
    )
    envelope = ToolResultEnvelope(
        call=ToolCall(
            tool_name="python", tool_call_id="call_py", args={"code": "import scipy"}
        ),
        decision=ToolPolicyDecision.ALLOW,
        structured_output={
            "error_kind": "policy",
            "allowed_imports": ["math", "statistics"],
            "remediation": "Use allowed imports only: math, statistics",
        },
        summary="python policy: imports blocked by sandbox (scipy)",
    )
    context = SimpleNamespace(
        llm_response=llm_response,
        run_input=SimpleNamespace(messages=(), input="hello"),
        metadata={},
    )
    _update_tool_protocol_messages(
        context=context, result=ToolExecutionResult(envelopes=[envelope], traces=[])
    )
    user_rows = [
        row
        for row in context.metadata["protocol_messages"]
        if row.get("role") == "user"
    ]
    assert any(
        "sandbox policy" in (row.get("content") or "").lower() for row in user_rows
    )
    assert any("math" in (row.get("content") or "") for row in user_rows)
    assert context.metadata.get("python_policy_hint_sent") is True


def test_update_tool_protocol_messages_compacts_web_fetch_payload() -> None:
    llm_response = LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content=""),
        finish_reason=LlmFinishReason.TOOL_CALLS,
        usage=UsageSummary(model_provider="fake", model_name="fake"),
        provider="fake",
        model="fake",
        metadata={
            "planned_tool_calls": [
                ToolCall(
                    tool_name="web_fetch",
                    tool_call_id="call_f1",
                    args={"url": "https://example.com"},
                ).model_dump(mode="json")
            ]
        },
    )
    envelope = ToolResultEnvelope(
        call=ToolCall(
            tool_name="web_fetch",
            tool_call_id="call_f1",
            args={"url": "https://example.com"},
        ),
        decision=ToolPolicyDecision.ALLOW,
        structured_output={
            "summary": "fetched https://example.com",
            "url": "https://example.com",
            "content": "z" * 10_000,
            "excerpt": "z" * 2_500,
            "metadata": {"title": "Example", "published_time": "2025-01-01"},
        },
        truncated=False,
    )
    context = SimpleNamespace(
        llm_response=llm_response,
        run_input=SimpleNamespace(messages=(), input="hello"),
        metadata={},
    )
    _update_tool_protocol_messages(
        context=context, result=ToolExecutionResult(envelopes=[envelope], traces=[])
    )
    rows = context.metadata["protocol_messages"]
    tool_rows = [row for row in rows if row.get("role") == ChatRole.TOOL.value]
    payload = json.loads(tool_rows[-1]["content"])
    assert "content" not in payload
    assert payload["metadata"]["title"] == "Example"
    assert payload["untrusted_data_notice"].startswith("Fetched web page content")
    assert len(payload["excerpt"]) <= 2500


def test_update_tool_protocol_messages_keeps_original_unknown_tool_name() -> None:
    llm_response = LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content=""),
        finish_reason=LlmFinishReason.TOOL_CALLS,
        usage=UsageSummary(model_provider="fake", model_name="fake"),
        provider="fake",
        model="fake",
        metadata={
            "planned_tool_calls": [
                ToolCall(
                    tool_name="read_url",
                    tool_call_id="call_f1",
                    args={"url": "https://example.com"},
                ).model_dump(mode="json")
            ]
        },
    )
    envelope = ToolResultEnvelope(
        call=ToolCall(
            tool_name="read_url",
            tool_call_id="call_f1",
            args={"url": "https://example.com"},
        ),
        decision=ToolPolicyDecision.DENY,
        error=ToolError(
            code="tool_not_registered",
            message="Tool 'read_url' is not registered.",
        ),
        structured_output={},
        truncated=False,
    )
    context = SimpleNamespace(
        llm_response=llm_response,
        run_input=SimpleNamespace(messages=(), input="hello"),
        metadata={},
    )
    _update_tool_protocol_messages(
        context=context, result=ToolExecutionResult(envelopes=[envelope], traces=[])
    )

    rows = context.metadata["protocol_messages"]
    assistant_rows = [
        row for row in rows if row.get("role") == ChatRole.ASSISTANT.value
    ]
    assert assistant_rows
    tool_calls = assistant_rows[-1]["metadata"]["tool_calls"]
    assert tool_calls[0]["function"]["name"] == "read_url"


def test_update_tool_protocol_messages_forces_final_after_repeated_unknown_tool() -> (
    None
):
    llm_response = LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content=""),
        finish_reason=LlmFinishReason.TOOL_CALLS,
        usage=UsageSummary(model_provider="fake", model_name="fake"),
        provider="fake",
        model="fake",
        metadata={
            "planned_tool_calls": [
                ToolCall(
                    tool_name="read_url",
                    tool_call_id="call_unknown",
                    args={"url": "https://example.com"},
                ).model_dump(mode="json")
            ]
        },
    )
    envelope = ToolResultEnvelope(
        call=ToolCall(
            tool_name="read_url",
            tool_call_id="call_unknown",
            args={"url": "https://example.com"},
        ),
        decision=ToolPolicyDecision.DENY,
        error=ToolError(
            code="tool_not_registered",
            message="Tool 'read_url' is not registered.",
        ),
        structured_output={},
        truncated=False,
    )
    context = SimpleNamespace(
        llm_response=llm_response,
        run_input=SimpleNamespace(messages=(), input="hello"),
        metadata={"unknown_tool_counts": {"read_url": 1}},
    )

    _update_tool_protocol_messages(
        context=context, result=ToolExecutionResult(envelopes=[envelope], traces=[])
    )

    assert context.metadata["force_final_answer"] is True
    assert context.metadata["force_final_answer_reason"] == "repeated_unknown_tool"
    rows = context.metadata["protocol_messages"]
    user_rows = [row for row in rows if row.get("role") == ChatRole.USER.value]
    assert "Unknown tool(s) repeated" in user_rows[-1]["content"]


def test_update_tool_protocol_messages_adds_web_fetch_verification_hint() -> None:
    llm_response = LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content=""),
        finish_reason=LlmFinishReason.TOOL_CALLS,
        usage=UsageSummary(model_provider="fake", model_name="fake"),
        provider="fake",
        model="fake",
        metadata={
            "planned_tool_calls": [
                ToolCall(
                    tool_name="web_search",
                    tool_call_id="call_w1",
                    args={"query": "sam3"},
                ).model_dump(mode="json")
            ]
        },
    )
    envelope = ToolResultEnvelope(
        call=ToolCall(
            tool_name="web_search", tool_call_id="call_w1", args={"query": "sam3"}
        ),
        decision=ToolPolicyDecision.ALLOW,
        structured_output={
            "summary": "2 results for 'sam3'",
            "results": [
                {
                    "title": "SAM3",
                    "url": "https://ai.meta.com/blog/segment-anything-model-3/",
                }
            ],
            "result_preview_urls": [
                "https://ai.meta.com/blog/segment-anything-model-3/"
            ],
        },
        truncated=False,
    )
    context = SimpleNamespace(
        llm_response=llm_response,
        run_input=SimpleNamespace(messages=(), input="hello"),
        metadata={},
    )
    _update_tool_protocol_messages(
        context=context, result=ToolExecutionResult(envelopes=[envelope], traces=[])
    )
    rows = context.metadata["protocol_messages"]
    tool_rows = [row for row in rows if row.get("role") == ChatRole.TOOL.value]
    assert tool_rows
    payload = json.loads(tool_rows[-1]["content"])
    assert payload["result_preview_urls"] == [
        "https://ai.meta.com/blog/segment-anything-model-3/"
    ]
    user_rows = [row for row in rows if row.get("role") == ChatRole.USER.value]
    assert any(
        "open at least one returned URL with web_fetch" in (item.get("content") or "")
        for item in user_rows
    )


def test_force_web_fetch_for_source_verified_research_after_search() -> None:
    context = SimpleNamespace(
        run_input=SimpleNamespace(
            tool_policy=SimpleNamespace(
                metadata={
                    "task_contract": {
                        "requires_research": True,
                        "research_depth": "source_verified_report",
                    }
                },
                allowed_tools=None,
                denied_tools=(),
            )
        ),
        metadata={
            "effective_tool_names": ("web_search", "web_fetch"),
            "tool_results": [
                {
                    "call": {
                        "tool_name": "web_search",
                        "tool_call_id": "call_search",
                        "args": {"query": "fork join"},
                    },
                    "structured_output": {
                        "results": [
                            {
                                "title": "Fork join",
                                "url": "https://example.com/fork",
                            }
                        ]
                    },
                }
            ],
        },
    )

    _force_web_fetch_for_source_verified_research(context)

    assert context.metadata["tool_choice_override"] == {
        "type": "tool",
        "name": "web_fetch",
    }
    assert (
        context.metadata["continuation_nudge_reason"]
        == "source_verified_fetch_required"
    )


def test_source_diversity_repair_forces_search_after_same_domain_fetches() -> None:
    context = SimpleNamespace(
        run_input=SimpleNamespace(
            tool_policy=SimpleNamespace(
                metadata={
                    "task_contract": {
                        "requires_research": True,
                        "research_depth": "source_verified_report",
                    }
                },
                allowed_tools=None,
                denied_tools=(),
            )
        ),
        metadata={
            "effective_tool_names": ("web_search", "web_fetch"),
            "tool_results": [
                {"call": {"tool_name": "web_search", "args": {"query": "q"}}},
                {
                    "call": {
                        "tool_name": "web_fetch",
                        "args": {"url": "https://example.com/a"},
                    },
                    "structured_output": {"url": "https://example.com/a"},
                },
                {
                    "call": {
                        "tool_name": "web_fetch",
                        "args": {"url": "https://example.com/b"},
                    },
                    "structured_output": {"url": "https://example.com/b"},
                },
            ],
        },
    )

    _force_web_fetch_for_source_verified_research(context)

    assert context.metadata["tool_choice_override"] == {
        "type": "tool",
        "name": "web_search",
    }
    assert context.metadata["continuation_nudge_reason"] == (
        "source_diversity_search_required"
    )
    assert context.metadata["research_avoid_domains"] == ["example.com"]


def test_source_diversity_repair_forces_fetch_after_search() -> None:
    context = SimpleNamespace(
        run_input=SimpleNamespace(
            tool_policy=SimpleNamespace(
                metadata={
                    "task_contract": {
                        "requires_research": True,
                        "research_depth": "source_verified_report",
                    }
                },
                allowed_tools=None,
                denied_tools=(),
            )
        ),
        metadata={
            "effective_tool_names": ("web_search", "web_fetch"),
            "tool_results": [
                {
                    "call": {
                        "tool_name": "web_fetch",
                        "args": {"url": "https://example.com/a"},
                    },
                    "structured_output": {"url": "https://example.com/a"},
                },
                {
                    "call": {
                        "tool_name": "web_fetch",
                        "args": {"url": "https://example.com/b"},
                    },
                    "structured_output": {"url": "https://example.com/b"},
                },
                {"call": {"tool_name": "web_search", "args": {"query": "q"}}},
            ],
        },
    )

    _force_web_fetch_for_source_verified_research(context)

    assert context.metadata["tool_choice_override"] == {
        "type": "tool",
        "name": "web_fetch",
    }
    assert context.metadata["continuation_nudge_reason"] == (
        "source_diversity_fetch_required"
    )


def test_update_tool_protocol_messages_coalesces_user_hints_with_force_final() -> None:
    llm_response = LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content=""),
        finish_reason=LlmFinishReason.TOOL_CALLS,
        usage=UsageSummary(model_provider="fake", model_name="fake"),
        provider="fake",
        model="fake",
        metadata={
            "planned_tool_calls": [
                ToolCall(
                    tool_name="file_write",
                    tool_call_id="call_d1",
                    args={"path": "/etc/passwd", "content": "x"},
                ).model_dump(mode="json")
            ]
        },
    )
    envelope = ToolResultEnvelope(
        call=ToolCall(
            tool_name="file_write",
            tool_call_id="call_d1",
            args={"path": "/etc/passwd", "content": "x"},
        ),
        decision=ToolPolicyDecision.DENY,
        structured_output={},
        truncated=False,
        error=ToolError(code="tool_handler_error", message="policy denied"),
    )
    context = SimpleNamespace(
        llm_response=llm_response,
        run_input=SimpleNamespace(messages=(), input="hello"),
        metadata={"force_final_answer": True},
    )
    _update_tool_protocol_messages(
        context=context, result=ToolExecutionResult(envelopes=[envelope], traces=[])
    )
    rows = context.metadata["protocol_messages"]
    user_rows = [row for row in rows if row.get("role") == ChatRole.USER.value]
    assert len(user_rows) == 2
    content = user_rows[-1].get("content") or ""
    assert "was denied" in content
    assert "Do not call additional tools unless absolutely required" in content


def test_update_tool_protocol_messages_drops_empty_assistant_before_user_hint() -> None:
    llm_response = LlmResponse(
        message=ChatMessage(
            role=ChatRole.ASSISTANT, content="<tool_call>{}</tool_call>"
        ),
        finish_reason=LlmFinishReason.TOOL_CALLS,
        usage=UsageSummary(model_provider="fake", model_name="fake"),
        provider="fake",
        model="fake",
        metadata={
            "planned_tool_calls": [
                ToolCall(
                    tool_name="web_search",
                    tool_call_id="call_w2",
                    args={"query": "sam3"},
                ).model_dump(mode="json")
            ]
        },
    )
    envelope = ToolResultEnvelope(
        call=ToolCall(
            tool_name="web_search", tool_call_id="call_w2", args={"query": "sam3"}
        ),
        decision=ToolPolicyDecision.ALLOW,
        structured_output={"results": [], "summary": "no results"},
        truncated=False,
    )
    context = SimpleNamespace(
        llm_response=llm_response,
        run_input=SimpleNamespace(messages=(), input="hello"),
        metadata={"force_final_answer": True},
    )
    _update_tool_protocol_messages(
        context=context, result=ToolExecutionResult(envelopes=[envelope], traces=[])
    )
    rows = context.metadata["protocol_messages"]
    assert not any(
        row.get("role") == ChatRole.ASSISTANT.value
        and not (row.get("content") or "").strip()
        and not (
            isinstance(row.get("metadata"), dict)
            and row.get("metadata", {}).get("tool_calls")
        )
        for row in rows
    )


def test_deliverable_request_forces_final_answer_after_data_tool() -> None:
    context = SimpleNamespace(
        tool_calls=1,
        llm_step_count=1,
        run_input=SimpleNamespace(
            max_tool_calls=20,
            max_steps=20,
            tool_policy=SimpleNamespace(
                metadata={"deliverable_request": {"enabled": True}}
            ),
        ),
        metadata={
            "tool_results": [
                {
                    "call": {
                        "tool_name": "web_search",
                        "args": {"query": "Fender history"},
                    }
                }
            ]
        },
    )
    assert _should_force_final_answer(context) is True


def test_source_verified_research_waits_for_fetched_sources() -> None:
    context = SimpleNamespace(
        tool_calls=1,
        llm_step_count=1,
        run_input=SimpleNamespace(
            max_tool_calls=20,
            max_steps=20,
            tool_policy=SimpleNamespace(
                metadata={
                    "task_contract": {
                        "kind": "research",
                        "requires_research": True,
                        "research_depth": "source_verified_report",
                    }
                },
                allowed_tools=None,
                denied_tools=[],
            ),
        ),
        metadata={
            "effective_tool_names": ("web_search", "web_fetch"),
            "tool_results": [
                {
                    "call": {
                        "tool_name": "web_search",
                        "args": {"query": "fork join queues"},
                    }
                }
            ],
        },
    )

    assert _should_force_final_answer(context) is False

    context.metadata["tool_results"].extend(
        [
            {
                "call": {
                    "tool_name": "web_fetch",
                    "args": {"url": "https://example.com/a"},
                },
                "structured_output": {"url": "https://example.com/a", "text": "a"},
                "error": None,
            },
            {
                "call": {
                    "tool_name": "web_fetch",
                    "args": {"url": "https://example.org/b"},
                },
                "structured_output": {"url": "https://example.org/b", "text": "b"},
                "error": None,
            },
        ]
    )

    assert _should_force_final_answer(context) is True


def test_source_verified_research_requires_distinct_fetched_domains() -> None:
    context = SimpleNamespace(
        tool_calls=3,
        llm_step_count=3,
        run_input=SimpleNamespace(
            max_tool_calls=20,
            max_steps=20,
            tool_policy=SimpleNamespace(
                metadata={
                    "task_contract": {
                        "kind": "research",
                        "requires_research": True,
                        "research_depth": "source_verified_report",
                    }
                },
                allowed_tools=None,
                denied_tools=[],
            ),
        ),
        metadata={
            "effective_tool_names": ("web_search", "web_fetch"),
            "tool_results": [
                {
                    "call": {
                        "tool_name": "web_search",
                        "args": {"query": "fork join queues"},
                    }
                },
                {
                    "call": {
                        "tool_name": "web_fetch",
                        "args": {"url": "https://example.com/a"},
                    },
                    "structured_output": {"url": "https://example.com/a", "text": "a"},
                    "error": None,
                },
                {
                    "call": {
                        "tool_name": "web_fetch",
                        "args": {"url": "https://example.com/b"},
                    },
                    "structured_output": {"url": "https://example.com/b", "text": "b"},
                    "error": None,
                },
            ],
        },
    )

    assert _should_force_final_answer(context) is False


def test_research_satisfied_can_force_final_for_synthesis_todo() -> None:
    context = SimpleNamespace(
        tool_calls=3,
        llm_step_count=3,
        run_input=SimpleNamespace(
            max_tool_calls=20,
            max_steps=20,
            tool_policy=SimpleNamespace(
                metadata={
                    "task_contract": {
                        "kind": "research",
                        "requires_research": True,
                        "research_depth": "source_verified_report",
                    }
                },
                allowed_tools=None,
                denied_tools=[],
            ),
        ),
        metadata={
            "effective_tool_names": ("web_search", "web_fetch", "todo_write"),
            "planning_state": {
                "run_id": "run_research",
                "todos": [
                    {"todo_id": "search", "content": "Search", "status": "completed"},
                    {"todo_id": "fetch", "content": "Fetch", "status": "completed"},
                    {
                        "todo_id": "synthesize",
                        "content": "Write final synthesis",
                        "status": "in_progress",
                    },
                ],
                "metadata": {},
            },
            "tool_results": [
                {"call": {"tool_name": "web_search", "args": {"query": "q"}}},
                {
                    "call": {
                        "tool_name": "web_fetch",
                        "args": {"url": "https://example.com/a"},
                    },
                    "structured_output": {"url": "https://example.com/a", "text": "a"},
                    "error": None,
                },
                {
                    "call": {
                        "tool_name": "web_fetch",
                        "args": {"url": "https://example.org/b"},
                    },
                    "structured_output": {"url": "https://example.org/b", "text": "b"},
                    "error": None,
                },
            ],
        },
    )

    assert _should_force_final_answer(context) is True
    assert context.metadata["final_readiness"] == "allowed"
    assert context.metadata["repair_required_reasons"] == []


def test_research_satisfied_forces_final_despite_stale_process_todos() -> None:
    context = SimpleNamespace(
        tool_calls=3,
        llm_step_count=3,
        run_input=SimpleNamespace(
            max_tool_calls=20,
            max_steps=20,
            tool_policy=SimpleNamespace(
                metadata={
                    "task_contract": {
                        "kind": "research",
                        "requires_research": True,
                        "research_depth": "source_verified_report",
                    }
                },
                allowed_tools=None,
                denied_tools=[],
            ),
        ),
        metadata={
            "effective_tool_names": ("web_search", "web_fetch", "todo_write"),
            "planning_state": {
                "run_id": "run_research",
                "todos": [
                    {
                        "todo_id": "search",
                        "content": "Search for sources",
                        "status": "in_progress",
                    },
                    {
                        "todo_id": "fetch",
                        "content": "Fetch relevant pages",
                        "status": "pending",
                    },
                    {
                        "todo_id": "synthesize",
                        "content": "Write final synthesis",
                        "status": "pending",
                    },
                ],
                "metadata": {},
            },
            "tool_results": [
                {"call": {"tool_name": "web_search", "args": {"query": "q"}}},
                {
                    "call": {
                        "tool_name": "web_fetch",
                        "args": {"url": "https://example.com/a"},
                    },
                    "structured_output": {"url": "https://example.com/a", "text": "a"},
                    "error": None,
                },
                {
                    "call": {
                        "tool_name": "web_fetch",
                        "args": {"url": "https://example.org/b"},
                    },
                    "structured_output": {"url": "https://example.org/b", "text": "b"},
                    "error": None,
                },
            ],
        },
    )

    assert _should_force_final_answer(context) is True
    assert context.metadata["final_readiness"] == "allowed"
    assert context.metadata["repair_required_reasons"] == []


def test_zero_result_force_final_does_not_bypass_unfinished_todos() -> None:
    context = SimpleNamespace(
        tool_calls=4,
        llm_step_count=4,
        run_input=SimpleNamespace(
            max_tool_calls=20,
            max_steps=20,
            tool_policy=SimpleNamespace(
                metadata={
                    "task_contract": {
                        "kind": "research",
                        "requires_research": True,
                        "research_depth": "source_verified_report",
                    }
                },
                allowed_tools=None,
                denied_tools=[],
            ),
        ),
        metadata={
            "web_search_zero_streak": 1,
            "effective_tool_names": ("web_search", "web_fetch", "todo_write"),
            "planning_state": {
                "run_id": "run_research",
                "todos": [
                    {"todo_id": "search", "content": "Search", "status": "completed"},
                    {
                        "todo_id": "analyze",
                        "content": "Analyze fetched sources",
                        "status": "in_progress",
                    },
                    {
                        "todo_id": "synthesize",
                        "content": "Write final synthesis",
                        "status": "in_progress",
                    },
                ],
                "metadata": {},
            },
            "tool_results": [
                {"call": {"tool_name": "web_search", "args": {"query": "q"}}},
                {
                    "call": {
                        "tool_name": "web_fetch",
                        "args": {"url": "https://example.com/a"},
                    },
                    "structured_output": {"url": "https://example.com/a", "text": "a"},
                    "error": None,
                },
                {
                    "call": {
                        "tool_name": "web_fetch",
                        "args": {"url": "https://example.com/b"},
                    },
                    "structured_output": {"url": "https://example.com/b", "text": "b"},
                    "error": None,
                },
            ],
        },
    )

    assert _should_force_final_answer(context) is False
    assert (
        context.metadata.get("force_final_answer_reason") != "web_search_zero_results"
    )
    assert context.metadata["repair_required_reasons"] == [
        "unfinished_todos",
        "insufficient_source_diversity",
    ]


def test_deep_research_search_only_does_not_force_research_satisfied() -> None:
    context = SimpleNamespace(
        tool_calls=2,
        llm_step_count=2,
        run_input=SimpleNamespace(
            max_tool_calls=20,
            max_steps=20,
            tool_policy=SimpleNamespace(
                metadata={
                    "deep_research_mode": {"enabled": True},
                    "task_contract": {
                        "kind": "research",
                        "requires_research": True,
                        "research_mode": "deep",
                        "research_depth": "source_verified_report",
                        "research_profile": "medium",
                    },
                },
                allowed_tools=None,
                denied_tools=[],
            ),
        ),
        metadata={
            "effective_tool_names": (
                "web_search",
                "web_fetch",
                "file_write",
                "todo_write",
            ),
            "planning_state": {
                "run_id": "run_deep_search_only",
                "todos": [
                    {
                        "todo_id": "discover",
                        "content": "Discover sources",
                        "status": "in_progress",
                    },
                    {
                        "todo_id": "write",
                        "content": "Write report",
                        "status": "pending",
                    },
                ],
                "metadata": {},
            },
            "tool_results": [
                {
                    "call": {
                        "tool_name": "web_search",
                        "args": {"query": "fork join"},
                    },
                    "structured_output": {
                        "results": [
                            {
                                "title": "Fork-join queue",
                                "url": "https://example.test/fork",
                            }
                        ]
                    },
                    "error": None,
                }
            ],
        },
    )

    assert _should_force_final_answer(context) is False
    assert "missing_fetched_sources" in context.metadata["repair_required_reasons"]
    assert context.metadata.get("force_final_answer_reason") != (
        "research_request_satisfied"
    )


def test_research_deliverable_waits_for_source_verified_fetches() -> None:
    context = SimpleNamespace(
        tool_calls=1,
        llm_step_count=1,
        run_input=SimpleNamespace(
            max_tool_calls=20,
            max_steps=20,
            tool_policy=SimpleNamespace(
                metadata={
                    "deliverable_request": {"enabled": True},
                    "task_contract": {
                        "kind": "deliverable",
                        "requires_research": True,
                        "research_depth": "source_verified_report",
                    },
                },
                allowed_tools=None,
                denied_tools=[],
            ),
        ),
        metadata={
            "effective_tool_names": ("web_search", "web_fetch"),
            "tool_results": [
                {
                    "call": {
                        "tool_name": "web_search",
                        "args": {"query": "Fender history"},
                    }
                }
            ],
        },
    )

    assert _should_force_final_answer(context) is False


def test_source_verified_research_allows_final_after_repeated_fetch_failures() -> None:
    context = SimpleNamespace(
        tool_calls=3,
        llm_step_count=3,
        run_input=SimpleNamespace(
            max_tool_calls=20,
            max_steps=20,
            tool_policy=SimpleNamespace(
                metadata={
                    "task_contract": {
                        "kind": "research",
                        "requires_research": True,
                        "research_depth": "source_verified_report",
                    }
                },
                allowed_tools=None,
                denied_tools=[],
            ),
        ),
        metadata={
            "effective_tool_names": ("web_search", "web_fetch"),
            "tool_results": [
                {"call": {"tool_name": "web_search", "args": {"query": "paper"}}},
                {
                    "call": {
                        "tool_name": "web_fetch",
                        "args": {"url": "https://example.com/a"},
                    },
                    "error": {"code": "fetch_failed"},
                },
                {
                    "call": {
                        "tool_name": "web_fetch",
                        "args": {"url": "https://example.org/b"},
                    },
                    "structured_output": {"error_code": "timeout"},
                },
            ],
        },
    )

    assert _should_force_final_answer(context) is True
    assert context.metadata["research_fetch_fallback_required"] is True


def test_failed_fetch_domain_does_not_satisfy_source_diversity() -> None:
    context = SimpleNamespace(
        tool_calls=3,
        llm_step_count=2,
        run_input=SimpleNamespace(
            max_tool_calls=20,
            max_steps=20,
            tool_policy=SimpleNamespace(
                metadata={
                    "task_contract": {
                        "kind": "research",
                        "requires_research": True,
                        "research_depth": "source_verified_report",
                    }
                },
                allowed_tools=None,
                denied_tools=[],
            ),
        ),
        metadata={
            "effective_tool_names": ("web_search", "web_fetch"),
            "tool_results": [
                {"call": {"tool_name": "web_search", "args": {"query": "paper"}}},
                {
                    "call": {
                        "tool_name": "web_fetch",
                        "args": {"url": "https://example.com/a"},
                    },
                    "structured_output": {"url": "https://example.com/a", "text": "a"},
                    "error": None,
                },
                {
                    "call": {
                        "tool_name": "web_fetch",
                        "args": {"url": "https://blocked.example.org/b"},
                    },
                    "structured_output": {
                        "url": "https://blocked.example.org/b",
                        "status": "failed",
                        "error": "HTTP 403",
                    },
                    "error": {"message": "HTTP 403"},
                },
            ],
        },
    )

    assert _should_force_final_answer(context) is False


def test_deliverable_request_waits_for_unfinished_todos_after_data_tool() -> None:
    context = SimpleNamespace(
        tool_calls=1,
        llm_step_count=1,
        run_input=SimpleNamespace(
            max_tool_calls=20,
            max_steps=20,
            tool_policy=SimpleNamespace(
                metadata={"deliverable_request": {"enabled": True}}
            ),
        ),
        metadata={
            "planning_state": {
                "run_id": "run_todos",
                "todos": [
                    {
                        "todo_id": "search",
                        "content": "Search sources",
                        "status": "completed",
                    },
                    {
                        "todo_id": "analyze",
                        "content": "Analyze second source",
                        "status": "pending",
                    },
                ],
                "metadata": {},
            },
            "tool_results": [
                {
                    "call": {
                        "tool_name": "web_search",
                        "args": {"query": "fork join queues"},
                    }
                }
            ],
        },
    )
    assert _should_force_final_answer(context) is False


def test_deliverable_request_forces_after_progress_only_tool() -> None:
    context = SimpleNamespace(
        tool_calls=1,
        llm_step_count=1,
        run_input=SimpleNamespace(
            max_tool_calls=20,
            max_steps=20,
            tool_policy=SimpleNamespace(
                metadata={"deliverable_request": {"enabled": True}}
            ),
        ),
        metadata={
            "tool_results": [
                {
                    "call": {
                        "tool_name": "planning_state_update",
                        "args": {},
                    }
                }
            ]
        },
    )
    assert _should_force_final_answer(context) is True


def test_python_reliability_request_forces_final_after_python_result() -> None:
    context = SimpleNamespace(
        tool_calls=1,
        llm_step_count=1,
        run_input=SimpleNamespace(
            max_tool_calls=20,
            max_steps=20,
            tool_policy=SimpleNamespace(
                metadata={"python_reliability_request": {"enabled": True}}
            ),
        ),
        metadata={
            "tool_results": [
                {
                    "call": {
                        "tool_name": "python",
                        "args": {"code": "print(17 * 23)"},
                    },
                    "summary": "python ok: 1 obs",
                    "error": None,
                }
            ]
        },
    )
    assert _should_force_final_answer(context) is True


def test_research_request_waits_for_pending_python_reliability_result() -> None:
    context = SimpleNamespace(
        tool_calls=1,
        llm_step_count=1,
        run_input=SimpleNamespace(
            max_tool_calls=20,
            max_steps=20,
            tool_policy=SimpleNamespace(
                metadata={
                    "task_contract": {
                        "kind": "research",
                        "requires_research": True,
                    },
                    "python_reliability_request": {"enabled": True},
                }
            ),
        ),
        metadata={
            "tool_results": [
                {
                    "call": {
                        "tool_name": "web_search",
                        "args": {"query": "population"},
                    }
                }
            ]
        },
    )
    assert _should_force_final_answer(context) is False


def test_python_policy_error_does_not_force_final_before_recovery() -> None:
    context = SimpleNamespace(
        tool_calls=1,
        llm_step_count=1,
        run_input=SimpleNamespace(
            max_tool_calls=20,
            max_steps=20,
            tool_policy=SimpleNamespace(
                metadata={"python_reliability_request": {"enabled": True}}
            ),
        ),
        metadata={
            "tool_results": [
                {
                    "call": {
                        "tool_name": "python",
                        "args": {"code": "import os"},
                    },
                    "summary": "python policy: imports blocked by sandbox (os)",
                    "error": None,
                }
            ]
        },
    )
    assert _should_force_final_answer(context) is False
