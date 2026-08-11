"""Markdown-defined agent types (coordination C2).

An *agent definition* is a reusable, domain-neutral description of a specialized
worker — its system prompt, which tools it may touch, which model/effort it runs
on, and its budget — authored as a Markdown file with YAML frontmatter (the same
shape Claude Code's ``.claude/agents`` and OpenHands' subagent registry use):

    ---
    name: explorer
    description: Read-only investigator; use to gather facts before acting.
    tools: [read, grep, web_search]
    model_role: simple
    max_tool_calls: 20
    ---
    You are a read-only explorer. Investigate and report concise findings.
    Never modify anything.

The body is the child's system prompt; the frontmatter fills a
:class:`~agent_driver.sdk.subagent.SubagentSpec`. Definitions are data, not code —
hot-loadable from disk, overridable by precedence (see :class:`AgentRegistry`), and
turned into a runnable spec by :func:`agent_definition_to_spec`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field, field_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.skills import split_frontmatter


def _clean_str(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def _str_list(value: Any) -> tuple[str, ...] | None:
    """Coerce a frontmatter list/CSV string into a tuple of tool names, or None."""
    if value is None:
        return None
    if isinstance(value, str):
        items = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple)):
        items = [str(part).strip() for part in value]
    else:
        return None
    names = tuple(item for item in items if item)
    return names or None


def _opt_int(value: Any) -> int | None:
    try:
        return int(str(value).strip()) if value is not None else None
    except (TypeError, ValueError):
        return None


def _opt_float(value: Any) -> float | None:
    try:
        return float(str(value).strip()) if value is not None else None
    except (TypeError, ValueError):
        return None


def _first_heading(body: str) -> str | None:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or None
    return None


class AgentDefinition(ContractModel):
    """One reusable agent type, typically loaded from a Markdown file.

    ``system_prompt`` is the child's system message (the Markdown body).
    ``allowed_tools`` / ``denied_tools`` are tool-policy allow/deny lists.
    ``model`` / ``model_role`` / ``reasoning_effort`` select the child's model
    (R-track). ``max_tool_calls`` / ``deadline_seconds`` / ``max_cost_usd`` bound it.
    ``description`` / ``when_to_use`` are routing hints an orchestrator (or model)
    uses to pick the right agent for a subtask.
    """

    name: str
    description: str = ""
    when_to_use: str | None = None
    system_prompt: str = ""
    allowed_tools: tuple[str, ...] | None = None
    denied_tools: tuple[str, ...] | None = None
    model: str | None = None
    model_role: str | None = None
    reasoning_effort: str | None = None
    max_tool_calls: int | None = None
    deadline_seconds: float | None = None
    max_cost_usd: float | None = None
    source: str = "programmatic"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        """A definition must have a non-empty name (its registry key)."""
        if not value or not value.strip():
            raise ValueError("agent definition name must be non-empty")
        return value.strip()


def parse_agent_markdown(
    text: str, *, name_fallback: str | None = None, source: str = "filesystem"
) -> AgentDefinition:
    """Parse one Markdown agent file (frontmatter + body) into an ``AgentDefinition``.

    The frontmatter accepts ``tools`` (or ``allowed_tools``) as a list or CSV, plus
    ``denied_tools``, ``model``, ``model_role``, ``reasoning_effort``,
    ``max_tool_calls``, ``deadline_seconds``, ``max_cost_usd``, ``description`` and
    ``when_to_use`` (aliased from ``whenToUse``). The body becomes ``system_prompt``.
    A missing ``name`` falls back to ``name_fallback`` (e.g. the file stem). Unknown
    frontmatter keys are preserved on ``metadata``.
    """
    frontmatter, body = split_frontmatter(text)
    known = {
        "name",
        "description",
        "when_to_use",
        "whenToUse",
        "tools",
        "allowed_tools",
        "denied_tools",
        "model",
        "model_role",
        "reasoning_effort",
        "max_tool_calls",
        "deadline_seconds",
        "max_cost_usd",
        "system_prompt",
    }
    name = _clean_str(frontmatter.get("name")) or name_fallback
    if not name:
        raise ValueError("agent markdown has no 'name' and no name_fallback")
    system_prompt = _clean_str(frontmatter.get("system_prompt")) or body.strip()
    return AgentDefinition(
        name=name,
        description=_clean_str(frontmatter.get("description"))
        or _first_heading(body)
        or "",
        when_to_use=_clean_str(frontmatter.get("when_to_use"))
        or _clean_str(frontmatter.get("whenToUse")),
        system_prompt=system_prompt,
        allowed_tools=_str_list(
            frontmatter.get("tools")
            if frontmatter.get("tools") is not None
            else frontmatter.get("allowed_tools")
        ),
        denied_tools=_str_list(frontmatter.get("denied_tools")),
        model=_clean_str(frontmatter.get("model")),
        model_role=_clean_str(frontmatter.get("model_role")),
        reasoning_effort=_clean_str(frontmatter.get("reasoning_effort")),
        max_tool_calls=_opt_int(frontmatter.get("max_tool_calls")),
        deadline_seconds=_opt_float(frontmatter.get("deadline_seconds")),
        max_cost_usd=_opt_float(frontmatter.get("max_cost_usd")),
        source=source,
        metadata={
            key: value for key, value in frontmatter.items() if key not in known
        },
    )


def load_agent_definitions(
    directory: str | Path, *, source: str = "filesystem"
) -> list[AgentDefinition]:
    """Load every ``*.md`` agent file in ``directory`` (non-recursive), name-sorted.

    Each file's stem is the name fallback. A file that fails to parse is skipped
    rather than aborting the whole load (a bad agent file must not break the others).
    Returns ``[]`` when the directory is missing.
    """
    base = Path(directory)
    if not base.is_dir():
        return []
    definitions: list[AgentDefinition] = []
    for path in sorted(base.glob("*.md")):
        try:
            definitions.append(
                parse_agent_markdown(
                    path.read_text(encoding="utf-8"),
                    name_fallback=path.stem,
                    source=source,
                )
            )
        except (ValueError, OSError):
            continue
    return definitions


__all__ = [
    "AgentDefinition",
    "load_agent_definitions",
    "parse_agent_markdown",
]
