"""Import-layering guard (opencode-adoption EPIC-01).

Mechanically enforce the dependency direction documented in ``docs/embedding.md``,
the way opencode enforces its ``Schema -> Core/Protocol -> Server`` rule via package
boundaries. Python has no boundary enforcement, so this AST test is it.

Three contracts, checked over MODULE-LEVEL imports only (top-of-file, not under
``if TYPE_CHECKING:``, not function/class-local — a lazy import to break a cycle is a
deliberate escape hatch, not layer drift):

* A — ``agent_driver.contracts.*`` is a pure wire/data leaf: imports NO implementation
  package.
* B — ``agent_driver.embedding`` imports only the public facades (identity re-exports).
* C — no implementation package imports the top ``agent_driver.sdk`` facade (inversion).

A small explicit BASELINE allowlists pre-existing exceptions (each a TODO to fix);
NEW violations fail. See ``docs/epics/opencode-adoption/EPIC-01-import-layering-guard.md``.
"""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_PKG = _ROOT / "agent_driver"

# Implementation packages: what ``contracts`` may not import, and the packages governed
# by contract C. (Every top-level agent_driver package that is not pure wire contracts.)
_IMPL_PKGS = {
    "runtime", "tools", "llm", "execution", "memory", "harness", "cli", "adapters",
    "mcp_server", "sdk", "gateway", "code_agent", "batch", "scheduler", "observability",
    "security", "skills", "context", "prompts", "permissions", "persistence",
    "structured", "subagents", "fs", "agents", "server", "evals",
}
# Application / adapter / facade-peer packages that sit ABOVE ``sdk`` and legitimately
# compose it (a CLI, an HTTP server, protocol adapters, the agent-definition registry).
# Contract C (no import of the sdk facade) does NOT govern these.
_APP_PKGS = {"cli", "server", "adapters", "mcp_server", "gateway", "evals", "agents"}
# Core implementation packages that sit strictly BELOW the sdk facade — these must not
# import it (contract C).
_CORE_IMPL = _IMPL_PKGS - _APP_PKGS - {"sdk"}

# Public facade roots ``embedding`` is allowed to re-export from.
_ALLOWED_FACADES = {"contracts", "sdk", "runtime", "llm", "tools", "execution", "memory"}

# Pre-existing exceptions (relative-file, imported-module). Shrinking this is follow-up
# work; growing it needs a deliberate, justified edit.
_BASELINE: set[tuple[str, str]] = {
    # provider-error classes live under sdk.errors but are consumed by the llm layer;
    # TODO relocate to contracts/llm and re-export from sdk.errors, then drop this.
    ("agent_driver/llm/error_classifier.py", "agent_driver.sdk.errors"),
}


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _module_level_import_nodes(tree: ast.Module) -> list[ast.stmt]:
    """Imports that execute at module import time (not lazy, not TYPE_CHECKING)."""
    found: list[ast.stmt] = []

    def walk(node: ast.AST, *, in_scope: bool, in_tc: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                walk(child, in_scope=True, in_tc=in_tc)
            elif isinstance(child, ast.If) and _is_type_checking(child.test):
                walk(child, in_scope=in_scope, in_tc=True)
            elif isinstance(child, (ast.Import, ast.ImportFrom)):
                if not in_scope and not in_tc:
                    found.append(child)
            else:
                walk(child, in_scope=in_scope, in_tc=in_tc)

    walk(tree, in_scope=False, in_tc=False)
    return found


def _module_name(path: Path) -> str:
    rel = path.relative_to(_ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imported_modules(path: Path) -> list[str]:
    """Absolute ``agent_driver.*`` modules imported at module level (relative resolved)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    self_mod = _module_name(path)
    out: list[str] = []
    for node in _module_level_import_nodes(tree):
        if isinstance(node, ast.Import):
            out.extend(a.name for a in node.names if a.name.startswith("agent_driver."))
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative — resolve to absolute
                base = self_mod.rsplit(".", node.level)[0] if node.level <= self_mod.count(".") + 1 else ""
                mod = f"{base}.{node.module}" if node.module else base
            else:
                mod = node.module or ""
            if mod.startswith("agent_driver."):
                out.append(mod)
    return out


def _pkg_root(module: str) -> str:
    """Second component: agent_driver.<root>.… -> <root>."""
    parts = module.split(".")
    return parts[1] if len(parts) > 1 else ""


def _py_files(root: Path):
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def _violations() -> list[str]:
    bad: list[str] = []

    def rel(path: Path) -> str:
        return str(path.relative_to(_ROOT))

    # A — contracts is a pure leaf.
    for path in _py_files(_PKG / "contracts"):
        for mod in _imported_modules(path):
            if _pkg_root(mod) in _IMPL_PKGS and (rel(path), mod) not in _BASELINE:
                bad.append(f"[A] {rel(path)} imports implementation `{mod}` (contracts must be pure)")

    # B — embedding imports only public facades.
    emb = _PKG / "embedding.py"
    if emb.exists():
        for mod in _imported_modules(emb):
            root = _pkg_root(mod)
            deep = ".single_agent." in mod or ".lifecycle." in mod
            if (root not in _ALLOWED_FACADES or deep) and (rel(emb), mod) not in _BASELINE:
                bad.append(f"[B] embedding.py imports non-facade `{mod}`")

    # C — no core-implementation package (below sdk) imports the sdk facade.
    for pkg in _CORE_IMPL:
        base = _PKG / pkg
        target = base if base.is_dir() else base.with_suffix(".py")
        if not target.exists():
            continue
        for path in (_py_files(base) if base.is_dir() else [target]):
            for mod in _imported_modules(path):
                if _pkg_root(mod) == "sdk" and (rel(path), mod) not in _BASELINE:
                    bad.append(f"[C] {rel(path)} imports the sdk facade `{mod}` (layering inversion)")
    return bad


def test_import_layering_holds() -> None:
    violations = _violations()
    assert not violations, "import-layering violations (EPIC-01):\n" + "\n".join(
        sorted(violations)
    )


def test_baseline_entries_are_still_present() -> None:
    """A baseline exception that no longer occurs should be deleted from _BASELINE.

    Guards against a stale allowlist silently masking a re-introduced violation later.
    """
    stale = []
    for rel_file, mod in _BASELINE:
        path = _ROOT / rel_file
        if not path.exists() or mod not in _imported_modules(path):
            stale.append(f"{rel_file} -> {mod}")
    assert not stale, "stale _BASELINE entries (remove them):\n" + "\n".join(sorted(stale))
