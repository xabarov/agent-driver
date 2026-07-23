"""Epic 025: status-protocol registry lock.

Every warning ``signal_id`` emitted anywhere in the engine must be documented
in docs/status-protocol.md — hosts map signals to user-facing labels, and an
unregistered signal is a silent stage / frozen label waiting to happen.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = ROOT / "docs" / "status-protocol.md"
PACKAGE_ROOT = ROOT / "agent_driver"

_SIGNAL_RE = re.compile(r"""signal_id["'\s:=]+["']([a-z0-9_]+)["']""")


def _emitted_signal_ids() -> set[str]:
    found: set[str] = set()
    for path in PACKAGE_ROOT.rglob("*.py"):
        found.update(_SIGNAL_RE.findall(path.read_text(encoding="utf-8")))
    return found


def test_all_signal_ids_are_registered() -> None:
    protocol = PROTOCOL_PATH.read_text(encoding="utf-8")
    missing = sorted(
        signal for signal in _emitted_signal_ids() if f"`{signal}`" not in protocol
    )
    assert missing == [], (
        "These warning signal_ids are emitted but not documented in "
        f"docs/status-protocol.md: {missing}. Add each to the registry table "
        "with its class (transient/durable) and a UI recommendation."
    )


def test_registry_has_no_ghost_signals() -> None:
    protocol = PROTOCOL_PATH.read_text(encoding="utf-8")
    registry_section = protocol.split("## Реестр warning", 1)[1].split("## Политика", 1)[0]
    declared = set(re.findall(r"^\| `([a-z0-9_]+)` \|", registry_section, flags=re.M))
    ghosts = sorted(declared - _emitted_signal_ids())
    assert ghosts == [], f"Registered but never emitted: {ghosts}"
