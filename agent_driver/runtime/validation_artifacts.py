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
    command_outputs: list[dict[str, Any]] | None = None,
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
    for index, payload in enumerate(command_outputs or [], start=1):
        command_id = payload.get("command_id") if isinstance(payload, dict) else None
        name = _safe_artifact_name(str(command_id or f"command_{index}"))
        artifacts.append(
            _write_json(
                root / "command_outputs" / f"{name}.json",
                "command_output",
                payload,
                root=root,
            )
        )
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(safe_payload, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return artifact_manifest_row(path, artifact_type, root=root)


def _write_text(
    path: Path,
    artifact_type: str,
    payload: str,
    *,
    root: Path,
) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    return artifact_manifest_row(path, artifact_type, root=root)


def _copy_screenshot(root: Path, source: Path) -> dict[str, Any]:
    screenshot_dir = root / "playwright_screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    target = screenshot_dir / source.name
    shutil.copyfile(source, target)
    return artifact_manifest_row(target, "playwright_screenshot", root=root)


def _safe_artifact_name(name: str) -> str:
    return "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in name)


def artifact_manifest_row(
    path: Path, artifact_type: str, *, root: Path, include_id: bool = False
) -> dict[str, Any]:
    """One artifact manifest row: type, root-relative path, size, sha256.

    With ``include_id`` the file name is emitted first as ``artifact_id`` (durable
    lifecycle reports carry it; adapter/validation manifests do not). Shared by the
    harness report builders and the validation-artifact writer so the manifest
    shape and hash algorithm can never drift between them."""
    data = path.read_bytes()
    row: dict[str, Any] = {}
    if include_id:
        row["artifact_id"] = path.name
    row["artifact_type"] = artifact_type
    row["path"] = str(path.relative_to(root))
    row["size_bytes"] = len(data)
    row["sha256"] = hashlib.sha256(data).hexdigest()
    return row


__all__ = ["write_validation_artifacts"]
