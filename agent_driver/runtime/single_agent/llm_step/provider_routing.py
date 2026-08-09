"""R3: resolve the provider for a request by its ``model_role`` (role → provider).

The step loop calls this instead of reaching for ``host._deps.provider`` directly so a
run's ``model_role`` can route to a different provider object (see
``RunnerDeps.provider_for`` / ``RunnerConfig(role_providers=...)``). It tolerates a
minimal duck-typed ``_deps`` (widely used in tests) that predates the resolver by
falling back to the default ``provider``.
"""

from __future__ import annotations

from typing import Any


def resolve_request_provider(host: Any, request: Any) -> Any:
    """Return the provider for ``request``'s ``model_role``: ``RunnerDeps.provider_for``
    when present, else the default ``provider``. Tolerates a minimal duck-typed ``_deps``
    (falls back to ``provider``) and a request without ``model_role`` (routes to default)."""
    deps = host._deps
    resolver = getattr(deps, "provider_for", None)
    if resolver is not None:
        return resolver(getattr(request, "model_role", None))
    return deps.provider
