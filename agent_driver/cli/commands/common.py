"""Shared helpers for CLI command handlers."""

from __future__ import annotations

import argparse
from collections.abc import Callable


def print_provider_health(status: object) -> None:
    """Print provider health line in common CLI format."""
    print(
        "provider> "
        f"name={status.provider_name} healthy={status.healthy} "
        f"configured={status.configured} latency_ms={status.latency_ms}"
    )


def build_provider_and_toolset(
    args: argparse.Namespace,
    *,
    provider_config_from_args: Callable[[argparse.Namespace], object],
    tool_config_from_args: Callable[[argparse.Namespace], object],
    build_cli_provider: Callable[[object], object],
    build_cli_toolset: Callable[[object], object],
    provider_error: type[Exception],
    tool_error: type[Exception],
) -> tuple[object | None, object | None, int | None]:
    """Resolve provider + toolset and normalize CLI error output."""
    try:
        provider = build_cli_provider(provider_config_from_args(args))
    except provider_error as exc:
        print(f"provider error: {exc}")
        return None, None, 2
    try:
        toolset = build_cli_toolset(tool_config_from_args(args))
    except tool_error as exc:
        print(f"tools error: {exc}")
        return None, None, 2
    return provider, toolset, None


__all__ = ["build_provider_and_toolset", "print_provider_health"]
