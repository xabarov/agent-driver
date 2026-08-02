"""Provider request/error normalization helpers for LLM-call step."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

# Below this the completion cannot carry even a minimal tool call or one-line
# final, so an explicit provider "afford" ceiling under it is treated as a
# genuinely depleted balance and we stop reducing (the 402 propagates as a
# clear error rather than looping on unusable budgets). 256 max_tokens is
# empirically enough for a single tool call on reasoning models.
_MIN_AFFORDABLE_MAX_TOKENS = 64
# Stay just under the stated ceiling: the affordable figure drifts slightly
# between attempts (prompt-token rounding / price ticks), so a bare clamp can
# still 402. A 10% margin absorbs that drift in one retry.
_AFFORDABLE_SAFETY_MARGIN = 0.9
_AFFORDABLE_RE = re.compile(r"can only afford\s+(\d+)")

from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmRequest
from agent_driver.contracts.scaffolding import scaffolding_metadata
from agent_driver.llm.message_hygiene import (  # re-export for llm_step imports
    quarantine_inline_reasoning,
    repair_empty_non_final_messages,
)


def provider_error_message(response: httpx.Response) -> str:
    """Extract a compact provider error message from an HTTP response."""
    body = response.text.strip()
    if not body:
        return f"Provider rejected the request with HTTP {response.status_code}."
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body[:500]
    if not isinstance(payload, dict):
        return body[:500]
    error = payload.get("error")
    if isinstance(error, dict):
        for key in ("message", "code", "type"):
            value = error.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:500]
    for key in ("message", "detail"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:500]
    return body[:500]


def is_invalid_encrypted_reasoning_error(exc: httpx.HTTPStatusError) -> bool:
    """Return whether provider rejected echoed encrypted reasoning metadata."""
    if exc.response.status_code != 400:
        return False
    body = exc.response.text.lower()
    return (
        "invalid_encrypted_content" in body
        or "encrypted content" in body
        and "could not be" in body
    )


def is_forced_tool_choice_provider_error(
    exc: httpx.HTTPStatusError,
    request: Any,
) -> bool:
    """Return whether a provider rejected a forced tool_choice request."""
    if exc.response.status_code not in {400, 404}:
        return False
    if not isinstance(request, LlmRequest):
        return False
    return forced_named_tool_choice(request.tool_choice) is not None


def forced_named_tool_choice(tool_choice: object) -> str | None:
    """Return forced tool name from provider-neutral tool_choice, if present."""
    if not isinstance(tool_choice, dict):
        return None
    if tool_choice.get("type") != "tool":
        return None
    name = tool_choice.get("name")
    return name if isinstance(name, str) and name.strip() else None


def narrow_request_tools_to_forced_choice(request: Any) -> Any:
    """Restrict tool catalog to the forced tool_choice when possible."""
    if not isinstance(request, LlmRequest):
        return request
    forced_tool_name = forced_named_tool_choice(request.tool_choice)
    if not forced_tool_name:
        return request
    tools = request_tools_matching(request.tools, forced_tool_name)
    if not tools or len(tools) == len(request.tools):
        return request
    metadata = dict(request.metadata)
    metadata["forced_tool_catalog"] = forced_tool_name
    return request.model_copy(update={"metadata": metadata, "tools": tools})


def request_without_forced_tool_choice(request: LlmRequest) -> LlmRequest:
    """Remove forced tool_choice and keep the matching catalog when available."""
    forced_tool_name = forced_named_tool_choice(request.tool_choice)
    metadata = dict(request.metadata)
    metadata["forced_tool_choice_retry"] = "removed_after_provider_rejection"
    tools = request.tools
    if forced_tool_name:
        tools = request_tools_matching(request.tools, forced_tool_name)
    return request.model_copy(
        update={
            "metadata": metadata,
            "tools": tools,
            "tool_choice": None,
        }
    )


def request_tools_matching(
    tools: list[dict[str, Any]],
    tool_name: str,
) -> list[dict[str, Any]]:
    """Return request tool specs matching one function name."""
    return [tool for tool in tools if request_tool_name(tool) == tool_name]


def request_tool_name(tool: object) -> str | None:
    """Extract OpenAI-style function tool name from a request tool spec."""
    if not isinstance(tool, dict):
        return None
    function = tool.get("function")
    if not isinstance(function, dict):
        return None
    name = function.get("name")
    return name if isinstance(name, str) and name.strip() else None


def strip_reasoning_echo(request: Any) -> Any:
    """Remove provider reasoning echoes from request messages before retrying."""
    if not isinstance(request, LlmRequest):
        return request
    changed = False
    messages: list[ChatMessage] = []
    for message in request.messages:
        metadata = message.metadata if isinstance(message.metadata, dict) else {}
        if "reasoning_details" not in metadata and "reasoning" not in metadata:
            messages.append(message)
            continue
        updated_metadata = dict(metadata)
        updated_metadata.pop("reasoning_details", None)
        updated_metadata.pop("reasoning", None)
        messages.append(message.model_copy(update={"metadata": updated_metadata}))
        changed = True
    if not changed:
        return request
    return request.model_copy(update={"messages": messages})


def is_reduce_max_tokens_credit_error(exc: httpx.HTTPStatusError) -> bool:
    """Return whether provider says the request should use fewer output tokens."""
    if exc.response.status_code != 402:
        return False
    body = exc.response.text.lower()
    return "fewer max_tokens" in body or "requested up to" in body


def affordable_max_tokens_from_error(exc: httpx.HTTPStatusError) -> int | None:
    """Parse the exact output budget the provider says the balance can afford.

    OpenRouter's credit 402 states ``"...but can only afford 298..."`` — the
    precise ceiling the remaining balance supports. Return the smallest such
    figure in the body (the current attempt plus any ``previous_errors`` all
    carry one; the min is the safe target), or ``None`` when absent.
    """
    matches = _AFFORDABLE_RE.findall(exc.response.text.lower())
    if not matches:
        return None
    return min(int(value) for value in matches)


def request_with_reduced_max_tokens(request: Any, affordable: int | None = None) -> Any:
    """Return request retry copy with a smaller max_tokens budget.

    Without ``affordable`` the budget is halved with a 512 floor (a low-cost
    step down from a generic over-budget 402). When the provider states an
    exact affordable ceiling (``affordable``), clamp to just under it — even
    below 512 — because that figure is authoritative: a near-empty balance can
    afford fewer tokens than the generic floor, and refusing to go lower turns
    a recoverable best-effort answer into a hard failure. A ceiling below
    ``_MIN_AFFORDABLE_MAX_TOKENS`` is treated as a depleted balance: no change,
    so the 402 propagates as a clear error instead of looping on an unusable
    budget.
    """
    if not isinstance(request, LlmRequest):
        return request
    current = request.max_tokens if request.max_tokens is not None else 4096
    reduced = max(512, min(2048, int(current) // 2))
    if affordable is not None:
        target = int(affordable * _AFFORDABLE_SAFETY_MARGIN)
        if target < _MIN_AFFORDABLE_MAX_TOKENS:
            return request
        reduced = min(reduced, target)
    if request.max_tokens == reduced:
        return request
    return request.model_copy(update={"max_tokens": reduced})


def request_without_tools(
    request: LlmRequest, *, provider_name: str | None = None
) -> LlmRequest:
    """Disable tools for a forced-final retry."""
    messages = [
        *request.messages,
        ChatMessage(
            role=ChatRole.USER,
            content=(
                "Final answer retry: the previous forced-final attempt returned "
                "empty content. Tools are now disabled. Return the final answer "
                "now, in the user's language, using only the evidence and tool "
                "results already present in this conversation. Do not mention "
                "tool limitations; include source links when the task used web "
                "sources."
            ),
            metadata=scaffolding_metadata(
                "empty_forced_final_no_tools",
                base={"runtime_retry": "empty_forced_final_no_tools"},
            ),
        ),
    ]
    metadata = dict(request.metadata)
    if should_disable_reasoning_for_no_tools_retry(
        request=request,
        provider_name=provider_name,
    ):
        provider_extra_body = dict(metadata.get("provider_extra_body") or {})
        provider_extra_body["reasoning"] = {"enabled": False, "exclude": True}
        metadata["provider_extra_body"] = provider_extra_body
    return request.model_copy(
        update={
            "messages": messages,
            "metadata": metadata,
            "stream": False,
            "tools": [],
            "tool_choice": None,
            "parallel_tool_calls": None,
        }
    )


def should_disable_reasoning_for_no_tools_retry(
    *, request: LlmRequest, provider_name: str | None
) -> bool:
    """Return whether retry should disable provider reasoning output."""
    provider_l = (provider_name or "").strip().lower()
    model_l = (request.model or "").strip().lower()
    return provider_l == "openrouter" and "deepseek" in model_l


def request_with_folded_tool_history(
    request: LlmRequest, *, provider_name: str | None = None
) -> LlmRequest:
    """Forced-final retry with tool exchanges folded into plain user/assistant turns.

    Some models (observed: deepseek via openrouter) persistently return an EMPTY
    forced-final completion when the trailing history carries the tool-call protocol
    shape (assistant ``tool_calls`` + ``tool``-role results) — and the no-tools retry
    inherits that shape, so it comes back empty too. The fold itself lives in the
    reusable history normalizer (epic 018); this wrapper adds the retry nudge and the
    no-tools/non-stream request shape.
    """
    from agent_driver.runtime.single_agent.context_management.history_normalizer import (  # pylint: disable=import-outside-toplevel
        fold_tool_history,
    )

    if not isinstance(request, LlmRequest):
        return request
    folded, changed = fold_tool_history(list(request.messages))
    if not changed:
        return request
    folded.append(
        ChatMessage(
            role=ChatRole.USER,
            content=(
                "Final answer retry: previous attempts returned empty content. The tool "
                "results above are now plain messages. Produce the final answer NOW, in "
                "the user's language, using only the evidence already present in this "
                "conversation. Do not call tools and do not mention tool limitations."
            ),
            metadata=scaffolding_metadata(
                "empty_forced_final_history_fold",
                base={"runtime_retry": "empty_forced_final_history_fold"},
            ),
        )
    )
    metadata = dict(request.metadata)
    if should_disable_reasoning_for_no_tools_retry(
        request=request,
        provider_name=provider_name,
    ):
        provider_extra_body = dict(metadata.get("provider_extra_body") or {})
        provider_extra_body["reasoning"] = {"enabled": False, "exclude": True}
        metadata["provider_extra_body"] = provider_extra_body
    return request.model_copy(
        update={
            "messages": folded,
            "metadata": metadata,
            "stream": False,
            "tools": [],
            "tool_choice": None,
            "parallel_tool_calls": None,
        }
    )


__all__ = [
    "forced_named_tool_choice",
    "is_forced_tool_choice_provider_error",
    "is_invalid_encrypted_reasoning_error",
    "is_reduce_max_tokens_credit_error",
    "narrow_request_tools_to_forced_choice",
    "provider_error_message",
    "request_tool_name",
    "request_tools_matching",
    "request_with_folded_tool_history",
    "request_with_reduced_max_tokens",
    "request_without_forced_tool_choice",
    "request_without_tools",
    "should_disable_reasoning_for_no_tools_retry",
    "strip_reasoning_echo",
]
