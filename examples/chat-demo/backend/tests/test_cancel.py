from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_cancel_run_marks_cancelled(client) -> None:
    response = await client.post("/api/chat/runs/run_test_cancel/cancel")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["run_id"] == "run_test_cancel"
    assert payload["cancelled"] is True
