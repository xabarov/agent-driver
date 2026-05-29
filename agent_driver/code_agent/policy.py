"""Static policy checks for CodeAgent actions."""

from __future__ import annotations

import ast
from dataclasses import dataclass

from agent_driver.code_agent.contracts import CodeAgentLimits

DEFAULT_FORBIDDEN_MODULES = {"os", "subprocess", "socket", "shutil"}
DEFAULT_FORBIDDEN_FUNCTIONS = {"exec", "eval", "compile", "__import__"}


@dataclass(frozen=True, slots=True)
class PolicyViolation:
    """One policy violation emitted during action checks."""

    code: str
    message: str


def _contains_forbidden_import(
    node: ast.AST, forbidden_modules: set[str]
) -> str | None:
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name.split(".")[0] in forbidden_modules:
                return alias.name
    if isinstance(node, ast.ImportFrom) and node.module:
        if node.module.split(".")[0] in forbidden_modules:
            return node.module
    return None


def validate_code_action(  # pylint: disable=too-many-locals
    *,
    code: str,
    limits: CodeAgentLimits,
    authorized_imports: set[str] | None = None,
    forbidden_modules: set[str] | None = None,
    forbidden_functions: set[str] | None = None,
) -> list[PolicyViolation]:
    """Validate code action against import/call/dunder/loop limits."""
    violations: list[PolicyViolation] = []
    allowed_imports = authorized_imports or set()
    blocked_modules = forbidden_modules or DEFAULT_FORBIDDEN_MODULES
    blocked_functions = forbidden_functions or DEFAULT_FORBIDDEN_FUNCTIONS
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [PolicyViolation(code="syntax_error", message=str(exc))]
    if len(list(ast.walk(tree))) > limits.max_operations:
        violations.append(
            PolicyViolation(code="max_operations", message="operation limit exceeded")
        )
    loop_count = sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, (ast.For, ast.While, ast.AsyncFor))
    )
    if loop_count > limits.max_loops:
        violations.append(
            PolicyViolation(code="max_loops", message="loop limit exceeded")
        )
    for node in ast.walk(tree):
        forbidden_import = _contains_forbidden_import(node, blocked_modules)
        if forbidden_import:
            violations.append(
                PolicyViolation(
                    code="forbidden_import",
                    message=f"forbidden import '{forbidden_import}'",
                )
            )
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imported_names: list[str] = []
            if isinstance(node, ast.Import):
                imported_names = [alias.name.split(".")[0] for alias in node.names]
            elif node.module:
                imported_names = [node.module.split(".")[0]]
            for imported in imported_names:
                if imported not in allowed_imports:
                    violations.append(
                        PolicyViolation(
                            code="unauthorized_import",
                            message=f"unauthorized import '{imported}'",
                        )
                    )
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            violations.append(
                PolicyViolation(
                    code="dunder_access", message="dunder attribute forbidden"
                )
            )
        if isinstance(node, ast.Name) and node.id in blocked_functions:
            violations.append(
                PolicyViolation(
                    code="forbidden_function",
                    message=f"forbidden function '{node.id}'",
                )
            )
    return violations


__all__ = ["PolicyViolation", "validate_code_action"]
