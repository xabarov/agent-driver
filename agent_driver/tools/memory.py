"""Model-callable long-term memory write tool (memory epic M1).

Gives the agent *agency* over what to persist: instead of relying solely on the
end-of-run extractor, the model can decide in-the-moment that a fact is worth
keeping and call ``remember``. The call does not touch the store directly — it
returns an ``applied_memory_write`` envelope that the tool stage buffers into
``MemoryRuntimeState``; the :class:`MemoryLifecycleHook` flushes buffered writes
to the durable store at run completion and, when the model wrote memory itself
this turn, skips the automatic extractor (openclaude-style mutual exclusion) so
memory is never double-written and the extraction LLM call is saved.

The tool is registered only when a memory provider is configured (see
``sdk/factory.build_default_registry``); without a store to flush to it would be
a silent no-op.
"""

from __future__ import annotations

from typing import Any

from agent_driver.contracts.enums import (
    ApprovalMode,
    SideEffectClass,
    ToolRisk,
)
from agent_driver.contracts.tools import ToolManifest
from agent_driver.memory.guidance import MEMORY_WRITE_GATING
from agent_driver.tools.registry import ToolRegistry

_REMEMBER_DESCRIPTION = (
    "Save a durable fact to long-term memory that persists across future "
    "sessions. Use PROACTIVELY the moment the user states a lasting preference, "
    "a correction of your behavior, a standing decision, a stable identity/"
    "environment fact, or explicitly says 'remember this'. Prefer this over "
    "waiting for automatic extraction. Write ONE self-contained fact per call, "
    "phrased so it is useful with no surrounding context. To update a fact you "
    "saved before, pass the same 'slot' — the newer write supersedes the older. "
    # Epic M2: the shared write-gating discipline (same source of truth as the
    # automatic fact extractor).
    + MEMORY_WRITE_GATING
)

_REMEMBER_MAX_CHARS = 2000


async def _remember_tool(args: dict[str, Any]) -> dict[str, Any]:
    content = str(args.get("content") or "").strip()
    if not content:
        raise ValueError("content is required")
    if len(content) > _REMEMBER_MAX_CHARS:
        raise ValueError(f"content must be <= {_REMEMBER_MAX_CHARS} characters")
    slot_raw = args.get("slot")
    slot = (
        str(slot_raw).strip()
        if isinstance(slot_raw, str) and slot_raw.strip()
        else None
    )
    summary = "Saved to long-term memory" + (f" (slot: {slot})." if slot else ".")
    return {
        # Terminal success response (hermes): a clear done-signal so the model
        # does not re-issue the same write in a loop.
        "summary": summary,
        "next_action": (
            "This fact will persist for future sessions. Do not repeat this "
            "call; continue with the task."
        ),
        # Consumed by apply_memory_writes_from_envelopes → MemoryRuntimeState.
        "applied_memory_write": {"text": content, "slot": slot},
        "structured": {"remembered": True, "slot": slot},
    }


def register_memory_tool(registry: ToolRegistry) -> None:
    """Register the model-callable ``remember`` tool when absent."""
    if registry.get("remember") is not None:
        return
    registry.register(
        ToolManifest(
            name="remember",
            description=_REMEMBER_DESCRIPTION,
            risk=ToolRisk.LOW,
            side_effect=SideEffectClass.NONE,
            approval_mode=ApprovalMode.NEVER,
            remediation_hints=[
                "One self-contained fact per call.",
                "Reuse the same 'slot' to update a fact you saved earlier.",
                "Do not save secrets, ephemeral task state, or re-derivable facts.",
            ],
            args_schema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": (
                            "The single durable fact to remember, phrased to be "
                            "useful with no surrounding context."
                        ),
                    },
                    "slot": {
                        "type": "string",
                        "description": (
                            "Optional short stable key naming the subject (e.g. "
                            "'output-format', 'user-timezone'); reusing it "
                            "supersedes the earlier fact for that subject."
                        ),
                    },
                },
                "required": ["content"],
                "additionalProperties": False,
            },
            output_type="json",
        ),
        _remember_tool,
    )


__all__ = ["register_memory_tool"]
