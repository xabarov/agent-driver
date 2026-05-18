"""Compaction orchestration constants and helpers (Phase 8)."""

from agent_driver.context.compaction.eligibility import decide_compaction
from agent_driver.context.compaction.llm_full import run_full_llm_compaction
from agent_driver.context.compaction.orchestrator import (
    CompactionOrchestrator,
    SessionMemoryCompactionOutput,
    SessionMemoryFreshness,
    build_session_memory_compaction,
    evaluate_session_memory_freshness,
)
from agent_driver.context.compaction.prompts import (
    build_full_compaction_prompt,
    strip_private_draft,
)
from agent_driver.context.compaction.retry import ptl_retry_drop_oldest_groups
from agent_driver.context.compaction.sanitizers import sanitize_compaction_text
from agent_driver.context.compaction.session_memory_store import (
    load_session_memory,
    save_session_memory,
    session_memory_artifact_id,
)

COMPACTION_DECISION_KEY = "compaction_decision"
COMPACTION_AUDIT_KEY = "compaction_audit"
COMPACTION_RESULT_KEY = "compaction_result"
COMPACTION_FAILURES_KEY = "compaction_failures"

__all__ = [
    "COMPACTION_AUDIT_KEY",
    "COMPACTION_DECISION_KEY",
    "COMPACTION_FAILURES_KEY",
    "COMPACTION_RESULT_KEY",
    "CompactionOrchestrator",
    "SessionMemoryFreshness",
    "SessionMemoryCompactionOutput",
    "build_full_compaction_prompt",
    "build_session_memory_compaction",
    "decide_compaction",
    "evaluate_session_memory_freshness",
    "load_session_memory",
    "ptl_retry_drop_oldest_groups",
    "run_full_llm_compaction",
    "sanitize_compaction_text",
    "save_session_memory",
    "session_memory_artifact_id",
    "strip_private_draft",
]
