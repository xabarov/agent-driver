"""R3 — role → provider registry: a run's model_role can route to a different provider
object (cross-provider role distribution), falling back to the default provider."""

from __future__ import annotations

from types import SimpleNamespace

from agent_driver.runtime.single_agent.llm_step.provider_routing import (
    resolve_request_provider,
)
from agent_driver.runtime.single_agent.types import RunnerConfig, RunnerDeps


class _Provider:
    def __init__(self, name: str) -> None:
        self.name = name


def _deps(provider, role_providers) -> SimpleNamespace:
    # provider_for is an unbound method on RunnerDeps; bind it over a light object that
    # carries just the two fields it reads, so we exercise the real resolution logic.
    holder = SimpleNamespace(provider=provider, role_providers=role_providers)
    holder.provider_for = lambda role: RunnerDeps.provider_for(holder, role)
    return holder


# --- RunnerDeps.provider_for -------------------------------------------------------


def test_provider_for_routes_to_role():
    default, planner = _Provider("default"), _Provider("planner")
    d = _deps(default, {"planner": planner})
    assert d.provider_for("planner") is planner


def test_provider_for_unmapped_role_falls_back_to_default():
    default, planner = _Provider("default"), _Provider("planner")
    d = _deps(default, {"planner": planner})
    assert d.provider_for("executor") is default
    assert d.provider_for(None) is default


def test_provider_for_empty_registry_is_default():
    default = _Provider("default")
    d = _deps(default, {})
    assert d.provider_for("planner") is default


# --- RunnerConfig flat kwarg + copy ------------------------------------------------


def test_runner_config_accepts_role_providers():
    planner = _Provider("planner")
    rc = RunnerConfig(role_providers={"planner": planner})
    assert rc.role_providers == {"planner": planner}


def test_runner_config_defaults_empty():
    assert RunnerConfig().role_providers == {}


# --- resolve_request_provider helper (call-site seam) ------------------------------


def test_resolver_uses_provider_for_when_present():
    default, planner = _Provider("default"), _Provider("planner")
    host = SimpleNamespace(_deps=_deps(default, {"planner": planner}))
    request = SimpleNamespace(model_role="planner")
    assert resolve_request_provider(host, request) is planner


def test_resolver_falls_back_for_minimal_deps():
    # A minimal duck-typed _deps (no provider_for, as in many tests) → default provider.
    default = _Provider("default")
    host = SimpleNamespace(_deps=SimpleNamespace(provider=default))
    request = SimpleNamespace(model_role="planner")
    assert resolve_request_provider(host, request) is default


def test_resolver_tolerates_request_without_model_role():
    default, planner = _Provider("default"), _Provider("planner")
    host = SimpleNamespace(_deps=_deps(default, {"planner": planner}))
    # request is a bare string (some retry tests pass loose fakes) → default provider.
    assert resolve_request_provider(host, "not-a-request") is default
