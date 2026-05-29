"""Structured prompts for full no-tool LLM compaction."""

from __future__ import annotations


def build_full_compaction_prompt(*, history_excerpt: str, user_request: str) -> str:
    """Build structured prompt with private draft and persisted summary sections."""
    return (
        "You are a context compactor. Produce two top-level XML blocks:\n"
        "<private_draft>...</private_draft>\n"
        "<persisted_summary>{json}</persisted_summary>\n\n"
        "Persisted summary JSON must include keys:\n"
        "request_intent, key_concepts, files_code, errors_fixes, problems, "
        "user_messages, pending_tasks, current_work, next_step.\n\n"
        "History excerpt:\n"
        f"{history_excerpt}\n\n"
        "Current user request:\n"
        f"{user_request}\n"
    )


def strip_private_draft(raw_text: str) -> tuple[str, str | None]:
    """Strip private draft section from provider output."""
    start = raw_text.find("<private_draft>")
    end = raw_text.find("</private_draft>")
    if start == -1 or end == -1 or end < start:
        return raw_text, None
    draft = raw_text[start : end + len("</private_draft>")]
    clean = raw_text.replace(draft, "").strip()
    return clean, draft


__all__ = ["build_full_compaction_prompt", "strip_private_draft"]
