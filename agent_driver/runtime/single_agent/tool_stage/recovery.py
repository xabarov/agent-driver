"""Synthetic recovery / repair-hint messages appended after a tool stage.

Extracted verbatim from ``tool_stage/__init__`` (god-module split, behaviour-neutral).
Each helper inspects the tool-execution result and, on a specific failure shape, appends
one bounded, scaffolding-tagged user-role hint so the model recovers on the next turn
instead of looping the same broken call. All are leaf helpers (they call only external
runtime helpers, never other tool_stage flow functions), so ``__init__`` imports them
one-way and re-exports them for existing callers/tests.
"""

from __future__ import annotations

from typing import Any

from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.scaffolding import scaffolding_metadata
from agent_driver.runtime.metadata_state import get_tool_loop_state
from agent_driver.runtime.single_agent.types import RunContext
from agent_driver.runtime.tools import ToolExecutionResult


def _append_tool_call_parse_error_feedback(
    context: RunContext,
    _result: ToolExecutionResult,
    messages: list[ChatMessage],
) -> None:
    """Phase 13 H29.3 wire-up — surface text-form tool-call parse errors.

    The provider's normalization step (``OpenAICompatibleProvider`` /
    ``AnthropicMessagesProvider``) calls ``extract_text_form_tool_calls``
    and stores the resulting ``parse_errors`` in
    ``LlmResponse.metadata["tool_call_parse_errors"]``. Previously
    those errors propagated to ``stream_metadata`` but never reached the
    LLM as feedback — when the model emitted a malformed
    ``<tool_call>{...}</tool_call>`` block (missing ``name``, malformed
    JSON args, etc.) the next turn saw NOTHING (the block was silently
    dropped) and the model often retried the same broken call multiple
    times.

    This helper formats parse errors via the H29.3 fallback feedback
    helpers and appends ONE synthetic user-role ChatMessage with the
    aggregated hint. Only fires when:

      * at least one parse_error is present in the LlmResponse metadata,
        AND
      * we're already adding tool messages (i.e. the assistant emitted
        SOMETHING the runtime is responding to), so a dangling user
        note doesn't interrupt a quiet turn.

    Deduped by ``context.metadata["parse_error_feedback_sent_keys"]`` so
    repeat parse errors across consecutive turns don't loop.
    """
    response = context.llm_response
    if response is None:
        return
    parse_errors = response.metadata.get("tool_call_parse_errors")
    if not isinstance(parse_errors, list) or not parse_errors:
        return
    # Only emit when we're already adding tool messages (i.e. some
    # tool calls DID succeed) — pure-malformed-block turns are rare
    # and the cleanest signal is silence + the natural next-turn
    # recovery; injecting a feedback message into an otherwise-empty
    # tool stage would risk double-emission with other recovery hints.
    if not any(m.role == ChatRole.TOOL for m in messages):
        return

    # Dedup — don't loop on the same parse-error fingerprint turn after turn.
    seen_keys: set[str] = set(
        context.metadata.get("parse_error_feedback_sent_keys") or []
    )
    new_keys: list[str] = []
    new_errors: list[dict[str, Any]] = []
    for err in parse_errors:
        if not isinstance(err, dict):
            continue
        key = "|".join(
            str(err.get(k, "")) for k in ("source", "error", "tool_name", "index")
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        new_keys.append(key)
        new_errors.append(err)
    if not new_errors:
        return

    try:
        from agent_driver.tools.fallback_feedback import (
            build_arguments_parse_feedback,
            build_missing_tool_name_feedback,
        )
    except ImportError:
        return

    lines: list[str] = []
    for err in new_errors[:5]:  # cap so a chatty model can't blow context
        code = str(err.get("error") or "").strip()
        if code == "missing_tool_name":
            lines.append("- " + build_missing_tool_name_feedback())
        elif code in ("arguments_json_parse_failed", "arguments_json_must_be_object"):
            lines.append(
                "- "
                + build_arguments_parse_feedback(
                    str(err.get("tool_name") or "(unknown)"),
                    raw_arguments=err.get("raw_arguments"),
                    error_detail=code,
                )
            )
        elif code == "payload_json_parse_failed":
            raw = err.get("raw_payload")
            snippet = ""
            if isinstance(raw, str) and raw:
                trim = raw.strip()
                if len(trim) > 200:
                    trim = trim[:200] + "…"
                snippet = f" Raw payload seen: `{trim}`."
            lines.append(
                f"- Tool-call block JSON failed to parse.{snippet} "
                'Emit `{"name": "<tool>", "arguments": {...}}` exactly.'
            )
        elif code in ("payload_json_must_be_object", "tool_call_validation_failed"):
            lines.append(
                f"- Tool-call payload was malformed (code: {code}). "
                'Each block must be a JSON object with a "name" string and '
                'an "arguments" object.'
            )
        else:
            # Unknown error code — preserve diagnostic without inventing
            # specific advice.
            lines.append(f"- Tool-call parse error: {code or '(unspecified)'}.")

    if not lines:
        return

    body = (
        "Note: the runtime detected malformed tool-call blocks in your "
        "previous response that were dropped (not executed). Fix and retry:\n"
        + "\n".join(lines)
    )
    messages.append(
        ChatMessage(
            role=ChatRole.USER,
            content=body,
            metadata=scaffolding_metadata("tool_parse_error_feedback"),
        )
    )
    seen_keys_list = list(seen_keys)
    context.metadata["parse_error_feedback_sent_keys"] = seen_keys_list


def _append_disallowed_management_tool_recovery_hint(
    context: RunContext, result: ToolExecutionResult, messages: list[ChatMessage]
) -> None:
    """Append a one-shot repair hint after a disallowed management-tool denial.

    A scoped node restricts ``allowed_tools`` to real executable tools; when the
    model emits an out-of-schema management call (``todo_write`` …) the governed
    executor denies it with ``structured_output.error_kind ==
    'disallowed_management_tool'``. Surface a user-role hint that the tool is
    unavailable *for this run* and list the allowed executable tools, so the
    model retries with them instead of finalizing with "I cannot execute tools".
    """
    blocked: str | None = None
    allowed: list[str] = []
    for envelope in result.envelopes:
        structured = envelope.structured_output
        if not isinstance(structured, dict):
            continue
        if structured.get("error_kind") != "disallowed_management_tool":
            continue
        blocked = envelope.call.tool_name
        raw_allowed = structured.get("allowed_tools")
        if isinstance(raw_allowed, list):
            allowed = [str(name) for name in raw_allowed if str(name)]
        break
    if blocked is None:
        return
    # Dedup so a model that keeps emitting management calls doesn't accrete hints.
    sent = context.metadata.get("disallowed_management_tool_hint_sent")
    if not isinstance(sent, list):
        sent = []
    if blocked in sent:
        return
    sent.append(blocked)
    context.metadata["disallowed_management_tool_hint_sent"] = sent
    allowed_text = ", ".join(allowed) if allowed else "(none configured)"
    messages.append(
        ChatMessage(
            role=ChatRole.USER,
            content=(
                f"The tool '{blocked}' is a planning/management tool and is not "
                "available for this run — this node executes a fixed tool "
                "allowlist. Do not call it again and do not say you lack tools. "
                f"Call one of the allowed executable tools now: {allowed_text}."
            ),
            metadata=scaffolding_metadata("disallowed_management_tool_hint"),
        )
    )


def _append_python_policy_recovery_hint(
    context: RunContext, result: ToolExecutionResult, messages: list[ChatMessage]
) -> None:
    """Nudge model after python import policy rejection (stdlib-only sandbox)."""
    if context.metadata.get("python_policy_hint_sent") is True:
        return
    for envelope in result.envelopes:
        if envelope.call.tool_name != "python":
            continue
        structured = envelope.structured_output
        if not isinstance(structured, dict):
            continue
        if structured.get("error_kind") != "policy":
            continue
        allowed = structured.get("allowed_imports")
        if isinstance(allowed, list) and any(
            name in allowed for name in ("numpy", "scipy", "pandas")
        ):
            return
        allowed_text = (
            ", ".join(str(item) for item in allowed[:12])
            if isinstance(allowed, list) and allowed
            else "see Allowed imports in system policy"
        )
        remediation = structured.get("remediation")
        remediation_text = (
            str(remediation).strip()
            if isinstance(remediation, str) and remediation.strip()
            else f"Use allowed imports only: {allowed_text}"
        )
        messages.append(
            ChatMessage(
                role=ChatRole.USER,
                content=(
                    "Python import was blocked by sandbox policy "
                    "(not because scipy/numpy are missing). Do not import "
                    "numpy, scipy, pandas, or sklearn. "
                    f"{remediation_text}. For gamma/statistics use math and statistics."
                ),
                metadata=scaffolding_metadata("python_import_blocked_hint"),
            )
        )
        context.metadata["python_policy_hint_sent"] = True
        return


def _append_denial_recovery_message(
    context: RunContext, result: ToolExecutionResult, messages: list[ChatMessage]
) -> None:
    """Append one-shot corrective hint after tool_handler_error denials."""
    denied_signature: str | None = None
    denied_tool_name: str | None = None
    denied_message: str | None = None
    denied_code: str | None = None
    for envelope in result.envelopes:
        error = envelope.error
        if error is None:
            continue
        if error.code == "policy_denied" and "deep_research_parent_synthesis_gate" in (
            error.message or ""
        ):
            denied_tool_name = envelope.call.tool_name
            denied_code = error.code
            denied_message = (error.message or "").strip()
            denied_signature = f"{denied_tool_name}:{error.code}:{denied_message}"
            break
        if error.code == "policy_denied" and "deep_research_initial_subagent_gate" in (
            error.message or ""
        ):
            denied_tool_name = envelope.call.tool_name
            denied_code = error.code
            denied_message = (error.message or "").strip()
            denied_signature = f"{denied_tool_name}:{error.code}:{denied_message}"
            break
        if error.code != "tool_handler_error":
            continue
        denied_tool_name = envelope.call.tool_name
        denied_code = error.code
        denied_message = (error.message or "").strip()
        denied_signature = f"{denied_tool_name}:{error.code}:{denied_message}"
        break
    if denied_signature is None:
        return
    reason = denied_message or "tool handler policy denied this call"
    if (
        denied_code == "policy_denied"
        and "deep_research_parent_synthesis_gate" in reason
    ):
        if context.metadata.get("last_denied_signature") == denied_signature:
            return
        get_tool_loop_state(context).set_tool_choice_override(
            {"type": "tool", "name": "file_write"}
        )
        messages.append(
            ChatMessage(
                role=ChatRole.USER,
                content=(
                    f"Deep Research parent synthesis gate denied '{denied_tool_name}'. "
                    "Joined child research notes are already embedded in this "
                    "conversation. Do not call web_search, glob_search, "
                    "grep_search, artifact_list, artifact_read, read_file, "
                    "skill_tool, skill_view, or agent_tool now. Use web_fetch "
                    "only for concrete candidate URLs from the child notes, or call "
                    "file_write to create research/report.md from the embedded notes, "
                    "then call file_write for research/sources.jsonl if source "
                    "ledger facts are available."
                ),
                metadata=scaffolding_metadata("deep_research_parent_synthesis_hint"),
            )
        )
        context.metadata["deep_research_parent_synthesis_recovery"] = {
            "tool": "file_write",
            "reason": "parent_synthesis_gate_denied",
        }
        context.metadata["last_denied_signature"] = denied_signature
        return
    if (
        denied_code == "policy_denied"
        and "deep_research_initial_subagent_gate" in reason
    ):
        denied_counts = context.metadata.get("denied_tool_counts")
        if not isinstance(denied_counts, dict):
            denied_counts = {}
        tool_key = denied_tool_name or "unknown"
        denied_counts[tool_key] = int(denied_counts.get(tool_key, 0)) + 1
        context.metadata["denied_tool_counts"] = denied_counts
        get_tool_loop_state(context).set_tool_choice_override(
            {"type": "tool", "name": "agent_tool"}
        )
        repeat_clause = (
            " This is a repeated denied call; the request tool schema now "
            "contains only agent_tool, so any web_search/web_fetch call will "
            "be ignored as contract drift."
            if denied_counts[tool_key] > 1
            else ""
        )
        messages.append(
            ChatMessage(
                role=ChatRole.USER,
                content=(
                    f"Deep Research initial subagent gate denied '{denied_tool_name}'. "
                    "This medium/hard research run must delegate bounded source "
                    "discovery before direct web search or writing. Call agent_tool "
                    "now with 1-2 focused child research tasks; do not call "
                    "web_search, web_fetch, skill_view, or write tools until at "
                    f"least one child result has joined.{repeat_clause}"
                ),
                metadata=scaffolding_metadata("deep_research_initial_subagent_hint"),
            )
        )
        context.metadata["deep_research_initial_subagent_recovery"] = {
            "tool": "agent_tool",
            "reason": "initial_subagent_gate_denied",
        }
        context.metadata["last_denied_signature"] = denied_signature
        return
    if context.metadata.get("last_denied_signature") == denied_signature:
        return
    denied_counts = context.metadata.get("denied_tool_counts")
    if not isinstance(denied_counts, dict):
        denied_counts = {}
    tool_key = denied_tool_name or "unknown"
    prior_count = int(denied_counts.get(tool_key, 0))
    denied_counts[tool_key] = prior_count + 1
    context.metadata["denied_tool_counts"] = denied_counts
    if denied_counts[tool_key] >= 2:
        get_tool_loop_state(context).force_final_answer(
            reason="repeated_tool_handler_error"
        )
        messages.append(
            ChatMessage(
                role=ChatRole.USER,
                content=(
                    f"Tool '{denied_tool_name}' failed twice with '{denied_code}'. "
                    "Stop calling this tool and answer with what you have, "
                    "or ask one clarification."
                ),
                metadata=scaffolding_metadata("denial_recovery"),
            )
        )
        context.metadata["last_denied_signature"] = denied_signature
        return
    messages.append(
        ChatMessage(
            role=ChatRole.USER,
            content=(
                f"Tool '{denied_tool_name}' was denied: {reason}. "
                "Retry with corrected arguments; do not repeat the same denied call."
            ),
        )
    )
    context.metadata["last_denied_signature"] = denied_signature


def _append_unknown_tool_recovery_message(
    context: RunContext, result: ToolExecutionResult, messages: list[ChatMessage]
) -> None:
    """Add bounded recovery guidance for hallucinated tool names."""
    unknown_names: list[str] = []
    for envelope in result.envelopes:
        error = envelope.error
        if error is None or error.code != "tool_not_registered":
            continue
        unknown_names.append(envelope.call.tool_name)
    if not unknown_names:
        return
    counts = context.metadata.get("unknown_tool_counts")
    if not isinstance(counts, dict):
        counts = {}
    repeated: list[str] = []
    for name in unknown_names:
        prior = int(counts.get(name, 0))
        counts[name] = prior + 1
        if counts[name] >= 2:
            repeated.append(name)
    context.metadata["unknown_tool_counts"] = counts
    if repeated:
        get_tool_loop_state(context).force_final_answer(reason="repeated_unknown_tool")
        names = ", ".join(sorted(set(repeated)))
        messages.append(
            ChatMessage(
                role=ChatRole.USER,
                content=(
                    f"Unknown tool(s) repeated: {names}. Stop trying those names. "
                    "Use only the registered tools already listed in the tool error, "
                    "or answer with a clear partial result."
                ),
                metadata=scaffolding_metadata("unknown_tool_recovery"),
            )
        )
        return
    names = ", ".join(sorted(set(unknown_names)))
    messages.append(
        ChatMessage(
            role=ChatRole.USER,
            content=(
                f"Tool name correction needed for: {names}. Do not invent tool "
                "names. Use the registered tool names shown in the previous tool "
                "error and retry only if a real tool is needed."
            ),
            metadata=scaffolding_metadata("unknown_tool_correction"),
        )
    )


__all__ = [
    "_append_tool_call_parse_error_feedback",
    "_append_disallowed_management_tool_recovery_hint",
    "_append_python_policy_recovery_hint",
    "_append_denial_recovery_message",
    "_append_unknown_tool_recovery_message",
]
