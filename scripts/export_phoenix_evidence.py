#!/usr/bin/env python3
"""Export small Phoenix evidence JSON for live-run artifacts."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECTS_QUERY = """
query PhoenixProjectEvidence($first: Int!) {
  projects(first: $first) {
    edges {
      node {
        id
        name
        recordCount
        traceCount
        tokenCountTotal
        startTime
        endTime
      }
    }
  }
}
"""


def _graphql(base_url: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps(
        {"query": query, "variables": variables},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/graphql",
        data=payload,
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
        body = response.read().decode("utf-8")
    data = json.loads(body)
    if isinstance(data, dict) and data.get("errors"):
        raise RuntimeError(json.dumps(data["errors"], ensure_ascii=False))
    if not isinstance(data, dict):
        raise RuntimeError("Phoenix GraphQL returned a non-object payload")
    return data


def export_evidence(
    *,
    base_url: str,
    project_name: str | None,
    first: int,
) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url.rstrip("/"),
        "ui_url": base_url.rstrip("/"),
        "project_name": project_name,
        "ok": False,
        "projects": [],
        "selected_project": None,
        "error": None,
    }
    try:
        response = _graphql(base_url, PROJECTS_QUERY, {"first": first})
        edges = (
            ((response.get("data") or {}).get("projects") or {}).get("edges")
            if isinstance(response.get("data"), dict)
            else None
        )
        projects = [
            item.get("node")
            for item in edges or []
            if isinstance(item, dict) and isinstance(item.get("node"), dict)
        ]
        evidence["projects"] = projects
        selected = None
        if project_name:
            selected = next(
                (
                    project
                    for project in projects
                    if project.get("name") == project_name
                ),
                None,
            )
        evidence["selected_project"] = selected or (projects[0] if projects else None)
        evidence["ok"] = True
    except (OSError, urllib.error.URLError, RuntimeError, json.JSONDecodeError) as exc:
        evidence["error"] = str(exc)
    return evidence


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:6006",
        help="Phoenix UI/GraphQL base URL.",
    )
    parser.add_argument(
        "--project",
        default=None,
        help="Preferred Phoenix project name to select in the output.",
    )
    parser.add_argument(
        "--first",
        type=int,
        default=20,
        help="How many Phoenix projects to inspect.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Path to write evidence JSON.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    evidence = export_evidence(
        base_url=args.base_url,
        project_name=args.project,
        first=max(1, args.first),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if not evidence["ok"]:
        print(f"Phoenix evidence export failed: {evidence['error']}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
