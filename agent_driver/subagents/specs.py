"""Internal specs for subagent orchestration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from agent_driver.contracts.enums import (
    AgentProfile,
    SubagentJoinPolicy,
    SubagentMergeMode,
)


@dataclass(frozen=True, slots=True)
class SubagentTaskSpec:
    """One child task scheduled by parent run."""

    task_id: str
    task: str
    description: str
    profile: AgentProfile = AgentProfile.REACT_TEXT
    context_refs: tuple[str, ...] = ()
    deadline_seconds: float | None = None
    token_budget: int | None = None
    cost_budget_usd: float | None = None
    idempotency_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        """Serialize task spec for metadata/checkpoint payloads."""
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SubagentGroupSpec:
    """Fan-out group spec consumed by subagent executor."""

    group_id: str
    purpose: str
    join_policy: SubagentJoinPolicy = SubagentJoinPolicy.WAIT_ALL
    merge_mode: SubagentMergeMode = SubagentMergeMode.APPEND
    tasks: tuple[SubagentTaskSpec, ...] = ()
    max_parallel: int | None = None
    deadline_seconds: float | None = None
    token_budget: int | None = None
    cost_budget_usd: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        """Serialize group spec for metadata/checkpoint payloads."""
        payload = asdict(self)
        payload["tasks"] = [item.to_payload() for item in self.tasks]
        return payload


__all__ = ["SubagentGroupSpec", "SubagentTaskSpec"]
