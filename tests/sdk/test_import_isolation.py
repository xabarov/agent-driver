"""SDK import isolation checks."""

from __future__ import annotations

import importlib
import sys


def test_sdk_import_does_not_load_cli_or_demo_modules() -> None:
    """Importing agent_driver.sdk should not pull app-specific packages."""
    for name in list(sys.modules):
        if name.startswith(("agent_driver.cli", "examples.chat-demo")):
            sys.modules.pop(name, None)

    sdk = importlib.import_module("agent_driver.sdk")

    assert sdk.Agent is not None
    assert sdk.Session is not None
    assert not any(name.startswith("agent_driver.cli") for name in sys.modules)
    assert not any(name.startswith("examples.chat-demo") for name in sys.modules)
