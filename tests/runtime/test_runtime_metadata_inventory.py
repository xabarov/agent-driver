"""Guard runtime metadata key inventory discipline."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = ROOT / "agent_driver" / "runtime"
INVENTORY_PATH = ROOT / "docs" / "runtime-metadata.md"

CONTEXT_METADATA_KEY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r'context\.metadata\["([^"]+)"\]'),
    re.compile(r'context\.metadata\.get\("([^"]+)"'),
    re.compile(r'context\.metadata\.pop\("([^"]+)"'),
    re.compile(r'context\.metadata\.setdefault\("([^"]+)"'),
)


def _runtime_context_metadata_keys() -> set[str]:
    keys: set[str] = set()
    for path in RUNTIME_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pattern in CONTEXT_METADATA_KEY_PATTERNS:
            keys.update(pattern.findall(text))
    return keys


def test_runtime_context_metadata_keys_are_in_inventory() -> None:
    """New literal context.metadata keys must update docs/runtime-metadata.md."""
    inventory = INVENTORY_PATH.read_text(encoding="utf-8")
    missing = sorted(key for key in _runtime_context_metadata_keys() if key not in inventory)

    assert missing == []
