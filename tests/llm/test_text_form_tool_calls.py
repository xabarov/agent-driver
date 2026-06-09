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


def test_extract_deepseek_dsml_invoke_with_string_and_json_values() -> None:
    # DeepSeek v4 leaks tool calls in Claude-style invoke/parameter XML wrapped
    # in fullwidth ｜｜DSML｜｜ markers. ``string="false"`` → JSON-parse the value.
    text = (
        "Записываю значение.\n\n"
        "<｜｜DSML｜｜tool_calls>\n"
        "<｜｜DSML｜｜invoke name=\"excel_set_cell\">\n"
        "<｜｜DSML｜｜parameter name=\"sheet_name\" string=\"true\">Sales</｜｜DSML｜｜parameter>\n"
        "<｜｜DSML｜｜parameter name=\"value\" string=\"false\">1420</｜｜DSML｜｜parameter>\n"
        "</｜｜DSML｜｜invoke>\n"
        "</｜｜DSML｜｜tool_calls>"
    )
    planned, errors = extract_text_form_tool_calls(text)
    assert not errors
    assert planned and planned[0]["tool_name"] == "excel_set_cell"
    assert planned[0]["args"] == {"sheet_name": "Sales", "value": 1420}


def test_extract_deepseek_dsml_parses_2d_array_value() -> None:
    text = (
        "<｜｜DSML｜｜tool_calls>\n"
        "<｜｜DSML｜｜invoke name=\"excel_set_range\">\n"
        "<｜｜DSML｜｜parameter name=\"sheet_name\" string=\"true\">Summary</｜｜DSML｜｜parameter>\n"
        "<｜｜DSML｜｜parameter name=\"anchor\" string=\"true\">B2</｜｜DSML｜｜parameter>\n"
        "<｜｜DSML｜｜parameter name=\"values\" string=\"false\">[[2070], [600], [891]]</｜｜DSML｜｜parameter>\n"
        "</｜｜DSML｜｜invoke>\n"
        "</｜｜DSML｜｜tool_calls>"
    )
    planned, errors = extract_text_form_tool_calls(text)
    assert not errors
    assert planned[0]["tool_name"] == "excel_set_range"
    assert planned[0]["args"]["values"] == [[2070], [600], [891]]


def test_strip_deepseek_dsml_leaves_clean_prose() -> None:
    text = (
        "Сумма — 1420. Записываю.\n\n"
        "<｜｜DSML｜｜tool_calls>\n"
        "<｜｜DSML｜｜invoke name=\"excel_set_cell\">\n"
        "<｜｜DSML｜｜parameter name=\"value\" string=\"false\">1420</｜｜DSML｜｜parameter>\n"
        "</｜｜DSML｜｜invoke>\n"
        "</｜｜DSML｜｜tool_calls>"
    )
    assert strip_text_form_tool_calls(text) == "Сумма — 1420. Записываю."


def test_dsml_marker_absent_is_noop() -> None:
    # Plain prose mentioning DSML-free text must not trigger the parser.
    planned, errors = extract_text_form_tool_calls("Обычный ответ без инструментов.")
    assert planned == [] and errors == []
