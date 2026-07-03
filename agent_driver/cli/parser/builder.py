"""Top-level parser builder for agent-driver CLI."""

from __future__ import annotations

import argparse

from agent_driver.cli.parser.options import (
    add_capability_options,
    add_provider_options,
    add_runtime_bounds_options,
    add_store_options,
    add_tool_options,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-driver",
        description="agent-driver CLI for run/replay/tail/tree/chat/ops.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run", help="Execute one run and print stream lines."
    )
    run_parser.add_argument("prompt", help="User prompt text for one run.")
    run_parser.add_argument("--run-id", default=None, help="Optional run identifier.")
    run_parser.add_argument("--agent-id", default="agent.cli", help="Agent identifier.")
    run_parser.add_argument(
        "--graph-preset",
        default="single_react",
        help="Graph preset passed into AgentRunInput.",
    )
    run_parser.add_argument(
        "--rich",
        action="store_true",
        help="Force rich rendering when optional dependency is available.",
    )
    run_parser.add_argument(
        "--plain",
        action="store_true",
        help="Disable rich rendering and force deterministic plain text.",
    )
    run_parser.add_argument(
        "--stream-poll-interval-ms",
        type=int,
        default=20,
        help="Polling interval for incremental stream projection.",
    )
    run_parser.add_argument(
        "--workspace",
        default=None,
        help="Workspace directory for filesystem and shell tools.",
    )
    add_runtime_bounds_options(
        run_parser,
        default_max_steps=12,
        default_max_tool_calls=6,
        default_deadline_seconds=90.0,
    )
    add_provider_options(run_parser)
    add_tool_options(run_parser)
    add_store_options(run_parser)
    add_capability_options(run_parser)

    replay_parser = subparsers.add_parser(
        "replay", help="Replay all events for one run id."
    )
    replay_parser.add_argument(
        "--run-id", required=True, help="Run identifier to replay."
    )
    add_store_options(replay_parser)

    tail_parser = subparsers.add_parser("tail", help="Show tail of run events.")
    tail_parser.add_argument(
        "--run-id", required=True, help="Run identifier to inspect."
    )
    tail_parser.add_argument(
        "--last-n", type=int, default=20, help="Number of trailing events."
    )
    tail_parser.add_argument(
        "--follow",
        action="store_true",
        help="Continue polling for new events after initial tail output.",
    )
    tail_parser.add_argument(
        "--poll-interval-ms",
        type=int,
        default=250,
        help="Follow polling interval in milliseconds.",
    )
    add_store_options(tail_parser)

    tree_parser = subparsers.add_parser("tree", help="Render step tree for one run id.")
    tree_parser.add_argument(
        "--run-id", required=True, help="Run identifier to inspect."
    )
    add_store_options(tree_parser)

    chat_parser = subparsers.add_parser("chat", help="Interactive chat session.")
    chat_parser.add_argument(
        "--agent-id", default="agent.cli", help="Agent identifier."
    )
    chat_parser.add_argument(
        "--graph-preset",
        default="single_react",
        help="Graph preset passed into AgentRunInput.",
    )
    chat_parser.add_argument(
        "--plain",
        action="store_true",
        help="Disable rich rendering and force deterministic plain text.",
    )
    chat_parser.add_argument(
        "--rich",
        action="store_true",
        help="Force rich rendering when optional dependency is available.",
    )
    chat_parser.add_argument(
        "--stream-poll-interval-ms",
        type=int,
        default=20,
        help="Polling interval for incremental stream projection.",
    )
    chat_parser.add_argument(
        "--workspace",
        default=None,
        help="Initial workspace directory for filesystem and shell tools.",
    )
    add_runtime_bounds_options(
        chat_parser,
        default_max_steps=24,
        default_max_tool_calls=12,
        default_deadline_seconds=180.0,
    )
    add_provider_options(chat_parser)
    add_tool_options(chat_parser)
    add_store_options(chat_parser)
    add_capability_options(chat_parser)
    chat_parser.add_argument(
        "--resume-session",
        default=None,
        help="Resume from persisted local chat session id.",
    )

    acp_parser = subparsers.add_parser(
        "acp",
        help="Serve the agent over the Agent Client Protocol (stdio).",
    )
    acp_parser.add_argument("--agent-id", default="agent.acp", help="Agent identifier.")
    acp_parser.add_argument(
        "--graph-preset",
        default="single_react",
        help="Graph preset passed into AgentRunInput.",
    )
    acp_parser.add_argument(
        "--acp-name",
        default="agent-driver",
        help="Agent name advertised to ACP clients.",
    )
    acp_parser.add_argument(
        "--acp-version",
        default="0.1.0",
        help="Agent version advertised to ACP clients.",
    )
    acp_parser.add_argument(
        "--acp-unstable",
        action="store_true",
        help="Negotiate the unstable ACP protocol variant.",
    )
    add_runtime_bounds_options(
        acp_parser,
        default_max_steps=24,
        default_max_tool_calls=12,
        default_deadline_seconds=180.0,
    )
    add_provider_options(acp_parser)
    add_tool_options(acp_parser)
    add_store_options(acp_parser)
    add_capability_options(acp_parser)

    serve_parser = subparsers.add_parser(
        "serve",
        help="Serve the agent over an OpenAI-compatible HTTP/SSE API.",
    )
    serve_parser.add_argument(
        "--agent-id", default="agent.serve", help="Agent identifier."
    )
    serve_parser.add_argument(
        "--graph-preset",
        default="single_react",
        help="Graph preset passed into AgentRunInput.",
    )
    serve_parser.add_argument(
        "--host", default="127.0.0.1", help="Bind host (default loopback)."
    )
    serve_parser.add_argument("--port", type=int, default=8000, help="Bind port.")
    serve_parser.add_argument(
        "--served-model-id",
        default="agent-driver",
        help="Model id advertised at /v1/models and echoed in responses.",
    )
    serve_parser.add_argument(
        "--api-key-server",
        default=None,
        help="Bearer key required from clients (else $AGENT_DRIVER_SERVER_API_KEY).",
    )
    serve_parser.add_argument(
        "--mcp",
        action="store_true",
        help="Also mount the MCP Streamable-HTTP endpoint at /mcp.",
    )
    serve_parser.add_argument(
        "--a2a",
        action="store_true",
        help="Also mount the A2A Agent Card + JSON-RPC endpoint (/a2a).",
    )
    serve_parser.add_argument(
        "--persist",
        default=None,
        metavar="SQLITE_PATH",
        help="Persist server state (sessions/responses/A2A tasks) to a SQLite "
        "file so it survives restart (default: in-memory).",
    )
    serve_parser.add_argument(
        "--cors-origin",
        action="append",
        default=None,
        metavar="ORIGIN",
        help="Allowed CORS origin for browser clients (repeatable; '*' for any).",
    )
    add_runtime_bounds_options(
        serve_parser,
        default_max_steps=24,
        default_max_tool_calls=12,
        default_deadline_seconds=180.0,
    )
    add_provider_options(serve_parser)
    add_tool_options(serve_parser)
    add_store_options(serve_parser)
    add_capability_options(serve_parser)

    config_parser = subparsers.add_parser("config", help="Configuration helpers.")
    config_subparsers = config_parser.add_subparsers(
        dest="config_command", required=True
    )
    config_subparsers.add_parser("show", help="Print resolved CLI config (JSON).")

    doctor_parser = subparsers.add_parser(
        "doctor", help="Diagnostics for config/provider/runtime."
    )
    doctor_parser.add_argument(
        "--agent-id", default="agent.cli", help="Agent identifier."
    )
    doctor_parser.add_argument(
        "--graph-preset",
        default="single_react",
        help="Graph preset for optional live check.",
    )
    doctor_parser.add_argument(
        "--live-check",
        action="store_true",
        help="Run one short live check against configured provider/runtime.",
    )
    add_runtime_bounds_options(
        doctor_parser,
        default_max_steps=8,
        default_max_tool_calls=4,
        default_deadline_seconds=30.0,
    )
    add_provider_options(doctor_parser)
    add_tool_options(doctor_parser)
    add_store_options(doctor_parser)

    inspect_parser = subparsers.add_parser(
        "inspect", help="Inspect one run in text/json."
    )
    inspect_parser.add_argument(
        "--run-id", required=True, help="Run identifier to inspect."
    )
    inspect_parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Inspect output format.",
    )
    add_store_options(inspect_parser)

    export_parser = subparsers.add_parser("export", help="Export one run to file.")
    export_parser.add_argument(
        "--run-id", required=True, help="Run identifier to export."
    )
    export_parser.add_argument(
        "--format",
        choices=("markdown", "jsonl"),
        default="markdown",
        help="Export file format.",
    )
    export_parser.add_argument(
        "--output",
        required=True,
        help="Output path for exported run artifact.",
    )
    add_store_options(export_parser)

    sessions_parser = subparsers.add_parser(
        "sessions", help="Manage local chat session metadata."
    )
    sessions_subparsers = sessions_parser.add_subparsers(
        dest="sessions_command", required=True
    )
    sessions_subparsers.add_parser("list", help="List local sessions.")
    sessions_show = sessions_subparsers.add_parser(
        "show", help="Show one session details."
    )
    sessions_show.add_argument(
        "--session-id", required=True, help="Session identifier."
    )

    capability_pack_parser = subparsers.add_parser(
        "capability-pack",
        help="Inspect and dry-run harness capability packs.",
    )
    capability_pack_sub = capability_pack_parser.add_subparsers(
        dest="capability_pack_command", required=True
    )
    capability_pack_dry_run = capability_pack_sub.add_parser(
        "dry-run",
        help="Resolve pack/scenario gates without executing commands.",
    )
    capability_pack_dry_run.add_argument(
        "--pack-id",
        required=True,
        choices=("excel_workbook_chat", "deep_research_chat_demo"),
        help="Capability pack id to resolve.",
    )
    capability_pack_dry_run.add_argument(
        "--adapter-id",
        choices=("excel_ai", "chat_demo"),
        default=None,
        help="Adapter manifest id; defaults from the selected pack family.",
    )
    capability_pack_dry_run.add_argument(
        "--scenario-id",
        action="append",
        default=[],
        help="Scenario id to include; defaults to all seed scenarios for adapter.",
    )
    capability_pack_dry_run.add_argument(
        "--output-dir",
        default=None,
        help="Optional directory for dry-run manifest and evidence_index.json.",
    )
    capability_pack_run = capability_pack_sub.add_parser(
        "run-deterministic",
        help="Execute deterministic pack gates with guarded command capture.",
    )
    capability_pack_run.add_argument(
        "--pack-id",
        required=True,
        choices=("excel_workbook_chat", "deep_research_chat_demo"),
        help="Capability pack id to resolve.",
    )
    capability_pack_run.add_argument(
        "--adapter-id",
        choices=("excel_ai", "chat_demo"),
        default=None,
        help="Adapter manifest id; defaults from the selected pack family.",
    )
    capability_pack_run.add_argument(
        "--scenario-id",
        action="append",
        default=[],
        help="Scenario id to include; defaults to all seed scenarios for adapter.",
    )
    capability_pack_run.add_argument(
        "--deterministic-command",
        action="append",
        default=[],
        help=(
            "Concrete deterministic command to execute instead of adapter "
            "templates (repeatable)."
        ),
    )
    capability_pack_run.add_argument(
        "--cwd",
        default=".",
        help="Working directory for deterministic commands.",
    )
    capability_pack_run.add_argument(
        "--timeout-seconds",
        type=float,
        default=60.0,
        help="Timeout per deterministic command.",
    )
    capability_pack_run.add_argument(
        "--output-dir",
        default=None,
        help="Optional directory for command outputs and evidence_index.json.",
    )
    capability_pack_audit = capability_pack_sub.add_parser(
        "audit",
        help="Audit persisted evidence indexes and write validation reports.",
    )
    capability_pack_audit.add_argument(
        "--evidence-index-dir",
        action="append",
        required=True,
        help=(
            "Directory containing evidence_index.json/manifest.json, or a direct "
            "path to evidence_index.json (repeatable)."
        ),
    )
    capability_pack_audit.add_argument(
        "--baseline-id",
        action="append",
        default=[],
        help="Seed baseline id to compare against; defaults from pack ids.",
    )
    capability_pack_audit.add_argument(
        "--quarantine-file",
        default=None,
        help="Optional FlakeRecord JSON list, or {'flakes': [...]}, to apply.",
    )
    capability_pack_audit.add_argument(
        "--no-live",
        action="store_true",
        help="Mark unexecuted live/provider/UI/benchmark gates as no-claim.",
    )
    capability_pack_audit.add_argument(
        "--strict",
        action="store_true",
        help="Return exit code 1 when required deterministic evidence is invalid.",
    )
    capability_pack_audit.add_argument(
        "--output-dir",
        default=None,
        help="Optional directory for validation_run.json and validation_report.md.",
    )

    provider_catalog_parser = subparsers.add_parser(
        "provider-catalog",
        help="Build deterministic provider plugin/catalog/routing evidence.",
    )
    provider_catalog_sub = provider_catalog_parser.add_subparsers(
        dest="provider_catalog_command", required=True
    )
    provider_catalog_audit = provider_catalog_sub.add_parser(
        "audit",
        help="Write offline provider compatibility reports and sanitizer matrix.",
    )
    provider_catalog_audit.add_argument(
        "--scenario",
        default="provider_catalog.sanitizer_matrix.v1",
        choices=(
            "provider_catalog.plugin_registry.v1",
            "provider_catalog.sanitizer_matrix.v1",
            "provider_catalog.openrouter_preflight.v1",
            "provider_catalog.excel_workbook_routes.v1",
            "provider_catalog.chat_demo_research_routes.v1",
        ),
        help="Provider catalog scenario id to label the deterministic report.",
    )
    provider_catalog_audit.add_argument(
        "--no-live",
        action="store_true",
        help="Keep provider/Phoenix/benchmark gates as no-claim.",
    )
    provider_catalog_audit.add_argument(
        "--live",
        action="store_true",
        help="Reserved for future opt-in live provider probes.",
    )
    provider_catalog_audit.add_argument(
        "--output-dir",
        default=".agent-driver/provider-catalog/deterministic",
        help="Directory for provider compatibility artifacts.",
    )

    harness_adapter_parser = subparsers.add_parser(
        "harness-adapter",
        help="Build deterministic harness adapter compatibility reports.",
    )
    harness_adapter_sub = harness_adapter_parser.add_subparsers(
        dest="harness_adapter_command", required=True
    )
    harness_adapter_compat = harness_adapter_sub.add_parser(
        "compat",
        help="Project offline evidence and optional run replay into adapter report.",
    )
    harness_adapter_compat.add_argument(
        "--adapter",
        required=True,
        choices=("acp", "chat_demo", "excel_ai"),
        help="Adapter/product compatibility target.",
    )
    harness_adapter_compat.add_argument(
        "--evidence-index-dir",
        required=True,
        help="Directory containing evidence_index.json, or direct index path.",
    )
    harness_adapter_compat.add_argument(
        "--run-id",
        default=None,
        help="Optional persisted run id to project into adapter events.",
    )
    harness_adapter_compat.add_argument(
        "--session-id",
        default=None,
        help="Optional host session id to attach to adapter rows.",
    )
    harness_adapter_compat.add_argument(
        "--no-live",
        action="store_true",
        help="Do not claim live/provider/Phoenix/Playwright evidence.",
    )
    harness_adapter_compat.add_argument(
        "--output-dir",
        default=None,
        help="Optional directory for adapter compatibility report artifacts.",
    )
    add_store_options(harness_adapter_compat)

    skills_lifecycle_parser = subparsers.add_parser(
        "skills-lifecycle",
        help="Build deterministic skills lifecycle/provenance artifacts.",
    )
    skills_lifecycle_sub = skills_lifecycle_parser.add_subparsers(
        dest="skills_lifecycle_command", required=True
    )
    skills_lifecycle_audit = skills_lifecycle_sub.add_parser(
        "audit",
        help="Scan skills and write inventory/lock/diff/selection evidence.",
    )
    skills_lifecycle_audit.add_argument(
        "--scenario",
        default="skills_lifecycle.inventory_lock_diff.v1",
        choices=(
            "skills_lifecycle.inventory_lock_diff.v1",
            "skills_lifecycle.selection_evidence.v1",
            "skills_lifecycle.invocation_provenance.v1",
            "skills_lifecycle.excel_workbook_skills.v1",
            "skills_lifecycle.chat_demo_research_skills.v1",
        ),
        help="Skill lifecycle scenario id to label the deterministic report.",
    )
    skills_lifecycle_audit.add_argument(
        "--skills-dir",
        default=None,
        help="Directory containing SKILL.md packages; defaults to curated skills.",
    )
    skills_lifecycle_audit.add_argument(
        "--previous-lock",
        default=None,
        help="Optional previous skills_lock.json for reload diff comparison.",
    )
    skills_lifecycle_audit.add_argument(
        "--product-family",
        default=None,
        help="Product family filter; defaults from scenario.",
    )
    skills_lifecycle_audit.add_argument(
        "--host-profile",
        default=None,
        help="Host/profile id for generated lock/report.",
    )
    skills_lifecycle_audit.add_argument(
        "--task-intent",
        default=None,
        help="Optional task intent for selection evidence.",
    )
    skills_lifecycle_audit.add_argument(
        "--max-results",
        type=int,
        default=200,
        help="Maximum number of SKILL.md manifests to inventory.",
    )
    skills_lifecycle_audit.add_argument(
        "--no-live",
        action="store_true",
        help="Keep provider/Phoenix/UI/benchmark gates as no-claim.",
    )
    skills_lifecycle_audit.add_argument(
        "--output-dir",
        default=".agent-driver/skills-lifecycle/deterministic",
        help="Directory for skills lifecycle artifacts.",
    )

    resume_parser = subparsers.add_parser(
        "resume", help="Resume pending interrupt decisions."
    )
    resume_parser.add_argument(
        "action",
        choices=("approve", "reject", "cancel", "clarify", "edit"),
        help="Resume action to apply.",
    )
    resume_parser.add_argument("--run-id", required=True, help="Paused run identifier.")
    resume_parser.add_argument(
        "--interrupt-id",
        required=True,
        help="Interrupt identifier returned by paused run.",
    )
    resume_parser.add_argument(
        "--message",
        default=None,
        help="Optional message for reject/clarify actions.",
    )
    resume_parser.add_argument(
        "--edited-tool-args",
        default=None,
        help="JSON object for edit action tool args override.",
    )
    add_provider_options(resume_parser)
    add_tool_options(resume_parser)
    add_store_options(resume_parser)

    eval_parser = subparsers.add_parser(
        "eval", help="Run and inspect live CLI evaluation bundle."
    )
    eval_sub = eval_parser.add_subparsers(dest="eval_command", required=True)
    eval_run = eval_sub.add_parser("run", help="Execute live eval scenarios.")
    eval_run.add_argument(
        "--output-dir",
        default=".agent-driver/evals",
        help="Directory to write eval artifacts.",
    )
    eval_run.add_argument(
        "--offline",
        action="store_true",
        help="Run deterministic offline mode with fake provider.",
    )
    eval_run.add_argument(
        "--allow-live-without-env",
        action="store_true",
        help="Allow running eval harness without AGENT_DRIVER_RUN_LIVE_CLI_EVALS=1.",
    )
    eval_run.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue suite after a scenario failure; write failures.json in bundle.",
    )
    eval_run.add_argument(
        "--suite",
        choices=("default", "default_smoke", "deep", "regression", "all"),
        default="default",
        help="Evaluation scenario suite.",
    )
    add_provider_options(eval_run)
    add_tool_options(eval_run)
    add_store_options(eval_run)
    eval_compare = eval_sub.add_parser(
        "compare",
        help="Baseline-vs-treatment harness comparison on the general suite.",
    )
    eval_compare.add_argument(
        "--treatment",
        choices=(
            "prompt_cache",
            "tool_arg_truncation",
            "tool_concurrency",
            "budget_grace",
        ),
        default="prompt_cache",
        help="Harness axis to flip off vs on (one axis at a time).",
    )
    eval_compare.add_argument(
        "--tier",
        choices=("small", "mid", "large"),
        default="mid",
        help="Open-weight model tier (OpenRouter) for live runs.",
    )
    eval_compare.add_argument(
        "--repeats",
        type=int,
        default=5,
        help="Runs per task (N-run reliability; report median over N).",
    )
    eval_compare.add_argument(
        "--concurrency", type=int, default=4, help="Concurrent runs."
    )
    eval_compare.add_argument(
        "--max-cost-usd",
        type=float,
        default=5.0,
        help="Per-side suite spend ceiling; runs past it are skipped.",
    )
    eval_compare.add_argument(
        "--offline",
        action="store_true",
        help="Deterministic dry run with the fake provider (no network).",
    )
    add_tool_options(eval_compare)

    eval_inspect = eval_sub.add_parser(
        "inspect", help="Inspect one eval summary JSON row."
    )
    eval_inspect.add_argument(
        "--summary-json",
        default=None,
        help="Path to summary.json produced by eval run.",
    )
    eval_inspect.add_argument(
        "--artifact-json",
        default=None,
        help="Path to one scenario artifact json file produced by eval run.",
    )
    eval_inspect.add_argument(
        "--scenario-id",
        default=None,
        help="Optional scenario id filter for inspect.",
    )
    return parser


__all__ = ["build_parser"]
