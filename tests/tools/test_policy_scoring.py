"""Tests for policy-aware tool choice scoring and antipattern detection."""

from __future__ import annotations

import pytest

from agent_driver.contracts.enums import (
    AgentProfile,
    ApprovalMode,
    SideEffectClass,
    ToolRisk,
)
from agent_driver.contracts.tools import ToolManifest
from agent_driver.tools import (
    AntipatternMatch,
    ToolChoiceContext,
    ToolChoicePolicyRegistry,
    ToolChoiceScore,
    antipattern_to_warning_payload,
    build_default_tool_choice_registry,
    generic_after_specialized_search,
    prefer_specialized_over_generic,
)


def _manifest(
    name: str,
    *,
    capabilities: list[str] | None = None,
    risk: ToolRisk = ToolRisk.LOW,
) -> ToolManifest:
    metadata: dict = {}
    if capabilities is not None:
        metadata["capabilities"] = capabilities
    return ToolManifest(
        name=name,
        description=f"Tool {name}.",
        risk=risk,
        side_effect=SideEffectClass.READ_ONLY,
        approval_mode=ApprovalMode.NEVER,
        remediation_hints=["Try a smaller scope when output is empty."],
        supported_profiles=[AgentProfile.TOOL_CALLING],
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# ToolChoiceContext
# ---------------------------------------------------------------------------


def test_context_previous_tool_returns_last_call() -> None:
    """previous_tool returns the most recent entry or None."""
    assert ToolChoiceContext().previous_tool() is None
    assert ToolChoiceContext(recent_tool_calls=("a",)).previous_tool() == "a"
    assert ToolChoiceContext(recent_tool_calls=("a", "b", "c")).previous_tool() == "c"


# ---------------------------------------------------------------------------
# Registration validation
# ---------------------------------------------------------------------------


def test_registry_rejects_empty_rule_id_for_preference() -> None:
    """register_preference rejects empty rule ids."""
    registry = ToolChoicePolicyRegistry()
    with pytest.raises(ValueError, match="rule_id"):
        registry.register_preference("", lambda m, c: (0.0, None))


def test_registry_rejects_non_callable_preference() -> None:
    """register_preference rejects non-callable rules."""
    registry = ToolChoicePolicyRegistry()
    with pytest.raises(TypeError, match="callable"):
        registry.register_preference("p", "not_callable")  # type: ignore[arg-type]


def test_registry_rejects_empty_rule_id_for_antipattern() -> None:
    """register_antipattern rejects empty rule ids."""
    registry = ToolChoicePolicyRegistry()
    with pytest.raises(ValueError, match="rule_id"):
        registry.register_antipattern("", lambda name, ctx: None)


def test_registry_lists_registered_ids_in_registration_order() -> None:
    """preference_rule_ids and antipattern_rule_ids preserve insertion order."""
    registry = ToolChoicePolicyRegistry()
    registry.register_preference("p1", lambda m, c: (0.0, None))
    registry.register_preference("p2", lambda m, c: (0.0, None))
    registry.register_antipattern("a1", lambda name, ctx: None)
    assert registry.preference_rule_ids() == ("p1", "p2")
    assert registry.antipattern_rule_ids() == ("a1",)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def test_score_candidates_accumulates_deltas() -> None:
    """Multiple preference rules accumulate score deltas per tool."""
    registry = ToolChoicePolicyRegistry()
    registry.register_preference("p_a", lambda m, c: (0.5, "a"))
    registry.register_preference("p_b", lambda m, c: (0.25, "b"))
    context = ToolChoiceContext(candidate_tools=(_manifest("foo"),))
    scores = registry.score_candidates(context)
    assert len(scores) == 1
    assert scores[0].tool_name == "foo"
    assert scores[0].score == pytest.approx(0.75)
    assert scores[0].reasons == ("p_a:a", "p_b:b")


def test_score_candidates_skips_zero_deltas_in_reasons() -> None:
    """A zero delta does not contribute a reason entry even if rule returns text."""
    registry = ToolChoicePolicyRegistry()
    registry.register_preference("p_zero", lambda m, c: (0.0, "ignored"))
    registry.register_preference("p_nonzero", lambda m, c: (0.5, "boost"))
    scores = registry.score_candidates(
        ToolChoiceContext(candidate_tools=(_manifest("foo"),))
    )
    assert scores[0].reasons == ("p_nonzero:boost",)


def test_score_candidates_isolates_raising_rule() -> None:
    """A rule that raises produces an explicit rule_error reason and contributes 0."""

    def bad_rule(_manifest, _context):
        raise RuntimeError("boom")

    registry = ToolChoicePolicyRegistry()
    registry.register_preference("p_bad", bad_rule)
    registry.register_preference("p_good", lambda m, c: (0.5, "ok"))
    scores = registry.score_candidates(
        ToolChoiceContext(candidate_tools=(_manifest("foo"),))
    )
    assert scores[0].score == pytest.approx(0.5)
    assert any("rule_error:p_bad:RuntimeError" in r for r in scores[0].reasons)


def test_score_candidates_rejects_invalid_delta_type() -> None:
    """A rule returning a non-numeric delta is dropped with a synthetic reason."""

    def bad_rule(_manifest, _context):
        return "not_a_number", "ignored"

    registry = ToolChoicePolicyRegistry()
    registry.register_preference("p_bad", bad_rule)  # type: ignore[arg-type]
    scores = registry.score_candidates(
        ToolChoiceContext(candidate_tools=(_manifest("foo"),))
    )
    assert scores[0].score == 0.0
    assert "rule_invalid_delta:p_bad" in scores[0].reasons


def test_score_candidates_uses_base_score() -> None:
    """base_score offsets every candidate before rules run."""
    registry = ToolChoicePolicyRegistry()
    registry.register_preference("p_a", lambda m, c: (0.25, None))
    scores = registry.score_candidates(
        ToolChoiceContext(candidate_tools=(_manifest("foo"),)),
        base_score=1.0,
    )
    assert scores[0].score == pytest.approx(1.25)


def test_score_candidates_preserves_candidate_order() -> None:
    """Output order matches input candidate order."""
    registry = ToolChoicePolicyRegistry()
    registry.register_preference("p", lambda m, c: (0.0, None))
    context = ToolChoiceContext(
        candidate_tools=(_manifest("z"), _manifest("a"), _manifest("m"))
    )
    names = [s.tool_name for s in registry.score_candidates(context)]
    assert names == ["z", "a", "m"]


# ---------------------------------------------------------------------------
# Antipattern detection
# ---------------------------------------------------------------------------


def test_detect_antipatterns_returns_empty_when_no_match() -> None:
    """No registered antipattern matching → empty list."""
    registry = ToolChoicePolicyRegistry()
    registry.register_antipattern("a1", lambda name, ctx: None)
    matches = registry.detect_antipatterns("nmap", ToolChoiceContext())
    assert not matches


def test_detect_antipatterns_collects_all_matches() -> None:
    """All matching rules contribute one AntipatternMatch each."""
    registry = ToolChoicePolicyRegistry()
    registry.register_antipattern(
        "a1",
        lambda name, ctx: AntipatternMatch(pattern_id="x", description="rule a1 hit"),
    )
    registry.register_antipattern(
        "a2",
        lambda name, ctx: AntipatternMatch(pattern_id="y", description="rule a2 hit"),
    )
    matches = registry.detect_antipatterns("nmap", ToolChoiceContext())
    pattern_ids = [m.pattern_id for m in matches]
    assert pattern_ids == ["x", "y"]


def test_detect_antipatterns_isolates_raising_rule() -> None:
    """Raising rule produces synthetic match with rule_error pattern id."""

    def raising_rule(_name, _ctx):
        raise ValueError("boom")

    registry = ToolChoicePolicyRegistry()
    registry.register_antipattern("a_bad", raising_rule)
    matches = registry.detect_antipatterns("nmap", ToolChoiceContext())
    assert len(matches) == 1
    assert matches[0].pattern_id == "rule_error:a_bad"
    assert matches[0].severity == "info"
    assert "ValueError" in matches[0].description


def test_detect_antipatterns_rejects_invalid_return_type() -> None:
    """Non-AntipatternMatch returns produce rule_invalid_return entry."""
    registry = ToolChoicePolicyRegistry()
    registry.register_antipattern(
        "a_invalid", lambda name, ctx: {"not": "a match"}  # type: ignore[arg-type]
    )
    matches = registry.detect_antipatterns("nmap", ToolChoiceContext())
    assert matches[0].pattern_id == "rule_invalid_return:a_invalid"


# ---------------------------------------------------------------------------
# Reference built-in rules
# ---------------------------------------------------------------------------


def test_prefer_specialized_over_generic_boosts_capabilities() -> None:
    """Manifests with capabilities receive a 0.25 boost; bare manifests do not."""
    specialized = _manifest("nmap", capabilities=["scan", "discovery"])
    generic = _manifest("bash")
    delta_spec, reason_spec = prefer_specialized_over_generic(
        specialized, ToolChoiceContext()
    )
    delta_gen, reason_gen = prefer_specialized_over_generic(
        generic, ToolChoiceContext()
    )
    assert delta_spec == pytest.approx(0.25)
    assert reason_spec == "capabilities=2"
    assert delta_gen == 0.0
    assert reason_gen is None


def test_generic_after_specialized_search_triggers_on_default_sets() -> None:
    """Default name sets cover the canonical tool_search → bash sequence."""
    match = generic_after_specialized_search(
        "bash", ToolChoiceContext(recent_tool_calls=("tool_search",))
    )
    assert match is not None
    assert match.pattern_id == "generic_after_specialized_search"
    assert match.severity == "warning"
    assert match.matched_recent_tool == "tool_search"
    assert match.matched_current_tool == "bash"


def test_generic_after_specialized_search_does_not_trigger_without_prior() -> None:
    """No previous tool → no match."""
    assert generic_after_specialized_search("bash", ToolChoiceContext()) is None


def test_generic_after_specialized_search_respects_custom_sets() -> None:
    """Hosts can extend the specialized/generic name sets."""
    match = generic_after_specialized_search(
        "execute_command",
        ToolChoiceContext(recent_tool_calls=("recall",)),
        specialized_search_tool_names=("tool_search", "recall"),
        generic_tool_names=("execute_command",),
    )
    assert match is not None
    assert match.matched_recent_tool == "recall"


# ---------------------------------------------------------------------------
# Default registry preset
# ---------------------------------------------------------------------------


def test_default_registry_includes_reference_rules() -> None:
    """build_default_tool_choice_registry pre-loads the reference rules."""
    registry = build_default_tool_choice_registry()
    assert "prefer_specialized_over_generic" in registry.preference_rule_ids()
    assert "generic_after_specialized_search" in registry.antipattern_rule_ids()


def test_default_registry_end_to_end_score_and_detect() -> None:
    """Reference rules work end-to-end through the registry."""
    registry = build_default_tool_choice_registry()
    context = ToolChoiceContext(
        recent_tool_calls=("tool_search",),
        candidate_tools=(
            _manifest("nmap", capabilities=["scan"]),
            _manifest("bash"),
        ),
    )
    scores = registry.score_candidates(context)
    score_by_name = {s.tool_name: s.score for s in scores}
    assert score_by_name["nmap"] > score_by_name["bash"]

    matches = registry.detect_antipatterns("bash", context)
    assert any(m.pattern_id == "generic_after_specialized_search" for m in matches)


# ---------------------------------------------------------------------------
# Warning payload projection
# ---------------------------------------------------------------------------


def test_antipattern_to_warning_payload_carries_required_fields() -> None:
    """Warning payload carries kind/signal_id/severity/description."""
    match = AntipatternMatch(
        pattern_id="generic_after_specialized_search",
        severity="warning",
        description="x",
        matched_recent_tool="tool_search",
        matched_current_tool="bash",
        metadata={"trace": "operator"},
    )
    payload = antipattern_to_warning_payload(match)
    assert payload["kind"] == "tool_choice_antipattern"
    assert payload["signal_id"] == "generic_after_specialized_search"
    assert payload["severity"] == "warning"
    assert payload["description"] == "x"
    assert payload["matched_recent_tool"] == "tool_search"
    assert payload["matched_current_tool"] == "bash"
    assert payload["rule_metadata"] == {"trace": "operator"}


def test_antipattern_to_warning_payload_normalizes_invalid_severity() -> None:
    """Unknown severity values default to 'warning' to keep transport stable."""
    match = AntipatternMatch(
        pattern_id="x",
        severity="bogus",
        description="x",
    )
    payload = antipattern_to_warning_payload(match)
    assert payload["severity"] == "warning"


def test_antipattern_to_warning_payload_omits_optional_fields() -> None:
    """Optional fields not set on the match do not leak into the payload."""
    match = AntipatternMatch(pattern_id="x", description="x")
    payload = antipattern_to_warning_payload(match)
    assert "matched_recent_tool" not in payload
    assert "matched_current_tool" not in payload
    assert "rule_metadata" not in payload


def test_tool_choice_score_dataclass_roundtrip() -> None:
    """ToolChoiceScore exposes tool_name/score/reasons as readable fields."""
    score = ToolChoiceScore(
        tool_name="nmap", score=0.75, reasons=("rule1:hit", "rule2:hit")
    )
    assert score.tool_name == "nmap"
    assert score.score == 0.75
    assert score.reasons == ("rule1:hit", "rule2:hit")
