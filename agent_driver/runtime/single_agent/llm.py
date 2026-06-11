"""Compatibility shim for LLM request-building helpers."""

# SHIM-REMOVE-BY: 2026-12-01 (re-export kept for importers; see refactoring-plan-2026-06-10)

from agent_driver.runtime.single_agent.llm_step.build import (
    LlmRequestBuildContext,
    _provider_compatible_json_schema,
    _request_tools_from_registry,
    build_single_agent_llm_request,
)

__all__ = [
    "LlmRequestBuildContext",
    "_provider_compatible_json_schema",
    "_request_tools_from_registry",
    "build_single_agent_llm_request",
]
