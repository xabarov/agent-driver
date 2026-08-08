"""Skills S2 — skill_view protocol payload keeps a reload hint, not a generic marker."""

from __future__ import annotations

from agent_driver.runtime.single_agent.tool_stage.protocol_messages import (
    _compact_tool_payload_for_protocol,
)


def _skill_payload(content: str, **extra) -> dict:
    base = {
        "summary": "Loaded skill_body for skill 'myskill'",
        "content": content,
        "skill_dir": "/base/myskill",
        "skill_invocation": {"name": "myskill", "path": "/base/myskill/SKILL.md"},
    }
    base.update(extra)
    return base


def test_long_skill_body_gets_reload_hint() -> None:
    out = _compact_tool_payload_for_protocol("skill_view", _skill_payload("STEP " * 100))
    content = out["content"]
    # Prefix retained, generic "use summary/artifacts" marker replaced by a reload hint.
    assert content.startswith("STEP STEP")
    assert "use summary/artifacts" not in content
    assert "call skill_view(" in content
    assert "name='myskill'" in content
    assert "base_dir='/base/myskill'" in content
    assert out["content_omitted_chars"] == len("STEP " * 100) - 240


def test_short_skill_body_is_untouched() -> None:
    out = _compact_tool_payload_for_protocol("skill_view", _skill_payload("tiny body"))
    assert out["content"] == "tiny body"
    assert "content_omitted_chars" not in out


def test_skill_invocation_record_is_preserved() -> None:
    out = _compact_tool_payload_for_protocol("skill_view", _skill_payload("X" * 2000))
    assert out["skill_invocation"] == {
        "name": "myskill",
        "path": "/base/myskill/SKILL.md",
    }


def test_reload_hint_includes_relative_file_when_present() -> None:
    out = _compact_tool_payload_for_protocol(
        "skill_view", _skill_payload("Y" * 2000, relative_file="references/api.md")
    )
    assert "relative_file='references/api.md'" in out["content"]


def test_reload_hint_degrades_gracefully_without_name_or_dir() -> None:
    payload = {"summary": "s", "content": "Z" * 2000}
    out = _compact_tool_payload_for_protocol("skill_view", payload)
    # Still emits a hint (empty arg list) rather than the misleading generic marker.
    assert "call skill_view(" in out["content"]
    assert "use summary/artifacts" not in out["content"]


def test_generic_tool_still_uses_generic_marker() -> None:
    # Regression guard: only skill_view gets the reload hint; other bulky tools keep
    # the generic "raw output omitted" marker.
    out = _compact_tool_payload_for_protocol(
        "sandbox_execute_pandas", {"summary": "s", "content": "Q" * 2000}
    )
    assert "use summary/artifacts" in out["content"]
    assert "call skill_view(" not in out["content"]
