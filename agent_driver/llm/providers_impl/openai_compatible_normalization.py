"""Compatibility shim for OpenAI-compatible response normalization helpers."""

from agent_driver.llm.providers_impl.openai_compatible.normalization import (
    estimate_cost_usd,
    extract_reasoning_metadata,
    extract_usage,
    extract_usage_metadata,
    first_choice,
    forced_tool_choice_name,
    map_finish_reason,
    normalize_openai_completion_payload,
    normalize_openai_stream_chunk,
    parse_cost_usd_from_usage,
    parse_forced_tool_args_fragment,
    parse_forced_web_search_query_fragment,
    parse_json_object_prefix,
    planned_tool_call_from_forced_text,
    planned_tool_calls_from_openai,
    suppress_text_form_tool_calls_when_tools_disabled,
)

__all__ = [
    "estimate_cost_usd",
    "extract_reasoning_metadata",
    "extract_usage",
    "extract_usage_metadata",
    "first_choice",
    "forced_tool_choice_name",
    "map_finish_reason",
    "normalize_openai_completion_payload",
    "normalize_openai_stream_chunk",
    "parse_cost_usd_from_usage",
    "parse_forced_tool_args_fragment",
    "parse_forced_web_search_query_fragment",
    "parse_json_object_prefix",
    "planned_tool_call_from_forced_text",
    "planned_tool_calls_from_openai",
    "suppress_text_form_tool_calls_when_tools_disabled",
]
