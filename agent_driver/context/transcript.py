"""Reusable helpers for simple role/text transcripts and run mapping."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from agent_driver.contracts import ChatMessage
from agent_driver.contracts.enums import ChatRole

Transcript = list[tuple[str, str]]


def transcript_to_messages(transcript: Iterable[tuple[str, str]]) -> list[ChatMessage]:
    """Convert a role/text transcript into typed chat messages."""
    messages: list[ChatMessage] = []
    for role, content in transcript:
        if not content.strip():
            continue
        if role == "user":
            messages.append(ChatMessage(role=ChatRole.USER, content=content))
        elif role == "assistant":
            messages.append(ChatMessage(role=ChatRole.ASSISTANT, content=content))
        elif role == "system":
            messages.append(ChatMessage(role=ChatRole.SYSTEM, content=content))
    return messages


def truncate_transcript_for_retry(
    *,
    transcript: Transcript,
    run_ids: list[str],
    retry_from_run_id: str | None,
) -> tuple[Transcript, list[str]]:
    """Drop the retried run and everything after it from persisted chat history."""
    if not retry_from_run_id:
        return transcript, run_ids
    try:
        run_index = run_ids.index(retry_from_run_id)
    except ValueError:
        return transcript, run_ids

    user_seen = 0
    cut_index = len(transcript)
    for index, (role, _content) in enumerate(transcript):
        if role != "user":
            continue
        if user_seen == run_index:
            cut_index = index
            break
        user_seen += 1
    return transcript[:cut_index], run_ids[:run_index]


def record_mapping_dict(
    record: object | None, field_name: str
) -> dict[str, dict[str, Any]]:
    """Return a stable dict copy for tuple-based SessionRecord mappings."""
    rows = getattr(record, field_name, ()) if record is not None else ()
    return {str(key): dict(value) for key, value in rows}


def filter_client_requests_for_runs(
    client_requests: dict[str, dict[str, object]],
    run_ids: list[str],
) -> dict[str, dict[str, object]]:
    """Keep idempotency request records whose run still exists in history."""
    allowed = set(run_ids)
    return {
        key: value
        for key, value in client_requests.items()
        if isinstance(value.get("run_id"), str) and value["run_id"] in allowed
    }


def turn_text_for_run(
    *,
    transcript: Iterable[tuple[str, str]],
    run_ids: Iterable[str],
    run_id: str,
) -> tuple[str | None, str | None]:
    """Return the user prompt and adjacent assistant text for one run id."""
    run_list = list(run_ids)
    rows = list(transcript)
    try:
        run_index = run_list.index(run_id)
    except ValueError:
        return None, None
    user_seen = -1
    for index, (role, content) in enumerate(rows):
        if role != "user":
            continue
        user_seen += 1
        if user_seen != run_index:
            continue
        assistant_text = None
        if index + 1 < len(rows) and rows[index + 1][0] == "assistant":
            assistant_text = str(rows[index + 1][1])
        return str(content), assistant_text
    return None, None


__all__ = [
    "Transcript",
    "filter_client_requests_for_runs",
    "record_mapping_dict",
    "transcript_to_messages",
    "truncate_transcript_for_retry",
    "turn_text_for_run",
]
