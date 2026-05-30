"""Tests for reusable transcript/run mapping helpers."""

from __future__ import annotations

from agent_driver.context import (
    filter_client_requests_for_runs,
    record_mapping_dict,
    transcript_to_messages,
    truncate_transcript_for_retry,
    turn_text_for_run,
)


class _Record:
    metadata_by_run = (("run_1", {"tokens": 10}),)
    client_requests = (("req_1", {"run_id": "run_1"}),)


def test_transcript_to_messages_skips_empty_and_maps_roles() -> None:
    messages = transcript_to_messages(
        [
            ("system", "rules"),
            ("user", "hello"),
            ("assistant", "hi"),
            ("assistant", " "),
        ]
    )

    assert [message.role.value for message in messages] == [
        "system",
        "user",
        "assistant",
    ]


def test_truncate_transcript_for_retry_drops_retried_turn_and_after() -> None:
    transcript, run_ids = truncate_transcript_for_retry(
        transcript=[
            ("user", "one"),
            ("assistant", "answer one"),
            ("user", "two"),
            ("assistant", "answer two"),
        ],
        run_ids=["run_1", "run_2"],
        retry_from_run_id="run_2",
    )

    assert transcript == [("user", "one"), ("assistant", "answer one")]
    assert run_ids == ["run_1"]


def test_turn_text_for_run_returns_adjacent_assistant() -> None:
    user_text, assistant_text = turn_text_for_run(
        transcript=[
            ("user", "one"),
            ("assistant", "answer one"),
            ("user", "two"),
            ("assistant", "answer two"),
        ],
        run_ids=["run_1", "run_2"],
        run_id="run_2",
    )

    assert user_text == "two"
    assert assistant_text == "answer two"


def test_record_mapping_and_client_request_filter() -> None:
    assert record_mapping_dict(_Record(), "metadata_by_run") == {
        "run_1": {"tokens": 10}
    }
    assert filter_client_requests_for_runs(
        {
            "req_1": {"run_id": "run_1"},
            "req_2": {"run_id": "run_deleted"},
            "req_3": {"other": "shape"},
        },
        ["run_1"],
    ) == {"req_1": {"run_id": "run_1"}}
