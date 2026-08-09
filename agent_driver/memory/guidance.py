"""Canonical long-term memory write-gating discipline (memory epic M2).

A single source of truth for "what NOT to keep in memory", shared by every write
path — the model-callable ``remember`` tool and the automatic fact extractor — so
the discipline can't drift between them (the guidance-triplication smell seen in
downstream consumers). All three reference harnesses (openclaude, hermes,
openhands-sdk) converge on the same exclusions, and openclaude's key insight is
the last clause: the exclusions hold *even when the user explicitly asks to save*
— you keep the durable intent behind the request, not the transient detail.
"""

from __future__ import annotations

MEMORY_WRITE_GATING = (
    "Never keep in long-term memory: secrets or credentials (API keys, tokens, "
    "passwords, gate/access codes); ephemeral task state or progress (what is "
    "being done right now, step logs, 'currently working on…'); or facts "
    "trivially re-derivable from the code, files, or the current conversation "
    "(function names, file layout, git history). These exclusions hold EVEN when "
    "the user explicitly says 'remember this' — keep the durable preference or "
    "decision behind such a request, not the transient detail."
)

__all__ = ["MEMORY_WRITE_GATING"]
