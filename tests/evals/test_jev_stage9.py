from agent_driver.evals.jev_stage9 import build_active_evidence_labels, evaluate_stage9
from agent_driver.llm import JevOutcomeLabel


def _routing_quality_labels() -> list[JevOutcomeLabel]:
    return [
        JevOutcomeLabel(
            label_id=f"rq-{gate}-{window}-{index}",
            decision_id=f"rq-decision-{gate}-{window}-{index}",
            gate=gate,
            task="normal_chat",
            correct=True,
            safety_passed=True,
            fallback=False,
            latency_ms=800,
            cost_usd=0.00003,
            source="production_review",
            window_id=window,
        )
        for gate in ("routing", "quality")
        for window in ("w1", "w2")
        for index in range(5)
    ]


def _reports() -> tuple[dict, dict]:
    compaction = {
        "schema": "jev-compaction-benchmark.v1", "live": True,
        "rows": [
            {
                "mode": "jev", "scenario": "topic_switch", "repeat": repeat,
                "answer_exact_match": True, "protected_fact_recall": 1.0,
                "compaction_success": True, "error": None,
                "durable_transcript_unchanged": True, "cost_complete": True,
                "prepass": {},
                "decision_responses": [{
                    "request_id": f"compact-{repeat}", "latency_ms": 1000,
                    "cost_usd": 0.0002,
                }],
            }
            for repeat in range(2)
            for _ in range(4)
        ],
    }
    # Make decision ids unique while keeping two independent windows.
    for index, row in enumerate(compaction["rows"]):
        row["decision_responses"][0]["request_id"] = f"compact-{row['repeat']}-{index}"
    memory = {
        "schema": "jev-live-validation.v1", "live": True,
        "rows": [
            {
                "surface": "memory", "repeat": repeat,
                "treatments": [{}, {}, {
                    "decision_correct": True, "fallback": False,
                    "invariants": {
                        "memory_write_invariant": True,
                        "memory_provenance": True,
                        "sensitive_redacted": True,
                    },
                }],
                "call": {"request_id": f"memory-{repeat}", "latency_ms": 1000, "cost_usd": 0.00005},
            }
            for repeat in range(5)
        ],
    }
    return compaction, memory


def test_active_labels_are_raw_free_and_cover_held_gates() -> None:
    compaction, memory = _reports()
    labels = build_active_evidence_labels(compaction, memory)

    assert len(labels) == 13
    assert {label.gate for label in labels} == {"compaction", "memory"}
    assert all(label.source == "production_review" for label in labels)
    assert all("prompt" not in label.to_dict() for label in labels)


def test_stage9_requires_operator_approval_for_full_default_candidate() -> None:
    compaction, memory = _reports()
    report, labels = evaluate_stage9(
        compaction_report=compaction,
        memory_report=memory,
        routing_quality_labels=_routing_quality_labels(),
        stage7_report={"promotion": {"passed": True}},
    )

    assert len(labels) == 13
    assert report["promotion"]["promotion"]["stage"] == "normal_chat_canary"
    assert report["promotion"]["promotion"]["operator_approval_required"] is True
    assert report["promotion"]["promotion"]["default_on_enabled"] is False


def test_stage9_holds_when_active_safety_invariant_fails() -> None:
    compaction, memory = _reports()
    memory["rows"][0]["treatments"][2]["invariants"]["sensitive_redacted"] = False
    report, _ = evaluate_stage9(
        compaction_report=compaction,
        memory_report=memory,
        routing_quality_labels=_routing_quality_labels(),
        stage7_report={"promotion": {"passed": True}},
    )

    assert report["promotion"]["promotion"]["rollback_required"] is True
    assert "memory_safety" in report["promotion"]["promotion"]["reasons"]
