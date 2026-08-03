"""Public RunnerConfig compatibility helper contract."""

from __future__ import annotations

from agent_driver.runtime import RunnerConfig, runner_config_parameter_names


def test_parameter_names_cover_direct_extra_and_flattened_settings() -> None:
    names = runner_config_parameter_names()

    assert set(RunnerConfig.__annotations__).issubset(names)
    assert {
        "default_max_tool_calls",
        "default_hard_max_seconds",
        "default_idle_timeout_seconds",
        "fallback_providers",
        "finalize_hook_timeout",
        "stage_heartbeat_seconds",
        "trim_max_chars",
        "token_compact_threshold",
        "enable_subagents",
        "code_limits",
        "python_tool",
        "auxiliary_model",
    }.issubset(names)


def test_every_reported_parameter_is_accepted_individually() -> None:
    baseline = RunnerConfig()

    for name in runner_config_parameter_names():
        value = getattr(baseline, name, None)
        RunnerConfig(**{name: value})
