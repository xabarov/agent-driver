"""Guard the RunnerConfig construction contract (A3 foot-gun removal)."""

from __future__ import annotations

import pytest

from agent_driver.runtime.single_agent.types import RunnerConfig


def test_default_construction_sets_all_groups() -> None:
    """A no-arg config exposes its sub-settings and simple defaults."""
    config = RunnerConfig()
    assert config.graph_id == "single_agent_runtime"
    assert config.observation_max_chars == 400
    assert config.memory_provider is None
    assert config.lifecycle_hooks == ()
    # Sub-settings groups are present.
    assert config.trimming is not None
    assert config.compaction is not None


def test_flat_kwargs_route_into_sub_settings() -> None:
    """Flattened keyword args still populate the grouped settings objects."""
    config = RunnerConfig(trim_max_chars=4242, enable_compaction=False)
    assert config.trim_max_chars == 4242
    assert config.enable_compaction is False


def test_unknown_kwarg_is_rejected() -> None:
    """Unknown constructor arguments raise, catching typos early."""
    with pytest.raises(TypeError):
        RunnerConfig(definitely_not_a_field=1)


def test_with_overrides_is_independent() -> None:
    """with_overrides yields a clone without mutating the original."""
    base = RunnerConfig(observation_max_chars=11)
    clone = base.with_overrides(observation_max_chars=22)
    assert base.observation_max_chars == 11
    assert clone.observation_max_chars == 22
    # Nested settings are shared (shallow copy) but never mutated.
    assert base.trimming is clone.trimming


def test_capability_settings_field_snapshot() -> None:
    """Lock the capability surface — adding a flat field must be deliberate.

    R1 consolidated the E1–E8 capability flags into CapabilitySettings. This
    snapshot makes a future flat addition to RunnerConfig a conscious choice
    (extend CapabilitySettings + this set), not silent sprawl.
    """
    from dataclasses import fields

    from agent_driver.runtime.single_agent.lifecycle.config_sections import (
        CapabilitySettings,
    )

    assert {f.name for f in fields(CapabilitySettings)} == {
        "enable_prompt_cache",
        "harness_profiles",
        "auxiliary_provider",
        "auxiliary_model",
        "project_memory_sources",
        "tool_concurrency_limit",
        "subagent_model_routing",
    }


def test_capability_flat_kwargs_and_group_are_equivalent() -> None:
    """Flat kwargs and an explicit CapabilitySettings produce the same reads."""
    from agent_driver.runtime.single_agent.lifecycle.config_sections import (
        CapabilitySettings,
    )

    flat = RunnerConfig(enable_prompt_cache=True, tool_concurrency_limit=4)
    grouped = RunnerConfig(
        capabilities=CapabilitySettings(
            enable_prompt_cache=True, tool_concurrency_limit=4
        )
    )
    for cfg in (flat, grouped):
        assert cfg.enable_prompt_cache is True
        assert cfg.tool_concurrency_limit == 4
        assert cfg.capabilities.enable_prompt_cache is True


def test_capability_defaults_normalized() -> None:
    """Defaults match the pre-R1 flat behavior (empty containers, False)."""
    cfg = RunnerConfig()
    assert cfg.enable_prompt_cache is False
    assert cfg.harness_profiles == ()
    assert cfg.project_memory_sources == ()
    assert cfg.subagent_model_routing == {}
    assert cfg.auxiliary_provider is None
    assert cfg.auxiliary_model is None
    assert cfg.tool_concurrency_limit is None
