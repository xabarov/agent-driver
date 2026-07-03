"""Regression tests for planned tool argument normalization."""

from __future__ import annotations

from agent_driver.tools.executor.normalization import _normalize_tool_args


def test_file_write_content_preserves_jsonl_string() -> None:
    content = '{"status": "candidate"}\n{"status": "verified"}\n'

    args = _normalize_tool_args(
        "file_write",
        {"path": "research/sources.jsonl", "content": content},
    )

    assert args["content"] == content


def test_non_file_write_json_string_args_still_coerce() -> None:
    args = _normalize_tool_args(
        "notebook_edit",
        {"path": "analysis.ipynb", "cells": '[{"cell_type": "markdown"}]'},
    )

    assert args["cells"] == [{"cell_type": "markdown"}]
