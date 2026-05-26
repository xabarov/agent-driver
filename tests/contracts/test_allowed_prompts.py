"""Phase 11 H13 — tests for prompt-based permission contracts.

Pins:
* AllowedPrompt round-trips through Pydantic with regex validation;
* matching is cautious-by-default — all arg_patterns must match;
* non-string args coerce to JSON for regex check;
* first-match semantic in ``find_matching_prompt``;
* InterruptRequest.proposed_prompts + ResumeCommand.approved_prompts
  round-trip cleanly.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_driver.contracts.enums import InterruptReason, ResumeAction, ToolRisk
from agent_driver.contracts.interrupts import (
    AllowedPrompt,
    AllowedPromptPattern,
    InterruptRequest,
    ResumeCommand,
    find_matching_prompt,
    matches_allowed_prompt,
)


def _prompt(**overrides) -> AllowedPrompt:
    base = dict(
        category_id="run_tests",
        description="Run npm test variants",
        tool_name="shell.command",
        arg_patterns=[AllowedPromptPattern(arg_name="command", regex=r"^npm test")],
    )
    base.update(overrides)
    return AllowedPrompt(**base)


def test_allowed_prompt_regex_validation_rejects_bad_pattern():
    with pytest.raises(ValidationError):
        AllowedPromptPattern(arg_name="command", regex="(unclosed[")


def test_allowed_prompt_category_id_strips_whitespace():
    p = AllowedPrompt(
        category_id="  run_tests  ",
        description="d",
        tool_name="shell.command",
    )
    assert p.category_id == "run_tests"


def test_allowed_prompt_category_id_rejects_whitespace_inside():
    with pytest.raises(ValidationError):
        AllowedPrompt(
            category_id="run tests",
            description="d",
            tool_name="shell.command",
        )


def test_matches_when_tool_and_all_patterns_match():
    prompt = _prompt()
    assert matches_allowed_prompt(
        tool_name="shell.command",
        args={"command": "npm test"},
        allowed=prompt,
    )
    assert matches_allowed_prompt(
        tool_name="shell.command",
        args={"command": "npm test -- --watch=false"},
        allowed=prompt,
    )


def test_no_match_when_tool_name_differs():
    prompt = _prompt()
    assert not matches_allowed_prompt(
        tool_name="shell.execute",
        args={"command": "npm test"},
        allowed=prompt,
    )


def test_no_match_when_pattern_misses():
    prompt = _prompt()
    assert not matches_allowed_prompt(
        tool_name="shell.command",
        args={"command": "rm -rf /"},
        allowed=prompt,
    )


def test_no_match_when_required_arg_absent():
    """Cautious: missing arg = no match."""
    prompt = _prompt()
    assert not matches_allowed_prompt(
        tool_name="shell.command",
        args={"shell": "/bin/zsh"},  # 'command' key missing
        allowed=prompt,
    )


def test_empty_arg_patterns_blanket_matches_any_args():
    """Use carefully: empty patterns = trust the whole tool."""
    prompt = AllowedPrompt(
        category_id="read_only_fs",
        description="any file_read",
        tool_name="file_read",
        arg_patterns=[],
    )
    assert matches_allowed_prompt(
        tool_name="file_read",
        args={"path": "/etc/passwd"},
        allowed=prompt,
    )
    assert matches_allowed_prompt(
        tool_name="file_read",
        args={},
        allowed=prompt,
    )


def test_non_string_arg_coerces_to_json_for_regex():
    """Arg of type list/int/dict serializes to JSON for matching."""
    prompt = AllowedPrompt(
        category_id="bulk_paths",
        description="multiple paths",
        tool_name="file_read",
        arg_patterns=[AllowedPromptPattern(arg_name="paths", regex=r"\.json")],
    )
    assert matches_allowed_prompt(
        tool_name="file_read",
        args={"paths": ["a.json", "b.json"]},
        allowed=prompt,
    )
    assert not matches_allowed_prompt(
        tool_name="file_read",
        args={"paths": ["a.txt", "b.txt"]},
        allowed=prompt,
    )


def test_find_matching_prompt_returns_first_hit():
    approved = [
        _prompt(category_id="npm_tests"),
        AllowedPrompt(
            category_id="blanket_shell",
            description="any shell",
            tool_name="shell.command",
            arg_patterns=[],
        ),
    ]
    hit = find_matching_prompt(
        tool_name="shell.command",
        args={"command": "npm test"},
        approved=approved,
    )
    assert hit is not None
    assert hit.category_id == "npm_tests"  # first wins


def test_find_matching_prompt_returns_none_when_no_hit():
    approved = [_prompt()]
    hit = find_matching_prompt(
        tool_name="shell.command",
        args={"command": "git log"},
        approved=approved,
    )
    assert hit is None


def test_resume_command_round_trips_approved_prompts():
    cmd = ResumeCommand(
        interrupt_id="int-1",
        action=ResumeAction.APPROVE,
        approved_prompts=[_prompt()],
    )
    raw = cmd.model_dump()
    assert raw["approved_prompts"][0]["category_id"] == "run_tests"
    restored = ResumeCommand.model_validate(raw)
    assert len(restored.approved_prompts) == 1
    assert restored.approved_prompts[0].tool_name == "shell.command"


def test_interrupt_request_round_trips_proposed_prompts():
    req = InterruptRequest(
        interrupt_id="int-1",
        run_id="run-1",
        attempt_id="att-1",
        checkpoint_id="ck-1",
        reason=InterruptReason.APPROVAL_REQUIRED,
        title="t",
        description="d",
        risk=ToolRisk.MEDIUM,
        allowed_actions=[ResumeAction.APPROVE, ResumeAction.REJECT],
        proposed_prompts=[_prompt()],
    )
    raw = req.model_dump()
    assert len(raw["proposed_prompts"]) == 1
    restored = InterruptRequest.model_validate(raw)
    assert restored.proposed_prompts[0].category_id == "run_tests"


def test_resume_command_default_approved_prompts_is_empty():
    """Backwards-compat: existing hosts that don't set approved_prompts
    still produce valid ResumeCommands."""
    cmd = ResumeCommand(interrupt_id="int-1", action=ResumeAction.REJECT)
    assert cmd.approved_prompts == []
