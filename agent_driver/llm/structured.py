"""Structured output via the tool-call channel (epic 036).

Weak models (deepseek-v4-flash) intermittently answer a «return only JSON»
instruction with prose or a fenced block, forcing every consumer to hand-roll a
parse-ladder with bounded retries (memory fact extraction — the dd9a5ee flake,
epic 027C; host graders; benchmark runners). The reliable channel — confirmed by
MeetScript's own speaker-rename move from ``json_schema`` mode to a tools-mode
extractor — is a **forced tool call**: the model emits the result as a tool call
whose arguments are validated at the tool-call layer, and an invalid emit is a
tool error the model corrects, not free text a caller has to salvage.

``structured_completion`` is the reusable primitive: force one tool named
``emit_result`` whose ``parameters`` are the caller's JSON schema, read the
parsed+repaired arguments from ``planned_tool_calls`` (the provider adapter
already repairs arg JSON), validate against the schema, retry with corrective
feedback on mismatch, and return a typed dict — or raise ``StructuredOutputError``
so «unparseable final» becomes impossible by construction rather than salvaged.
"""

from __future__ import annotations

from typing import Any

from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmRequest

_EMIT_TOOL_NAME = "emit_result"


class StructuredOutputError(Exception):
    """Raised when the model could not produce a schema-valid structured emit."""


def _emit_tool(schema: dict[str, Any], *, description: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": _EMIT_TOOL_NAME,
            "description": description,
            "parameters": schema,
        },
    }


def _validate(args: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """Lightweight JSON-schema validation (dependency-free): required keys +
    top-level property types. Returns a list of human-readable violations."""
    errors: list[str] = []
    required = schema.get("required")
    if isinstance(required, list):
        for key in required:
            if key not in args:
                errors.append(f"missing required field '{key}'")
    props = schema.get("properties")
    if isinstance(props, dict):
        type_map = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        for key, spec in props.items():
            if key not in args or not isinstance(spec, dict):
                continue
            expected = spec.get("type")
            py = type_map.get(expected) if isinstance(expected, str) else None
            if py is not None and not isinstance(args[key], py):
                # bool is a subclass of int — reject it where an integer/number is asked
                if expected in ("integer", "number") and isinstance(args[key], bool):
                    errors.append(f"field '{key}' must be {expected}, got boolean")
                elif not isinstance(args[key], py):
                    errors.append(f"field '{key}' must be {expected}")
    return errors


def _extract_emit_args(response: Any) -> dict[str, Any] | None:
    """Read the ``emit_result`` call from a normalized response.

    The OpenAI-compatible provider puts parsed tool calls on the RESPONSE
    metadata (``response.metadata["planned_tool_calls"]``) with each call keyed
    by ``tool_name`` — NOT on ``message.metadata`` and NOT keyed ``name``. Both
    locations and both key spellings are accepted so this works against the real
    provider (top-level ``metadata`` / ``tool_name``) and any adapter that mirrors
    it onto the message. Reading only ``message.metadata['...']['name']`` — as the
    first cut did — silently never matched live (only FakeProvider-shaped tests),
    so structured_completion always raised on real providers.
    """
    planned: Any = None
    for owner in (response, getattr(response, "message", None)):
        metadata = getattr(owner, "metadata", None)
        if isinstance(metadata, dict):
            candidate = metadata.get("planned_tool_calls")
            if isinstance(candidate, list) and candidate:
                planned = candidate
                break
    if not isinstance(planned, list):
        return None
    for call in planned:
        if not isinstance(call, dict):
            continue
        name = str(call.get("tool_name") or call.get("name") or "")
        if name == _EMIT_TOOL_NAME:
            args = call.get("args")
            return args if isinstance(args, dict) else {}
    return None


async def structured_completion(
    *,
    provider: Any,
    messages: list[ChatMessage],
    schema: dict[str, Any],
    model: str | None = None,
    description: str = "Emit the final result as structured data.",
    max_retries: int = 1,
    metadata: dict[str, Any] | None = None,
    disable_reasoning: bool = True,
) -> dict[str, Any]:
    """Force a schema-valid structured emit via the tool-call channel.

    Returns the validated arguments dict. Raises :class:`StructuredOutputError`
    if the model never produces a schema-valid emit within ``max_retries + 1``
    attempts — the caller decides fallback (never a silently-salvaged parse).

    Reasoning handling is ADAPTIVE, not unconditional. Two opposite thinking-mode
    quirks exist across providers (both caught live via OpenRouter):
      * Qwen3-thinking REJECTS a forced object/``required`` tool_choice ("does not
        support being set to required or object in thinking mode") — it needs
        reasoning disabled.
      * Kimi-k2-thinking MANDATES reasoning ("Reasoning is mandatory ... cannot be
        disabled") — sending ``reasoning={"enabled": False}`` is a hard 400.
    So the plain forced call is tried FIRST (works for normal + reasoning-mandatory
    models); only if it RAISES and ``disable_reasoning`` is set do we retry that call
    once with reasoning disabled (the Qwen3 path). This never disables reasoning for a
    model whose plain call already succeeds. Set ``disable_reasoning=False`` to
    suppress even the fallback (a backend that rejects an unknown ``reasoning`` key).
    """
    convo = list(messages)
    tool = _emit_tool(schema, description=description)
    last_error = "no tool call produced"
    reasoning_override: dict[str, Any] | None = None

    async def _complete(reasoning: dict[str, Any] | None) -> Any:
        request = LlmRequest(
            messages=convo,
            model=model,
            tools=[tool],
            tool_choice={"type": "function", "function": {"name": _EMIT_TOOL_NAME}},
            temperature=0.0,
            reasoning=reasoning,
            metadata={"purpose": "structured_output", **(metadata or {})},
        )
        return await provider.complete(request)

    for attempt in range(max_retries + 1):
        try:
            response = await _complete(reasoning_override)
        except Exception:  # noqa: BLE001
            # Plain call failed. If reasoning-disable is allowed and not yet applied,
            # this may be the Qwen3-thinking forced-tool_choice conflict — retry once
            # with reasoning disabled and keep it for subsequent corrective turns.
            if disable_reasoning and reasoning_override is None:
                reasoning_override = {"enabled": False}
                response = await _complete(reasoning_override)
            else:
                raise
        args = _extract_emit_args(response)
        if args is not None:
            violations = _validate(args, schema)
            if not violations:
                return args
            last_error = "; ".join(violations)
        else:
            last_error = "no emit_result tool call in the response"
        if attempt < max_retries:
            # Corrective turn: show the model what was wrong so it self-repairs.
            convo = convo + [
                ChatMessage(
                    role=ChatRole.ASSISTANT,
                    content=f"(invalid structured emit: {last_error})",
                ),
                ChatMessage(
                    role=ChatRole.USER,
                    content=(
                        "Твой предыдущий результат не прошёл проверку схемы: "
                        f"{last_error}. Вызови инструмент "
                        f"{_EMIT_TOOL_NAME} ещё раз с корректными полями."
                    ),
                ),
            ]
    raise StructuredOutputError(last_error)


__all__ = ["StructuredOutputError", "structured_completion"]
