"""Payload normalization helpers for OpenAI-compatible provider responses."""

from __future__ import annotations

import json
import re
from typing import Any

from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.tools import ToolCall
from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.contracts import LlmFinishReason, LlmResponse, LlmStreamEvent
from agent_driver.llm.tool_call_parser import extract_text_form_tool_calls


def map_finish_reason(reason: str | None) -> LlmFinishReason:
    if reason == "stop":
        return LlmFinishReason.STOP
    if reason in {"length", "max_tokens"}:
        return LlmFinishReason.LENGTH
    if reason in {"tool_calls", "function_call"}:
        return LlmFinishReason.TOOL_CALLS
    if reason == "error":
        return LlmFinishReason.ERROR
    return LlmFinishReason.UNKNOWN


def parse_cost_usd_from_usage(usage: dict[str, Any]) -> float | None:
    """Read provider-reported cost from OpenRouter and similar APIs."""
    for key in ("total_cost", "cost", "generation_cost"):
        value = usage.get(key)
        if isinstance(value, (int, float)):
            return max(0.0, float(value))
    return None


def estimate_cost_usd(total_tokens: int, cost_per_1k_tokens: float) -> float | None:
    if cost_per_1k_tokens <= 0 or total_tokens <= 0:
        return None
    return (total_tokens / 1000.0) * cost_per_1k_tokens


def extract_usage(
    payload: dict[str, Any],
    *,
    provider: str,
    model: str,
    cost_per_1k_tokens: float = 0.0,
) -> UsageSummary:
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    prompt_tokens = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
    completion_tokens = int(
        usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
    )
    total_tokens = int(
        usage.get("total_tokens", prompt_tokens + completion_tokens) or 0
    )
    total_tokens = max(0, total_tokens)
    cost_usd = parse_cost_usd_from_usage(usage)
    if cost_usd is None:
        cost_usd = estimate_cost_usd(total_tokens, cost_per_1k_tokens)
    return UsageSummary(
        input_tokens=max(0, prompt_tokens),
        output_tokens=max(0, completion_tokens),
        total_tokens=total_tokens,
        cost_usd_estimate=cost_usd,
        model_provider=provider,
        model_name=model,
    )


def extract_usage_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract provider-specific usage metadata without changing public contract."""
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    prompt_details = (
        usage.get("prompt_tokens_details")
        if isinstance(usage.get("prompt_tokens_details"), dict)
        else {}
    )
    completion_details = (
        usage.get("completion_tokens_details")
        if isinstance(usage.get("completion_tokens_details"), dict)
        else {}
    )
    metadata: dict[str, Any] = {}
    if usage:
        metadata["provider_usage_raw"] = usage
    if prompt_details.get("cached_tokens") is not None:
        metadata["cached_input_tokens"] = int(
            prompt_details.get("cached_tokens", 0) or 0
        )
    if completion_details:
        metadata["completion_token_details"] = completion_details
    return metadata


def extract_reasoning_metadata(message_payload: dict[str, Any]) -> dict[str, Any]:
    """Extract provider reasoning fields without exposing hidden text."""
    metadata: dict[str, Any] = {}
    reasoning_details = message_payload.get("reasoning_details")
    if isinstance(reasoning_details, list):
        metadata["provider_reasoning_details_present"] = bool(reasoning_details)
        metadata["provider_reasoning_details_count"] = len(reasoning_details)
        metadata["provider_reasoning_details"] = reasoning_details
    reasoning = message_payload.get("reasoning")
    if isinstance(reasoning, str) and reasoning:
        metadata["provider_reasoning_text_present"] = True
        metadata["provider_reasoning"] = reasoning
    return metadata


def planned_tool_calls_from_openai(
    tool_calls_payload: object,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    planned: list[dict[str, Any]] = []
    parse_errors: list[dict[str, Any]] = []
    if not isinstance(tool_calls_payload, list):
        return planned, parse_errors
    for index, item in enumerate(tool_calls_payload):
        if not isinstance(item, dict):
            continue
        function = item.get("function")
        if not isinstance(function, dict):
            continue
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        raw_args = function.get("arguments", "{}")
        args: dict[str, Any] = {}
        if isinstance(raw_args, str):
            stripped = raw_args.strip()
            if stripped:
                try:
                    parsed = json.loads(stripped)
                    if isinstance(parsed, dict):
                        args = parsed
                    else:
                        parse_errors.append(
                            {
                                "index": index,
                                "tool_name": name,
                                "error": "arguments_json_must_be_object",
                                "raw_arguments": stripped,
                            }
                        )
                except json.JSONDecodeError:
                    parse_errors.append(
                        {
                            "index": index,
                            "tool_name": name,
                            "error": "arguments_json_parse_failed",
                            "raw_arguments": stripped,
                        }
                    )
        elif isinstance(raw_args, dict):
            args = raw_args
        try:
            planned_call = ToolCall(
                tool_name=name,
                args=args,
                tool_call_id=(
                    str(item.get("id"))
                    if isinstance(item.get("id"), str) and str(item.get("id")).strip()
                    else None
                ),
                metadata={"provider_tool_call_index": index},
            )
        except (TypeError, ValueError):
            continue
        planned.append(planned_call.model_dump(mode="json"))
    return planned, parse_errors


def forced_tool_choice_name(tool_choice: object) -> str | None:
    if not isinstance(tool_choice, dict):
        return None
    if tool_choice.get("type") != "tool":
        return None
    name = tool_choice.get("name")
    return name if isinstance(name, str) and name.strip() else None


def planned_tool_call_from_forced_text(
    *,
    tool_name: str | None,
    text: str,
) -> list[dict[str, Any]]:
    if not tool_name:
        return []
    args = parse_forced_tool_args_fragment(text)
    if args is None and tool_name == "web_search":
        args = parse_forced_web_search_query_fragment(text)
    if args is None:
        return []
    call = ToolCall(
        tool_name=tool_name,
        args=args,
        metadata={"text_form_source": "forced_tool_choice_text"},
    )
    return [call.model_dump(mode="json")]


def suppress_text_form_tool_calls_when_tools_disabled(
    event: LlmStreamEvent,
    *,
    tool_choice: object,
) -> LlmStreamEvent:
    if tool_choice != "none":
        return event
    if event.metadata.get("text_form_tool_calls_parsed") is not True:
        return event
    metadata = dict(event.metadata)
    metadata.pop("planned_tool_calls", None)
    metadata.pop("tool_call_parse_errors", None)
    metadata["text_form_tool_calls_suppressed"] = True
    return event.model_copy(update={"metadata": metadata})


def parse_forced_tool_args_fragment(text: str) -> dict[str, Any] | None:
    clean = re.sub(r"</?tool_call>", "", text, flags=re.IGNORECASE).strip()
    if not clean:
        return None
    candidates = [clean]
    key_match = re.search(r'"[A-Za-z_][A-Za-z0-9_]*"\s*:', clean)
    if key_match is not None:
        candidate = "{" + clean[key_match.start() :]
        closing = candidate.rfind("}")
        if closing >= 0:
            candidate = candidate[: closing + 1]
        else:
            candidate = f"{candidate}}}"
        candidates.append(candidate)
    for candidate in candidates:
        parsed = parse_json_object_prefix(candidate)
        if isinstance(parsed, dict):
            return parsed
    return None


def parse_forced_web_search_query_fragment(text: str) -> dict[str, Any] | None:
    """Recover Qwen/OpenRouter positional web_search query fragments."""
    clean = re.sub(r"</?tool_call>", "", text, flags=re.IGNORECASE).strip()
    object_match = re.search(r"(?:^|[\s,{])\d+\s*:\s*(?P<object>\{[\s\S]*\})", clean)
    if object_match is not None:
        raw_object = object_match.group("object").strip()
        parsed = parse_json_object_prefix(raw_object)
        if isinstance(parsed, dict) and isinstance(parsed.get("query"), str):
            query = parsed["query"].strip()
            return {"query": query} if query else None
    match = re.search(r"(?:^|[\s,{])\d+\s*:\s*\"(?P<query>[^\"]+)\"", clean)
    if match is None:
        return None
    query = match.group("query").strip()
    return {"query": query} if query else None


def parse_json_object_prefix(text: str) -> dict[str, Any] | None:
    """Parse the shortest valid JSON object prefix from text."""
    decoder = json.JSONDecoder()
    try:
        parsed, _index = decoder.raw_decode(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def normalize_openai_completion_payload(
    payload: dict[str, Any],
    *,
    provider_name: str,
    fallback_model: str,
    cost_per_1k_tokens: float = 0.0,
) -> LlmResponse:
    """Normalize OpenAI-compatible completion payload to provider-neutral response."""
    choice = payload.get("choices", [{}])[0]
    message_payload = choice.get("message", {}) if isinstance(choice, dict) else {}
    text = str(message_payload.get("content", "") or "")
    model_name = str(payload.get("model") or fallback_model)
    usage = extract_usage(
        payload,
        provider=provider_name,
        model=model_name,
        cost_per_1k_tokens=cost_per_1k_tokens,
    )
    metadata = extract_usage_metadata(payload)
    metadata.update(extract_reasoning_metadata(message_payload))
    planned_tool_calls, parse_errors = planned_tool_calls_from_openai(
        message_payload.get("tool_calls")
    )
    if not planned_tool_calls and text:
        text_planned, text_errors = extract_text_form_tool_calls(text)
        if text_planned:
            planned_tool_calls = text_planned
            metadata["text_form_tool_calls_parsed"] = True
        if text_errors:
            parse_errors.extend(text_errors)
    if planned_tool_calls:
        metadata["planned_tool_calls"] = planned_tool_calls
    if parse_errors:
        metadata["tool_call_parse_errors"] = parse_errors
    # Output media: when ``modalities`` includes "audio", the model returns an
    # assistant ``audio`` object ({id, data, transcript, expires_at, format}).
    # Carry it on the message metadata; when ``content`` is null (the common
    # case for audio replies) surface ``transcript`` as the message text so the
    # run's answer isn't empty.
    message_metadata: dict[str, Any] = {}
    output_audio = message_payload.get("audio")
    if isinstance(output_audio, dict):
        message_metadata["output_audio"] = output_audio
        if not text:
            transcript = output_audio.get("transcript")
            if isinstance(transcript, str):
                text = transcript
    return LlmResponse(
        message=ChatMessage(
            role="assistant", content=text, metadata=message_metadata
        ),
        finish_reason=map_finish_reason(choice.get("finish_reason")),
        usage=usage,
        provider=provider_name,
        model=model_name,
        raw_response=payload if isinstance(payload, dict) else {},
        metadata=metadata,
    )


def normalize_openai_stream_chunk(
    payload: dict[str, Any],
    *,
    provider_name: str,
    fallback_model: str,
    cost_per_1k_tokens: float = 0.0,
) -> LlmStreamEvent:
    """Normalize one OpenAI-compatible stream chunk."""
    choice = first_choice(payload)
    delta = choice.get("delta", {}) if isinstance(choice, dict) else {}
    text = str(delta.get("content", "") or "")
    # vLLM-served Qwen3 + DeepSeek-R1 surface chain-of-thought in a
    # separate ``reasoning_content`` field (parallel to ``content``);
    # capture it so consumers can render a separate reasoning channel.
    reasoning = str(delta.get("reasoning_content", "") or "")
    finish_reason = map_finish_reason(choice.get("finish_reason"))
    usage = (
        extract_usage(
            payload,
            provider=provider_name,
            model=fallback_model,
            cost_per_1k_tokens=cost_per_1k_tokens,
        )
        if isinstance(payload, dict) and payload.get("usage")
        else None
    )
    metadata = extract_usage_metadata(payload)
    if isinstance(delta, dict):
        metadata.update(extract_reasoning_metadata(delta))
    choice_payload = choice if isinstance(choice, dict) else {}
    planned_tool_calls, parse_errors = planned_tool_calls_from_openai(
        choice_payload.get("tool_calls")
    )
    if not planned_tool_calls and text:
        text_planned, text_errors = extract_text_form_tool_calls(text)
        if text_planned:
            planned_tool_calls = text_planned
            metadata["text_form_tool_calls_parsed"] = True
        if text_errors:
            parse_errors.extend(text_errors)
    if planned_tool_calls:
        metadata["planned_tool_calls"] = planned_tool_calls
    if parse_errors:
        metadata["tool_call_parse_errors"] = parse_errors
    if isinstance(delta, dict) and delta.get("tool_calls"):
        metadata["stream_tool_call_delta"] = True
    # Output media: streamed assistant audio arrives as incremental ``delta.audio``
    # objects ({data, transcript, id, expires_at}); carry each delta so the
    # runtime can accumulate it into the final ``output_audio``.
    if isinstance(delta, dict):
        audio_delta = delta.get("audio")
        if isinstance(audio_delta, dict):
            metadata["output_audio_delta"] = audio_delta
    return LlmStreamEvent(
        event="delta",
        delta_text=text,
        delta_reasoning=reasoning,
        finish_reason=(
            finish_reason if finish_reason != LlmFinishReason.UNKNOWN else None
        ),
        usage=usage,
        metadata=metadata,
    )


def first_choice(payload: dict[str, Any]) -> dict[str, Any]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return {}
    choice = choices[0]
    return choice if isinstance(choice, dict) else {}


__all__ = [
    "extract_usage",
    "extract_usage_metadata",
    "first_choice",
    "forced_tool_choice_name",
    "map_finish_reason",
    "normalize_openai_completion_payload",
    "normalize_openai_stream_chunk",
    "planned_tool_call_from_forced_text",
    "planned_tool_calls_from_openai",
    "suppress_text_form_tool_calls_when_tools_disabled",
]
