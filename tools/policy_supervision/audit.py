"""Audit policy-supervision validation artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_driver.runtime.policy_supervision_audit import (
    audit_policy_supervision_artifacts,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root-dir",
        default=".agent-driver/policy-supervision",
        help="Root directory containing policy-supervision artifact subdirectories.",
    )
    parser.add_argument(
        "--require-passed",
        action="store_true",
        help="Exit non-zero unless every acceptance item is passed.",
    )
    args = parser.parse_args()
    result = audit_policy_supervision_artifacts(Path(args.root_dir))
    print(json.dumps(result, ensure_ascii=True, indent=2))
    if args.require_passed and result["status"] != "passed":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
