"""Buffer model-authored memory writes from tool results (memory epic M1).

The ``remember`` tool cannot reach the durable store from the tool-execution
stage (the memory package stays free of runtime imports, and the store lives on
the :class:`MemoryLifecycleHook`). Instead the tool returns an
``applied_memory_write`` envelope; this stage buffers it onto
``MemoryRuntimeState``, and the hook flushes buffered writes to the store at run
completion. Mirrors ``apply_planning_updates_from_envelopes`` for the planning
tools.
"""

from __future__ import annotations

from agent_driver.runtime.metadata_state import get_memory_runtime_state
from agent_driver.runtime.single_agent.types import RunContext
from agent_driver.runtime.tools import ToolExecutionResult

MEMORY_WRITE_TOOL_NAMES = frozenset({"remember"})


def apply_memory_writes_from_envelopes(
    context: RunContext, result: ToolExecutionResult
) -> int:
    """Buffer ``remember`` writes onto MemoryRuntimeState; return count buffered."""
    memory_state = get_memory_runtime_state(context)
    buffered = 0
    for envelope in result.envelopes:
        if envelope.call.tool_name not in MEMORY_WRITE_TOOL_NAMES:
            continue
        structured = envelope.structured_output
        if not isinstance(structured, dict):
            continue
        write = structured.get("applied_memory_write")
        if not isinstance(write, dict):
            continue
        text = str(write.get("text") or "").strip()
        if not text:
            continue
        slot_raw = write.get("slot")
        slot = (
            slot_raw.strip()
            if isinstance(slot_raw, str) and slot_raw.strip()
            else None
        )
        memory_state.add_pending_write({"text": text, "slot": slot})
        buffered += 1
    if buffered:
        context.metadata["memory_explicit_write_count"] = (
            int(context.metadata.get("memory_explicit_write_count", 0)) + buffered
        )
    return buffered


__all__ = ["apply_memory_writes_from_envelopes", "MEMORY_WRITE_TOOL_NAMES"]
