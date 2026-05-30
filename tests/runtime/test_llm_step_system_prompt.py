"""Tests for ReAct system instruction composition."""

from __future__ import annotations

from types import SimpleNamespace

from agent_driver.contracts import AgentRunInput, ToolManifest, ToolPolicyInput
from agent_driver.runtime.single_agent.config_sections import PythonToolSettings
from agent_driver.runtime.single_agent.llm_step import (
    _effective_code_agent_imports,
    _react_system_instruction,
    _runtime_attachment_messages,
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
    assert "Do not invent hidden/internal tools" in instruction


def test_runtime_attachments_include_deliverable_runtime_reminder() -> None:
    """Deliverable turns should get an explicit anti-replanning attachment."""
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
    attachment_text = "\n".join(
        message.content for message in _runtime_attachment_messages(context)
    )
    assert "deliverable_request_active" in attachment_text
    assert "produce the final deliverable" in attachment_text
    assert "do not restart planning" in attachment_text


def test_runtime_attachments_include_python_reliability_reminder() -> None:
    context = SimpleNamespace(
        run_input=AgentRunInput(
            input="Сколько букв r в strawberry? Проверь точно.",
            agent_id="agent",
            graph_preset="single_react",
            app_metadata={"chat_mode": True},
            tool_policy=ToolPolicyInput(
                metadata={"python_reliability_request": {"enabled": True}}
            ),
        ),
        metadata={},
    )

    attachment_text = "\n".join(
        message.content for message in _runtime_attachment_messages(context)
    )

    assert "python_reliability_request" in attachment_text
    assert "call the python tool before the final answer" in attachment_text


def test_runtime_attachments_skip_python_reminder_when_python_denied() -> None:
    context = SimpleNamespace(
        run_input=AgentRunInput(
            input="Сколько букв r в strawberry? Проверь точно.",
            agent_id="agent",
            graph_preset="single_react",
            app_metadata={"chat_mode": True},
            tool_policy=ToolPolicyInput(
                denied_tools=["python"],
                metadata={"python_reliability_request": {"enabled": True}},
            ),
        ),
        metadata={},
    )

    attachment_text = "\n".join(
        message.content for message in _runtime_attachment_messages(context)
    )

    assert "python_reliability_request" not in attachment_text


def test_runtime_attachments_include_task_contract_reminder() -> None:
    """Chat mode should surface the lightweight task contract to the model."""
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
    attachment_text = "\n".join(
        message.content for message in _runtime_attachment_messages(context)
    )
    assert "task_contract_active (deliverable)" in attachment_text
    assert "Final answer, not another plan" in attachment_text
    assert "Restarting the plan" in attachment_text


def test_runtime_attachments_include_source_verified_research_reminder() -> None:
    context = SimpleNamespace(
        run_input=AgentRunInput(
            input="составь todo и найди информацию для отчета",
            agent_id="agent",
            graph_preset="single_react",
            app_metadata={"chat_mode": True},
            tool_policy=ToolPolicyInput(
                metadata={
                    "task_contract": {
                        "kind": "research",
                        "requires_research": True,
                        "research_depth": "source_verified_report",
                        "goal": "составь todo и найди информацию для отчета",
                    }
                }
            ),
        ),
        metadata={},
    )

    attachment_text = "\n".join(
        message.content for message in _runtime_attachment_messages(context)
    )

    assert "source_verified_report" in attachment_text
    assert "search results are candidates" in attachment_text
    assert "multiple relevant URLs" in attachment_text
    assert "Markdown links" in attachment_text


def test_runtime_attachments_include_research_fetch_fallback_reminder() -> None:
    context = SimpleNamespace(
        run_input=AgentRunInput(
            input="найди информацию",
            agent_id="agent",
            graph_preset="single_react",
            app_metadata={"chat_mode": True},
        ),
        metadata={"research_fetch_fallback_required": True},
    )

    attachment_text = "\n".join(
        message.content for message in _runtime_attachment_messages(context)
    )

    assert "research_fetch_fallback" in attachment_text
    assert "full pages could not be verified" in attachment_text


def test_runtime_attachments_include_plan_mode_runtime_reminder() -> None:
    """Plan mode should be represented as a compact runtime attachment."""
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
    attachment_text = "\n".join(
        message.content for message in _runtime_attachment_messages(context)
    )
    assert "planning_mode_active" in attachment_text
    assert "Stay read-only" in attachment_text
    assert "exit_plan_mode_v2" in attachment_text


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
    assert "Decision guide:" in instruction
    assert "exact arithmetic" in instruction
    assert "synthesize a concise final answer" in instruction
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


def test_react_system_prompt_omits_denied_tool_fragments() -> None:
    """Prompt guidance should match the effective LLM-visible tool surface."""
    registry = ToolRegistry()
    for name in ("python", "web_search", "web_fetch", "agent_tool"):
        registry.register(
            ToolManifest(name=name, description=f"{name} tool"),
            lambda _args: {},
        )
    host = SimpleNamespace(
        _deps=SimpleNamespace(tool_registry=registry),
        _config=SimpleNamespace(
            python_tool=PythonToolSettings(enabled=True, default_imports=("math",))
        ),
    )
    context = SimpleNamespace(
        run_input=AgentRunInput(
            input="hello",
            agent_id="agent",
            graph_preset="single_react",
            app_metadata={"chat_mode": True},
            tool_policy=ToolPolicyInput(
                denied_tools=["python", "web_search", "web_fetch", "agent_tool"]
            ),
        ),
        metadata={},
    )

    instruction = _react_system_instruction(host, context)

    assert instruction is not None
    assert context.metadata["effective_tool_names"] == tuple()
    assert (
        "react_chat_tool_policy_python.txt" not in context.metadata["prompt_fragments"]
    )
    assert "Python Execution" not in instruction
    assert "Python tool sandbox policy:" not in instruction
    assert "Web Search" not in instruction
    assert "Web Fetch" not in instruction
    assert "Subagent Delegation" not in instruction


def test_react_system_prompt_includes_only_allowed_tool_fragments() -> None:
    """Allowlist should narrow both schema and system-prompt guidance."""
    registry = ToolRegistry()
    for name in ("python", "web_search", "web_fetch", "agent_tool"):
        registry.register(
            ToolManifest(name=name, description=f"{name} tool"),
            lambda _args: {},
        )
    host = SimpleNamespace(
        _deps=SimpleNamespace(tool_registry=registry),
        _config=SimpleNamespace(
            python_tool=PythonToolSettings(enabled=True, default_imports=("math",))
        ),
    )
    context = SimpleNamespace(
        run_input=AgentRunInput(
            input="hello",
            agent_id="agent",
            graph_preset="single_react",
            app_metadata={"chat_mode": True},
            tool_policy=ToolPolicyInput(allowed_tools=["python"]),
        ),
        metadata={},
    )

    instruction = _react_system_instruction(host, context)

    assert instruction is not None
    assert context.metadata["effective_tool_names"] == ("python",)
    assert context.metadata["prompt_fragments"] == (
        "react_chat_tool_policy_python.txt",
    )
    assert "Python Execution" in instruction
    assert "Python tool sandbox policy:" in instruction
    assert "Web Search" not in instruction
    assert "Web Fetch" not in instruction
    assert "Subagent Delegation" not in instruction


def test_react_system_prompt_keeps_partial_web_surface_specific() -> None:
    """A fetch-only surface should not teach the model to call web_search."""
    registry = ToolRegistry()
    for name in ("web_search", "web_fetch"):
        registry.register(
            ToolManifest(name=name, description=f"{name} tool"),
            lambda _args: {},
        )
    host = SimpleNamespace(
        _deps=SimpleNamespace(tool_registry=registry),
        _config=SimpleNamespace(python_tool=PythonToolSettings(enabled=False)),
    )
    context = SimpleNamespace(
        run_input=AgentRunInput(
            input="summarize this URL",
            agent_id="agent",
            graph_preset="single_react",
            app_metadata={"chat_mode": True},
            tool_policy=ToolPolicyInput(allowed_tools=["web_fetch"]),
        ),
        metadata={},
    )

    instruction = _react_system_instruction(host, context)

    assert instruction is not None
    assert "Web Fetch" in instruction
    assert "Web Search" not in instruction
    assert "`web_search`" not in instruction
    assert "Markdown links" in instruction


def test_react_system_prompt_web_search_requests_markdown_links() -> None:
    """Search guidance should ask for links only when web search is visible."""
    registry = ToolRegistry()
    registry.register(
        ToolManifest(name="web_search", description="web_search tool"),
        lambda _args: {},
    )
    host = SimpleNamespace(
        _deps=SimpleNamespace(tool_registry=registry),
        _config=SimpleNamespace(python_tool=PythonToolSettings(enabled=False)),
    )
    context = SimpleNamespace(
        run_input=AgentRunInput(
            input="find current sources",
            agent_id="agent",
            graph_preset="single_react",
            app_metadata={"chat_mode": True},
        ),
        metadata={},
    )

    instruction = _react_system_instruction(host, context)

    assert instruction is not None
    assert "Web Search" in instruction
    assert "Markdown links" in instruction
    assert "concrete result URLs" in instruction
    assert "Research Discipline" in instruction
    assert "Web Fetch" not in instruction
    assert "`web_fetch`" not in instruction


def test_react_system_prompt_includes_research_discipline_once_for_web_tools() -> None:
    registry = ToolRegistry()
    for name in ("web_search", "web_fetch"):
        registry.register(
            ToolManifest(name=name, description=f"{name} tool"),
            lambda _args: {},
        )
    host = SimpleNamespace(
        _deps=SimpleNamespace(tool_registry=registry),
        _config=SimpleNamespace(python_tool=PythonToolSettings(enabled=False)),
    )
    context = SimpleNamespace(
        run_input=AgentRunInput(
            input="research sources",
            agent_id="agent",
            graph_preset="single_react",
            app_metadata={"chat_mode": True},
        ),
        metadata={},
    )

    instruction = _react_system_instruction(host, context)

    assert instruction is not None
    assert instruction.count("## Research Discipline") == 1
    assert (
        "react_chat_tool_policy_research_discipline.txt"
        in context.metadata["prompt_fragments"]
    )


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
