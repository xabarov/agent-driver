from agent_driver.evals.jev_stage7 import evaluate_latency_calibration


def _report(latency: float, *, passed: bool = True) -> dict:
    return {
        "live": True,
        "passed": passed,
        "metrics": {
            "decision_calls": 4,
            "decision_accuracy": 1.0,
            "fallback_rate": 0.0,
            "latency_ms": latency,
            "known_cost_usd": 0.0001,
            "cost_usd": 0.0001,
        },
        "transport_telemetry": {
            "phases": {
                "connect_tcp_ms": {"p95_ms": latency / 2},
                "receive_response_headers_ms": {"p95_ms": latency / 2},
            }
        },
    }


def test_stage7_selects_pooled_transport_when_target_is_met() -> None:
    report = evaluate_latency_calibration(_report(6000), _report(3000))

    assert report["promotion"]["passed"] is True
    assert report["promotion"]["recommendation"] == "promote_pooled_transport"
    assert report["calibration"]["recommended_transport"] == "pooled"


def test_stage7_holds_when_pool_improves_but_budget_is_still_missed() -> None:
    report = evaluate_latency_calibration(_report(7000), _report(6000))

    assert report["promotion"]["passed"] is False
    assert report["promotion"]["recommendation"] == "continue_pooled_calibration"
    assert report["checks"]["target_p95"] is False
