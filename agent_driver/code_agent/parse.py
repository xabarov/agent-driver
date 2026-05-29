"""Parse CodeAgent actions from model responses."""

from __future__ import annotations

import re
from uuid import uuid4

from agent_driver.code_agent.contracts import CodeAgentAction
from agent_driver.llm.contracts import LlmResponse

_FENCED_CODE_RE = re.compile(r"```(?:python)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)


def _action_from_metadata(payload: object) -> CodeAgentAction | None:
    if payload is None:
        return None
    if isinstance(payload, dict):
        return CodeAgentAction.model_validate(payload)
    if isinstance(payload, str):
        code = payload.strip()
        if not code:
            raise ValueError("code_action metadata is empty")
        return CodeAgentAction(action_id=f"act_{uuid4().hex[:8]}", code=code)
    raise ValueError("code_action metadata must be dict or str")


def _action_from_message_text(text: str) -> CodeAgentAction | None:
    fenced_blocks = [block.strip() for block in _FENCED_CODE_RE.findall(text)]
    if not fenced_blocks:
        return None
    non_empty = [block for block in fenced_blocks if block]
    if len(non_empty) != 1:
        raise ValueError("expected exactly one non-empty fenced code block")
    return CodeAgentAction(action_id=f"act_{uuid4().hex[:8]}", code=non_empty[0])


def parse_code_action(response: LlmResponse) -> CodeAgentAction | None:
    """Parse one code action from response metadata or fenced code block."""
    from_metadata = _action_from_metadata(response.metadata.get("code_action"))
    if from_metadata is not None:
        return from_metadata
    return _action_from_message_text(response.message.content)


__all__ = ["parse_code_action"]
