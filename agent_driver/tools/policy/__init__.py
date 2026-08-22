"""Policy evaluation and tool-choice scoring for planned tool calls."""

from agent_driver.tools.policy.decision_hooks import (
    ToolDecisionHook,
    ToolDecisionHookResult,
    apply_decision_hooks,
    tighten_decision,
)
from agent_driver.tools.policy.evaluator import evaluate_tool_policy
from agent_driver.tools.policy.scoring import (
    AntipatternMatch,
    AntipatternRule,
    PreferenceRule,
    ToolChoiceContext,
    ToolChoicePolicyRegistry,
    ToolChoiceScore,
    antipattern_to_warning_payload,
    build_default_tool_choice_registry,
    generic_after_specialized_search,
    prefer_specialized_over_generic,
)

__all__ = [
    "AntipatternMatch",
    "AntipatternRule",
    "PreferenceRule",
    "ToolChoiceContext",
    "ToolChoicePolicyRegistry",
    "ToolChoiceScore",
    "ToolDecisionHook",
    "ToolDecisionHookResult",
    "antipattern_to_warning_payload",
    "apply_decision_hooks",
    "build_default_tool_choice_registry",
    "evaluate_tool_policy",
    "tighten_decision",
    "generic_after_specialized_search",
    "prefer_specialized_over_generic",
]
