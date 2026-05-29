"""Lightweight package-layout guardrails for refactoring hygiene."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_DRIVER_ROOT = REPO_ROOT / "agent_driver"

LEGACY_STEM_PATTERNS = (
    "tools_",
    "enums_",
    "single_agent_",
)


def _list_python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if path.is_file())


def _has_module_package_collision(py_file: Path) -> bool:
    if py_file.name == "__init__.py":
        return False
    return py_file.with_suffix("").is_dir()


def _is_legacy_stem(path: Path) -> bool:
    return any(path.stem.startswith(prefix) for prefix in LEGACY_STEM_PATTERNS)


def _is_unbounded_shim(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    marker_hits = (
        "Compatibility shim",
        "Backward-compatible import path",
    )
    if not any(marker in text for marker in marker_hits):
        return False
    return "SHIM-REMOVE-BY:" not in text


def main() -> int:
    errors: list[str] = []
    files = _list_python_files(AGENT_DRIVER_ROOT)

    for path in files:
        rel = path.relative_to(REPO_ROOT)
        if _has_module_package_collision(path):
            errors.append(f"module/package collision: {rel}")

        if _is_legacy_stem(path):
            sibling_pkg = path.with_suffix("")
            if sibling_pkg.is_dir():
                errors.append(f"legacy flat module with package sibling: {rel}")

        if _is_unbounded_shim(path):
            errors.append(f"shim without removal date: {rel}")

    if errors:
        print("Package layout check failed:")
        for err in errors:
            print(f" - {err}")
        return 1

    print("Package layout check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
