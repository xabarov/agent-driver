from __future__ import annotations

from app.api.chat import _run_failed_message


def test_run_failed_message_explains_provider_402() -> None:
    assert _run_failed_message(
        [
            {
                "event": "run_failed",
                "data": {
                    "reason": "model_error",
                    "status_code": 402,
                    "message": "Insufficient credits for this model.",
                },
            }
        ]
    ) == (
        "**Run failed**\n\nProvider rejected the request with HTTP 402. "
        "Insufficient credits for this model."
    )
