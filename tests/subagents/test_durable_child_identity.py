"""opencode-adoption EPIC-11 (Stage 1) — durable, addressable subagent identity.

A spawned child is persisted in the ``subagent_store`` keyed by its ``child_run_id``;
``find_run_by_child_run_id`` addresses one across parents (and, for the durable backend,
process restarts), and ``Agent.find_subagent_run`` exposes it on the SDK. Stage 2 (actually
resuming the child run) builds on this identity.
"""

from __future__ import annotations

import tempfile

import pytest

from agent_driver.contracts.enums.subagents import SubagentStatus
from agent_driver.contracts.subagents import SubagentRun
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime.single_agent.types import RunnerConfig
from agent_driver.sdk import create_agent
from agent_driver.subagents.store import (
    InMemorySubagentStore,
    SqliteSubagentStore,
)


def _run(sub: str, parent: str, child: str) -> SubagentRun:
    return SubagentRun(
        subagent_run_id=sub,
        parent_run_id=parent,
        parent_attempt_id="a1",
        child_run_id=child,
        task_id="t1",
        task_type="generic",
        description="d",
        status=SubagentStatus.RUNNING,
    )


def _stores():
    yield InMemorySubagentStore()
    yield SqliteSubagentStore(path=tempfile.mktemp(suffix=".db"))


@pytest.mark.parametrize("store", list(_stores()))
def test_find_run_by_child_run_id(store) -> None:
    store.upsert_run(_run("sub_1", "par_1", "child_aaa"))
    store.upsert_run(_run("sub_2", "par_1", "child_bbb"))
    store.upsert_run(_run("sub_3", "par_2", "child_ccc"))

    found = store.find_run_by_child_run_id("child_bbb")
    assert found is not None
    assert found.subagent_run_id == "sub_2"
    assert found.parent_run_id == "par_1"
    # addresses across parents without knowing the parent id
    assert store.find_run_by_child_run_id("child_ccc").parent_run_id == "par_2"


@pytest.mark.parametrize("store", list(_stores()))
def test_find_run_missing_and_empty(store) -> None:
    store.upsert_run(_run("sub_1", "par_1", "child_aaa"))
    assert store.find_run_by_child_run_id("child_nope") is None
    assert store.find_run_by_child_run_id("") is None


def test_sqlite_lookup_survives_reopen() -> None:
    path = tempfile.mktemp(suffix=".db")
    SqliteSubagentStore(path=path).upsert_run(_run("sub_1", "par_1", "child_durable"))
    # a fresh store instance (simulating a process restart) still resolves it
    reopened = SqliteSubagentStore(path=path)
    found = reopened.find_run_by_child_run_id("child_durable")
    assert found is not None and found.subagent_run_id == "sub_1"


def test_agent_find_subagent_run() -> None:
    store = InMemorySubagentStore()
    store.upsert_run(_run("sub_1", "par_1", "child_zzz"))
    agent = create_agent(
        provider=FakeProvider(),
        config=RunnerConfig(subagent_store=store),
    )
    found = agent.find_subagent_run("child_zzz")
    assert found is not None
    assert found.child_run_id == "child_zzz"
    assert agent.find_subagent_run("child_absent") is None
