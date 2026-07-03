"""Persist validation evidence artifacts for policy/supervision gates."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from agent_driver.contracts.validation import ensure_json_serializable


def write_validation_artifacts(
    output_dir: str | Path,
    *,
    support_bundle: dict[str, Any] | None = None,
    trace_summary: dict[str, Any] | None = None,
    validation_gates: dict[str, Any] | None = None,
    evidence_index: dict[str, Any] | None = None,
    benchmark_json: dict[str, Any] | None = None,
    benchmark_markdown: str | None = None,
    phoenix_run_ids: list[str] | None = None,
    playwright_screenshots: list[str | Path] | None = None,
    extra_json_artifacts: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Write supplied validation artifacts and return a manifest.

    The helper is intentionally file-format level only: hosts decide which
    gates to run, then pass the evidence they have. Missing live artifacts are
    omitted instead of fabricated.
    """

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    artifacts: list[dict[str, Any]] = []

    if support_bundle is not None:
        artifacts.append(
            _write_json(root / "support_bundle.json", "support_bundle", support_bundle, root=root)
        )
    if trace_summary is not None:
        artifacts.append(
            _write_json(root / "trace_summary.json", "trace_summary", trace_summary, root=root)
        )
    if validation_gates is not None:
        artifacts.append(
            _write_json(
                root / "validation_gates.json",
                "validation_gates",
                validation_gates,
                root=root,
            )
        )
    if evidence_index is not None:
        artifacts.append(
            _write_json(
                root / "evidence_index.json",
                "evidence_index",
                evidence_index,
                root=root,
            )
        )
    if benchmark_json is not None:
        artifacts.append(
            _write_json(
                root / "benchmark_report.json",
                "benchmark_json",
                benchmark_json,
                root=root,
            )
        )
    if benchmark_markdown is not None:
        artifacts.append(
            _write_text(
                root / "benchmark_report.md",
                "benchmark_markdown",
                benchmark_markdown,
                root=root,
            )
        )
    if phoenix_run_ids:
        artifacts.append(
            _write_json(
                root / "phoenix_run_ids.json",
                "phoenix_run_ids",
                {"run_ids": [str(item) for item in phoenix_run_ids if str(item)]},
                root=root,
            )
        )
    for screenshot in playwright_screenshots or []:
        artifacts.append(_copy_screenshot(root, Path(screenshot)))
    for name, payload in (extra_json_artifacts or {}).items():
        safe_name = _safe_artifact_name(name)
        artifacts.append(
            _write_json(
                root / f"{safe_name}.json",
                safe_name,
                payload,
                root=root,
            )
        )

    manifest = {
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "redaction": {"safe_by_default": True},
    }
    _write_json(root / "manifest.json", "manifest", manifest, root=root)
    return manifest


def _write_json(
    path: Path,
    artifact_type: str,
    payload: dict[str, Any],
    *,
    root: Path,
) -> dict[str, Any]:
    safe_payload = ensure_json_serializable(payload, field_name=f"{artifact_type} artifact")
    path.write_text(
        json.dumps(safe_payload, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return _artifact_row(path, artifact_type, root=root)


def _write_text(
    path: Path,
    artifact_type: str,
    payload: str,
    *,
    root: Path,
) -> dict[str, Any]:
    path.write_text(payload, encoding="utf-8")
    return _artifact_row(path, artifact_type, root=root)


def _copy_screenshot(root: Path, source: Path) -> dict[str, Any]:
    screenshot_dir = root / "playwright_screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    target = screenshot_dir / source.name
    shutil.copyfile(source, target)
    return _artifact_row(target, "playwright_screenshot", root=root)


def _safe_artifact_name(name: str) -> str:
    return "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in name)


def _artifact_row(path: Path, artifact_type: str, *, root: Path) -> dict[str, Any]:
    data = path.read_bytes()
    relative = path.relative_to(root)
    return {
        "artifact_type": artifact_type,
        "path": str(relative),
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


__all__ = ["write_validation_artifacts"]
