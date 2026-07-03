"""Durable lifecycle repository, planner and fixture tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from agent_driver.contracts import RuntimeEventType, new_runtime_event
from agent_driver.contracts.checkpoints import CheckpointRef
from agent_driver.contracts.durable_lifecycle import (
    BackgroundRunLease,
    DurableApprovalRecord,
    DurableApprovalStatus,
    DurableCheckpointIndex,
    DurableDurabilityLevel,
    DurableInterruptRecord,
    DurableInterruptStatus,
    DurableLeaseStatus,
    DurableLifecycleStatus,
    DurablePlanVerdict,
    DurableRunRecord,
    DurableSessionRecord,
    DurableSideEffectSafety,
)
from agent_driver.harness import (
    DurableLifecycleRepository,
    build_durable_lifecycle_compatibility_report,
    seed_durable_lifecycle_compatibility_reports,
    seed_durable_lifecycle_repository,
    seed_scenario_specs,
    write_durable_lifecycle_artifacts,
)
from agent_driver.runtime.stream import project_runtime_events


def _events(run_id: str):
    return project_runtime_events(
        [
            new_runtime_event(
                event_type=RuntimeEventType.RUN_STARTED,
                context={"run_id": run_id, "attempt_id": "attempt_1", "seq": 1},
            ),
            new_runtime_event(
                event_type=RuntimeEventType.RUN_PAUSED,
                context={"run_id": run_id, "attempt_id": "attempt_1", "seq": 2},
                options={"payload": {"interrupt_id": "interrupt_1"}},
            ),
            new_runtime_event(
                event_type=RuntimeEventType.RUN_COMPLETED,
                context={"run_id": run_id, "attempt_id": "attempt_1", "seq": 3},
            ),
        ]
    )


def test_repository_upsert_list_search_and_checkpoint_ref_bridge() -> None:
    repository = DurableLifecycleRepository()
    repository.upsert_session(
        DurableSessionRecord(
            session_id="session_1",
            workspace_id="workspace",
            adapter_id="excel_ai",
            search_metadata={"title": "Workbook lifecycle"},
        )
    )
    repository.upsert_run(DurableRunRecord(run_id="run_1", session_id="session_1"))
    checkpoint = repository.record_checkpoint_ref(
        CheckpointRef(
            checkpoint_id="checkpoint_1",
            run_id="run_1",
            attempt_id="attempt_1",
            graph_id="agent",
            created_at="2026-07-03T00:00:00Z",
            state_version="v1",
            storage_backend="sqlite",
            metadata={"api_key": "OPENROUTER_API_KEY"},
        ),
        resumable=True,
        forkable=True,
        side_effect_safety=DurableSideEffectSafety.SAFE,
    )

    assert repository.list_sessions(adapter_id="excel_ai")[0].session_id == "session_1"
    assert repository.search_sessions("workbook")[0].session_id == "session_1"
    assert checkpoint.storage_backend == DurableDurabilityLevel.SQLITE
    assert repository.get_run("run_1").latest_checkpoint_id == "checkpoint_1"


def test_attach_resume_and_fork_plans_are_truthful() -> None:
    repository = DurableLifecycleRepository()
    repository.upsert_session(
        DurableSessionRecord(
            session_id="session_1",
            transcript_available=True,
            artifact_refs=["support.json"],
        )
    )
    repository.upsert_run(
        DurableRunRecord(
            run_id="run_live",
            session_id="session_1",
            status=DurableLifecycleStatus.ACTIVE,
            active_lease_id="lease_1",
            latest_seq=1,
            reconnect_cursor="run_live:1",
            latest_checkpoint_id="checkpoint_live",
            paused_interrupt_id="interrupt_live",
        )
    )
    repository.upsert_lease(
        BackgroundRunLease(
            lease_id="lease_1",
            run_id="run_live",
            status=DurableLeaseStatus.ACTIVE,
        )
    )
    repository.upsert_checkpoint(
        DurableCheckpointIndex(
            checkpoint_id="checkpoint_live",
            run_id="run_live",
            graph_id="agent",
            state_version="v1",
            storage_backend=DurableDurabilityLevel.SQLITE,
            resumable=True,
            forkable=True,
            side_effect_safety=DurableSideEffectSafety.SAFE,
        )
    )
    repository.upsert_interrupt(
        DurableInterruptRecord(
            interrupt_id="interrupt_live",
            run_id="run_live",
            checkpoint_id="checkpoint_live",
            reason="approval",
            status=DurableInterruptStatus.PENDING,
            allowed_actions=["approve", "reject"],
        )
    )
    repository.upsert_approval(
        DurableApprovalRecord(
            approval_id="approval_live",
            interrupt_id="interrupt_live",
            run_id="run_live",
            status=DurableApprovalStatus.PENDING,
        )
    )

    attach = repository.attach_plan("run_live")
    resume = repository.resume_plan("run_live")
    fork = repository.fork_plan(run_id="run_live", session_id="session_1")

    assert attach.verdict == DurablePlanVerdict.ATTACH_LIVE
    assert resume.verdict == DurablePlanVerdict.APPROVAL_REQUIRED
    assert fork.verdict == DurablePlanVerdict.FORK_AVAILABLE


def test_checkpoint_only_does_not_claim_resume() -> None:
    repository = DurableLifecycleRepository()
    repository.upsert_run(
        DurableRunRecord(
            run_id="run_checkpoint_only",
            session_id="session_1",
            status=DurableLifecycleStatus.PAUSED,
            latest_checkpoint_id="checkpoint_only",
        )
    )
    repository.upsert_checkpoint(
        DurableCheckpointIndex(
            checkpoint_id="checkpoint_only",
            run_id="run_checkpoint_only",
            graph_id="agent",
            state_version="v1",
            storage_backend=DurableDurabilityLevel.SQLITE,
            resumable=True,
            forkable=True,
            side_effect_safety=DurableSideEffectSafety.SAFE,
        )
    )

    plan = repository.resume_plan("run_checkpoint_only")

    assert plan.verdict == DurablePlanVerdict.NO_CLAIM
    assert plan.reason == "checkpoint_exists_but_interrupt_state_missing"


def test_resume_blocks_unsafe_and_unsupported_storage() -> None:
    repository = DurableLifecycleRepository()
    for run_id, checkpoint_id, backend, safety in [
        (
            "run_unsafe",
            "checkpoint_unsafe",
            DurableDurabilityLevel.SQLITE,
            DurableSideEffectSafety.UNSAFE,
        ),
        (
            "run_process_local",
            "checkpoint_process_local",
            DurableDurabilityLevel.PROCESS_LOCAL,
            DurableSideEffectSafety.SAFE,
        ),
    ]:
        repository.upsert_run(
            DurableRunRecord(
                run_id=run_id,
                session_id="session_1",
                status=DurableLifecycleStatus.PAUSED,
                latest_checkpoint_id=checkpoint_id,
                paused_interrupt_id=f"{run_id}:interrupt",
            )
        )
        repository.upsert_checkpoint(
            DurableCheckpointIndex(
                checkpoint_id=checkpoint_id,
                run_id=run_id,
                graph_id="agent",
                state_version="v1",
                storage_backend=backend,
                resumable=True,
                side_effect_safety=safety,
            )
        )
        repository.upsert_interrupt(
            DurableInterruptRecord(
                interrupt_id=f"{run_id}:interrupt",
                run_id=run_id,
                checkpoint_id=checkpoint_id,
                reason="approval",
            )
        )

    assert repository.resume_plan("run_unsafe").verdict == (
        DurablePlanVerdict.SIDE_EFFECT_UNSAFE
    )
    assert repository.resume_plan("run_process_local").verdict == (
        DurablePlanVerdict.STORAGE_UNSUPPORTED
    )


def test_stale_lease_marks_run_orphaned_and_keeps_replay_available() -> None:
    repository = DurableLifecycleRepository()
    repository.upsert_run(
        DurableRunRecord(
            run_id="run_orphan",
            session_id="session_1",
            status=DurableLifecycleStatus.ACTIVE,
            active_lease_id="lease_1",
            latest_seq=2,
            reconnect_cursor="run_orphan:2",
        )
    )
    repository.upsert_lease(
        BackgroundRunLease(
            lease_id="lease_1",
            run_id="run_orphan",
            expires_at="2026-07-03T00:00:00Z",
            takeover_policy="manual",
        )
    )

    updated = repository.mark_expired_leases_orphaned(
        now=datetime(2026, 7, 3, 0, 1, tzinfo=timezone.utc)
    )
    attach = repository.attach_plan("run_orphan")

    assert updated[0].status == DurableLeaseStatus.ORPHANED
    assert repository.get_run("run_orphan").status == DurableLifecycleStatus.ORPHANED
    assert attach.verdict == DurablePlanVerdict.ORPHANED
    assert attach.can_replay is True


def test_terminal_runs_replay_and_fork_but_do_not_resume() -> None:
    repository = DurableLifecycleRepository()
    repository.upsert_run(
        DurableRunRecord(
            run_id="run_done",
            session_id="session_1",
            status=DurableLifecycleStatus.COMPLETED,
            latest_seq=3,
            reconnect_cursor="run_done:3",
            support_bundle_refs=["support.json"],
        )
    )

    assert repository.attach_plan("run_done").verdict == DurablePlanVerdict.TERMINAL
    assert repository.attach_plan("run_done").can_replay is True
    assert repository.resume_plan("run_done").verdict == DurablePlanVerdict.TERMINAL
    assert repository.fork_plan(run_id="run_done").verdict == (
        DurablePlanVerdict.FORK_AVAILABLE
    )


def test_adapter_replay_cursors_are_stable_after_seq() -> None:
    repository = DurableLifecycleRepository()
    repository.upsert_run(DurableRunRecord(run_id="run_stream", session_id="session_1"))
    repository.append_events(_events("run_stream"))

    rows = repository.project_adapter_events("run_stream", after_seq=1)

    assert [row.cursor for row in rows] == ["run_stream:2", "run_stream:3"]
    assert repository.get_run("run_stream").reconnect_cursor == "run_stream:3"


def test_seed_durable_lifecycle_reports_cover_products_and_truthful_no_claims() -> None:
    reports = seed_durable_lifecycle_compatibility_reports(
        generated_at=datetime(2026, 7, 3, tzinfo=timezone.utc)
    )

    assert set(reports) == {"generic", "excel_ai", "chat_demo"}
    assert reports["generic"].feature_statuses["resume_plan"] == "supported"
    assert reports["excel_ai"].resume_plans[0].verdict == DurablePlanVerdict.NO_CLAIM
    assert reports["excel_ai"].resume_plans[0].reason == (
        "side_effect_idempotency_evidence_missing"
    )
    assert reports["chat_demo"].attach_plans[0].verdict == DurablePlanVerdict.ORPHANED
    assert reports["chat_demo"].feature_statuses["background_logs"] == "supported"


def test_durable_lifecycle_artifacts_are_written(tmp_path) -> None:
    repository = seed_durable_lifecycle_repository("chat_demo")
    report = build_durable_lifecycle_compatibility_report(
        repository,
        product_family="chat_demo",
        generated_at=datetime(2026, 7, 3, tzinfo=timezone.utc),
    )
    paths = write_durable_lifecycle_artifacts(
        tmp_path,
        report,
        adapter_events=repository.project_adapter_events("chat_demo_research_run"),
    )

    payload = json.loads(
        (tmp_path / "durable_lifecycle_compatibility_report.json").read_text(
            encoding="utf-8"
        )
    )
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))

    assert payload["scenario_ids"] == [
        "durable_lifecycle.session_run_records.v1",
        "durable_lifecycle.interrupt_resume_plan.v1",
        "durable_lifecycle.background_attach_replay.v1",
        "durable_lifecycle.chat_demo_research_pause.v1",
    ]
    assert paths["adapter_events_jsonl"] is not None
    assert manifest["artifact_count"] == 4


def test_seed_scenarios_include_durable_lifecycle_targets() -> None:
    scenarios = seed_scenario_specs()

    assert "durable_lifecycle.session_run_records.v1" in scenarios
    assert "durable_lifecycle.interrupt_resume_plan.v1" in scenarios
    assert "durable_lifecycle.background_attach_replay.v1" in scenarios
    assert "durable_lifecycle.excel_workbook_pause.v1" in scenarios
    assert "durable_lifecycle.chat_demo_research_pause.v1" in scenarios
