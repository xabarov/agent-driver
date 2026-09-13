"""Per-call content-free transport observations; never a provider compute claim."""

from __future__ import annotations

import re
from time import monotonic
from typing import Any


def mark_transport(diagnostics: dict[str, Any], key: str) -> None:
    diagnostics.setdefault(
        key, round((monotonic() - diagnostics["started_at_monotonic"]) * 1000, 3)
    )


def transport_snapshot(diagnostics: dict[str, Any]) -> dict[str, Any]:
    snapshot = {
        key: diagnostics[key]
        for key in (
            "attempts",
            "first_data_ms",
            "first_visible_ms",
            "first_reasoning_ms",
            "finished_ms",
            "data_chunks",
            "streamed",
        )
        if key in diagnostics
    }
    generation = diagnostics.get("generation_id")
    if isinstance(generation, str) and re.fullmatch(
        r"[A-Za-z0-9_-]{1,200}", generation
    ):
        snapshot["generation_id"] = generation
    return snapshot
