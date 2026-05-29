"""Policy-aware tool choice scoring and antipattern detection.

This module gives host applications a generic framework for two related
runtime behaviors that are typically built one-off per product:

1. **Tool preference scoring.** After a candidate set of tool manifests is
   obtained (from ``tool_search``, the merged registry, an MCP catalog, ...),
   a host may want to bias the model toward tools matching the current
   context — e.g. prefer tools with specialized capabilities over generic
   shells, prefer read-only tools for passive scopes, etc.
2. **Antipattern detection.** After the model picks a tool, a host may want
   to flag obviously suboptimal choices — e.g. "model fell back to a
   generic shell right after a focused tool-search returned narrow
   matches". The runtime needs only to *detect* the antipattern; emission
   of metrics and SSE warnings stays in the host adapter layer.

The framework is intentionally domain-neutral:

- Rules are plain callables, not classes; hosts compose what they need.
- No tool-name allowlist is hard-coded in the runtime — every rule is
  registered by the host (one reference rule is shipped per direction so
  callers have working examples).
- Detection results plug into the runtime warning-event contract via
  ``antipattern_to_warning_payload`` — the existing
  ``agent_driver.adapters.project_warning_event`` projector recognizes
  ``kind="tool_choice_antipattern"`` so SSE consumers get one stable
  vocabulary across all warning kinds.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from agent_driver.contracts.tools import ToolManifest


@dataclass(frozen=True, slots=True)
class ToolChoiceContext:
    """Inputs used by preference and antipattern rules."""

    recent_tool_calls: tuple[str, ...] = ()
    candidate_tools: tuple[ToolManifest, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def previous_tool(self) -> str | None:
        """Return the most recent tool name or ``None`` for the first turn."""
        return self.recent_tool_calls[-1] if self.recent_tool_calls else None


@dataclass(frozen=True, slots=True)
class ToolChoiceScore:
    """One scored candidate produced by ``ToolChoicePolicyRegistry.score_candidates``."""

    tool_name: str
    score: float
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AntipatternMatch:
    """One antipattern detected by ``ToolChoicePolicyRegistry.detect_antipatterns``."""

    pattern_id: str
    severity: str = "warning"
    description: str = ""
    matched_recent_tool: str | None = None
    matched_current_tool: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


# Returns (delta, reason | None). A None reason indicates "rule did not apply".
PreferenceRule = Callable[[ToolManifest, ToolChoiceContext], "tuple[float, str | None]"]

# Returns match or None when the antipattern does not trigger.
AntipatternRule = Callable[[str, ToolChoiceContext], "AntipatternMatch | None"]


_SEVERITIES: frozenset[str] = frozenset({"info", "warning", "critical"})


class ToolChoicePolicyRegistry:
    """Composable registry of preference and antipattern rules.

    A host typically constructs one registry at startup, registers the
    rules it needs, and reuses the instance across runs. Rules are pure
    functions; the registry itself holds no mutable per-run state.
    """

    def __init__(self) -> None:
        self._preferences: list[tuple[str, PreferenceRule]] = []
        self._antipatterns: list[tuple[str, AntipatternRule]] = []

    def register_preference(self, rule_id: str, rule: PreferenceRule) -> None:
        """Register one preference rule under a stable id."""
        if not isinstance(rule_id, str) or not rule_id.strip():
            raise ValueError("rule_id must be a non-empty string")
        if not callable(rule):
            raise TypeError("rule must be callable")
        self._preferences.append((rule_id.strip(), rule))

    def register_antipattern(self, rule_id: str, rule: AntipatternRule) -> None:
        """Register one antipattern rule under a stable id."""
        if not isinstance(rule_id, str) or not rule_id.strip():
            raise ValueError("rule_id must be a non-empty string")
        if not callable(rule):
            raise TypeError("rule must be callable")
        self._antipatterns.append((rule_id.strip(), rule))

    def preference_rule_ids(self) -> tuple[str, ...]:
        """Return registered preference rule ids in registration order."""
        return tuple(rule_id for rule_id, _ in self._preferences)

    def antipattern_rule_ids(self) -> tuple[str, ...]:
        """Return registered antipattern rule ids in registration order."""
        return tuple(rule_id for rule_id, _ in self._antipatterns)

    def score_candidates(
        self, context: ToolChoiceContext, *, base_score: float = 0.0
    ) -> list[ToolChoiceScore]:
        """Score each candidate manifest under all registered preference rules.

        Returns one ``ToolChoiceScore`` per candidate, in candidate order.
        ``reasons`` collects the ``rule_id:reason`` tags whose rule
        contributed a non-zero delta with a non-None reason.

        A rule that raises is isolated — its delta is treated as 0 and a
        synthetic reason ``"rule_error:<ExcClass>"`` is appended so hosts
        can spot misbehaving rules.
        """
        scored: list[ToolChoiceScore] = []
        for manifest in context.candidate_tools:
            score = float(base_score)
            reasons: list[str] = []
            for rule_id, rule in self._preferences:
                try:
                    delta, reason = rule(manifest, context)
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    reasons.append(f"rule_error:{rule_id}:{type(exc).__name__}")
                    continue
                if not isinstance(delta, (int, float)):
                    reasons.append(f"rule_invalid_delta:{rule_id}")
                    continue
                if delta == 0:
                    continue
                score += float(delta)
                if isinstance(reason, str) and reason:
                    reasons.append(f"{rule_id}:{reason}")
            scored.append(
                ToolChoiceScore(
                    tool_name=manifest.name,
                    score=round(score, 4),
                    reasons=tuple(reasons),
                )
            )
        return scored

    def detect_antipatterns(
        self, tool_name: str, context: ToolChoiceContext
    ) -> list[AntipatternMatch]:
        """Run every registered antipattern rule against the chosen tool.

        Rules that raise are isolated and reported as synthetic matches
        with ``severity="info"`` and ``pattern_id=f"rule_error:{rule_id}"``.
        Rules that return non-``AntipatternMatch`` values are also
        reported as ``rule_invalid_return:{rule_id}``.
        """
        matches: list[AntipatternMatch] = []
        for rule_id, rule in self._antipatterns:
            try:
                result = rule(tool_name, context)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                matches.append(
                    AntipatternMatch(
                        pattern_id=f"rule_error:{rule_id}",
                        severity="info",
                        description=f"rule '{rule_id}' raised {type(exc).__name__}",
                    )
                )
                continue
            if result is None:
                continue
            if not isinstance(result, AntipatternMatch):
                matches.append(
                    AntipatternMatch(
                        pattern_id=f"rule_invalid_return:{rule_id}",
                        severity="info",
                        description=(
                            f"rule '{rule_id}' returned "
                            f"{type(result).__name__} instead of AntipatternMatch"
                        ),
                    )
                )
                continue
            matches.append(result)
        return matches


def antipattern_to_warning_payload(match: AntipatternMatch) -> dict[str, Any]:
    """Project an ``AntipatternMatch`` into a runtime warning payload.

    Returns a dict suitable as the ``payload`` of a
    ``RuntimeEventType.WARNING`` event with ``kind="tool_choice_antipattern"``.
    The same dict is also what
    ``agent_driver.adapters.project_warning_event`` parses for the
    ``tool_choice_antipattern`` kind. Fields:

    - ``kind`` — always ``"tool_choice_antipattern"``;
    - ``signal_id`` — copied from ``match.pattern_id``;
    - ``severity`` — copied from ``match.severity`` (defaults to
      ``"warning"`` if the value is not one of ``info|warning|critical``);
    - ``description`` — copied from ``match.description``;
    - ``matched_recent_tool`` / ``matched_current_tool`` — optional;
    - ``rule_metadata`` — copy of ``match.metadata`` (may be empty).
    """
    severity = match.severity if match.severity in _SEVERITIES else "warning"
    payload: dict[str, Any] = {
        "kind": "tool_choice_antipattern",
        "signal_id": match.pattern_id,
        "severity": severity,
        "description": match.description,
    }
    if match.matched_recent_tool is not None:
        payload["matched_recent_tool"] = match.matched_recent_tool
    if match.matched_current_tool is not None:
        payload["matched_current_tool"] = match.matched_current_tool
    if match.metadata:
        payload["rule_metadata"] = dict(match.metadata)
    return payload


# ---------------------------------------------------------------------------
# Reference built-in rules (host can import directly or write its own)
# ---------------------------------------------------------------------------


def prefer_specialized_over_generic(
    manifest: ToolManifest, _context: ToolChoiceContext
) -> tuple[float, str | None]:
    """Boost tools that declare non-empty ``capabilities`` metadata.

    A tool whose manifest carries
    ``metadata["capabilities"]`` as a non-empty iterable receives a small
    positive delta; tools without that signal stay at the base score.
    Host applications layering richer signals (stage tags, scope alignment,
    etc.) typically combine this rule with their own.
    """
    capabilities = manifest.metadata.get("capabilities")
    if isinstance(capabilities, (list, tuple, set, frozenset)) and capabilities:
        return 0.25, f"capabilities={len(capabilities)}"
    return 0.0, None


def generic_after_specialized_search(
    chosen_tool_name: str,
    context: ToolChoiceContext,
    *,
    specialized_search_tool_names: Iterable[str] = ("tool_search",),
    generic_tool_names: Iterable[str] = ("bash", "shell", "execute_command"),
    pattern_id: str = "generic_after_specialized_search",
) -> AntipatternMatch | None:
    """Flag a generic-shell pick that follows a specialized-search call.

    The default tool-name sets are configurable so hosts can layer their
    own catalogues on top (e.g. ZION may include
    ``"tool_search"`` plus ``"recall"``).
    """
    previous = context.previous_tool()
    if previous is None:
        return None
    specialized = frozenset(specialized_search_tool_names)
    if previous not in specialized:
        return None
    generic = frozenset(generic_tool_names)
    if chosen_tool_name not in generic:
        return None
    return AntipatternMatch(
        pattern_id=pattern_id,
        severity="warning",
        description=(
            f"chose generic tool {chosen_tool_name!r} immediately after specialized "
            f"search {previous!r}"
        ),
        matched_recent_tool=previous,
        matched_current_tool=chosen_tool_name,
    )


def build_default_tool_choice_registry() -> ToolChoicePolicyRegistry:
    """Build a registry pre-loaded with the reference built-in rules.

    Hosts can either start from this registry and add their own rules, or
    create a fresh ``ToolChoicePolicyRegistry()`` and register only the
    rules they want. The default registry includes:

    - preference ``prefer_specialized_over_generic`` (capabilities boost);
    - antipattern ``generic_after_specialized_search`` (shell after
      ``tool_search`` warning).
    """
    registry = ToolChoicePolicyRegistry()
    registry.register_preference(
        "prefer_specialized_over_generic", prefer_specialized_over_generic
    )
    registry.register_antipattern(
        "generic_after_specialized_search", generic_after_specialized_search
    )
    return registry


def _is_sequence_of_str(value: Any) -> bool:
    """Return True for a non-mutable sequence whose entries are all strings."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return False
    return all(isinstance(item, str) for item in value)


__all__ = [
    "AntipatternMatch",
    "AntipatternRule",
    "PreferenceRule",
    "ToolChoiceContext",
    "ToolChoicePolicyRegistry",
    "ToolChoiceScore",
    "antipattern_to_warning_payload",
    "build_default_tool_choice_registry",
    "generic_after_specialized_search",
    "prefer_specialized_over_generic",
]
