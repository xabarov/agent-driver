"""Portable contracts requested by Zion's 0.3.2 embedding acceptance.

These tests intentionally reproduce the current gaps. The remediation branch is
ready when all cells pass without a Zion dependency or product-specific policy.
"""

from __future__ import annotations

import json

from agent_driver.context import (
    TokenPressureInput,
    estimate_token_pressure,
    trim_context,
    truncate_tool_call_args,
)
from agent_driver.contracts import ContextBudget
from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage
from agent_driver.runtime.single_agent.protocol_validate import (
    validate_and_repair_protocol_messages,
)


def test_message_cap_preserves_system_contract_and_current_turn() -> None:
    messages: list[dict[str, object]] = [
        {"role": "system", "content": "immutable embedder contract"}
    ]
    messages.extend(
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"turn-{index}",
        }
        for index in range(30)
    )

    trimmed = trim_context(
        budget=ContextBudget(max_chars=100_000, max_messages=24),
        prompt_messages=messages,
    )

    assert len(trimmed.prompt_messages) == 24
    assert trimmed.prompt_messages[0] == messages[0]
    assert trimmed.prompt_messages[-1] == messages[-1]


def test_material_evidence_reaches_compaction_before_route_trimming() -> None:
    evidence = json.dumps(
        {"units": [{"id": index, "fact": "x" * 120} for index in range(40)]}
    )
    messages: list[dict[str, object]] = [
        {
            "role": "system",
            "content": "sealed embedder contract",
            "metadata": {"compaction_protected": True},
        },
        {
            "role": "user",
            "content": evidence,
            "metadata": {
                "compaction_evidence": True,
                "material_unit_hashes": ["unit-early", "unit-late"],
            },
        },
        {
            "role": "user",
            "content": "return exact JSON",
            "metadata": {"compaction_protected": True},
        },
    ]

    trimmed = trim_context(
        budget=ContextBudget(max_chars=128, max_messages=2),
        prompt_messages=messages,
    )

    assert trimmed.prompt_messages == messages
    assert trimmed.metadata["final_chars"] > 128
    assert any(
        row.reason == "material_context_preserved_for_compaction_over_budget"
        and row.metadata.get("material_unit_count") == 2
        for row in trimmed.audit
    )


def test_structured_current_turn_and_tool_result_are_never_raw_sliced() -> None:
    structured = json.dumps(
        {"rows": [{"id": index, "fact": "x" * 40} for index in range(80)]}
    )
    trimmed = trim_context(
        budget=ContextBudget(max_chars=128, max_messages=24),
        prompt_messages=[
            {"role": "system", "content": "immutable embedder contract"},
            {"role": "user", "content": structured},
        ],
    )
    assert json.loads(str(trimmed.prompt_messages[-1]["content"]))["rows"][-1]["id"] == 79

    repaired = validate_and_repair_protocol_messages(
        [
            ChatMessage(
                role=ChatRole.ASSISTANT,
                content="",
                metadata={
                    "tool_calls": [
                        {
                            "id": "call_json",
                            "type": "function",
                            "function": {"name": "lookup", "arguments": "{}"},
                        }
                    ]
                },
            ),
            ChatMessage(
                role=ChatRole.TOOL,
                name="lookup",
                tool_call_id="call_json",
                content=structured,
            ),
        ],
        max_total_content_chars=600,
    )
    assert (repaired.messages[-1].content or "").startswith(
        "[trimmed] Full raw payload"
    )
    assert not (repaired.messages[-1].content or "").startswith(structured[:40])


def test_pressure_counts_tool_call_metadata_and_tool_catalog() -> None:
    pressure = estimate_token_pressure(
        TokenPressureInput(
            prompt_messages=(
                {
                    "role": "assistant",
                    "content": "",
                    "metadata": {
                        "tool_calls": [
                            {"id": "call_1", "function": {"name": "lookup"}}
                        ]
                    },
                },
            ),
            tool_schemas=(
                {
                    "type": "function",
                    "function": {"name": "lookup", "description": "d" * 400},
                },
            ),
        )
    )

    assert pressure["prompt_metadata_chars"] > 0
    assert pressure["tool_schema_chars"] > 400
    assert pressure["tool_schema_count"] == 1


def test_large_structured_tool_argument_stays_atomic() -> None:
    structured = json.dumps(
        {"rows": [{"id": index, "fact": "x" * 20} for index in range(500)]}
    )
    message = ChatMessage(
        role=ChatRole.ASSISTANT,
        content="",
        metadata={
            "tool_calls": [
                {"tool_name": "write_artifact", "args": {"payload": structured}}
            ]
        },
    )

    result = truncate_tool_call_args(
        [message, ChatMessage(role=ChatRole.USER, content="continue")],
        max_arg_chars=100,
        protect_last=1,
    )

    assert result.messages[0].metadata["tool_calls"][0]["args"]["payload"] == structured
    assert (
        result.retained_structured[0]["strategy"]
        == "structured_json_retained_atomically"
    )
