"""Tests for A2.1 hook chains (declarative reactive fallbacks).

Two layers under test:

1. The contract (:mod:`agent_driver.contracts.hook_chains`) —
   pydantic validation: regex compiles, depth/cooldown ranges,
   unique rule names.
2. The executor (:mod:`agent_driver.runtime.hook_chains`) — trigger
   matching for each event kind, condition narrowing (includes +
   regex), per-rule cooldown + depth budgets, placeholder
   substitution (including missing-key fallback), first-match
   ordering.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_driver.contracts import RuntimeEventType
from agent_driver.contracts.events import RuntimeEvent
from agent_driver.contracts.hook_chains import (
    HookAction,
    HookActionType,
    HookChainConfig,
    HookCondition,
    HookRule,
    HookTrigger,
    HookTriggerEvent,
)
from agent_driver.runtime.hook_chains import (
    FallbackSpec,
    HookChainExecutor,
)

# ---------------------------------------------------------------------------
# Contract validation
# ---------------------------------------------------------------------------


def test_hook_rule_minimum_construction() -> None:
    rule = HookRule(
        name="r1",
        trigger=HookTrigger(event=HookTriggerEvent.RUN_FAILED),
        action=HookAction(
            type=HookActionType.SPAWN_FALLBACK,
            agent_type="fallback",
            prompt_template="hi",
        ),
    )
    assert rule.condition.error_includes is None
    assert rule.depth_limit == 1
    assert rule.cooldown_seconds == 0.0


def test_hook_condition_rejects_invalid_regex() -> None:
    """A bad regex must fail at load time so operators can't ship
    a config that crashes mid-run."""
    with pytest.raises(ValidationError) as excinfo:
        HookCondition(error_regex="(unclosed group")
    assert "does not compile" in str(excinfo.value)


def test_hook_rule_rejects_negative_cooldown() -> None:
    with pytest.raises(ValidationError):
        HookRule(
            name="r",
            trigger=HookTrigger(event=HookTriggerEvent.RUN_FAILED),
            action=HookAction(
                type=HookActionType.SPAWN_FALLBACK,
                agent_type="x",
                prompt_template="y",
            ),
            cooldown_seconds=-1.0,
        )


def test_hook_rule_rejects_negative_depth() -> None:
    with pytest.raises(ValidationError):
        HookRule(
            name="r",
            trigger=HookTrigger(event=HookTriggerEvent.RUN_FAILED),
            action=HookAction(
                type=HookActionType.SPAWN_FALLBACK,
                agent_type="x",
                prompt_template="y",
            ),
            depth_limit=-1,
        )


def test_hook_chain_config_rejects_duplicate_names() -> None:
    """Rule names are the bookkeeping key — duplicates would make
    cooldown / depth budgets ambiguous."""
    rule = HookRule(
        name="dup",
        trigger=HookTrigger(event=HookTriggerEvent.RUN_FAILED),
        action=HookAction(
            type=HookActionType.SPAWN_FALLBACK,
            agent_type="x",
            prompt_template="y",
        ),
    )
    with pytest.raises(ValidationError) as excinfo:
        HookChainConfig(rules=[rule, rule])
    assert "duplicate rule name" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _event(event_type: RuntimeEventType, payload: dict) -> RuntimeEvent:
    return RuntimeEvent(
        event_id="e1",
        run_id="run-test",
        attempt_id="att",
        seq=1,
        type=event_type,
        payload=payload,
        created_at="2026-05-29T09:00:00+00:00",
    )


def _tool_failed_event(*, tool: str, error: str = "") -> RuntimeEvent:
    return _event(
        RuntimeEventType.TOOL_CALL_COMPLETED,
        {
            "tools": [{"tool_name": tool, "error": error}],
            "statuses": ["failed"],
        },
    )


def _run_failed_event(reason: str = "model_error") -> RuntimeEvent:
    return _event(RuntimeEventType.RUN_FAILED, {"reason": reason})


def _simple_rule(
    *,
    name: str = "r",
    event: HookTriggerEvent = HookTriggerEvent.TOOL_CALL_FAILED,
    tool: str | None = None,
    error_includes: str | None = None,
    error_regex: str | None = None,
    cooldown_seconds: float = 0.0,
    depth_limit: int = 1,
    prompt_template: str = "Retry {tool_name}",
    agent_type: str = "fallback",
) -> HookRule:
    return HookRule(
        name=name,
        trigger=HookTrigger(event=event, tool=tool),
        condition=HookCondition(error_includes=error_includes, error_regex=error_regex),
        action=HookAction(
            type=HookActionType.SPAWN_FALLBACK,
            agent_type=agent_type,
            prompt_template=prompt_template,
        ),
        cooldown_seconds=cooldown_seconds,
        depth_limit=depth_limit,
    )


# ---------------------------------------------------------------------------
# Trigger matching
# ---------------------------------------------------------------------------


def test_observe_returns_nothing_for_unrelated_event() -> None:
    """Events the executor doesn't care about must be cheap."""
    cfg = HookChainConfig(rules=[_simple_rule()])
    executor = HookChainExecutor(cfg)
    # RUN_STARTED is not in the trigger set.
    out = executor.observe(_event(RuntimeEventType.RUN_STARTED, {}))
    assert out == []


def test_tool_call_failed_rule_fires_on_failed_envelope() -> None:
    cfg = HookChainConfig(
        rules=[_simple_rule(tool="chart_vegalite", agent_type="simplified_chart")]
    )
    executor = HookChainExecutor(cfg)
    out = executor.observe(
        _tool_failed_event(tool="chart_vegalite", error="out of memory"),
        placeholders={"tool_name": "chart_vegalite"},
    )
    assert len(out) == 1
    assert isinstance(out[0], FallbackSpec)
    assert out[0].agent_type == "simplified_chart"
    assert out[0].triggered_by == "tool_call_failed:chart_vegalite"


def test_tool_filter_narrows_to_specific_tool() -> None:
    """``trigger.tool`` lets multiple rules listen to the same
    event kind without cross-firing."""
    cfg = HookChainConfig(
        rules=[
            _simple_rule(name="r_chart", tool="chart_vegalite"),
            _simple_rule(name="r_sandbox", tool="sandbox_execute_pandas"),
        ]
    )
    executor = HookChainExecutor(cfg)
    out = executor.observe(
        _tool_failed_event(tool="chart_vegalite", error="boom"),
        placeholders={"tool_name": "chart_vegalite"},
    )
    assert [s.rule_name for s in out] == ["r_chart"]


def test_run_failed_event_matches_run_failed_rule() -> None:
    cfg = HookChainConfig(
        rules=[_simple_rule(name="r_run", event=HookTriggerEvent.RUN_FAILED)]
    )
    executor = HookChainExecutor(cfg)
    out = executor.observe(_run_failed_event(reason="model_error"))
    assert [s.rule_name for s in out] == ["r_run"]
    assert out[0].triggered_by == "run_failed"


def test_completed_tool_call_does_not_match_failed_rule() -> None:
    """Only failed envelopes fire ``tool_call_failed`` rules — a
    successful call must not trigger a fallback."""
    cfg = HookChainConfig(rules=[_simple_rule(tool="chart_vegalite")])
    executor = HookChainExecutor(cfg)
    out = executor.observe(
        _event(
            RuntimeEventType.TOOL_CALL_COMPLETED,
            {"tools": [{"tool_name": "chart_vegalite"}], "statuses": ["completed"]},
        )
    )
    assert out == []


# ---------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------


def test_error_includes_narrows_match() -> None:
    cfg = HookChainConfig(
        rules=[_simple_rule(tool="chart_vegalite", error_includes="memory")]
    )
    executor = HookChainExecutor(cfg)
    # Matches.
    out = executor.observe(
        _tool_failed_event(tool="chart_vegalite", error="out of memory boom")
    )
    assert len(out) == 1
    # Doesn't match — different error text.
    out = executor.observe(_tool_failed_event(tool="chart_vegalite", error="timeout"))
    assert out == []


def test_error_includes_is_case_insensitive() -> None:
    cfg = HookChainConfig(
        rules=[_simple_rule(tool="chart_vegalite", error_includes="OOM")]
    )
    executor = HookChainExecutor(cfg)
    out = executor.observe(
        _tool_failed_event(tool="chart_vegalite", error="oom killed")
    )
    assert len(out) == 1


def test_error_regex_narrows_match() -> None:
    cfg = HookChainConfig(
        rules=[
            _simple_rule(
                tool="chart_vegalite",
                error_regex=r"memory|allocation",
            )
        ]
    )
    executor = HookChainExecutor(cfg)
    out = executor.observe(
        _tool_failed_event(tool="chart_vegalite", error="allocation failed")
    )
    assert len(out) == 1


def test_combined_condition_is_AND() -> None:
    """Both includes + regex set means both must match."""
    cfg = HookChainConfig(
        rules=[
            _simple_rule(
                tool="chart_vegalite",
                error_includes="memory",
                error_regex=r"\d+ bytes",
            )
        ]
    )
    executor = HookChainExecutor(cfg)
    # Both match.
    out = executor.observe(
        _tool_failed_event(tool="chart_vegalite", error="out of memory 1024 bytes")
    )
    assert len(out) == 1
    # Only one matches (no bytes count).
    out = executor.observe(
        _tool_failed_event(tool="chart_vegalite", error="out of memory")
    )
    assert out == []


# ---------------------------------------------------------------------------
# Budgets: cooldown + depth
# ---------------------------------------------------------------------------


def test_depth_limit_caps_total_fires_per_rule() -> None:
    cfg = HookChainConfig(rules=[_simple_rule(tool="chart_vegalite", depth_limit=2)])
    executor = HookChainExecutor(cfg)
    # Fire 3 times; only first 2 should yield a fallback.
    out1 = executor.observe(_tool_failed_event(tool="chart_vegalite", error="x"))
    out2 = executor.observe(_tool_failed_event(tool="chart_vegalite", error="x"))
    out3 = executor.observe(_tool_failed_event(tool="chart_vegalite", error="x"))
    assert len(out1) == 1
    assert len(out2) == 1
    assert out3 == []


def test_depth_limit_zero_disables_rule() -> None:
    """``depth_limit=0`` is the operator's "shipped but off" knob."""
    cfg = HookChainConfig(rules=[_simple_rule(tool="chart_vegalite", depth_limit=0)])
    executor = HookChainExecutor(cfg)
    out = executor.observe(_tool_failed_event(tool="chart_vegalite", error="x"))
    assert out == []


def test_cooldown_enforced_between_fires() -> None:
    """Pin cooldown via an injected fake clock so we don't sleep."""
    fake_clock = {"now": 100.0}
    cfg = HookChainConfig(
        rules=[
            _simple_rule(
                tool="chart_vegalite",
                cooldown_seconds=60.0,
                depth_limit=5,
            )
        ]
    )
    executor = HookChainExecutor(cfg, now=lambda: fake_clock["now"])
    # Fire 1: clock=100, last_fired=100.
    out1 = executor.observe(_tool_failed_event(tool="chart_vegalite", error="x"))
    assert len(out1) == 1
    # Fire 2 immediately — still within cooldown → blocked.
    fake_clock["now"] = 110.0
    out2 = executor.observe(_tool_failed_event(tool="chart_vegalite", error="x"))
    assert out2 == []
    # Past cooldown — allowed again.
    fake_clock["now"] = 161.0
    out3 = executor.observe(_tool_failed_event(tool="chart_vegalite", error="x"))
    assert len(out3) == 1


def test_per_rule_cooldown_is_independent() -> None:
    """Firing rule A must NOT reset rule B's cooldown."""
    fake_clock = {"now": 0.0}
    cfg = HookChainConfig(
        rules=[
            _simple_rule(name="a", tool="t_a", cooldown_seconds=10.0),
            _simple_rule(name="b", tool="t_b", cooldown_seconds=10.0),
        ]
    )
    executor = HookChainExecutor(cfg, now=lambda: fake_clock["now"])
    executor.observe(_tool_failed_event(tool="t_a", error=""))
    # rule b never fired — so b still has full budget.
    out_b = executor.observe(_tool_failed_event(tool="t_b", error=""))
    assert len(out_b) == 1


# ---------------------------------------------------------------------------
# Placeholder rendering
# ---------------------------------------------------------------------------


def test_prompt_template_renders_placeholders() -> None:
    cfg = HookChainConfig(
        rules=[
            _simple_rule(
                tool="chart_vegalite",
                prompt_template=(
                    "Tool {tool_name} failed for question {original_question!s}. "
                    "Error was: {error_message}."
                ),
            )
        ]
    )
    executor = HookChainExecutor(cfg)
    placeholders = HookChainExecutor.default_placeholders(
        tool_name="chart_vegalite",
        error_message="out of memory",
        original_question="Build chart for sales",
    )
    out = executor.observe(
        _tool_failed_event(tool="chart_vegalite", error="out of memory"),
        placeholders=placeholders,
    )
    assert len(out) == 1
    assert "chart_vegalite" in out[0].prompt
    assert "out of memory" in out[0].prompt
    assert "Build chart for sales" in out[0].prompt


def test_missing_placeholder_renders_empty_string_not_keyerror() -> None:
    """Misspelled / unknown placeholder names must NOT crash the
    fire — they fall back to empty string so the operator gets a
    debug-able prompt instead of a runtime exception."""
    cfg = HookChainConfig(
        rules=[
            _simple_rule(
                tool="chart_vegalite",
                prompt_template="Hi {unknown_token}, retry {tool_name}",
            )
        ]
    )
    executor = HookChainExecutor(cfg)
    out = executor.observe(
        _tool_failed_event(tool="chart_vegalite", error=""),
        placeholders={"tool_name": "chart_vegalite"},
    )
    assert len(out) == 1
    assert "{unknown_token}" not in out[0].prompt
    assert "Hi , retry chart_vegalite" in out[0].prompt


# ---------------------------------------------------------------------------
# Rule ordering
# ---------------------------------------------------------------------------


def test_first_matching_rule_wins_when_multiple_apply() -> None:
    """When two rules match the same event the executor returns
    them in declaration order — useful for the "specific before
    blanket" rule layout."""
    cfg = HookChainConfig(
        rules=[
            _simple_rule(
                name="specific",
                tool="chart_vegalite",
                error_includes="memory",
                agent_type="simplified_chart",
            ),
            _simple_rule(
                name="blanket",
                tool="chart_vegalite",
                agent_type="any_chart_fallback",
            ),
        ]
    )
    executor = HookChainExecutor(cfg)
    out = executor.observe(
        _tool_failed_event(tool="chart_vegalite", error="out of memory")
    )
    # BOTH match — executor returns BOTH (host can decide to take
    # only the first; we don't pre-filter so the host's strategy
    # is its own).
    assert [s.rule_name for s in out] == ["specific", "blanket"]


# ---------------------------------------------------------------------------
# Action shape mirroring
# ---------------------------------------------------------------------------


def test_fallback_spec_carries_subagent_shape_fields() -> None:
    """The :class:`FallbackSpec` mirrors :class:`SubagentSpec`'s
    fields so the host can build a spec without translation."""
    cfg = HookChainConfig(
        rules=[
            HookRule(
                name="r",
                trigger=HookTrigger(
                    event=HookTriggerEvent.TOOL_CALL_FAILED,
                    tool="chart_vegalite",
                ),
                action=HookAction(
                    type=HookActionType.SPAWN_FALLBACK,
                    agent_type="simple_chart",
                    prompt_template="hi",
                    allowed_tools=("chart_vegalite",),
                    tool_choice={"type": "tool", "name": "chart_vegalite"},
                    response_format={"type": "json_object"},
                    max_tool_calls=1,
                    deadline_seconds=30.0,
                    max_cost_usd=0.05,
                ),
            )
        ]
    )
    executor = HookChainExecutor(cfg)
    out = executor.observe(_tool_failed_event(tool="chart_vegalite", error=""))
    assert len(out) == 1
    spec = out[0]
    assert spec.allowed_tools == ("chart_vegalite",)
    assert spec.tool_choice == {"type": "tool", "name": "chart_vegalite"}
    assert spec.response_format == {"type": "json_object"}
    assert spec.max_tool_calls == 1
    assert spec.deadline_seconds == 30.0
    assert spec.max_cost_usd == 0.05


# ---------------------------------------------------------------------------
# N4: field_equals conditions + dedup window
# ---------------------------------------------------------------------------


def _denied_tool_event(*, tool: str, error: str = "blocked") -> RuntimeEvent:
    return _event(
        RuntimeEventType.TOOL_CALL_COMPLETED,
        {"tools": [{"tool_name": tool, "error": error}], "statuses": ["denied"]},
    )


def _field_rule(field_equals: dict[str, str], *, name: str = "r") -> HookRule:
    return HookRule(
        name=name,
        trigger=HookTrigger(event=HookTriggerEvent.TOOL_CALL_FAILED),
        condition=HookCondition(field_equals=field_equals),
        action=HookAction(
            type=HookActionType.SPAWN_FALLBACK,
            agent_type="fallback",
            prompt_template="retry {tool_name}",
        ),
    )


def test_field_equals_matches_tool_name() -> None:
    """A field filter narrows to one tool by structured field, not error text."""
    executor = HookChainExecutor(
        HookChainConfig(rules=[_field_rule({"tool_name": "bash"})])
    )
    assert len(executor.observe(_tool_failed_event(tool="bash"))) == 1
    assert executor.observe(_tool_failed_event(tool="python")) == []


def test_field_equals_matches_raw_status_distinguishing_denied() -> None:
    """status filter sees the raw denied/failed value, not a collapsed one."""
    denied_rule = HookChainConfig(rules=[_field_rule({"status": "denied"})])
    assert (
        len(HookChainExecutor(denied_rule).observe(_denied_tool_event(tool="bash")))
        == 1
    )
    # A plain failed event must NOT satisfy a status=denied filter.
    assert HookChainExecutor(denied_rule).observe(_tool_failed_event(tool="bash")) == []


def test_field_equals_is_case_insensitive() -> None:
    executor = HookChainExecutor(
        HookChainConfig(rules=[_field_rule({"tool_name": "BASH"})])
    )
    assert len(executor.observe(_tool_failed_event(tool="bash"))) == 1


def test_field_equals_combines_with_error_text_as_and() -> None:
    rule = HookRule(
        name="r",
        trigger=HookTrigger(event=HookTriggerEvent.TOOL_CALL_FAILED),
        condition=HookCondition(
            error_includes="rate limit", field_equals={"tool_name": "bash"}
        ),
        action=HookAction(
            type=HookActionType.SPAWN_FALLBACK, agent_type="f", prompt_template="x"
        ),
        depth_limit=5,
    )
    executor = HookChainExecutor(HookChainConfig(rules=[rule]))
    # Both conditions hold → fires.
    assert (
        len(executor.observe(_tool_failed_event(tool="bash", error="hit rate limit")))
        == 1
    )
    # Right tool, wrong error → no fire.
    assert executor.observe(_tool_failed_event(tool="bash", error="boom")) == []
    # Right error, wrong tool → no fire.
    assert executor.observe(_tool_failed_event(tool="python", error="rate limit")) == []


def test_dedup_window_suppresses_same_signature_but_allows_different() -> None:
    """Same tool+error within the window fires once; a different error fires."""
    clock = {"now": 0.0}
    rule = HookRule(
        name="r",
        trigger=HookTrigger(event=HookTriggerEvent.TOOL_CALL_FAILED),
        action=HookAction(
            type=HookActionType.SPAWN_FALLBACK, agent_type="f", prompt_template="x"
        ),
        dedup_window_seconds=60.0,
        depth_limit=10,
    )
    executor = HookChainExecutor(
        HookChainConfig(rules=[rule]), now=lambda: clock["now"]
    )
    # First failure fires.
    assert len(executor.observe(_tool_failed_event(tool="bash", error="boom"))) == 1
    # Identical signature 10s later is deduped.
    clock["now"] = 10.0
    assert executor.observe(_tool_failed_event(tool="bash", error="boom")) == []
    # A DIFFERENT error (distinct signature) still fires inside the window.
    assert len(executor.observe(_tool_failed_event(tool="bash", error="other"))) == 1
    # Past the window, the original signature fires again.
    clock["now"] = 71.0
    assert len(executor.observe(_tool_failed_event(tool="bash", error="boom"))) == 1


def test_dedup_window_zero_keeps_legacy_behavior() -> None:
    """dedup_window_seconds=0 (default) does not suppress repeats."""
    rule = _simple_rule(depth_limit=5)
    assert rule.dedup_window_seconds == 0.0
    executor = HookChainExecutor(HookChainConfig(rules=[rule]))
    assert len(executor.observe(_tool_failed_event(tool="bash", error="boom"))) == 1
    assert len(executor.observe(_tool_failed_event(tool="bash", error="boom"))) == 1
