"""Python execution builtin tool."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from agent_driver.code_agent.contracts import CodeAgentLimits
from agent_driver.code_agent.execution_common import CodeExecutionError, CodePolicyError
from agent_driver.contracts import ApprovalMode, SideEffectClass, ToolManifest, ToolRisk
from agent_driver.tools.context import get_tool_call_context
from agent_driver.tools.registry import ToolRegistry
from agent_driver.code_agent.backends.base import PythonExecutorBackend

_PYTHON_TOOL = "python"


class PythonToolSettingsLike(Protocol):
    enabled: bool
    default_imports: tuple[str, ...]
    allow_overlay: bool
    limits: CodeAgentLimits
    session_idle_seconds: float


@dataclass(frozen=True, slots=True)
class PythonToolRuntimeFacts:
    """Derived python-tool runtime facts for prompt/manifest rendering."""

    imports_sorted: tuple[str, ...]
    imports_inline: str
    imports_short: str
    overlay_allowed: bool
    max_exec_ms: int
    max_output_chars: int
    session_idle_seconds: float
    policy_summary: str


def python_tool_runtime_facts(settings: PythonToolSettingsLike) -> PythonToolRuntimeFacts:
    """Build stable, reusable runtime facts for python tool."""
    imports_sorted = tuple(
        sorted(
            {
                item.strip()
                for item in settings.default_imports
                if isinstance(item, str) and item.strip()
            }
        )
    )
    imports_inline, imports_short = _format_import_previews(imports_sorted)
    return PythonToolRuntimeFacts(
        imports_sorted=imports_sorted,
        imports_inline=imports_inline,
        imports_short=imports_short,
        overlay_allowed=settings.allow_overlay,
        max_exec_ms=settings.limits.max_exec_ms,
        max_output_chars=settings.limits.max_output_chars,
        session_idle_seconds=float(settings.session_idle_seconds),
        policy_summary=(
            "no network/fs access; subprocess sandbox; "
            "persistent session by session_id"
        ),
    )


def _format_import_previews(imports_sorted: tuple[str, ...]) -> tuple[str, str]:
    imports_inline = ", ".join(imports_sorted) if imports_sorted else "none"
    preview_limit = 8
    if len(imports_sorted) <= preview_limit:
        return imports_inline, imports_inline
    preview = ", ".join(imports_sorted[:preview_limit])
    return imports_inline, f"{preview}, +{len(imports_sorted) - preview_limit} more"


def build_python_tool_manifest(settings: PythonToolSettingsLike) -> ToolManifest:
    """Build python tool manifest with dynamic runtime facts."""
    facts = python_tool_runtime_facts(settings)
    properties: dict[str, Any] = {
        "code": {
            "type": "string",
            "description": (
                "Python code to execute; only these stdlib modules are importable: "
                f"{facts.imports_short}"
            ),
        },
        "session_id": {
            "type": "string",
            "description": "Optional interpreter session id",
        },
        "timeout_seconds": {
            "type": "number",
            "minimum": 0.1,
            "maximum": 120,
            "description": "Optional timeout override for this call",
        },
    }
    if facts.overlay_allowed:
        properties["authorized_imports"] = {
            "type": "array",
            "items": {"type": "string"},
            "description": "Extra imports to allow for this call (subject to runtime policy).",
        }
    return ToolManifest(
        name=_PYTHON_TOOL,
        description=(
            "Execute restricted Python code in a sandboxed backend. "
            "Supports session_id for persistent interpreter state. "
            f"Allowed imports: {facts.imports_inline}. {facts.policy_summary}."
        ),
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.READ_ONLY,
        approval_mode=ApprovalMode.ON_POLICY_MATCH,
        timeout_seconds=20.0,
        output_char_budget=9000,
        idempotent=False,
        args_schema={
            "type": "object",
            "properties": properties,
            "required": ["code"],
            "additionalProperties": False,
        },
        output_type="json",
        remediation_hints=[
            f"Use only allowed imports: {facts.imports_inline}.",
            "Avoid os/subprocess/socket/shutil; sandbox blocks them.",
            "Reuse the same session_id for multi-call workflows.",
        ],
    )


def python_tool_manifest() -> ToolManifest:
    """Backward-compatible default manifest builder."""
    from agent_driver.runtime.single_agent.config_sections import PythonToolSettings

    return build_python_tool_manifest(PythonToolSettings(enabled=True))


def register_python_tool(
    registry: ToolRegistry,
    *,
    backend: PythonExecutorBackend,
    settings: PythonToolSettingsLike,
) -> None:
    """Register python tool using configured backend/settings."""

    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        return await python_tool_handler(args=args, backend=backend, settings=settings)

    registry.register(build_python_tool_manifest(settings), _handler)


async def python_tool_handler(
    *,
    args: dict[str, Any],
    backend: PythonExecutorBackend,
    settings: PythonToolSettingsLike,
) -> dict[str, Any]:
    """Execute python snippet and map backend payload to tool output."""
    facts = python_tool_runtime_facts(settings)
    code = str(args.get("code") or "").strip()
    if not code:
        raise ValueError("code is required")
    context = get_tool_call_context()
    explicit_session = str(args.get("session_id") or "").strip()
    session_id = (
        explicit_session
        or context.get("thread_id")
        or context.get("run_id")
        or "python_default"
    )
    imports = set(facts.imports_sorted)
    overlay_raw = args.get("authorized_imports")
    if settings.allow_overlay and isinstance(overlay_raw, list):
        imports.update(
            str(item).strip()
            for item in overlay_raw
            if isinstance(item, str) and item.strip()
        )
    effective_imports = tuple(sorted(item for item in imports if item))
    _, effective_imports_short = _format_import_previews(effective_imports)
    limits = _resolve_limits(settings=settings, timeout_seconds=args.get("timeout_seconds"))
    try:
        result = await backend.execute(
            code=code,
            session_id=session_id,
            authorized_imports=imports,
            limits=limits,
            serialization_policy=None,
        )
    except CodePolicyError as exc:
        return {
            "summary": f"python policy: {exc}",
            "error_kind": "policy",
            "session_id": session_id,
            "executor_mode": getattr(backend, "mode", "unknown"),
            "policy_reasons": [str(exc)],
            "allowed_imports": list(effective_imports),
            "remediation": f"Use allowed imports only: {effective_imports_short}",
            "stdout": "",
            "stderr": "",
            "truncated": False,
            "result_repr": None,
            "final_answer": None,
            "structured": {"error_kind": "policy"},
        }
    except CodeExecutionError as exc:
        return {
            "summary": f"python error: {exc}",
            "error_kind": "runtime",
            "session_id": session_id,
            "executor_mode": getattr(backend, "mode", "unknown"),
            "policy_reasons": [str(exc)],
            "remediation": (
                "If no value appears, end code with an expression or use print(...); "
                "ensure variables are defined before use."
            ),
            "stdout": "",
            "stderr": "",
            "truncated": False,
            "result_repr": None,
            "final_answer": None,
            "structured": {"error_kind": "runtime"},
        }
    stdout_preview = ""
    stderr_preview = ""
    truncated = False
    for item in result.observations:
        if item.source == "stdout":
            stdout_preview = item.text_preview
            truncated = truncated or item.truncated
        elif item.source == "stderr":
            stderr_preview = item.text_preview
            truncated = truncated or item.truncated
    final_answer = result.final_answer.text if result.final_answer is not None else None
    result_repr = result.metadata.get("result_repr")
    elapsed_ms = int(result.metadata.get("elapsed_ms") or 0)
    result_repr_text = result_repr if isinstance(result_repr, str) else None
    summary = f"python ok: {len(result.observations)} obs"
    if result_repr_text:
        short_repr = result_repr_text if len(result_repr_text) <= 80 else f"{result_repr_text[:80]}..."
        summary += f", result={short_repr}"
    if elapsed_ms > 0:
        summary += f", elapsed_ms={elapsed_ms}"
    payload = {
        "summary": summary,
        "session_id": session_id,
        "executor_mode": str(
            result.metadata.get("executor_mode") or getattr(backend, "mode", "unknown")
        ),
        "elapsed_ms": elapsed_ms if elapsed_ms > 0 else None,
        "stdout": stdout_preview,
        "stderr": stderr_preview,
        "truncated": truncated,
        "result_repr": result_repr_text,
        "final_answer": final_answer,
        "structured": result.model_dump(mode="json"),
    }
    if not result.observations and result_repr_text is None and final_answer is None:
        payload["tip"] = (
            "hint: nothing was printed; use print(...) or end with an expression to "
            "surface the value"
        )
    return payload


def _resolve_limits(
    *, settings: PythonToolSettingsLike, timeout_seconds: Any
) -> CodeAgentLimits:
    limits = settings.limits.model_copy(deep=True)
    if timeout_seconds is None:
        return limits
    value = float(timeout_seconds)
    if value <= 0:
        raise ValueError("timeout_seconds must be > 0")
    override_ms = int(value * 1000)
    if override_ms < limits.max_exec_ms:
        limits.max_exec_ms = override_ms
    return limits


__all__ = [
    "PythonToolRuntimeFacts",
    "build_python_tool_manifest",
    "python_tool_manifest",
    "python_tool_runtime_facts",
    "register_python_tool",
    "python_tool_handler",
]
