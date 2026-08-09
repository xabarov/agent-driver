"""Optional pluggable long-term, cross-session memory layer."""

from agent_driver.memory.extraction import (
    FactExtractingMemoryProvider,
    parse_extracted_facts,
    supersede_by_slot,
)
from agent_driver.memory.factory import build_memory_provider
from agent_driver.memory.semantic import (
    EmbeddingMemoryProvider,
    MemoryEmbedder,
    cosine_similarity,
)
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
    "EmbeddingMemoryProvider",
    "FactExtractingMemoryProvider",
    "InMemoryMemoryStore",
    "MemoryEmbedder",
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
    "build_memory_provider",
    "cosine_similarity",
    "match_query",
    "parse_extracted_facts",
    "render_recall_block",
    "supersede_by_slot",
]
