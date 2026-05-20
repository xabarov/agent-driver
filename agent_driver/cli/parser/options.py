"""Argument-group helpers for CLI parser construction."""

from __future__ import annotations

import argparse


def add_provider_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--provider",
        choices=("fake", "openrouter", "vllm", "ollama"),
        default="fake",
        help="Provider backend for run/chat execution.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model name override for selected provider.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Provider base URL override.",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Provider API key (prefer env variables in real usage).",
    )
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=30.0,
        help="Provider request timeout in seconds.",
    )
    parser.add_argument(
        "--provider-healthcheck",
        action="store_true",
        help="Run provider healthcheck before starting run/chat.",
    )
    parser.add_argument(
        "--fake-response",
        default="ok",
        help="Fake provider response text for deterministic local runs.",
    )


def add_tool_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--tools",
        choices=("default", "none", "all"),
        default="default",
        help="Tool surface mode for model-visible and executable tools.",
    )
    parser.add_argument(
        "--tool",
        action="append",
        default=[],
        help="Exact tool name to include (repeatable).",
    )
    parser.add_argument(
        "--tool-pack",
        action="append",
        default=[],
        help="Built-in tool pack to include (repeatable).",
    )
    parser.add_argument(
        "--max-tool-risk",
        choices=("low", "medium", "high"),
        default=None,
        help="Maximum allowed tool risk for selected surface.",
    )
    parser.add_argument(
        "--allow-dangerous-tools",
        action="store_true",
        help="Allow dangerous tool packs such as shell/filesystem_write.",
    )
    parser.add_argument(
        "--enable-python",
        action="store_true",
        help="Enable python_exec tool pack and python backend runtime wiring.",
    )
    parser.add_argument(
        "--python-backend",
        choices=("local", "docker", "e2b", "wasm"),
        default=None,
        help="Python backend mode when python tool is enabled.",
    )
    parser.add_argument(
        "--python-allow-imports",
        default=None,
        help="Comma-separated extra imports allowed for python tool.",
    )
    parser.add_argument(
        "--no-python-scientific",
        action="store_true",
        help="Disable numpy/scipy/pandas in python sandbox (stdlib-only).",
    )


def add_store_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--store-kind",
        choices=("memory", "sqlite", "postgres"),
        default="memory",
        help="Runtime storage backend kind.",
    )
    parser.add_argument(
        "--sqlite-path",
        default=None,
        help="SQLite file path when --store-kind=sqlite.",
    )
    parser.add_argument(
        "--postgres-dsn",
        default=None,
        help="PostgreSQL DSN when --store-kind=postgres.",
    )
    parser.add_argument(
        "--postgres-schema",
        default="public",
        help="PostgreSQL schema for runtime rows.",
    )
    parser.add_argument(
        "--postgres-auto-create-schema",
        dest="postgres_auto_create_schema",
        action="store_true",
        default=True,
        help="Auto-create schema for postgres backend (default).",
    )
    parser.add_argument(
        "--no-postgres-auto-create-schema",
        dest="postgres_auto_create_schema",
        action="store_false",
        help="Disable schema auto-creation for postgres backend.",
    )


def add_runtime_bounds_options(
    parser: argparse.ArgumentParser,
    *,
    default_max_steps: int | None,
    default_max_tool_calls: int | None,
    default_deadline_seconds: float | None,
) -> None:
    parser.add_argument(
        "--max-steps",
        type=int,
        default=default_max_steps,
        help="Maximum runtime step budget before run fails.",
    )
    parser.add_argument(
        "--max-tool-calls",
        type=int,
        default=default_max_tool_calls,
        help="Maximum executed tool calls before run fails.",
    )
    parser.add_argument(
        "--deadline-seconds",
        type=float,
        default=default_deadline_seconds,
        help="Wall-clock deadline for one run.",
    )
    parser.add_argument(
        "--debug-tool-protocol",
        action="store_true",
        help="Emit sanitized tool-protocol debug events in runtime stream.",
    )


__all__ = [
    "add_provider_options",
    "add_runtime_bounds_options",
    "add_store_options",
    "add_tool_options",
]
