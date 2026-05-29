"""Tests for fallback text-form tool-call parser."""

from __future__ import annotations

from agent_driver.llm.tool_call_parser import (
    extract_text_form_tool_calls,
    strip_text_form_tool_calls,
)


def test_extract_text_form_tool_call_block_qwen_style() -> None:
    text = (
        "Сейчас вызову инструмент\n"
        "<tool_call>{\"name\":\"glob_search\",\"arguments\":{\"pattern\":\"*.md\"}}</tool_call>"
    )
    planned, errors = extract_text_form_tool_calls(text)
    assert not errors
    assert planned and planned[0]["tool_name"] == "glob_search"
    assert planned[0]["args"] == {"pattern": "*.md"}


def test_extract_text_form_tool_call_block_llama_python_tag() -> None:
    text = (
        "<|python_tag|>"
        "{\"name\":\"web_search\",\"parameters\":{\"query\":\"sam-3 model\"}}"
        "<|eom_id|>"
    )
    planned, errors = extract_text_form_tool_calls(text)
    assert not errors
    assert planned and planned[0]["tool_name"] == "web_search"
    assert planned[0]["args"] == {"query": "sam-3 model"}


def test_extract_text_form_tool_call_json_fence_after_tool_call_marker() -> None:
    text = (
        "tool_call:\n"
        "```json\n"
        "{\"name\":\"read_file\",\"arguments\":{\"path\":\"README.md\"}}\n"
        "```"
    )
    planned, errors = extract_text_form_tool_calls(text)
    assert not errors
    assert planned and planned[0]["tool_name"] == "read_file"
    assert planned[0]["args"] == {"path": "README.md"}


def test_strip_text_form_tool_calls_removes_markup_blocks() -> None:
    text = (
        "Сначала ищу.\n"
        "<tool_call>{\"name\":\"glob_search\",\"arguments\":{\"pattern\":\"*\"}}</tool_call>\n"
        "Готово."
    )
    assert strip_text_form_tool_calls(text) == "Сначала ищу.\n\nГотово."
