"""Option B2 phase-2: rolling-summary cadence deferral + marker-reset."""

from __future__ import annotations

from types import SimpleNamespace

from agent_driver.runtime.single_agent.context_management.compaction_stage import (
    _reset_rolling_summary_state,
    _rolling_cadence_should_defer,
)


def _host(*, every_n: int, rolling: bool = True) -> SimpleNamespace:
    config = SimpleNamespace(
        enable_compaction=True,
        enable_llm_compaction=True,
        enable_rolling_summary=rolling,
        rolling_summary_every_n_turns=every_n,
    )
    return SimpleNamespace(_config=config)


def _decision(mode: str = "llm_full", *, eligible: bool = True) -> SimpleNamespace:
    return SimpleNamespace(eligible=eligible, mode=SimpleNamespace(value=mode))


def _ctx(metadata: dict) -> SimpleNamespace:
    return SimpleNamespace(metadata=metadata)


def test_cadence_one_never_defers() -> None:
    ctx = _ctx({"rolling_summary": "S"})
    assert not _rolling_cadence_should_defer(
        _host(every_n=1), context=ctx, decision=_decision(), token_pressure_state="compact"
    )


def test_cadence_never_defers_first_fold() -> None:
    # No prior summary yet -> the first fold must run.
    ctx = _ctx({})
    assert not _rolling_cadence_should_defer(
        _host(every_n=3), context=ctx, decision=_decision(), token_pressure_state="compact"
    )


def test_cadence_defers_then_folds_on_nth() -> None:
    host = _host(every_n=3)
    ctx = _ctx({"rolling_summary": "S"})
    # firings 1 and 2 defer, firing 3 folds (reset).
    assert _rolling_cadence_should_defer(
        host, context=ctx, decision=_decision(), token_pressure_state="compact"
    )
    assert ctx.metadata["rolling_skip_count"] == 1
    assert _rolling_cadence_should_defer(
        host, context=ctx, decision=_decision(), token_pressure_state="compact"
    )
    assert ctx.metadata["rolling_skip_count"] == 2
    assert not _rolling_cadence_should_defer(
        host, context=ctx, decision=_decision(), token_pressure_state="compact"
    )
    assert ctx.metadata["rolling_skip_count"] == 0


def test_cadence_never_defers_under_blocking() -> None:
    host = _host(every_n=3)
    ctx = _ctx({"rolling_summary": "S", "rolling_skip_count": 1})
    assert not _rolling_cadence_should_defer(
        host, context=ctx, decision=_decision(), token_pressure_state="blocking"
    )
    # blocking resets the counter so cadence restarts cleanly after the forced fold.
    assert ctx.metadata["rolling_skip_count"] == 0


def test_cadence_disabled_when_rolling_off() -> None:
    ctx = _ctx({"rolling_summary": "S"})
    assert not _rolling_cadence_should_defer(
        _host(every_n=3, rolling=False),
        context=ctx,
        decision=_decision(),
        token_pressure_state="compact",
    )


def test_reset_clears_rolling_state() -> None:
    ctx = _ctx(
        {
            "rolling_summary": "S",
            "rolling_summary_covers_upto": 7,
            "rolling_skip_count": 2,
            "other": "keep",
        }
    )
    _reset_rolling_summary_state(ctx)
    assert "rolling_summary" not in ctx.metadata
    assert "rolling_summary_covers_upto" not in ctx.metadata
    assert "rolling_skip_count" not in ctx.metadata
    assert ctx.metadata["other"] == "keep"
