"""Optional pluggable long-term, cross-session memory layer."""

from agent_driver.memory.provider import (
    MemoryKind,
    MemoryProvider,
    MemoryRecord,
    MemoryStore,
    MemoryTurn,
    RecallQuery,
    RecallResult,
    StoreBackedMemoryProvider,
    apply_recall,
    match_query,
    render_recall_block,
)
from agent_driver.memory.stores import InMemoryMemoryStore, SqliteMemoryStore

__all__ = [
    "InMemoryMemoryStore",
    "MemoryKind",
    "MemoryProvider",
    "MemoryRecord",
    "MemoryStore",
    "MemoryTurn",
    "RecallQuery",
    "RecallResult",
    "SqliteMemoryStore",
    "StoreBackedMemoryProvider",
    "apply_recall",
    "match_query",
    "render_recall_block",
]
