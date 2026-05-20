"""Tests for chat TUI status spinner."""

from __future__ import annotations

import asyncio

import pytest

from agent_driver.cli.tui.spinner import StatusSpinner


@pytest.mark.asyncio
async def test_status_spinner_updates_rich_status() -> None:
    """Spinner should drive rich status start/update/stop lifecycle."""

    class _FakeStatus:
        def __init__(self) -> None:
            self.started = False
            self.stopped = False
            self.updates: list[str] = []

        def start(self) -> None:
            self.started = True

        def update(self, value: str) -> None:
            self.updates.append(value)

        def stop(self) -> None:
            self.stopped = True

    class _FakeConsole:
        def __init__(self) -> None:
            self.status_obj = _FakeStatus()

        def status(self, _label: str, spinner: str = "dots") -> _FakeStatus:
            assert spinner == "dots"
            return self.status_obj

    console = _FakeConsole()
    spinner = StatusSpinner(output=lambda _text: None, enabled=True, console=console)
    spinner.set_label("Calling web_search...")
    spinner.start()
    await asyncio.sleep(0.05)
    await spinner.stop()

    assert console.status_obj.started is True
    assert console.status_obj.stopped is True
    assert any("Calling web_search..." in line for line in console.status_obj.updates)
