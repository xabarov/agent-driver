"""Tests for ReAct system instruction composition."""

from __future__ import annotations

from types import SimpleNamespace

from agent_driver.contracts import AgentRunInput, ToolManifest, ToolPolicyInput
from agent_driver.runtime.single_agent.config_sections import PythonToolSettings
from agent_driver.runtime.single_agent.llm_step import (
    _effective_code_agent_imports,
    _react_system_instruction,
)
from agent_driver.tools.registry import ToolRegistry


def test_react_system_prompt_contains_todo_status_rules() -> None:
    """ReAct profile should always receive base system instruction.

    Note: ``chat_mode=True`` causes ``_react_system_instruction`` to read
    ``context.metadata.get("planning_state")`` — give the mock an empty
    metadata dict so the lookup falls through cleanly. (Production code
    uses a real ``RunContext`` where ``metadata`` is always a dict.)
    """
    registry = ToolRegistry()
    host = SimpleNamespace(
        _deps=SimpleNamespace(tool_registry=registry),
        _config=SimpleNamespace(python_tool=PythonToolSettings(enabled=False)),
    )
    context = SimpleNamespace(
        run_input=AgentRunInput(
            input="hello",
            agent_id="agent",
            graph_preset="single_react",
            app_metadata={"chat_mode": True},
        ),
        metadata={},
    )
    instruction = _react_system_instruction(host, context)
    assert instruction is not None
    # The exact wording of the policy section drifts over time; pin the
    # invariant ("todo statuses are listed") rather than the prose.
    assert "todo_write" in instruction
    assert "pending" in instruction and "in_progress" in instruction
    assert "completed" in instruction and "cancelled" in instruction
    assert "at most one todo may be in_progress" in instruction


def test_react_system_prompt_includes_deliverable_runtime_reminder() -> None:
    """Deliverable turns should get an explicit anti-replanning reminder."""
    registry = ToolRegistry()
    host = SimpleNamespace(
        _deps=SimpleNamespace(tool_registry=registry),
        _config=SimpleNamespace(python_tool=PythonToolSettings(enabled=False)),
    )
    context = SimpleNamespace(
        run_input=AgentRunInput(
            input="напиши итоговый черновик, не план",
            agent_id="agent",
            graph_preset="single_react",
            app_metadata={"chat_mode": True},
            tool_policy=ToolPolicyInput(
                metadata={"deliverable_request": {"enabled": True}}
            ),
        ),
        metadata={
            "planning_state": {
                "todos": [
                    {"id": "research", "content": "Research", "status": "completed"}
                ]
            }
        },
    )
    instruction = _react_system_instruction(host, context)
    assert instruction is not None
    assert "deliverable_request_active" in instruction
    assert "produce the requested final answer" in instruction
    assert "do not restart planning" in instruction


def test_react_system_prompt_includes_task_contract_reminder() -> None:
    """Chat mode should surface the lightweight task contract to the model."""
    registry = ToolRegistry()
    host = SimpleNamespace(
        _deps=SimpleNamespace(tool_registry=registry),
        _config=SimpleNamespace(python_tool=PythonToolSettings(enabled=False)),
    )
    context = SimpleNamespace(
        run_input=AgentRunInput(
            input="напиши реферат по Fender, не план",
            agent_id="agent",
            graph_preset="single_react",
            app_metadata={"chat_mode": True},
            tool_policy=ToolPolicyInput(
                metadata={
                    "task_contract": {
                        "kind": "deliverable",
                        "goal": "напиши реферат по Fender, не план",
                        "approach": "Answer now.",
                        "acceptance_criteria": ["Final answer, not another plan."],
                        "out_of_scope": ["Restarting the plan."],
                    }
                }
            ),
        ),
        metadata={},
    )
    instruction = _react_system_instruction(host, context)
    assert instruction is not None
    assert "task_contract_active (deliverable)" in instruction
    assert "Final answer, not another plan" in instruction
    assert "Restarting the plan" in instruction


def test_react_system_prompt_includes_plan_mode_runtime_reminder() -> None:
    """Plan mode should be represented as a compact runtime reminder."""
    registry = ToolRegistry()
    host = SimpleNamespace(
        _deps=SimpleNamespace(tool_registry=registry),
        _config=SimpleNamespace(python_tool=PythonToolSettings(enabled=False)),
    )
    context = SimpleNamespace(
        run_input=AgentRunInput(
            input="implement feature",
            agent_id="agent",
            graph_preset="single_react",
            app_metadata={"chat_mode": True},
        ),
        metadata={
            "planning_state": {
                "todos": [],
                "metadata": {"planning_mode": "plan"},
            }
        },
    )
    instruction = _react_system_instruction(host, context)
    assert instruction is not None
    assert "planning_mode_active" in instruction
    assert "Stay read-only" in instruction
    assert "exit_plan_mode_v2" in instruction


def test_react_system_prompt_includes_workspace_cwd_when_present() -> None:
    """Workspace hint should be appended when app metadata has workspace_cwd."""
    registry = ToolRegistry()
    host = SimpleNamespace(
        _deps=SimpleNamespace(tool_registry=registry),
        _config=SimpleNamespace(python_tool=PythonToolSettings(enabled=False)),
    )
    context = SimpleNamespace(
        run_input=AgentRunInput(
            input="hello",
            agent_id="agent",
            graph_preset="single_react",
            app_metadata={"workspace_cwd": "/tmp/workspace"},
        )
    )
    instruction = _react_system_instruction(host, context)
    assert instruction is not None
    assert "Workspace cwd: /tmp/workspace" in instruction


def test_react_system_prompt_includes_python_addendum_when_tool_enabled() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolManifest(name="python", description="python tool"),
        lambda _args: {},
    )
    host = SimpleNamespace(
        _deps=SimpleNamespace(tool_registry=registry),
        _config=SimpleNamespace(
            python_tool=PythonToolSettings(
                enabled=True,
                include_scientific_stack=False,
                default_imports=("math", "re"),
            )
        ),
    )
    context = SimpleNamespace(
        run_input=AgentRunInput(
            input="hello",
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    instruction = _react_system_instruction(host, context)
    assert instruction is not None
    assert "Python tool sandbox policy:" in instruction
    assert "Allowed imports: math, re" in instruction
    assert "Limits: exec <=" in instruction
    assert "Sessions are persistent per session_id" in instruction
    assert "scipy" in instruction
    assert "numpy" in instruction
    assert (
        "do not assume numpy" in instruction.lower() or "NOT available" in instruction
    )


def test_react_system_prompt_omits_python_addendum_when_disabled() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolManifest(name="python", description="python tool"),
        lambda _args: {},
    )
    host = SimpleNamespace(
        _deps=SimpleNamespace(tool_registry=registry),
        _config=SimpleNamespace(python_tool=PythonToolSettings(enabled=False)),
    )
    context = SimpleNamespace(
        run_input=AgentRunInput(
            input="hello",
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    instruction = _react_system_instruction(host, context)
    assert instruction is not None
    assert "Python tool sandbox policy:" not in instruction


def test_effective_code_agent_imports_fallback_to_python_tool_defaults() -> None:
    host = SimpleNamespace(
        _config=SimpleNamespace(
            authorized_imports=tuple(),
            python_tool=PythonToolSettings(
                enabled=True, default_imports=("math", "re")
            ),
        )
    )
    assert _effective_code_agent_imports(host) == ("math", "re")
