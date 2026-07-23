"""Epic 032 phase A: per-task auxiliary-model seam."""

from __future__ import annotations

from agent_driver.runtime.single_agent.types import RunnerConfig


def test_aux_model_registry_resolution() -> None:
    config = RunnerConfig(
        auxiliary_models={"grader": "gemini-flash-lite", "title": "haiku"},
        auxiliary_model="shared-aux",
    )
    assert config.aux_model_for("grader") == "gemini-flash-lite"
    assert config.aux_model_for("title") == "haiku"
    # Unknown task falls back to the shared auxiliary_model.
    assert config.aux_model_for("extraction") == "shared-aux"


def test_aux_model_none_without_config() -> None:
    assert RunnerConfig().aux_model_for("grader") is None
    # Registry present but task missing, no shared model → None (provider default).
    assert RunnerConfig(auxiliary_models={"title": "m"}).aux_model_for("grader") is None
