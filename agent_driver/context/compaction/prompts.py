"""Structured prompts for full no-tool LLM compaction."""

from __future__ import annotations


def build_full_compaction_prompt(
    *, history_excerpt: str, user_request: str, prior_summary: str | None = None
) -> str:
    """Build structured prompt with private draft and persisted summary sections.

    Option B2 rolling mode: when ``prior_summary`` is given, ``history_excerpt`` is only
    the NEW slice since that summary was produced; the model folds the slice into the
    prior summary instead of re-summarising the whole history (amortization)."""
    if prior_summary:
        # opencode-adoption EPIC-05: the carry-forward-or-lose rolling contract
        # (ported from opencode's SUMMARY_UPDATE_INSTRUCTIONS). The prior summary is
        # about to be discarded, so the model must actively re-carry standing context
        # rather than assume it survives; the newer slice wins on conflict.
        history_section = (
            "Prior persisted summary (it already covers the earlier history and is "
            "DISCARDED after this step — anything you do not carry into the new summary "
            "is lost). Carry forward its objectives, constraints, user directives, "
            "decisions, and parallel workstreams even when the new slice does not "
            "mention them; drop only what is finished and no longer needed:\n"
            f"{prior_summary}\n\n"
            "New history slice to fold into the summary above. It is MORE RECENT than "
            "the prior summary: where they conflict, the new slice wins — state the "
            "corrected fact and drop the stale claim. Move now-finished items from "
            "current_work into completed_work; when a blocker has cleared, update it "
            "while keeping any detail still needed to continue; refresh request_intent "
            "and next_step to reflect the current state:\n"
            f"{history_excerpt}\n\n"
        )
    else:
        history_section = f"History excerpt:\n{history_excerpt}\n\n"
    return (
        "You are a context compactor. Produce two top-level XML blocks:\n"
        "<private_draft>...</private_draft>\n"
        "<persisted_summary>{json}</persisted_summary>\n\n"
        "Persisted summary JSON must include keys:\n"
        "request_intent, key_concepts, files_code, errors_fixes, problems, "
        "user_messages, completed_work, current_work, pending_tasks, next_step.\n"
        # EPIC-05: explicit work-state buckets (opencode's Completed/Active/Blocked +
        # Next Move). ``completed_work`` is the new bucket; keep every key even when
        # empty so the structure is stable across rolling folds.
        "Bucket the work state explicitly: completed_work = finished/verified work and "
        "changes already made; current_work = what is in progress or under "
        "investigation right now; problems = blockers, failing commands, and unknowns; "
        "pending_tasks + next_step = the concrete moves that remain. Keep every key "
        'even when it is empty (use an empty list or "").\n'
        # EPIC-05: verbatim-preservation rule (opencode) — resume correctness depends
        # on exact identifiers surviving the compaction.
        "Preserve exact file paths, symbols, function names, commands, error strings, "
        "URLs, and identifiers verbatim — never paraphrase, abbreviate, or elide "
        "them.\n"
        # Epic 039 / hermes language-preservation: a summary that silently
        # switches to English breaks a RU conversation on resume.
        "Write all summary VALUES in the same language the user was using in "
        "the conversation - do not translate them to English. Quote the "
        "user's request_intent and user_messages verbatim.\n\n"
        + history_section
        + "Current user request:\n"
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
