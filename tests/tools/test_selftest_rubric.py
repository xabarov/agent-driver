"""Tests for CLI self-test rubric helpers."""

from __future__ import annotations

from tools.selftest.rubric import (
    CheckResult,
    detect_provider_error,
    score_log,
    split_product_infra_checks,
)


def test_score_log_detects_chat_tool_line() -> None:
    text = "tool> web_search(query=test) status=completed\nassistant> https://example.com\n"
    checks = score_log(scenario_id="A", text=text)
    names = {item.name: item.passed for item in checks}
    assert names["used_web_search"] is True
    assert names["final_has_url"] is True


def test_score_log_detects_run_event_tool_name() -> None:
    text = "'tool_name': 'glob_search', 'status': 'completed'"
    checks = score_log(scenario_id="B", text=text)
    names = {item.name: item.passed for item in checks}
    assert names["used_glob_search"] is True


def test_detect_provider_error_maps_400_and_provider_protocol() -> None:
    assert (
        detect_provider_error("run_failed:provider_protocol")
        == "provider_protocol_error"
    )
    assert (
        detect_provider_error("httpx.HTTPStatusError: 400 Bad Request")
        == "http_400_bad_request"
    )


def test_split_product_infra_checks() -> None:
    checks = score_log(scenario_id="A", text="tool> web_search(query=x)\nhttps://x.test\n")
    checks.append(type(checks[0])(name="exit_code_zero", passed=True))
    product, infra = split_product_infra_checks(checks)
    assert any(item.name == "used_web_search" for item in product)
    assert any(item.name == "exit_code_zero" for item in infra)
