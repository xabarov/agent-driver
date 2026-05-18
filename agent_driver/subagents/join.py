"""Join policy evaluation for subagent groups."""

from __future__ import annotations

from dataclasses import dataclass

from agent_driver.contracts.enums import SubagentJoinPolicy, SubagentStatus
from agent_driver.contracts.subagents import SubagentRun


@dataclass(frozen=True, slots=True)
class JoinDecision:
    """Join evaluation result."""

    done: bool
    state: str
    completed_ids: tuple[str, ...]
    failed_ids: tuple[str, ...]
    cancelled_ids: tuple[str, ...]


def evaluate_join_policy(
    *,
    join_policy: SubagentJoinPolicy,
    runs: list[SubagentRun],
    k: int | None = None,
    deadline_reached: bool = False,
) -> JoinDecision:
    """Evaluate whether group can be joined under policy."""
    completed = [item.subagent_run_id for item in runs if item.status == SubagentStatus.COMPLETED]
    failed = [item.subagent_run_id for item in runs if item.status == SubagentStatus.FAILED]
    cancelled = [
        item.subagent_run_id
        for item in runs
        if item.status in {SubagentStatus.CANCELLED, SubagentStatus.TIMED_OUT}
    ]
    if join_policy == SubagentJoinPolicy.WAIT_ALL:
        done = len(runs) > 0 and len(completed) + len(failed) + len(cancelled) == len(runs)
        return JoinDecision(done=done, state="joined" if done else "waiting", completed_ids=tuple(completed), failed_ids=tuple(failed), cancelled_ids=tuple(cancelled))
    if join_policy == SubagentJoinPolicy.WAIT_ANY:
        done = len(completed) >= 1
        return JoinDecision(done=done, state="joined" if done else "waiting", completed_ids=tuple(completed), failed_ids=tuple(failed), cancelled_ids=tuple(cancelled))
    if join_policy == SubagentJoinPolicy.K_OF_N:
        target = max(1, k or 1)
        done = len(completed) >= target
        return JoinDecision(done=done, state="joined" if done else "waiting", completed_ids=tuple(completed), failed_ids=tuple(failed), cancelled_ids=tuple(cancelled))
    if join_policy == SubagentJoinPolicy.BEST_EFFORT_UNTIL_DEADLINE:
        done = deadline_reached
        return JoinDecision(done=done, state="partial_joined" if done else "waiting", completed_ids=tuple(completed), failed_ids=tuple(failed), cancelled_ids=tuple(cancelled))
    if join_policy == SubagentJoinPolicy.RACE:
        done = len(completed) >= 1
        return JoinDecision(done=done, state="race_won" if done else "waiting", completed_ids=tuple(completed), failed_ids=tuple(failed), cancelled_ids=tuple(cancelled))
    return JoinDecision(
        done=False,
        state="manual_review_pending",
        completed_ids=tuple(completed),
        failed_ids=tuple(failed),
        cancelled_ids=tuple(cancelled),
    )


__all__ = ["JoinDecision", "evaluate_join_policy"]
