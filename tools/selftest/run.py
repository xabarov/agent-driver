"""Run reproducible CLI self-tests for interactive scenarios."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import subprocess
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from rubric import CheckResult, detect_provider_error, score_log, summarize_score  # type: ignore
else:
    from .rubric import CheckResult, detect_provider_error, score_log, summarize_score

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIO_DIR = Path(__file__).resolve().parent / "scenarios"


@dataclass(frozen=True, slots=True)
class ScenarioSpec:
    scenario_id: str
    mode: str
    prompt: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run CLI self-test matrix.")
    parser.add_argument(
        "--matrix",
        default="m1=qwen/qwen3-235b-a22b-2507,m2=openai/gpt-4o-mini",
        help="Comma-separated model matrix (label=model).",
    )
    parser.add_argument(
        "--scenarios",
        default="A,B,C",
        help="Comma-separated scenario IDs to run.",
    )
    parser.add_argument(
        "--provider",
        default="openrouter",
        help="Provider passed to agent-driver CLI.",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Optional output directory. Default: .agent-driver/selftest/<timestamp>",
    )
    parser.add_argument("--max-steps", type=int, default=24)
    parser.add_argument("--max-tool-calls", type=int, default=12)
    parser.add_argument("--deadline-seconds", type=float, default=180.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    matrix = parse_matrix(args.matrix)
    scenario_ids = [item.strip() for item in args.scenarios.split(",") if item.strip()]
    scenarios = [load_scenario(scenario_id) for scenario_id in scenario_ids]
    out_dir = build_output_dir(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str | int]] = []
    for label, model in matrix.items():
        for scenario in scenarios:
            log_name = f"{scenario.scenario_id}_{label}.log"
            log_path = out_dir / log_name
            exit_code = run_scenario(
                scenario=scenario,
                model=model,
                provider=args.provider,
                max_steps=args.max_steps,
                max_tool_calls=args.max_tool_calls,
                deadline_seconds=args.deadline_seconds,
                log_path=log_path,
            )
            text = log_path.read_text(encoding="utf-8", errors="replace")
            checks = score_log(scenario_id=scenario.scenario_id, text=text)
            checks.append(CheckResult("exit_code_zero", exit_code == 0))
            passed, total = summarize_score(checks=checks)
            provider_error = detect_provider_error(text)
            rows.append(
                {
                    "scenario": scenario.scenario_id,
                    "model_label": label,
                    "model": model,
                    "exit_code": exit_code,
                    "score": f"{passed}/{total}",
                    "checks": ", ".join(
                        f"{item.name}={'ok' if item.passed else 'fail'}" for item in checks
                    ),
                    "provider_error": provider_error or "-",
                    "log": str(log_path),
                }
            )
    scorecard_path = out_dir / "scorecard.md"
    scorecard_path.write_text(render_scorecard(rows), encoding="utf-8")
    print(f"Self-test logs: {out_dir}")
    print(f"Scorecard: {scorecard_path}")
    return 0


def parse_matrix(raw: str) -> dict[str, str]:
    matrix: dict[str, str] = {}
    for chunk in raw.split(","):
        item = chunk.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError(f"invalid matrix item: {item}")
        label, model = item.split("=", maxsplit=1)
        matrix[label.strip()] = model.strip()
    if not matrix:
        raise ValueError("matrix cannot be empty")
    return matrix


def load_scenario(scenario_id: str) -> ScenarioSpec:
    path = SCENARIO_DIR / f"{scenario_id}.txt"
    if not path.exists():
        raise FileNotFoundError(f"missing scenario file: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    mode = "chat"
    body: list[str] = []
    for line in lines:
        if line.startswith("mode:"):
            mode = line.split(":", maxsplit=1)[1].strip().lower()
            continue
        body.append(line)
    prompt = "\n".join(body).strip() + "\n"
    if mode not in {"chat", "run"}:
        raise ValueError(f"unsupported scenario mode for {scenario_id}: {mode}")
    return ScenarioSpec(scenario_id=scenario_id, mode=mode, prompt=prompt)


def build_output_dir(raw_out: str) -> Path:
    if raw_out.strip():
        return Path(raw_out).resolve()
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return (REPO_ROOT / ".agent-driver" / "selftest" / ts).resolve()


def run_scenario(
    *,
    scenario: ScenarioSpec,
    model: str,
    provider: str,
    max_steps: int,
    max_tool_calls: int,
    deadline_seconds: float,
    log_path: Path,
) -> int:
    env = os.environ.copy()
    env["AGENT_DRIVER_MODEL"] = model
    if scenario.mode == "chat":
        cmd = [
            "uv",
            "run",
            "agent-driver",
            "chat",
            "--plain",
            "--provider",
            provider,
            "--max-steps",
            str(max_steps),
            "--max-tool-calls",
            str(max_tool_calls),
            "--deadline-seconds",
            str(deadline_seconds),
        ]
        completed = subprocess.run(  # noqa: S603
            cmd,
            cwd=REPO_ROOT,
            env=env,
            input=scenario.prompt,
            text=True,
            capture_output=True,
            check=False,
        )
    else:
        cmd = [
            "uv",
            "run",
            "agent-driver",
            "run",
            scenario.prompt.strip(),
            "--plain",
            "--provider",
            provider,
            "--max-steps",
            str(max_steps),
            "--max-tool-calls",
            str(max_tool_calls),
            "--deadline-seconds",
            str(deadline_seconds),
        ]
        completed = subprocess.run(  # noqa: S603
            cmd,
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
    log_path.write_text(
        f"$ {' '.join(cmd)}\n\n{completed.stdout}\n{completed.stderr}",
        encoding="utf-8",
    )
    return completed.returncode


def render_scorecard(rows: list[dict[str, str | int]]) -> str:
    lines = [
        "# Self-test scorecard",
        "",
        "| scenario | model_label | exit_code | score | provider_error | checks | log |",
        "| --- | --- | ---: | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| {scenario} | {model_label} | {exit_code} | {score} | {provider_error} | {checks} | `{log}` |".format(
                **row
            )
        )
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"selftest failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
