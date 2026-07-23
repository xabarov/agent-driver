"""Epic 024: terminal-phase contract inventory lock.

Every engine class overriding ``on_finalize`` / ``on_run_completed`` must be
declared (with its class 1/2 assignment) in docs/terminal-phase-contract.md —
adding post-final work silently is exactly how the 22-139s tails happened.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "docs" / "terminal-phase-contract.md"
PACKAGE_ROOT = ROOT / "agent_driver"

# The protocol + no-op base definitions themselves, not implementations.
_DEFINITION_MODULE = PACKAGE_ROOT / "runtime" / "lifecycle_hooks.py"
_TERMINAL_METHODS = {"on_finalize", "on_run_completed"}


def _terminal_hook_classes() -> set[str]:
    found: set[str] = set()
    for path in PACKAGE_ROOT.rglob("*.py"):
        if path == _DEFINITION_MODULE:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - non-source artifacts
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for item in node.body:
                if (
                    isinstance(item, (ast.AsyncFunctionDef, ast.FunctionDef))
                    and item.name in _TERMINAL_METHODS
                ):
                    found.add(node.name)
    return found


def test_terminal_hooks_are_declared_in_contract() -> None:
    """New on_finalize/on_run_completed implementations must update the contract."""
    contract = CONTRACT_PATH.read_text(encoding="utf-8")
    missing = sorted(
        name for name in _terminal_hook_classes() if f"`{name}`" not in contract
    )
    assert missing == [], (
        "These classes implement terminal-phase hooks but are not declared in "
        f"docs/terminal-phase-contract.md: {missing}. Declare each with its "
        "class (1 = blocking-by-semantics under finalize_hook_timeout, "
        "2 = background/scheduled)."
    )


def test_contract_inventory_has_no_ghosts() -> None:
    """Contract inventory must not list classes that no longer implement hooks."""
    contract = CONTRACT_PATH.read_text(encoding="utf-8")
    inventory_section = contract.split("## Инвентарь", 1)[1]
    declared = {
        line.split("`")[1]
        for line in inventory_section.splitlines()
        if line.strip().startswith("- `")
    }
    actual = _terminal_hook_classes()
    ghosts = sorted(declared - actual)
    assert ghosts == [], f"Declared in contract but no longer implement hooks: {ghosts}"
