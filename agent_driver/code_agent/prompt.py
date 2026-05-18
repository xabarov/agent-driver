"""CodeAgent-specific prompt rendering helpers."""

from __future__ import annotations

from hashlib import sha256

from agent_driver.contracts.enums import AgentProfile
from agent_driver.contracts.profiles import PromptRenderResult

_CODE_AGENT_TEMPLATE_ID = "code_agent.default"
_CODE_AGENT_TEMPLATE_VERSION = 1


def render_code_agent_prompt(
    *,
    task: str,
    tool_docs: str,
    authorized_imports: tuple[str, ...],
    observations: list[str],
    clarification: str | None,
) -> PromptRenderResult:
    """Render deterministic CodeAgent prompt block."""
    observation_block = (
        "\n".join(f"- {item}" for item in observations) if observations else "- none"
    )
    imports_block = ", ".join(sorted(set(authorized_imports))) or "none"
    clarification_block = clarification.strip() if clarification else "none"
    rendered_text = (
        "You are a CodeAgent. Write one Python code block action.\n"
        "Safety rules:\n"
        "- Use only authorized imports.\n"
        "- Do not use dangerous modules/functions/dunder access.\n"
        "- Use tools as callable Python functions when needed.\n"
        "- Provide final answer only via final_answer(...).\n\n"
        f"Task:\n{task}\n\n"
        f"Clarification:\n{clarification_block}\n\n"
        f"Authorized imports:\n{imports_block}\n\n"
        f"Callable tools:\n{tool_docs or 'none'}\n\n"
        f"Observations:\n{observation_block}\n"
    )
    rendered_hash = sha256(rendered_text.encode("utf-8")).hexdigest()
    return PromptRenderResult(
        template_id=_CODE_AGENT_TEMPLATE_ID,
        template_version=_CODE_AGENT_TEMPLATE_VERSION,
        profile=AgentProfile.CODE_AGENT,
        rendered_text=rendered_text,
        rendered_hash=rendered_hash,
        metadata={
            "authorized_imports": list(sorted(set(authorized_imports))),
            "observation_count": len(observations),
        },
    )


__all__ = ["render_code_agent_prompt"]
