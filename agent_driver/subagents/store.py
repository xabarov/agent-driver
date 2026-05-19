"""Durable in-memory subagent run/group store."""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_driver.contracts.subagents import SubagentGroup, SubagentRun


@dataclass(slots=True)
class InMemorySubagentStore:
    """In-memory subagent state store with idempotent child spawn."""

    _runs_by_parent: dict[str, list[SubagentRun]] = field(default_factory=dict)
    _groups_by_parent: dict[str, list[SubagentGroup]] = field(default_factory=dict)
    _run_by_idempotency: dict[tuple[str, str], SubagentRun] = field(default_factory=dict)

    def upsert_group(self, group: SubagentGroup) -> SubagentGroup:
        """Insert or replace subagent group row."""
        rows = self._groups_by_parent.setdefault(group.parent_run_id, [])
        for idx, existing in enumerate(rows):
            if existing.group_id == group.group_id:
                rows[idx] = group
                return group
        rows.append(group)
        return group

    def list_groups(self, parent_run_id: str) -> list[SubagentGroup]:
        """List group rows by parent run."""
        return list(self._groups_by_parent.get(parent_run_id, []))

    def upsert_run(self, run: SubagentRun, *, idempotency_key: str | None = None) -> SubagentRun:
        """Insert or replace subagent run row with optional idempotency key."""
        if idempotency_key:
            dedup_key = (run.parent_run_id, idempotency_key)
            existing = self._run_by_idempotency.get(dedup_key)
            if existing is not None:
                return existing
            self._run_by_idempotency[dedup_key] = run
        rows = self._runs_by_parent.setdefault(run.parent_run_id, [])
        for idx, existing in enumerate(rows):
            if existing.subagent_run_id == run.subagent_run_id:
                rows[idx] = run
                return run
        rows.append(run)
        return run

    def list_runs(self, parent_run_id: str) -> list[SubagentRun]:
        """List child run rows by parent run."""
        return list(self._runs_by_parent.get(parent_run_id, []))


__all__ = ["InMemorySubagentStore"]
