"""Governed tool executor: policy, interrupts, and staged guardrails."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

from agent_driver.contracts.enums import GuardrailDecision, ToolPolicyDecision
from agent_driver.contracts.hooks import HookResponse, ToolHook
from agent_driver.contracts.interrupts import (
    AllowedPrompt,
    find_matching_prompt,
)
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.contracts.tools import ToolCall, ToolResultEnvelope
from agent_driver.llm.contracts import LlmResponse
from agent_driver.runtime.planning_policy import tool_policy_with_planned_tool_hint
from agent_driver.runtime.tool_gate import (
    ToolGate,
    ToolGateAllow,
    ToolGateAsk,
    ToolGateContext,
    ToolGateDeny,
    ToolGateResult,
)
from agent_driver.tools.executor.allowed import execute_allowed_path
from agent_driver.tools.executor.blocks import append_blocked_call
from agent_driver.tools.executor.partition import (
    ParallelBatch,
    SerialCall,
    is_call_concurrency_safe,
    partition_concurrent_calls,
)
from agent_driver.tools.executor.planned import extract_planned_tool_calls
from agent_driver.tools.executor.policy_interrupt import record_interrupt_and_trace
from agent_driver.tools.executor.result import GovernedExecutionResult
from agent_driver.tools.executor.specs import (
    AllowedSpec,
    BlockSpec,
    ExecSpec,
    ToolApprovalContext,
    safe_manifest,
)
from agent_driver.tools.guardrails import GuardrailPipeline
from agent_driver.tools.policy import evaluate_tool_policy
from agent_driver.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# Phase 11 H12 — soft cap on tools running in one ``asyncio.gather`` parallel
# batch. The partitioner is unbounded; the semaphore here protects the
# host from spawning unbounded coroutines when a model emits a long
# read-only fan-out (e.g. 30 file_reads). Mirrors openclaude
# ``CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY``.
from agent_driver.tools.executor.normalization import (
    _coerce_json_string_args,  # re-exported for back-compat (tests import here)
)
from agent_driver.tools.executor.normalization import (  # noqa: F401
    _normalize_tool_alias,
)

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
            "AGENT_DRIVER_TOOL_CONCURRENCY=%r is not an integer; " "falling back to %d",
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


class GovernedToolExecutor:
    """Execute deterministic planned tool calls with policy and guardrails.

    Phase 11 H12 — adjacent concurrency-safe calls (per
    ``ToolManifest.is_concurrency_safe``) run in a single
    ``asyncio.gather`` batch capped by ``concurrency_limit``. Calls that
    aren't safe (writes, external actions) execute serially as before.
    Result ordering matches the original LLM-emit order regardless of
    completion order inside parallel batches.
    """

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        guardrails: GuardrailPipeline | None = None,
        concurrency_limit: int | None = None,
        tool_hooks: "list[ToolHook] | tuple[ToolHook, ...] | None" = None,
        artifact_store: Any = None,
    ) -> None:
        self._registry = registry
        self._guardrails = guardrails or GuardrailPipeline()
        self._concurrency_limit = (
            concurrency_limit
            if concurrency_limit is not None
            else _read_concurrency_limit_env()
        )
        # Phase 11 H15 — chain of optional pre/post hooks. Hooks run in
        # registration order. Failures are isolated per-hook
        # (deduplicated WARNING log; original value preserved before
        # entering the chain).
        self._tool_hooks: tuple[ToolHook, ...] = tuple(tool_hooks or ())
        self._tool_hooks_make_context = lambda call: {
            "tool_name": call.tool_name,
            "args": call.args,
        }
        # Phase 12 H18 — optional artifact store for spilling oversized
        # tool handler outputs to persistent storage. When ``None``,
        # legacy ``output_char_budget`` truncation runs.
        self._artifact_store = artifact_store

    @staticmethod
    def planned_calls(llm_response: LlmResponse) -> list[ToolCall]:
        """Parse planned tool calls from LLM response metadata."""
        return extract_planned_tool_calls(llm_response)

    def _append_block(
        self,
        *,
        result: GovernedExecutionResult,
        spec: BlockSpec,
    ) -> None:
        append_blocked_call(result=result, spec=spec)

    async def execute(
        self,
        run_input: AgentRunInput,
        llm_response: LlmResponse,
        *,
        current_tool_calls: int = 0,
        tool_gate: "ToolGate | None" = None,
    ) -> GovernedExecutionResult:
        """Run policy + guardrails + tool handlers for planned calls.

        Phase 11 H12 — partitions the planned-call sequence into parallel
        batches (concurrency-safe adjacent calls) and serial calls.
        ``ParallelBatch`` runs via ``asyncio.gather`` with a semaphore
        capping the per-batch coroutine count. Stops further units
        (parallel or serial) when any prior call records an interrupt or
        a STOP-style policy decision.

        A0.2 — when ``tool_gate`` is supplied, every planned call is
        passed through the gate AFTER the static
        :func:`evaluate_tool_policy` returns ALLOW. The gate can flip
        the decision to DENY (blocked envelope) or INTERRUPT
        (operator approval). See :mod:`agent_driver.runtime.tool_gate`.
        """
        result = GovernedExecutionResult()
        planned_calls = self._normalize_planned_calls(llm_response)
        planned_calls = await self._apply_pre_hook_stage(planned_calls)
        run_input = self._apply_policy_hint_stage(
            run_input,
            planned_calls,
            current_tool_calls=current_tool_calls,
        )
        units = self._partition_stage(planned_calls)
        await self._execute_units_stage(
            units=units,
            run_input=run_input,
            result=result,
            current_tool_calls=current_tool_calls,
            tool_gate=tool_gate,
        )
        return result

    def _lookup_manifest(self, tool_name: str):
        registered = self._registry.get(tool_name)
        return registered.manifest if registered is not None else None

    def _normalize_planned_calls(self, llm_response: LlmResponse) -> list[ToolCall]:
        """Extract planned calls and normalize explicit compatibility aliases."""
        available_tool_names = tuple(self._registry.list_names())
        return [
            _normalize_tool_alias(call, available_tool_names=available_tool_names)
            for call in extract_planned_tool_calls(llm_response)
        ]

    async def _apply_pre_hook_stage(
        self, planned_calls: list[ToolCall]
    ) -> list[ToolCall]:
        """Run pre_tool_use hooks before concurrency partitioning."""
        if not self._tool_hooks:
            return planned_calls
        transformed: list[ToolCall] = []
        for call in planned_calls:
            transformed.append(await self._apply_pre_hooks(call))
        return transformed

    def _apply_policy_hint_stage(
        self,
        run_input: AgentRunInput,
        planned_calls: list[ToolCall],
        *,
        current_tool_calls: int,
    ) -> AgentRunInput:
        """Enrich run policy with planned-tool context before execution."""
        if not planned_calls:
            return run_input
        return run_input.model_copy(
            update={
                "tool_policy": tool_policy_with_planned_tool_hint(
                    run_input.tool_policy,
                    planned_calls,
                    manifest_lookup=self._lookup_manifest,
                    current_tool_calls=current_tool_calls,
                )
            }
        )

    def _partition_stage(
        self, planned_calls: list[ToolCall]
    ) -> list[SerialCall[ToolCall] | ParallelBatch[ToolCall]]:
        """Partition planned calls into serial and concurrency-safe units."""
        return partition_concurrent_calls(
            planned_calls,
            is_safe=lambda c: is_call_concurrency_safe(
                c, manifest_lookup=self._lookup_manifest
            ),
        )

    async def _execute_units_stage(
        self,
        *,
        units: list[SerialCall[ToolCall] | ParallelBatch[ToolCall]],
        run_input: AgentRunInput,
        result: GovernedExecutionResult,
        current_tool_calls: int,
        tool_gate: "ToolGate | None" = None,
    ) -> None:
        """Execute partitioned units and collect envelopes/traces in order."""
        next_index = 1
        for unit in units:
            if isinstance(unit, SerialCall):
                stop = await self._execute_one_call_traced(
                    ExecSpec(
                        result=result,
                        run_input=run_input,
                        call=unit.item,
                        index=next_index,
                        current_tool_calls=current_tool_calls,
                        tool_gate=tool_gate,
                    )
                )
                next_index += 1
                if stop:
                    return
                continue
            stop = await self._execute_parallel_batch(
                batch=unit,
                run_input=run_input,
                result=result,
                start_index=next_index,
                current_tool_calls=current_tool_calls,
                tool_gate=tool_gate,
            )
            next_index += len(unit.items)
            if stop:
                return

    async def _apply_tool_gate(
        self,
        *,
        gate: "ToolGate",
        policy,
        call: ToolCall,
        manifest,
        run_input: AgentRunInput,
        current_tool_calls: int,
    ):
        """A0.2 — invoke the caller-supplied tool gate; translate to
        a policy decision flip.

        Returns a (possibly updated) ``ToolPolicyOutcome``. A gate
        exception is logged and treated as DENY with the exception
        text as reason — fail-closed by design (better to block one
        call than to silently bypass an operator-level risk check).

        ``policy`` is ``ToolPolicyOutcome``; left untyped above so the
        signature stays compatible with the implicit late-bound import.
        """
        gate_ctx = ToolGateContext(
            tool_name=call.tool_name,
            args=dict(call.args),
            run_id=run_input.run_id,
            thread_id=run_input.thread_id,
            agent_id=run_input.agent_id,
            risk=manifest.risk.value,
            side_effect=manifest.side_effect.value,
            current_tool_calls=current_tool_calls,
        )
        try:
            result: ToolGateResult = await gate(gate_ctx)
        except Exception as exc:  # pragma: no cover - simple translation
            logger.warning(
                "tool_gate raised for %r; treating as DENY (fail-closed): %s",
                call.tool_name,
                exc,
                exc_info=True,
            )
            return policy.model_copy(
                update={
                    "decision": ToolPolicyDecision.DENY,
                    "reason": f"tool_gate raised: {exc}",
                }
            )
        if isinstance(result, ToolGateAllow):
            return policy
        if isinstance(result, ToolGateDeny):
            return policy.model_copy(
                update={
                    "decision": ToolPolicyDecision.DENY,
                    "reason": f"tool_gate denied: {result.reason}",
                }
            )
        if isinstance(result, ToolGateAsk):
            return policy.model_copy(
                update={
                    "decision": ToolPolicyDecision.INTERRUPT,
                    "reason": result.message,
                    "interrupt_reason": "approval_required",
                    # Carry the host's optional heading override through to the
                    # interrupt (ToolGateAsk.title is documented to override the
                    # default "Approval required for '<tool>'" heading).
                    "interrupt_title": result.title,
                }
            )
        logger.warning(
            "tool_gate returned unsupported result type %r for %r; treating as DENY",
            type(result).__name__,
            call.tool_name,
        )
        return policy.model_copy(
            update={
                "decision": ToolPolicyDecision.DENY,
                "reason": f"tool_gate returned unsupported result: {type(result).__name__}",
            }
        )

    async def _invoke_hook_with_timeout(
        self,
        coro,
        *,
        hook,
        stage: str,
    ):
        """Phase 12 H22 — run one hook coroutine with optional timeout.

        Returns the coroutine's result, or raises asyncio.TimeoutError
        when the hook exceeds its declared ``timeout_seconds`` budget.
        Hooks without ``timeout_seconds`` (default ``None``) run
        unbounded — preserves the H15 behaviour for legacy hooks.
        """
        timeout = getattr(hook, "timeout_seconds", None)
        if timeout is None or timeout <= 0:
            return await coro
        return await asyncio.wait_for(coro, timeout=timeout)

    @staticmethod
    def _unwrap_hook_response(replacement, expected_type, hook):
        """Phase 12 H22 — normalize a hook's return into
        ``(value_or_None, prevent_continuation, additional_context)``.

        Accepts three legal shapes:
        * ``None`` — no change.
        * ``HookResponse[expected_type]`` — full aggregation envelope.
        * ``expected_type`` — bare value (legacy H15 shape).

        Anything else is ignored with a WARNING; treated as ``None``.
        """
        if replacement is None:
            return None, False, {}
        if isinstance(replacement, HookResponse):
            value = replacement.value
            if value is not None and not isinstance(value, expected_type):
                logger.warning(
                    "tool_hook %r HookResponse.value is %s (expected %s); "
                    "treating as None",
                    getattr(hook, "name", type(hook).__name__),
                    type(value).__name__,
                    expected_type.__name__,
                )
                value = None
            return (
                value,
                bool(replacement.prevent_continuation),
                dict(replacement.additional_context or {}),
            )
        if isinstance(replacement, expected_type):
            return replacement, False, {}
        logger.warning(
            "tool_hook %r returned %r (expected %s | HookResponse | None); ignoring",
            getattr(hook, "name", type(hook).__name__),
            type(replacement).__name__,
            expected_type.__name__,
        )
        return None, False, {}

    async def _apply_pre_hooks(self, call: ToolCall) -> ToolCall:
        """Phase 11 H15 + Phase 12 H22 — run the pre_tool_use chain.

        Hooks run in registration order; each sees the previous hook's
        output AND any ``additional_context`` accumulated from earlier
        hooks. On any hook exception or per-hook timeout the chain
        falls back to the pre-hook value for THAT hook and continues
        with the next hook (errors are isolated). Returns the final
        transformed call.

        Phase 12 additions:
        * ``HookResponse.prevent_continuation=True`` exits the chain
          early; subsequent hooks for this event are skipped.
        * ``HookResponse.additional_context`` accumulates (later
          hooks win on key collisions).
        * Hook ``timeout_seconds`` bounds each await; timeout is
          treated like an exception (preserve previous value).
        """
        current = call
        chained_context: dict[str, Any] = {}
        for hook in self._tool_hooks:
            base_context = self._tool_hooks_make_context(current)
            # Merge chained context FIRST so the hook's view contains
            # both its tool context and any prior aggregations; the
            # hook's tool context takes precedence on conflicts.
            context: dict[str, Any] = {**chained_context, **base_context}
            try:
                replacement = await self._invoke_hook_with_timeout(
                    hook.pre_tool_use(current, context),
                    hook=hook,
                    stage="pre_tool_use",
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "tool_hook %r timed out in pre_tool_use after %ss; "
                    "preserving previous call",
                    getattr(hook, "name", type(hook).__name__),
                    getattr(hook, "timeout_seconds", None),
                )
                continue
            except Exception:
                logger.warning(
                    "tool_hook %r raised in pre_tool_use; preserving " "previous call",
                    getattr(hook, "name", type(hook).__name__),
                    exc_info=True,
                )
                continue
            value, prevent_continuation, extra_ctx = self._unwrap_hook_response(
                replacement, ToolCall, hook
            )
            if value is not None:
                current = value
            if extra_ctx:
                chained_context.update(extra_ctx)
            if prevent_continuation:
                logger.debug(
                    "tool_hook %r requested prevent_continuation in "
                    "pre_tool_use; stopping chain",
                    getattr(hook, "name", type(hook).__name__),
                )
                break
        return current

    async def _apply_post_hooks(
        self, envelope: ToolResultEnvelope
    ) -> ToolResultEnvelope:
        """Phase 11 H15 + Phase 12 H22 — run the post_tool_use chain.

        Same semantics as ``_apply_pre_hooks`` (HookResponse support,
        additional_context accumulation, per-hook timeout, early-exit
        via prevent_continuation). Aggregated ``additional_context`` is
        merged into the final envelope's metadata under
        ``hook_chain_context`` so downstream consumers can inspect
        what each hook contributed.
        """
        current = envelope
        chained_context: dict[str, Any] = {}
        for hook in self._tool_hooks:
            base_context = {
                "tool_name": current.call.tool_name,
                "decision": current.decision.value,
                "guardrail_decision": current.guardrail_decision.value,
            }
            context = {**chained_context, **base_context}
            try:
                replacement = await self._invoke_hook_with_timeout(
                    hook.post_tool_use(current, context),
                    hook=hook,
                    stage="post_tool_use",
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "tool_hook %r timed out in post_tool_use after %ss; "
                    "preserving previous envelope",
                    getattr(hook, "name", type(hook).__name__),
                    getattr(hook, "timeout_seconds", None),
                )
                continue
            except Exception:
                logger.warning(
                    "tool_hook %r raised in post_tool_use; preserving "
                    "previous envelope",
                    getattr(hook, "name", type(hook).__name__),
                    exc_info=True,
                )
                continue
            value, prevent_continuation, extra_ctx = self._unwrap_hook_response(
                replacement, ToolResultEnvelope, hook
            )
            if value is not None:
                current = value
            if extra_ctx:
                chained_context.update(extra_ctx)
            if prevent_continuation:
                logger.debug(
                    "tool_hook %r requested prevent_continuation in "
                    "post_tool_use; stopping chain",
                    getattr(hook, "name", type(hook).__name__),
                )
                break
        # Surface aggregated chain context into envelope metadata so
        # downstream consumers (observability sinks, audit logs) can
        # inspect what each hook contributed without parsing logs.
        if chained_context:
            merged_metadata = dict(current.metadata or {})
            merged_metadata["hook_chain_context"] = chained_context
            current = current.model_copy(update={"metadata": merged_metadata})
        return current

    async def _execute_parallel_batch(
        self,
        *,
        batch: ParallelBatch[ToolCall],
        run_input: AgentRunInput,
        result: GovernedExecutionResult,
        start_index: int,
        current_tool_calls: int,
        tool_gate: "ToolGate | None" = None,
    ) -> bool:
        """Run a parallel batch; merge sub-results into ``result`` in order.

        Returns True when any call recorded a stop signal (interrupt or
        policy STOP); callers should not run subsequent units.

        Implementation notes:
        * each task gets its OWN ``GovernedExecutionResult`` so mutations
          don't race; we merge afterwards in original (start_index-based)
          order so the trace/envelope sequence stays deterministic for
          the LLM and observability;
        * semaphore caps active coroutines at ``concurrency_limit`` —
          partition emits unbounded batches because cap is a runtime
          concern, not a planning one;
        * exceptions inside any one task surface as
          ``BaseException`` propagation (``return_exceptions=False``) —
          this matches the existing serial executor which doesn't
          swallow handler exceptions. ``execute_allowed_path`` already
          catches handler exceptions itself and writes them into the
          sub-result, so this layer typically only sees task-cancellation
          / fatal errors.
        """
        if not batch.items:
            return False
        semaphore = asyncio.Semaphore(self._concurrency_limit)

        async def run_one(call: ToolCall, index: int) -> GovernedExecutionResult:
            async with semaphore:
                sub_result = GovernedExecutionResult()
                await self._execute_one_call_traced(
                    ExecSpec(
                        result=sub_result,
                        run_input=run_input,
                        call=call,
                        index=index,
                        current_tool_calls=current_tool_calls,
                        tool_gate=tool_gate,
                    )
                )
                return sub_result

        tasks = [
            run_one(call, start_index + offset)
            for offset, call in enumerate(batch.items)
        ]
        sub_results = await asyncio.gather(*tasks)
        stop_overall = False
        for sub_result in sub_results:
            for envelope, trace in zip(sub_result.envelopes, sub_result.traces):
                result.append(envelope=envelope, trace=trace)
            # Phase 11 H16 — propagate progress events from parallel
            # sub-results into the canonical result; preserve their
            # within-task order (which is already chronological) and
            # group by call_index.
            for entry in sub_result.progress_events:
                result.progress_events.append(entry)
            if sub_result.interrupt is not None and result.interrupt is None:
                # Preserve the FIRST (lowest-index) interrupt — matches
                # serial semantics where the loop stops on first
                # interrupt; for parallel batches we surface the
                # earliest planned-call interrupt as canonical.
                result.interrupt = sub_result.interrupt
                stop_overall = True
        return stop_overall

    async def _execute_one_call_traced(self, spec: ExecSpec) -> bool:
        """Wrap :meth:`_execute_one_call` in an OpenInference TOOL span.

        Phoenix then renders the tool call with its name, args, result and — when
        the call is denied/failed — a red error status carrying the reason (e.g.
        the SQLAlchemy "concurrent operations" message that made chart_vegalite
        get denied). The status/result are read back from the ToolTrace this call
        appends. No-op + never raises when tracing is off.
        """
        from agent_driver.observability.openinference import (  # noqa: PLC0415
            SPAN_KIND_TOOL,
            oi_span,
            record_status,
            set_io,
            set_tool,
        )

        call = spec.call
        before = len(spec.result.traces)
        with oi_span(call.tool_name, kind=SPAN_KIND_TOOL) as span:
            set_tool(
                span,
                name=call.tool_name,
                arguments=dict(call.args or {}),
                call_id=getattr(call, "tool_call_id", None),
            )
            stop = await self._execute_one_call(spec)
            new_traces = spec.result.traces[before:]
            trace = new_traces[-1] if new_traces else None
            if trace is not None:
                status_value = getattr(getattr(trace, "status", None), "value", "")
                ok = status_value == "completed"
                set_io(span, output=getattr(trace, "result_summary", None))
                record_status(
                    span,
                    ok=ok,
                    description=(
                        None
                        if ok
                        else (
                            getattr(trace, "result_summary", None)
                            or getattr(trace, "error_code", None)
                            or status_value
                        )
                    ),
                )
            return stop

    async def _execute_one_call(self, spec: ExecSpec) -> bool:
        """Execute one tool call, returning True when loop must stop."""
        result = spec.result
        run_input = spec.run_input
        call = spec.call
        index = spec.index
        run_metadata = {
            "run_id": run_input.run_id,
            "thread_id": run_input.thread_id,
            "attempt_id": f"attempt_{index}",
            "agent_id": run_input.agent_id,
            "agent_profile": run_input.agent_profile.value,
            "prompt_template_id": run_input.prompt_template_id,
            "prompt_template_version": run_input.prompt_template_version,
        }
        registered = self._registry.get(call.tool_name)
        manifest = (
            registered.manifest
            if registered is not None
            else safe_manifest(call.tool_name)
        )
        # Phase 11 H12 — use index-based cumulative count rather than
        # ``len(result.traces)``. In sequential mode the two are
        # equivalent (the result accumulates one trace per completed
        # call before the next iteration), but parallel batches all
        # see the same ``result.traces`` length because each task
        # owns a private sub-result. Index is monotonic across
        # serial/parallel units.
        policy = evaluate_tool_policy(
            policy=run_input.tool_policy,
            manifest=manifest,
            call=call,
            current_tool_calls=spec.current_tool_calls + spec.index - 1,
        )
        approved_interrupt_id = call.metadata.get("approved_interrupt_id")
        if (
            policy.decision == ToolPolicyDecision.INTERRUPT
            and isinstance(approved_interrupt_id, str)
            and approved_interrupt_id.strip()
        ):
            policy = policy.model_copy(
                update={
                    "decision": ToolPolicyDecision.ALLOW,
                    "reason": "approval previously granted",
                    "interrupt_reason": None,
                }
            )
        # Phase 11 H13 — prompt-based permissions. When the policy says
        # INTERRUPT but the call's shape matches a previously-approved
        # AllowedPrompt category for this run, collapse to ALLOW. The
        # host wires approved categories into
        # ``run_input.app_metadata["approved_prompts"]`` after an
        # operator approves them via ``ResumeCommand.approved_prompts``.
        # See ``agent_driver.contracts.interrupts.AllowedPrompt`` for
        # the matcher contract.
        if policy.decision == ToolPolicyDecision.INTERRUPT:
            matched = _match_run_approved_prompts(run_input=run_input, call=call)
            if matched is not None:
                policy = policy.model_copy(
                    update={
                        "decision": ToolPolicyDecision.ALLOW,
                        "reason": (
                            f"matches approved prompt category "
                            f"{matched.category_id!r}"
                        ),
                        "interrupt_reason": None,
                    }
                )
        # A0.2 — dynamic per-call tool gate. Runs ONLY when policy is
        # ALLOW (denial / interrupt are already final). The gate sees
        # the planned call's args + manifest risk + side_effect, returns
        # Allow / Deny / Ask. Errors are caught and treated as Deny
        # (fail-closed) so a malformed gate can't silently bypass
        # operator-level checks.
        #
        # Skip the gate for a call the operator already approved via a
        # prior interrupt (``approved_interrupt_id`` set on resume). A
        # stateless gate (e.g. ``build_permission_gate``) re-evaluates the
        # same risky call identically and would ASK again, re-parking the
        # run on the very interrupt the operator just cleared — an infinite
        # approve/ask loop. This mirrors the static-policy short-circuit
        # above that collapses INTERRUPT->ALLOW for approved calls.
        if (
            policy.decision == ToolPolicyDecision.ALLOW
            and spec.tool_gate is not None
            and not (
                isinstance(approved_interrupt_id, str) and approved_interrupt_id.strip()
            )
        ):
            policy = await self._apply_tool_gate(
                gate=spec.tool_gate,
                policy=policy,
                call=call,
                manifest=manifest,
                run_input=run_input,
                current_tool_calls=spec.current_tool_calls + spec.index - 1,
            )
        if policy.decision == ToolPolicyDecision.DENY:
            self._append_block(
                result=result,
                spec=BlockSpec(
                    index=index,
                    call=call,
                    manifest=manifest,
                    code="policy_denied",
                    reason=policy.reason,
                ),
            )
            return False
        if policy.decision == ToolPolicyDecision.INTERRUPT:
            record_interrupt_and_trace(
                result,
                ToolApprovalContext(
                    run_input=run_input,
                    call=call,
                    index=index,
                    manifest=manifest,
                    policy=policy,
                    run_metadata=run_metadata,
                ),
            )
            return True
        input_guard = await self._guardrails.on_input(
            {
                "run_id": run_input.run_id,
                "tool_name": call.tool_name,
                "args": call.args,
            }
        )
        if input_guard.decision == GuardrailDecision.BLOCK:
            self._append_block(
                result=result,
                spec=BlockSpec(
                    index=index,
                    call=call,
                    manifest=manifest,
                    reason=input_guard.reason or "guardrail blocked tool input",
                    code="guardrail_blocked",
                    stage="input",
                ),
            )
            return False
        envelopes_before = len(result.envelopes)
        outcome = await execute_allowed_path(
            guardrails=self._guardrails,
            spec=AllowedSpec(
                result=result,
                call=call,
                index=index,
                manifest=manifest,
                registered=registered,
                input_guard_decision=input_guard.decision,
                run_metadata=run_metadata,
                # Phase 12 H18 — pass the executor-scoped artifact store
                # so the allow-path can spill oversized outputs when
                # the manifest opts in via ``max_result_size_chars``.
                artifact_store=self._artifact_store,
                # Phase 13 H29.3 — give the allow-path the registry's
                # tool names so the unregistered-tool branch can build
                # a "did you mean: X" feedback string for the next LLM
                # turn instead of the bare "tool is not registered".
                available_tool_names=tuple(self._registry.list_names()),
            ),
        )
        # Phase 11 H15 — apply post_tool_use hook chain to any envelope
        # appended by ``execute_allowed_path``. We replace in place so
        # the trace pair remains aligned. Note that block-paths
        # (guardrail BLOCK, unregistered, etc.) also append an envelope
        # — hooks see those too; the typical pattern is to enrich
        # ``metadata`` regardless of decision.
        if self._tool_hooks and len(result.envelopes) > envelopes_before:
            for slot in range(envelopes_before, len(result.envelopes)):
                envelope = result.envelopes[slot]
                transformed = await self._apply_post_hooks(envelope)
                result.envelopes[slot] = transformed
        return outcome
