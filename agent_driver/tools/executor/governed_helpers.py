"""Pure module-level helpers for the governed tool executor (extracted from
governed.py).

Leaf module: stateless run-policy/config resolution predicates that read
``AgentRunInput``/env and return decisions, with zero dependency on
``GovernedToolExecutor`` — the import stays one-way (governed -> governed_helpers).
"""

from __future__ import annotations

import logging
import os
from agent_driver.contracts.interrupts import (
    AllowedPrompt,
    find_matching_prompt,
)
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.contracts.tools import (
    MANAGEMENT_TOOL_NAMES,
    ToolCall,
)
from agent_driver.tools.executor.blocks import (
    disallowed_management_tool_remediation,
)
from agent_driver.tools.executor.normalization import (  # noqa: F401
    _normalize_tool_alias,
)

logger = logging.getLogger(__name__)

DEFAULT_CONCURRENCY_LIMIT = 8


def _match_run_approved_prompts(
    *, run_input: AgentRunInput, call: ToolCall
) -> AllowedPrompt | None:
    """Phase 11 H13 — look up approved AllowedPrompt categories on the
    run and return the first match for this call.

    The host stores approved categories in
    ``AgentRunInput.app_metadata["approved_prompts"]`` (list of
    AllowedPrompt model_dump'd dicts). When absent or malformed, no
    bypass applies — the original INTERRUPT decision stands. Failures
    in parsing are swallowed (logged at WARNING) so a malformed entry
    can't make policy decisions unsafe (default = INTERRUPT preserved).
    """
    raw = (
        run_input.app_metadata.get("approved_prompts")
        if run_input.app_metadata
        else None
    )
    if not isinstance(raw, list) or not raw:
        return None
    approved: list[AllowedPrompt] = []
    for item in raw:
        try:
            if isinstance(item, AllowedPrompt):
                approved.append(item)
            elif isinstance(item, dict):
                approved.append(AllowedPrompt.model_validate(item))
        except Exception:
            logger.warning(
                "ignoring malformed approved_prompts entry in app_metadata",
                exc_info=True,
            )
    if not approved:
        return None
    return find_matching_prompt(
        tool_name=call.tool_name, args=call.args, approved=approved
    )


def _read_concurrency_limit_env() -> int:
    raw = os.environ.get("AGENT_DRIVER_TOOL_CONCURRENCY", "").strip()
    if not raw:
        return DEFAULT_CONCURRENCY_LIMIT
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "AGENT_DRIVER_TOOL_CONCURRENCY=%r is not an integer; falling back to %d",
            raw,
            DEFAULT_CONCURRENCY_LIMIT,
        )
        return DEFAULT_CONCURRENCY_LIMIT
    if value < 1:
        logger.warning(
            "AGENT_DRIVER_TOOL_CONCURRENCY=%d is < 1; falling back to %d",
            value,
            DEFAULT_CONCURRENCY_LIMIT,
        )
        return DEFAULT_CONCURRENCY_LIMIT
    return value


def _management_tool_denial_remediation(
    run_input: AgentRunInput, tool_name: str
) -> dict[str, object] | None:
    """Structured repair payload when a management tool is denied by the allowlist.

    Returns ``None`` (no special handling) unless the run has an active
    ``allowed_tools`` allowlist that omits this management tool — i.e. the scoped
    workflow-node case. When ``allowed_tools`` is ``None`` (no allowlist) or
    already includes the tool, behaviour is unchanged: chat/planning runs that
    grant these tools keep executing them normally.
    """
    if tool_name not in MANAGEMENT_TOOL_NAMES:
        return None
    allowed = run_input.tool_policy.allowed_tools
    if not allowed or tool_name in set(allowed):
        return None
    return disallowed_management_tool_remediation(
        tool_name=tool_name, allowed_tools=allowed
    )


def _plan_content_forbidden_terms(run_input: AgentRunInput) -> tuple[str, ...]:
    """Host-provided approval-plan text terms that must not be surfaced."""

    raw = (
        run_input.tool_policy.metadata.get("plan_content_forbidden_terms")
        if run_input.tool_policy and isinstance(run_input.tool_policy.metadata, dict)
        else None
    )
    if isinstance(raw, dict):
        raw = raw.get("terms")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple, set)):
        return ()
    terms: list[str] = []
    for item in raw:
        value = item.get("term") if isinstance(item, dict) else item
        term = str(value or "").strip()
        if term and term not in terms:
            terms.append(term)
    return tuple(terms)
