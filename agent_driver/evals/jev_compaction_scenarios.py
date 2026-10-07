"""Synthetic compaction cases. Oracles are scoring-only, never model input."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_driver.contracts.messages import ChatMessage


@dataclass(frozen=True)
class CompactionScenario:
    name: str
    description: str
    messages: tuple[ChatMessage, ...]
    expected_answer: dict[str, str]
    archive_message_indexes: frozenset[int]
    protected_facts: tuple[str, ...]
    pressure: str = "compact_recommended"
    active_request: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def _message(role: str, content: str, *, protected: bool = False) -> ChatMessage:
    return ChatMessage(
        role=role,
        content=content,
        metadata={"compaction_protected": True} if protected else {},
    )

def _start() -> list[ChatMessage]:
    return [_message("system", "Answer the latest request using the conversation. Preserve exact identifiers; if unknown, use null.")]


def _noise(messages: list[ChatMessage], topic: str, count: int = 6) -> frozenset[int]:
    indexes = set()
    for i in range(count):
        indexes.update((len(messages), len(messages) + 1))
        messages.append(_message("user", f"Past discussion, {topic}, item {i + 1}: compare these options."))
        details = " ".join(
            f"Option {j}: {topic}; sample reference OLD-{i:02}-{j:02}; notes about timing, alternatives and earlier observations."
            for j in range(9)
        )
        messages.append(_message("assistant", details + " This old discussion is completed."))
    return frozenset(indexes)


def synthetic_compaction_scenarios() -> tuple[CompactionScenario, ...]:
    """Four semantic cases plus a no-pressure control; no factual web dependencies."""
    cases = []
    messages = _start()
    archive = _noise(messages, "a finished trip to Lisbon and hotel choices")
    messages.extend([
        _message("user", "New task: migrate the synthetic Billing database. The target version is PostgreSQL 17. The maintenance window is 02:00-02:20 UTC."),
        _message("assistant", "I will use that version and maintenance window for Billing."),
        _message("user", "The rollback artifact is artifact://billing/rollback-r7. Preserve this reference exactly.", protected=True),
        _message("user", 'Return only JSON with fields "database_version", "window", "rollback" for the current Billing migration.'),
    ])
    cases.append(CompactionScenario(
        "topic_switch", "Completed travel discussion followed by a database migration.", tuple(messages),
        {"database_version": "PostgreSQL 17", "window": "02:00-02:20 UTC", "rollback": "artifact://billing/rollback-r7"},
        archive, ("artifact://billing/rollback-r7",),
        active_request="Migrate Billing to PostgreSQL 17 during 02:00-02:20 UTC using the rollback artifact, then return the required JSON.",
    ))

    messages = _start()
    indexes = set()
    for i in range(6):
        indexes.update((len(messages), len(messages) + 1))
        messages.extend([
            _message("assistant", f"Read the completed synthetic health-check batch {i}."),
            _message("tool", "\n".join(
                f"batch={i} probe={j} service=retired-demo status=200 latency_ms={10 + j} cache=warm health=green"
                for j in range(14)
            )),
        ])
    messages.extend([
        _message("user", "The current unresolved error is E_MIGRATION_LOCK_TIMEOUT. Do not mark the migration as fixed.", protected=True),
        _message("user", "The evidence artifact is artifact://billing/lock-trace-62 and its source path is /workspace/evidence/lock-62.log.", protected=True),
        _message("assistant", "The next action is inspect_lock_owner; no remediation has been performed."),
        _message("user", 'Return only JSON with fields "error", "artifact", "path", "next_action" for the unresolved incident.'),
    ])
    cases.append(CompactionScenario(
        "tool_noise_with_protected_artifact", "Completed tool batches surrounding an unresolved error and exact evidence.", tuple(messages),
        {"error": "E_MIGRATION_LOCK_TIMEOUT", "artifact": "artifact://billing/lock-trace-62", "path": "/workspace/evidence/lock-62.log", "next_action": "inspect_lock_owner"},
        frozenset(indexes), ("E_MIGRATION_LOCK_TIMEOUT", "artifact://billing/lock-trace-62", "/workspace/evidence/lock-62.log"),
        active_request="Investigate the unresolved Billing migration lock timeout without claiming it is fixed; return the exact evidence and next action.",
        metadata={"unresolved_errors": ["E_MIGRATION_LOCK_TIMEOUT"]},
    ))

    messages = _start()
    archive = _noise(messages, "retired documentation site color experiments", 5)
    messages.extend([
        _message("user", "Initial deployment draft for Falcon: use port 8080, mode http, image falcon:rc1."),
        _message("assistant", "Draft recorded, no deployment was made."),
        _message("user", "Correction, replacing that entire draft: final port is 8443, mode is https, image is falcon:rc3. The old values must not be used."),
        _message("assistant", "The corrected configuration is authoritative."),
        _message("user", "The final config file is /workspace/falcon/deploy/final.yaml.", protected=True),
        _message("user", 'Return only JSON with fields "port", "mode", "image", "config" for the final Falcon deployment.'),
    ])
    cases.append(CompactionScenario(
        "corrected_decision", "A later user correction overrides an unprotected draft.", tuple(messages),
        {"port": "8443", "mode": "https", "image": "falcon:rc3", "config": "/workspace/falcon/deploy/final.yaml"},
        # The superseded draft remains available to the summarizer so it can
        # preserve the correction relationship; only completed noise is safe
        # for direct archival.
        archive,
        ("/workspace/falcon/deploy/final.yaml",),
        active_request="Use only the corrected Falcon deployment configuration: port 8443, https, falcon:rc3, and the final config path.",
    ))

    messages = _start()
    _noise(messages, "Alpha and Beta deployment comparison; neither alternative has been rejected", 5)
    messages.extend([
        _message("user", "Alpha uses eu-north-1 and Beta uses eu-west-3. Both are active candidates; no service has been selected."),
        _message("assistant", "Selection is pending the user's clarification."),
        _message("user", "Approval remains approval-pending-42; do not deploy either service.", protected=True),
        _message("user", 'For the deployment comparison return only JSON with fields "alpha_region", "beta_region", "selected", "approval". Use "undecided" for a service not yet chosen.'),
    ])
    cases.append(CompactionScenario(
        "low_confidence_fallback", "Two still-active alternatives: preserve uncertainty instead of choosing one.", tuple(messages),
        {"alpha_region": "eu-north-1", "beta_region": "eu-west-3", "selected": "undecided", "approval": "approval-pending-42"},
        frozenset(), ("approval-pending-42",),
        active_request="Compare both still-active deployment alternatives; do not choose or deploy either one while approval is pending.",
    ))
    cases.append(CompactionScenario(
        "no_pressure_control", "Small context must cause zero decision or summary calls.",
        tuple(_start() + [
            _message("user", "The synthetic release label is mercury-r9."),
            _message("user", 'Return only JSON with field "release".'),
        ]),
        {"release": "mercury-r9"}, frozenset(), (), pressure="ok",
        active_request="Return the synthetic release label mercury-r9.",
    ))
    return tuple(cases)
