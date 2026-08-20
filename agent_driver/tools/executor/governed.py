"""Governed tool executor: policy, interrupts, and staged guardrails."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from agent_driver.runtime.abort import RunAbortHandle

from agent_driver.contracts.enums import GuardrailDecision, ToolPolicyDecision
from agent_driver.contracts.hooks import HookResponse, ToolHook
from agent_driver.contracts.interrupts import (
    AllowedPrompt,
    find_matching_prompt,
)
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.contracts.tools import (
    MANAGEMENT_TOOL_NAMES,
    ToolCall,
    ToolManifest,
    ToolResultEnvelope,
)
from agent_driver.llm.contracts import LlmResponse
from agent_driver.runtime.planning_policy import tool_policy_with_planned_tool_hint
from agent_driver.runtime.tool_gate import (
    ToolGate,
    ToolGateAllow,
    ToolGateAsk,
    RESERVED_GATE_DECISION_KEY,
    ToolGateContext,
    ToolGateDeny,
    ToolGateResult,
    build_gate_provenance_metadata,
    extract_reserved_metadata,
)
from agent_driver.tools.executor.allowed import execute_allowed_path
from agent_driver.tools.executor.blocks import (
    append_blocked_call,
    disallowed_management_tool_remediation,
)
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
        per_turn_output_budget_chars: int | None = None,
    ) -> None:
        self._registry = registry
        # Epic 033 B (tier 3): aggregate per-turn tool-output budget. None/0 = off.
        self._per_turn_output_budget_chars = per_turn_output_budget_chars
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

    @staticmethod
    def _capability_predispatch_block(
        call: ToolCall, manifest: ToolManifest, *, index: int
    ) -> BlockSpec | None:
        """Return a block spec when the tool's HARD execution requirement is
        unmet by the current run-scoped capability snapshot, else ``None``.

        Fail-safe and side-effect free: no snapshot in scope (no backend) or no
        requirement means no gating. The reason is host-facing and carries no
        secret values.
        """
        from agent_driver.execution.capabilities import check_manifest_requirement
        from agent_driver.tools.context import get_capability_snapshot

        check = check_manifest_requirement(manifest, get_capability_snapshot())
        if check is None or check.satisfied:
            return None
        return BlockSpec(
            index=index,
            call=call,
            manifest=manifest,
            reason=check.reason or "required execution capability not available",
            code="capability_unmet",
            stage="capability",
        )

    async def execute(
        self,
        run_input: AgentRunInput,
        llm_response: LlmResponse,
        *,
        current_tool_calls: int = 0,
        tool_gate: "ToolGate | None" = None,
        abort_handle: "RunAbortHandle | None" = None,
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
        # U4 — adapt the run's process-local abort handle into a predicate so
        # ``tools`` stays decoupled from the runtime abort primitive. Only build
        # one when a handle was actually plumbed in (default: no cancellation).
        cancelled_check = (
            (lambda: abort_handle.is_aborted) if abort_handle is not None else None
        )
        await self._execute_units_stage(
            units=units,
            run_input=run_input,
            result=result,
            current_tool_calls=current_tool_calls,
            tool_gate=tool_gate,
            cancelled_check=cancelled_check,
        )
        self._apply_turn_output_budget(result)
        return result

    def _apply_turn_output_budget(self, result: GovernedExecutionResult) -> None:
        """Epic 033 B tier 3: trim the turn's aggregate tool output if over budget."""
        if not self._per_turn_output_budget_chars:
            return
        from agent_driver.tools.executor.turn_budget import enforce_turn_output_budget

        trimmed, audit = enforce_turn_output_budget(
            result.envelopes, budget_chars=self._per_turn_output_budget_chars
        )
        if audit.get("activated"):
            result.envelopes[:] = trimmed
            result.turn_output_budget_audit = audit

    def _lookup_manifest(self, tool_name: str):
        registered = self._registry.get(tool_name)
        return registered.manifest if registered is not None else None

    def _effective_tool_names(self, run_input: AgentRunInput) -> tuple[str, ...]:
        registry_names = tuple(self._registry.list_names())
        allowed = run_input.tool_policy.allowed_tools
        refinement = run_input.tool_policy.metadata.get("plan_refinement_required")
        if isinstance(refinement, dict) and "previous_allowed_tools" in refinement:
            previous = refinement.get("previous_allowed_tools")
            allowed = previous if isinstance(previous, list) else None
        if allowed is None:
            return registry_names
        registered = set(registry_names)
        return tuple(
            dict.fromkeys(
                str(tool_name).strip()
                for tool_name in allowed
                if str(tool_name).strip() and str(tool_name).strip() in registered
            )
        )

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
        cancelled_check: "Callable[[], bool] | None" = None,
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
                        cancelled_check=cancelled_check,
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
                cancelled_check=cancelled_check,
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
        attempt_id: str | None = None,
    ):
        """A0.2 — invoke the caller-supplied tool gate; translate to
        a policy decision flip.

        Returns a (possibly updated) ``ToolPolicyOutcome``. A gate
        exception is logged and treated as DENY with the exception
        text as reason — fail-closed by design (better to block one
        call than to silently bypass an operator-level risk check).

        When a gate result carries :class:`GateProvenance`, the validated
        provenance is folded into the outcome metadata under the reserved
        :data:`RESERVED_GATE_PROVENANCE_KEY` namespace. Non-JSON / oversized /
        too-deep / reserved-key host provenance also fails closed (DENY) so a
        malformed host payload cannot silently pass unaudited.

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
            tool_call_id=call.tool_call_id,
            attempt_id=attempt_id,
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
        # Validate + pack any host provenance BEFORE the decision translation so
        # a malformed payload fails closed regardless of allow/deny/ask.
        provenance = getattr(result, "provenance", None)
        provenance_meta: dict[str, Any] = {}
        if provenance is not None:
            try:
                provenance_meta = build_gate_provenance_metadata(provenance)
            except ValueError as exc:
                logger.warning(
                    "tool_gate returned invalid provenance for %r; treating as DENY "
                    "(fail-closed): %s",
                    call.tool_name,
                    exc,
                )
                return policy.model_copy(
                    update={
                        "decision": ToolPolicyDecision.DENY,
                        "reason": f"tool_gate provenance rejected: {exc}",
                    }
                )

        def _with_reserved(update: dict[str, Any], *, mark: str) -> dict[str, Any]:
            # Always stamp the gate-decision marker (so a terminal/trace
            # projection can tell a gate decision from a static one — R1), plus
            # any validated host provenance. Both live under the reserved ``_ad_``
            # namespace so model/tool metadata can neither forge nor overwrite them.
            reserved = {RESERVED_GATE_DECISION_KEY: mark, **provenance_meta}
            return {**update, "metadata": {**policy.metadata, **reserved}}

        if isinstance(result, ToolGateAllow):
            # A transparent allow with no provenance is identical to no gate —
            # leave the outcome untouched (no marker, no projection noise).
            if not provenance_meta:
                return policy
            return policy.model_copy(update=_with_reserved({}, mark="allow"))
        if isinstance(result, ToolGateDeny):
            return policy.model_copy(
                update=_with_reserved(
                    {
                        "decision": ToolPolicyDecision.DENY,
                        "reason": f"tool_gate denied: {result.reason}",
                    },
                    mark="deny",
                )
            )
        if isinstance(result, ToolGateAsk):
            return policy.model_copy(
                update=_with_reserved(
                    {
                        "decision": ToolPolicyDecision.INTERRUPT,
                        "reason": result.message,
                        "interrupt_reason": "approval_required",
                        # Carry the host's optional heading override through to the
                        # interrupt (ToolGateAsk.title is documented to override the
                        # default "Approval required for '<tool>'" heading).
                        "interrupt_title": result.title,
                    },
                    mark="ask",
                )
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
                    "tool_hook %r raised in pre_tool_use; preserving previous call",
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
        cancelled_check: "Callable[[], bool] | None" = None,
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
                        cancelled_check=cancelled_check,
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

    async def _resolve_call_policy(self, spec: ExecSpec, *, manifest):
        """Resolve the effective ``ToolPolicyOutcome`` for a planned call: static
        policy evaluation, the two INTERRUPT->ALLOW collapses (a call the operator
        already approved via a prior interrupt, and a call matching a run-approved
        prompt category), then the dynamic per-call tool gate. The returned policy's
        ``decision`` drives dispatch in ``_execute_one_call``."""
        run_input = spec.run_input
        call = spec.call
        index = spec.index
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
                            f"matches approved prompt category {matched.category_id!r}"
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
                attempt_id=f"attempt_{index}",
            )
        return policy

    async def _postprocess_new_envelopes(
        self, result, *, envelopes_before: int, policy
    ) -> None:
        """Post-process every envelope the allow-path appended for this call: apply
        the post_tool_use hook chain, then merge reserved (``_ad_``) gate provenance
        onto them LAST so neither a hook nor tool output can overwrite host-authored
        provenance."""
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
        # R1 — preserve reserved (``_ad_``) gate provenance/decision on the
        # allow-path envelopes (e.g. a ToolGateAllow that attached provenance).
        # Merged LAST, after post-hooks, so neither a hook nor tool output can
        # overwrite host-authored provenance.
        gate_reserved = extract_reserved_metadata(policy.metadata)
        if gate_reserved and len(result.envelopes) > envelopes_before:
            for slot in range(envelopes_before, len(result.envelopes)):
                envelope = result.envelopes[slot]
                result.envelopes[slot] = envelope.model_copy(
                    update={"metadata": {**(envelope.metadata or {}), **gate_reserved}}
                )

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
        policy = await self._resolve_call_policy(spec, manifest=manifest)
        if policy.decision == ToolPolicyDecision.DENY:
            self._append_block(
                result=result,
                spec=BlockSpec(
                    index=index,
                    call=call,
                    manifest=manifest,
                    code="policy_denied",
                    reason=policy.reason,
                    structured_output=_management_tool_denial_remediation(
                        run_input, call.tool_name
                    ),
                    # R1 — carry gate provenance/decision onto the denied envelope
                    # (empty for a static policy DENY, present for a gate DENY).
                    reserved_metadata=extract_reserved_metadata(policy.metadata),
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
        # EPIC-02: re-check the tool's execution requirement against the CURRENT
        # run-scoped capability snapshot, immediately before dispatch. This is
        # below the model and after policy/gate/guardrail, so a model argument
        # cannot bypass it and a snapshot that drifted since the tool schema was
        # built (time-of-check/time-of-use) still fences the call here.
        capability_block = self._capability_predispatch_block(
            call, manifest, index=index
        )
        if capability_block is not None:
            self._append_block(result=result, spec=capability_block)
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
                cancelled_check=spec.cancelled_check,
                cancellation_deadline=getattr(spec.run_input, "deadline_seconds", None),
                # Phase 12 H18 — pass the executor-scoped artifact store
                # so the allow-path can spill oversized outputs when
                # the manifest opts in via ``max_result_size_chars``.
                artifact_store=self._artifact_store,
                # Phase 13 H29.3 — give the allow-path the registry's
                # tool names so the unregistered-tool branch can build
                # a "did you mean: X" feedback string for the next LLM
                # turn instead of the bare "tool is not registered".
                available_tool_names=tuple(self._registry.list_names()),
                effective_tool_names=self._effective_tool_names(run_input),
            ),
        )
        await self._postprocess_new_envelopes(
            result, envelopes_before=envelopes_before, policy=policy
        )
        return outcome
