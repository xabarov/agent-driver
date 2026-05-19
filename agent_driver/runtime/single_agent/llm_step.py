"""LLM call step for single-agent runtime."""

from __future__ import annotations

from typing import Any, Protocol

from agent_driver.context import (
    microcompact_observations,
    render_planning_step_prompt,
)
from agent_driver.contracts.context import PlanningStep
from agent_driver.contracts.enums import RuntimeEventType, TerminalReason
from agent_driver.llm.contracts import LlmResponse
from agent_driver.runtime.errors import RuntimeExecutionError
from agent_driver.runtime.single_agent.compaction_stage import (
    CompactionStageHost,
    apply_compaction_if_eligible,
)
from agent_driver.runtime.single_agent.llm import (
    LlmRequestBuildContext,
    build_single_agent_llm_request,
)
from agent_driver.runtime.single_agent.streaming import (
    complete_streaming_request,
    emit_token_delta_events,
    is_stream_enabled,
)
from agent_driver.runtime.single_agent.types import (
    EventSpec,
    RunContext,
    RunnerConfig,
    RunnerDeps,
    RuntimeStepResult,
)
from agent_driver.runtime.single_agent.step_events import emit_step_event


class LlmStepHost(CompactionStageHost, Protocol):
    """Host surface for LLM step execution."""

    _deps: RunnerDeps
    _config: RunnerConfig

    def _emit(self, event: EventSpec) -> None: ...
    def _save_checkpoint(self, context: RunContext, *, latest_output: Any, node_id: str) -> Any: ...
    def _maybe_fail_after_step(self, step_name: str) -> None: ...


async def execute_llm_call_step(host: LlmStepHost, context: RunContext) -> RuntimeStepResult:
    """Run LLM call step with trimming, compaction, and provider completion."""
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.LLM_CALL_STARTED,
        payload={"provider": host._deps.provider.name},
    )
    clarification = context.metadata.get("clarification")
    try:
        observations = _microcompact_context_observations(host, context)
        request, trim_payload = _build_trimmed_request(
            host, context, observations, clarification
        )
        context.metadata["trim_audit"] = trim_payload["trim_audit"]
        context.metadata["trim_metadata"] = trim_payload["trim_metadata"]
        context.metadata["token_pressure"] = trim_payload["token_pressure"]
        context.metadata["prompt_render"] = trim_payload["prompt_render"]
        token_state = _token_pressure_state(context.metadata.get("token_pressure", {}))
        await apply_compaction_if_eligible(
            host,
            context=context,
            request=request,
            token_pressure_state=token_state,
        )
        context.llm_response = await _complete_request(host, context, request)
    except (RuntimeError, ValueError) as exc:
        host._emit(
            EventSpec(
                run_id=context.run_id,
                attempt_id=context.attempt_id,
                event_type=RuntimeEventType.RUN_FAILED,
                payload={"reason": TerminalReason.MODEL_ERROR.value},
            )
        )
        raise RuntimeExecutionError("LLM completion failed") from exc
    token_chunks = context.llm_response.metadata.get("token_chunks")
    if isinstance(token_chunks, list) and not bool(
        context.llm_response.metadata.get("token_chunks_emitted")
    ):
        emit_token_delta_events(
            host,
            context,
            [chunk for chunk in token_chunks if isinstance(chunk, str)],
        )
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.LLM_CALL_COMPLETED,
        payload={
            "provider": context.llm_response.provider,
            "model": context.llm_response.model,
            "finish_reason": context.llm_response.finish_reason.value,
        },
    )
    _emit_token_pressure_warning(host, context)
    context.step_count += 1
    context.metadata.update(
        {
            "next_step": "tool_stage",
            "step_count": context.step_count,
            "tool_calls": context.tool_calls,
            "last_llm_response": context.llm_response.model_dump(mode="json"),
        }
    )
    host._save_checkpoint(context, latest_output=None, node_id="llm_call")
    host._maybe_fail_after_step("llm_call")
    return RuntimeStepResult(next_step="tool_stage")


def _microcompact_context_observations(
    host: LlmStepHost, context: RunContext
) -> list[dict[str, object]]:
    observations = context.metadata.get("observations", [])
    if not isinstance(observations, list):
        observations = []
    micro = microcompact_observations(
        [item for item in observations if isinstance(item, dict)],
        preserve_recent=host._config.microcompact_preserve_recent,
        max_preview_chars=host._config.microcompact_max_preview_chars,
    )
    context.metadata["observations"] = micro.observations
    context.metadata["microcompaction_audit"] = micro.audit
    context.metadata["microcompaction"] = {
        "bytes_saved": micro.bytes_saved,
        "estimated_tokens_saved": micro.estimated_tokens_saved,
    }
    return micro.observations


def _build_trimmed_request(
    host: LlmStepHost,
    context: RunContext,
    observations: list[dict[str, object]],
    clarification: object,
) -> tuple[Any, dict[str, object]]:
    digest_refs = context.metadata.get("digest_refs", [])
    if not isinstance(digest_refs, list):
        digest_refs = []
    artifact_refs = context.metadata.get("artifact_refs", [])
    if not isinstance(artifact_refs, list):
        artifact_refs = []
    planning_prompt = None
    planning_step_payload = context.metadata.get("planning_step")
    if host._config.include_planning_prompt and isinstance(planning_step_payload, dict):
        planning_prompt = render_planning_step_prompt(
            PlanningStep.model_validate(planning_step_payload)
        )
    return build_single_agent_llm_request(
        LlmRequestBuildContext(
            run_input=context.run_input,
            clarification=clarification if isinstance(clarification, str) else None,
            tool_docs=(
                context.metadata["code_tool_docs"]
                if isinstance(context.metadata.get("code_tool_docs"), str)
                else None
            ),
            authorized_imports=host._config.authorized_imports,
            registry=host._deps.tool_registry,
            observations=tuple(
                item for item in observations if isinstance(item, dict)
            ),
            planning_prompt=planning_prompt,
            digest_ids=tuple(
                str(item.get("digest_id"))
                for item in digest_refs
                if isinstance(item, dict) and item.get("digest_id")
            ),
            artifact_ids=tuple(
                str(item.get("artifact_id"))
                for item in artifact_refs
                if isinstance(item, dict) and item.get("artifact_id")
            ),
            max_chars=host._config.trim_max_chars,
            max_messages=host._config.trim_max_messages,
            max_observations=host._config.trim_max_observations,
            context_window_estimate=host._config.context_window_estimate,
            warning_threshold=host._config.token_warning_threshold,
            compact_threshold=host._config.token_compact_threshold,
            blocking_threshold=host._config.token_blocking_threshold,
            output_token_reserve=host._config.output_token_reserve,
            stream=is_stream_enabled(context.run_input),
        )
    )


async def _complete_request(host: LlmStepHost, context: RunContext, request: Any) -> LlmResponse:
    if not is_stream_enabled(context.run_input):
        return await host._deps.provider.complete(request)
    return await complete_streaming_request(host, context, request)


def _token_pressure_state(token_pressure: object) -> str:
    if not isinstance(token_pressure, dict):
        return "ok"
    return str(token_pressure.get("state", "ok"))


def _emit_token_pressure_warning(host: LlmStepHost, context: RunContext) -> None:
    token_pressure = context.metadata.get("token_pressure", {})
    if not isinstance(token_pressure, dict):
        return
    state = str(token_pressure.get("state", "ok"))
    if state not in {"warning", "compact_recommended", "blocking"}:
        return
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.WARNING,
        payload={
            "kind": "token_pressure",
            "state": state,
            "used_tokens_estimate": token_pressure.get("used_tokens_estimate"),
            "blocking_threshold": token_pressure.get("blocking_threshold"),
        },
    )


__all__ = ["LlmStepHost", "execute_llm_call_step"]
