"""EPIC-04 WP-B — tool progress → runtime TOOL_PROGRESS events.

A handler's report_tool_progress (and, through it, a backend job's bounded
observed events) now reaches the runtime event log / stream projection,
correlated to the originating tool_call_id. Driven through the real runner +
governed tool stage.
"""

import pytest

import agent_driver.execution as ex
from agent_driver.contracts import AgentRunInput, ToolCall, ToolManifest
from agent_driver.contracts.enums import RuntimeEventType
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunnerConfig,
    wrap_governed_executor,
)
from agent_driver.tools import GovernedToolExecutor, ToolRegistry
from agent_driver.tools.context import report_tool_progress


def _run(handler, *, tool_name="longtool"):
    reg = ToolRegistry()
    reg.register(ToolManifest(name=tool_name, description="d"), handler)
    cfg = RunnerConfig(
        tool_executor=wrap_governed_executor(GovernedToolExecutor(registry=reg)),
        default_max_tool_calls=1,
    )
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=cfg,
    )
    run_input = AgentRunInput(
        input="go",
        run_id="r1",
        agent_id="a",
        graph_preset="single_react",
        tool_policy={
            "metadata": {
                "planned_tool_calls": [
                    ToolCall(
                        tool_name=tool_name, args={}, tool_call_id="TC1"
                    ).model_dump(mode="json")
                ]
            }
        },
    )
    return runner.run(run_input)


@pytest.mark.asyncio
async def test_progress_events_projected_with_tool_call_id():
    async def handler(args):
        report_tool_progress(kind="scan", message="step 1", completion_ratio=0.5)
        report_tool_progress(kind="scan", message="step 2", completion_ratio=1.0)
        return {"summary": "done"}

    out = await _run(handler)
    progress = [e for e in out.events if e.type is RuntimeEventType.TOOL_PROGRESS]
    assert len(progress) == 2
    assert all(e.payload["tool_call_id"] == "TC1" for e in progress)
    assert [e.payload["message"] for e in progress] == ["step 1", "step 2"]
    assert progress[1].payload["completion_ratio"] == 1.0


@pytest.mark.asyncio
async def test_no_progress_calls_emits_no_events():
    async def handler(args):
        return {"summary": "quiet"}

    out = await _run(handler)
    assert [e for e in out.events if e.type is RuntimeEventType.TOOL_PROGRESS] == []


@pytest.mark.asyncio
async def test_backend_job_events_bridge_through_progress():
    # A handler that observes a backend job projects each bounded job event as a
    # TOOL_PROGRESS runtime event (the job -> runtime observation bridge).
    backend = ex.FakeExecutionBackend(
        job_pages=[
            ex.ExecutionEventPage(
                events=(
                    ex.ExecutionEvent(
                        execution_generation="gen-1",
                        sequence=0,
                        kind=ex.ExecutionEventKind.OUTPUT,
                        text="line-a",
                    ),
                    ex.ExecutionEvent(
                        execution_generation="gen-1",
                        sequence=1,
                        kind=ex.ExecutionEventKind.OUTPUT,
                        text="line-b",
                        terminal=True,
                    ),
                ),
                next_cursor=ex.ExecutionEventCursor(
                    job_id="job-req1", execution_generation="gen-1", last_sequence=1
                ),
                complete=True,
            )
        ]
    )

    async def handler(args):
        from agent_driver.contracts.execution import ExecutionIdentity

        ident = ExecutionIdentity(
            backend_id="fake",
            run_id="r1",
            attempt_id="a",
            tool_call_id="TC1",
            request_id="req1",
        )
        req = ex.ExecutionCommandRequest(
            identity=ident,
            command="tail -f log",
            cwd="/w",
            timeout_seconds=30,
            max_output_chars=4000,
        )
        handle = await backend.start_job(req)
        observer = ex.JobObserver(handle)
        page = await backend.observe(handle, ex.initial_cursor(handle))
        for event in observer.ingest(page):
            report_tool_progress(kind="job", message=event.text)
        return {"summary": "observed", "complete": observer.complete}

    out = await _run(handler)
    progress = [e for e in out.events if e.type is RuntimeEventType.TOOL_PROGRESS]
    assert [e.payload["message"] for e in progress] == ["line-a", "line-b"]
    assert all(e.payload["tool_call_id"] == "TC1" for e in progress)
