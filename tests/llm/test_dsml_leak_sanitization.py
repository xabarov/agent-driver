"""DSML leak sanitization: model-noise markup never becomes payload.

DeepSeek v4 sometimes nests the NEXT tool call's markup inside a
parameter value or emits it after the plan content. The swallowed
markup is never legitimate payload: the parser cuts it from string
values, and exit_plan_mode_v2 serves plan content without it.
"""

from __future__ import annotations

import asyncio

from agent_driver.llm.tool_call_parser import (
    extract_text_form_tool_call_details,
    strip_dsml_tool_call_markup,
)
from agent_driver.tools.planning import _exit_plan_mode_v2_tool


def test_nested_tool_call_markup_is_cut_from_parameter_values() -> None:
    text = (
        "План фазы 2.\n\n"
        "<｜DSML｜tool_calls>\n<｜DSML｜invoke name=\"exit_plan_mode_v2\">\n"
        "<｜DSML｜parameter name=\"content\" string=\"true\">"
        "Обнаружение маршрутов, затем параметр-фаза.\n\n"
        "<｜DSML｜tool_calls>\n<｜DSML｜invoke name=\"sqlmap\">\n"
        "<｜DSML｜parameter name=\"url\">http://x.test/</｜DSML｜parameter>\n"
        "</｜DSML｜invoke>\n</｜DSML｜tool_calls>"
    )
    details = extract_text_form_tool_call_details(text)
    assert details.tool_calls, "the outer tool call must still parse"
    args = details.tool_calls[0]["args"]
    content = str(args.get("content"))
    assert "DSML" not in content
    assert content.startswith("Обнаружение маршрутов")


def test_stray_marker_tokens_are_dropped_from_values() -> None:
    cleaned = strip_dsml_tool_call_markup(
        "план завершён.\n\n</｜DSML｜invoke></｜DSML｜tool_calls>"
    )
    assert cleaned.strip() == "план завершён."


def test_exit_plan_mode_v2_plan_content_is_clean() -> None:
    result = asyncio.run(
        _exit_plan_mode_v2_tool(
            {
                "content": (
                    "План фазы.\n\n"
                    "<｜DSML｜tool_calls><｜DSML｜invoke name=\"sqlmap\">"
                ),
                "requested_tools": ["sqlmap"],
                "target_urls": ["http://governed-web.pentestlens-lab.test:8080/"],
            }
        )
    )
    for payload_key in ("plan", "plan_approval"):
        payload = result.get(payload_key) or {}
        assert "DSML" not in str(payload.get("content"))


def test_well_formed_values_pass_through_unchanged() -> None:
    text = "Обычный многострочный\nплан без разметки."
    assert strip_dsml_tool_call_markup(text) == text
