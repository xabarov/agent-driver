"""Tests for fallback text-form tool-call parser."""

from __future__ import annotations

from agent_driver.llm.tool_call_parser import (
    extract_text_form_tool_call_details,
    extract_text_form_tool_calls,
    strip_text_form_tool_calls,
)


def test_extract_text_form_tool_call_block_qwen_style() -> None:
    text = (
        "Сейчас вызову инструмент\n"
        '<tool_call>{"name":"glob_search","arguments":{"pattern":"*.md"}}</tool_call>'
    )
    planned, errors = extract_text_form_tool_calls(text)
    assert not errors
    assert planned and planned[0]["tool_name"] == "glob_search"
    assert planned[0]["args"] == {"pattern": "*.md"}


def test_extract_text_form_tool_call_block_llama_python_tag() -> None:
    text = (
        "<|python_tag|>"
        '{"name":"web_search","parameters":{"query":"sam-3 model"}}'
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
        '{"name":"read_file","arguments":{"path":"README.md"}}\n'
        "```"
    )
    planned, errors = extract_text_form_tool_calls(text)
    assert not errors
    assert planned and planned[0]["tool_name"] == "read_file"
    assert planned[0]["args"] == {"path": "README.md"}


def test_strip_text_form_tool_calls_removes_markup_blocks() -> None:
    text = (
        "Сначала ищу.\n"
        '<tool_call>{"name":"glob_search","arguments":{"pattern":"*"}}</tool_call>\n'
        "Готово."
    )
    assert strip_text_form_tool_calls(text) == "Сначала ищу.\n\nГотово."


def test_extract_deepseek_dsml_invoke_with_string_and_json_values() -> None:
    # DeepSeek v4 leaks tool calls in Claude-style invoke/parameter XML wrapped
    # in fullwidth ｜｜DSML｜｜ markers. ``string="false"`` → JSON-parse the value.
    text = (
        "Записываю значение.\n\n"
        "<｜｜DSML｜｜tool_calls>\n"
        '<｜｜DSML｜｜invoke name="excel_set_cell">\n'
        '<｜｜DSML｜｜parameter name="sheet_name" string="true">Sales</｜｜DSML｜｜parameter>\n'
        '<｜｜DSML｜｜parameter name="value" string="false">1420</｜｜DSML｜｜parameter>\n'
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
        '<｜｜DSML｜｜invoke name="excel_set_range">\n'
        '<｜｜DSML｜｜parameter name="sheet_name" string="true">Summary</｜｜DSML｜｜parameter>\n'
        '<｜｜DSML｜｜parameter name="anchor" string="true">B2</｜｜DSML｜｜parameter>\n'
        '<｜｜DSML｜｜parameter name="values" string="false">[[2070], [600], [891]]</｜｜DSML｜｜parameter>\n'
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
        '<｜｜DSML｜｜invoke name="excel_set_cell">\n'
        '<｜｜DSML｜｜parameter name="value" string="false">1420</｜｜DSML｜｜parameter>\n'
        "</｜｜DSML｜｜invoke>\n"
        "</｜｜DSML｜｜tool_calls>"
    )
    assert strip_text_form_tool_calls(text) == "Сумма — 1420. Записываю."


def test_dsml_marker_absent_is_noop() -> None:
    # Plain prose mentioning DSML-free text must not trigger the parser.
    planned, errors = extract_text_form_tool_calls("Обычный ответ без инструментов.")
    assert planned == [] and errors == []


# The same DeepSeek leak is observed with ASCII ``|`` pipes (and whitespace
# around the markers) instead of the canonical fullwidth ``｜`` — depending on the
# provider/proxy + re-encoding. All variants must parse → execute, not leak.
_DSML_FW = (
    "<｜｜DSML｜｜tool_calls>"
    '<｜｜DSML｜｜invoke name="excel_write_table">'
    '<｜｜DSML｜｜parameter name="sheet_name" string="true">Sales</｜｜DSML｜｜parameter>'
    "</｜｜DSML｜｜invoke>"
    "</｜｜DSML｜｜tool_calls>"
)


def test_extract_deepseek_dsml_ascii_pipe_variant() -> None:
    planned, errors = extract_text_form_tool_calls(_DSML_FW.replace("｜", "|"))
    assert errors == []
    assert [c["tool_name"] for c in planned] == ["excel_write_table"]
    assert planned[0]["args"] == {"sheet_name": "Sales"}


def test_extract_deepseek_dsml_ascii_spaced_variant() -> None:
    spaced = (
        "Готово.\n"
        "< | DSML | tool_calls>"
        '< | DSML | invoke name="excel_write_table">'
        '< | DSML | parameter name="sheet_name" string="true">Sales</ | DSML | parameter>'
        "</ | DSML | invoke></ | DSML | tool_calls>"
    )
    planned, errors = extract_text_form_tool_calls(spaced)
    assert errors == []
    assert [c["tool_name"] for c in planned] == ["excel_write_table"]
    # The leak is stripped from the user-facing prose, leaving the clean text.
    assert strip_text_form_tool_calls(spaced) == "Готово."


def test_extract_details_returns_accepted_ranges_for_visible_filtering() -> None:
    text = (
        "До "
        '<tool_call>{"name":"glob_search","arguments":{"pattern":"*.md"}}</tool_call>'
        " после."
    )
    details = extract_text_form_tool_call_details(text)
    assert [call["tool_name"] for call in details.tool_calls] == ["glob_search"]
    assert details.parse_errors == []
    assert len(details.ranges) == 1
    tool_range = details.ranges[0]
    assert tool_range["accepted"] is True
    assert tool_range["tool_name"] == "glob_search"
    assert text[tool_range["start"] : tool_range["end"]].startswith("<tool_call>")


def test_duplicate_text_form_tool_calls_are_planned_once_but_all_ranges_remain() -> (
    None
):
    call = (
        '<tool_call>{"name":"glob_search","arguments":{"pattern":"*.md"}}</tool_call>'
    )
    text = f"{call}\n{call}"

    details = extract_text_form_tool_call_details(text)

    assert details.parse_errors == []
    assert [call["tool_name"] for call in details.tool_calls] == ["glob_search"]
    assert len(details.ranges) == 2
    assert all(item["accepted"] is True for item in details.ranges)
